"""
Linking operators: CityGML to OSM feature linking.
"""
import bpy
import os
from pathlib import Path
from datetime import datetime
from bpy.types import Operator


def log_info(msg):
    try:
        from ...utils.logging_system import log_info as _log_info
        _log_info(msg)
    except ImportError:
        print(msg)

def log_warn(msg):
    try:
        from ...utils.logging_system import log_warn as _log_warn
        _log_warn(msg)
    except ImportError:
        print(f"[WARN] {msg}")

def log_error(msg):
    try:
        from ...utils.logging_system import log_error as _log_error
        _log_error(msg)
    except ImportError:
        print(f"[ERROR] {msg}")

def _settings(context):
    """Get scene settings"""
    return getattr(context.scene, "m1dc_settings", None)


class M1DC_OT_LinkCityGMLtoOSM(Operator):
    bl_idname = "m1dc.link_citygml_osm"
    bl_label = "Link CityGML ↔ OSM"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        if not s.gpkg_path or not os.path.isfile(s.gpkg_path):
            self.report({"ERROR"}, "No GeoPackage file specified or not found.")
            return {"CANCELLED"}

        # ── Validate CityGML dir with proof logging ──
        citygml_dir = getattr(s, "citygml_dir", "").strip()
        if citygml_dir:
            citygml_dir = os.path.normpath(citygml_dir)
        log_info(f"[Link] citygml_dir={citygml_dir!r} isdir={os.path.isdir(citygml_dir) if citygml_dir else False}")

        # Check scene meshes as fallback
        try:
            from ..linking.mesh_discovery import collect_citygml_meshes
            scene_meshes = collect_citygml_meshes(log_prefix="[Link][Discovery]")
        except Exception:
            scene_meshes = []
        log_info(f"[Link] Scene CityGML meshes: {len(scene_meshes)}")

        if not citygml_dir and not scene_meshes:
            self.report({"ERROR"}, "No CityGML folder set and no CityGML meshes in scene.")
            return {"CANCELLED"}

        try:
            from ... import ops
            _link_gpkg_to_citygml = ops._link_gpkg_to_citygml

            log_info("[Link] Starting explicit linking: CityGML ↔ OSM")
            ok2, linked, confidences, no_match_reasons, tiles_count, samples = _link_gpkg_to_citygml(s)

            if not ok2:
                self.report({"ERROR"}, "Linking failed — check console for details.")
                log_error(f"[Link] Linking returned ok2=False — CANCELLED")
                return {"CANCELLED"}

            # Verify link DB was created — also check links/ subdirectory
            link_db = getattr(s, "links_db_path", "")
            if not link_db or not os.path.isfile(link_db):
                # Try auto-detect from output_dir/links/
                from pathlib import Path as _P
                out_dir = _P(getattr(s, "output_dir", "").strip() or "")
                links_dir = out_dir / "links"
                gpkg_stem = _P(getattr(s, "gpkg_path", "")).stem if getattr(s, "gpkg_path", "") else ""
                candidate = links_dir / f"{gpkg_stem}_links.sqlite" if gpkg_stem and links_dir.is_dir() else None
                if candidate and candidate.is_file():
                    s.links_db_path = str(candidate.resolve())
                    link_db = s.links_db_path
                    log_info(f"[Link][Artifacts] Auto-detected link DB in links/: {link_db}")
                else:
                    self.report({"ERROR"}, f"Link DB not created at: {link_db}")
                    log_error(f"[Link][Artifacts] Link DB missing after linking: {link_db!r}")
                    return {"CANCELLED"}

            # Final artifact proof
            db_size = os.path.getsize(link_db)
            log_info(f"[Link][Artifacts] links_db_path set to: {link_db}")
            log_info(f"[Link][Artifacts] file exists: True size={db_size}")

            # Display summary
            summary_lines = [
                f"Linking Summary:",
                f"  Tiles: {tiles_count}",
                f"  Linked buildings: {linked}",
            ]

            if confidences:
                min_conf = min(confidences)
                max_conf = max(confidences)
                avg_conf = sum(confidences) / len(confidences)
                summary_lines.extend([
                    f"  Confidence (min/max/avg): {min_conf:.3f} / {max_conf:.3f} / {avg_conf:.3f}",
                ])

            summary_text = "\n".join(summary_lines)
            try:
                s.status_text = summary_text
            except Exception:
                pass
            self.report({"INFO"}, summary_text)
            log_info(f"[Link] Complete: {linked} buildings linked, DB={link_db}")

            return {"FINISHED"}
        except Exception as ex:
            log_error(f"[Link] Linking failed: {ex}")
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Linking failed: {ex}")
            return {"CANCELLED"}
