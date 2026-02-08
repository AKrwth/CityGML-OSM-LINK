"""
Export and logging operators for M1DC add-on.

Contains operators for exporting logs, diagnostic reports, and link mappings.
"""

import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import bpy
from bpy.types import Operator

# Import utilities
try:
    from ...utils.common import (
        get_output_dir,
        open_db_readonly,
    )
except ImportError:
    from utils.common import (
        get_output_dir,
        open_db_readonly,
    )

try:
    from ...utils.logging_system import log_info, log_warn, log_error, get_logger
except ImportError:
    from utils.logging_system import log_info, log_warn, log_error, get_logger

try:
    from ...pipeline.diagnostics import (
        run_diagnostic,
        run_debug_report,
        run_full_gpkg_gml_report,
    )
except ImportError:
    from pipeline.diagnostics import (
        run_diagnostic,
        run_debug_report,
        run_full_gpkg_gml_report,
    )


def _settings(context):
    """Get M1DC settings from scene."""
    return getattr(context.scene, "m1dc_settings", None)


def _aggregate_citygml_buildings():
    """Aggregate CityGML building data from scene (for fallback export)."""
    city_buildings = []
    tile_count = 0
    # Placeholder - actual implementation would scan CityGML objects
    return city_buildings, tile_count


class M1DC_OT_ExportLinkMapping(Operator):
    bl_idname = "m1dc.export_link_mapping"
    bl_label = "Export Link Mapping (CSV)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        try:
            out_dir = Path(s.output_dir or str(get_output_dir()))
            out_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = out_dir / f"M1DC_LinkMapping_{timestamp}.csv"
            summary_path = out_dir / f"M1DC_LinkMapping_{timestamp}_summary.txt"

            link_db_guess = Path(getattr(s, "links_db_path", "")) if getattr(s, "links_db_path", "") else None
            if (not link_db_guess or not link_db_guess.exists()) and getattr(s, "gpkg_path", ""):
                link_db_guess = Path(out_dir) / f"{Path(s.gpkg_path).stem}_links.sqlite"

            csv_rows = []
            linked_count = 0
            unmatched_count = 0
            confidences = []

            if link_db_guess and link_db_guess.exists():
                if open_db_readonly:
                    conn = open_db_readonly(str(link_db_guess), log_open=False)
                else:
                    uri = f"file:{link_db_guess.as_posix()}?mode=ro"
                    conn = sqlite3.connect(uri, uri=True)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cols = {r[1] for r in cur.execute("PRAGMA table_info('gml_osm_links');").fetchall()}
                required = {"source_tile", "building_idx", "osm_way_id"}
                if required.issubset(cols):
                    rows = cur.execute(
                        "SELECT source_tile, building_idx, osm_way_id, confidence, dist_m FROM gml_osm_links ORDER BY source_tile, building_idx;"
                    ).fetchall()
                    conn.close()
                    for r in rows:
                        data = dict(r)
                        osm_id = data.get("osm_way_id")
                        conf = float(data.get("confidence") or 0.0)
                        dist_m = float(data.get("dist_m") or 0.0)
                        status = "linked" if osm_id else "unmatched"
                        match_method = "gml_osm_links"
                        if osm_id:
                            linked_count += 1
                        else:
                            unmatched_count += 1
                        confidences.append(conf)
                        csv_rows.append(
                            {
                                "source_tile": data.get("source_tile"),
                                "building_idx": data.get("building_idx"),
                                "osm_id": osm_id or "",
                                "confidence": round(conf, 3),
                                "match_method": match_method,
                                "status": status,
                                "candidates_checked": 0,
                                "centroid_distance_m": round(dist_m, 2),
                                "bbox_iou": 0.0,
                            }
                        )
                    log_info(f"[LinkExport] Exporting {len(csv_rows)} rows from {link_db_guess}")
                else:
                    conn.close()
                    log_warn(f"[LinkExport] gml_osm_links table missing required columns in {link_db_guess}; falling back to scene data")

            if not csv_rows:
                city_buildings, tile_count = _aggregate_citygml_buildings()
                log_info(f"[LinkExport] Building candidates: {len(city_buildings)} across {tile_count} tiles")
                for entry in sorted(city_buildings, key=lambda e: (e.get("key", ("", 0))[0], e.get("key", ("", 0))[1])):
                    src_tile, bidx = entry.get("key", ("?", -1))
                    obj = entry.get("obj")
                    mesh = getattr(obj, "data", None)

                    link_map_json = obj.get("osm_link_map_json", "{}") if obj else "{}"
                    try:
                        link_map = json.loads(link_map_json)
                    except Exception:
                        link_map = {}

                    osm_id = link_map.get(str(bidx)) if link_map else None

                    if osm_id is None and mesh and hasattr(mesh, "attributes"):
                        attr = mesh.attributes.get("osm_id")
                        if attr and entry.get("faces"):
                            face_idx = entry["faces"][0]
                            try:
                                osm_id = attr.data[face_idx].value
                            except Exception:
                                osm_id = None

                    status = "linked" if osm_id else "unmatched"
                    confidence = 0.9 if osm_id else 0.0
                    match_method = "face-group-iou+centroid" if osm_id else "unmatched"
                    candidates_checked = 0
                    centroid_distance_m = 0.0
                    bbox_iou = 0.0

                    if osm_id:
                        linked_count += 1
                    else:
                        unmatched_count += 1

                    confidences.append(confidence)
                    csv_rows.append({
                        "source_tile": src_tile,
                        "building_idx": bidx,
                        "osm_id": osm_id or "",
                        "confidence": round(confidence, 3),
                        "match_method": match_method,
                        "status": status,
                        "candidates_checked": candidates_checked,
                        "centroid_distance_m": round(centroid_distance_m, 2),
                        "bbox_iou": round(bbox_iou, 3),
                    })

            csv_content = "source_tile,building_idx,osm_id,confidence,match_method,status,candidates_checked,centroid_distance_m,bbox_iou\n"
            for row in csv_rows:
                csv_content += f"{row['source_tile']},{row['building_idx']},{row['osm_id']},{row['confidence']},{row['match_method']},{row['status']},{row['candidates_checked']},{row['centroid_distance_m']},{row['bbox_iou']}\n"

            log_info(f"[LinkExport] Rows to write: {len(csv_rows)}")
            if len(csv_rows) == 0:
                log_warn("[LinkExport] CSV has zero rows (no building candidates)")

            csv_path.write_text(csv_content, encoding="utf-8")
            log_info(f"Link mapping CSV exported to {csv_path}")

            summary_lines = [
                "=" * 80,
                "M1DC Link Mapping Summary",
                "=" * 80,
                f"Exported: {datetime.now().isoformat()}",
                "",
                f"Total rows: {len(csv_rows)}",
                f"Linked buildings: {linked_count}",
                f"Unmatched buildings: {unmatched_count}",
            ]

            if confidences:
                min_conf = min(confidences)
                max_conf = max(confidences)
                avg_conf = sum(confidences) / len(confidences)
                summary_lines.extend([
                    "",
                    "Confidence Statistics:",
                    f"  Min: {min_conf:.3f}",
                    f"  Max: {max_conf:.3f}",
                    f"  Avg: {avg_conf:.3f}",
                ])

            summary_lines.extend([
                "",
                "Usage Hint:",
                "  1. Open CSV in Excel or QGIS for verification",
                "  2. Filter by status='unmatched' to find missing matches",
                "  3. Check confidence < 0.7 for potentially incorrect matches",
            ])

            summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
            log_info(f"Link mapping summary exported to {summary_path}")

            self.report({"INFO"}, f"Link mapping exported:\n  {csv_path.name}\n  {summary_path.name}")
            return {"FINISHED"}
            
        except Exception as ex:
            log_error(f"Export link mapping failed: {ex}")
            self.report({"ERROR"}, f"Export failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_ExportLog(Operator):
    bl_idname = "m1dc.export_log"
    bl_label = "Export Log (.txt)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            from pathlib import Path
            
            s = _settings(context)
            out_dir = Path(s.output_dir) if s and getattr(s, "output_dir", "").strip() else Path(str(get_output_dir()))
            out_dir.mkdir(parents=True, exist_ok=True)
            
            logger = get_logger()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = out_dir / f"M1DC_Session_{timestamp}.txt"
            logger.export_txt(log_path)
            
            self.report({"INFO"}, f"Log exported to {log_path}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Export log failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_ClearLog(Operator):
    bl_idname = "m1dc.clear_log"
    bl_label = "Clear Log"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            logger = get_logger()
            logger.clear()
            self.report({"INFO"}, "Log cleared.")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Clear log failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_ExportDiagnostic(Operator):
    bl_idname = "m1dc.export_diagnostic"
    bl_label = "Export Diagnostic"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        try:
            out_path = run_diagnostic(gpkg_path=s.gpkg_path or "", out_path=None)
            self.report({"INFO"}, f"Diagnostic saved to {out_path}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Diagnostic failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_ExportDebugReport(Operator):
    bl_idname = "m1dc.export_debug_report"
    bl_label = "Export Debug Report (Lean)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        try:
            out_path = run_debug_report(out_path=None)
            self.report({"INFO"}, f"Debug report saved to {out_path}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Debug report failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_ExportFullReport(Operator):
    bl_idname = "m1dc.export_full_report"
    bl_label = "Export Full GPKG/GML Report"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        try:
            out_path = run_full_gpkg_gml_report(out_path=None)
            self.report({"INFO"}, f"Full report saved to {out_path}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Full report failed: {ex}")
            return {"CANCELLED"}


# Operator registration
CLASSES = [
    M1DC_OT_ExportLinkMapping,
    M1DC_OT_ExportLog,
    M1DC_OT_ClearLog,
    M1DC_OT_ExportDiagnostic,
    M1DC_OT_ExportDebugReport,
    M1DC_OT_ExportFullReport,
]


def register():
    """Register all operators in this module."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister all operators in this module."""
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
