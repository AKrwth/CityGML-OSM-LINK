"""
Legend encoding operators: Build categorical string -> integer code legends from GPKG.
"""
import bpy
import os
from bpy.types import Operator


def log_info(msg):
    try:
        from ...utils.logging_system import log_info as _log_info
        _log_info(msg)
    except ImportError:
        print(msg)

def log_error(msg):
    try:
        from ...utils.logging_system import log_error as _log_error
        _log_error(msg)
    except ImportError:
        print(f"[ERROR] {msg}")

def _settings(context):
    """Get scene settings"""
    return getattr(context.scene, "m1dc_settings", None)


class M1DC_OT_BuildLegends(Operator):
    """Build categorical string -> integer code legends from GPKG"""
    bl_idname = "m1dc.legends_build"
    bl_label = "Build Legends (Encode Categorical Columns)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        # Get GPKG path from settings
        gpkg_path = getattr(s, "gpkg_path", "").strip()
        if not gpkg_path:
            error_msg = "No GPKG path configured (set in Input Configuration)"
            log_error(f"[Legends] {error_msg}")
            self.report({"ERROR"}, error_msg)
            return {"CANCELLED"}

        if not os.path.exists(gpkg_path):
            error_msg = f"GPKG file not found: {gpkg_path}"
            log_error(f"[Legends] {error_msg}")
            self.report({"ERROR"}, error_msg)
            return {"CANCELLED"}

        # Get output directory
        output_dir = getattr(s, "output_dir", "").strip()
        if not output_dir:
            error_msg = "No output directory configured"
            log_error(f"[Legends] {error_msg}")
            self.report({"ERROR"}, error_msg)
            return {"CANCELLED"}

        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                log_info(f"[Legends] Created output directory: {output_dir}")
            except Exception as e:
                error_msg = f"Failed to create output directory: {e}"
                log_error(f"[Legends] {error_msg}")
                self.report({"ERROR"}, error_msg)
                return {"CANCELLED"}

        try:
            from ...pipeline.diagnostics import legend_encoding
            
            log_info("[Legends] Building legends from GPKG...")
            log_info(f"[Legends] GPKG: {gpkg_path}")
            log_info(f"[Legends] Output: {output_dir}")

            # Build all legends
            result = legend_encoding.build_all_legends(gpkg_path, output_dir, max_distinct=500)

            if not result.get('success', False):
                error_msg = result.get('error', 'Unknown error')
                log_error(f"[Legends] Failed: {error_msg}")
                self.report({"ERROR"}, error_msg)
                return {"CANCELLED"}

            # Extract results
            table_name = result['table_name']
            columns = result['columns']
            legends_dir = result['legends_dir']

            # Store table name in scene for reference
            context.scene["M1DC_OSM_TABLE"] = table_name

            # Log summary
            log_info(f"[Legends] ✓ Detected table: {table_name}")
            log_info(f"[Legends] ✓ Encoded {len(columns)} categorical columns:")

            for col_info in columns:
                col_name = col_info['column_name']
                distinct_real = col_info['distinct_real_values']
                max_code = col_info['max_code']
                legend_path = col_info['legend_path']

                log_info(f"[Legends]   - {col_name}: {distinct_real} values → codes 0-{max_code}")
                log_info(f"[Legends]     Legend: {os.path.basename(legend_path)}")

            log_info(f"[Legends] ✓ Legends saved to: {legends_dir}")

            # Store legend index in scene for inspector decoding
            legend_index = {}
            for col_info in columns:
                col_name = col_info['column_name']
                legend_index[col_name] = col_info['legend_path']

            context.scene["M1DC_LEGEND_INDEX"] = str(legend_index)  # Store as JSON string

            # ── NO-SILENT-SUCCESS: Verify legend CSVs exist on disk ──
            verified_count = 0
            for col_info in columns:
                if os.path.isfile(col_info.get('legend_path', '')):
                    verified_count += 1
            if verified_count == 0 and len(columns) > 0:
                error_msg = "Legend CSVs reported but none found on disk"
                log_error(f"[Legends] {error_msg}")
                self.report({"ERROR"}, error_msg)
                return {"CANCELLED"}
            log_info(f"[Legends] Verified {verified_count}/{len(columns)} legend CSV files on disk")

            success_msg = f"Built {len(columns)} legends for table '{table_name}'"
            self.report({"INFO"}, success_msg)
            log_info(f"[Legends] {success_msg}")

        except Exception as e:
            error_msg = f"Legend building failed: {e}"
            log_error(f"[Legends] {error_msg}")
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, error_msg)
            return {"CANCELLED"}

        return {"FINISHED"}
