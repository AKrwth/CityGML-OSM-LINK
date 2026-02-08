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

        try:
            # Import linking function from ops.py (temporary until fully refactored)
            # This will be replaced with proper modular imports later
            import ops as ops_module
            _link_gpkg_to_citygml = getattr(ops_module, '_link_gpkg_to_citygml', None)
            
            if not _link_gpkg_to_citygml:
                self.report({"ERROR"}, "Link function not available")
                return {"CANCELLED"}
            
            log_info("Starting explicit linking: CityGML ↔ OSM")
            ok2, linked, confidences, no_match_reasons, tiles_count, samples = _link_gpkg_to_citygml(s)
            
            if not ok2:
                self.report({"WARNING"}, "Linking completed with warnings or no matches.")
                log_warn(f"Linking returned ok2={ok2}")
                return {"FINISHED"}

            # Display summary
            summary_lines = [
                f"Linking Summary:",
                f"  Tiles: {tiles_count}",
                f"  Linked buildings: {linked}",
                f"  Unmatched buildings: {s.status_citygml_buildings - linked if s.status_citygml_buildings else 0}",
            ]
            
            if confidences:
                min_conf = min(confidences)
                max_conf = max(confidences)
                avg_conf = sum(confidences) / len(confidences)
                summary_lines.extend([
                    f"  Confidence (min/max/avg): {min_conf:.3f} / {max_conf:.3f} / {avg_conf:.3f}",
                ])
            
            summary_text = "\n".join(summary_lines)
            s.status_text = summary_text
            self.report({"INFO"}, summary_text)
            log_info(f"Linking complete: {linked} buildings linked")
            
            return {"FINISHED"}
        except Exception as ex:
            log_error(f"Linking failed: {ex}")
            self.report({"ERROR"}, f"Linking failed: {ex}")
            return {"CANCELLED"}
