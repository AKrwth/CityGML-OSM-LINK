"""
Inspector operators: Query and analyze materialized face attributes via UI.

These operators provide the interactive inspector panel functionality:
- Inspect active face attributes
- Filter buildings by legend text
- Apply/clear query filters
- Zoom to selected buildings
- Export building data reports
"""
import bpy
from bpy.types import Operator
from bpy.props import StringProperty


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


class M1DC_OT_InspectActiveFace(Operator):
    """Inspect active face attributes and populate inspector data"""
    bl_idname = "m1dc.inspect_active_face"
    bl_label = "Inspect Active Face"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"WARNING"}, "Select a mesh in Edit Mode")
            return {"CANCELLED"}
        if obj.mode != 'EDIT':
            self.report({"WARNING"}, "Switch to Edit Mode")
            return {"CANCELLED"}

        try:
            from ... import ops
            _sync_edit_mesh = getattr(ops, "_sync_edit_mesh", None)
            _get_active_face_poly_index = getattr(ops, "_get_active_face_poly_index", None)
            _inspect_active_face_impl = getattr(ops, "_inspect_active_face_impl", None)
            
            if not all([_sync_edit_mesh, _get_active_face_poly_index, _inspect_active_face_impl]):
                self.report({"ERROR"}, "Inspector logic not available")
                return {"CANCELLED"}
            
            _sync_edit_mesh(obj)
            mesh = obj.data
            poly_idx = _get_active_face_poly_index(obj)
            if poly_idx is None:
                self.report({"WARNING"}, "Select a face in Edit Mode")
                return {"CANCELLED"}
            
            result = _inspect_active_face_impl(s, mesh, poly_idx)
            if result:
                self.report({"INFO"}, f"Inspected face: {result.get('summary', 'OK')}")
            return {"FINISHED"}
        except Exception as ex:
            log_error(f"[Inspector] Inspect failed: {ex}")
            self.report({"ERROR"}, f"Inspect failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_FilterByLegendText(Operator):
    """Filter buildings by legend text substring"""
    bl_idname = "m1dc.filter_by_legend_text"
    bl_label = "Filter By Legend Text"
    bl_options = {"REGISTER", "UNDO"}

    # Properties expected by UI (ui.py:524-525)
    attr_name_code: StringProperty(
        name="Legend Code Attribute",
        description="Attribute name for legend code filtering",
        default="amenity_code"
    )
    text_value: StringProperty(
        name="Text Value",
        description="Substring to match in legend values",
        default=""
    )
    # Legacy property (kept for compatibility)
    text_filter: StringProperty(
        name="Text Filter",
        description="Substring to match in legend values",
        default=""
    )

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        
        try:
            from ... import ops
            _filter_by_legend_text_impl = getattr(ops, "_filter_by_legend_text_impl", None)
            if not _filter_by_legend_text_impl:
                self.report({"ERROR"}, "Legend filter logic not available")
                return {"CANCELLED"}
            
            matched = _filter_by_legend_text_impl(s, self.text_filter)
            self.report({"INFO"}, f"Matched {matched} buildings")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Filter failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_InspectorApplyQuery(Operator):
    """Apply inspector query filters to building list"""
    bl_idname = "m1dc.inspector_apply_query"
    bl_label = "Apply Query"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        
        try:
            from ... import ops
            _apply_inspector_query_impl = getattr(ops, "_apply_inspector_query_impl", None)
            if not _apply_inspector_query_impl:
                self.report({"ERROR"}, "Query logic not available")
                return {"CANCELLED"}
            
            matched = _apply_inspector_query_impl(s)
            self.report({"INFO"}, f"Query matched {matched} buildings")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Query failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_InspectorClearQuery(Operator):
    """Clear inspector query filters and reset building list"""
    bl_idname = "m1dc.inspector_clear_query"
    bl_label = "Clear Query"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        
        try:
            from ... import ops
            _clear_inspector_query_impl = getattr(ops, "_clear_inspector_query_impl", None)
            if not _clear_inspector_query_impl:
                self.report({"ERROR"}, "Clear query logic not available")
                return {"CANCELLED"}
            
            count = _clear_inspector_query_impl(s)
            self.report({"INFO"}, f"Cleared query, showing all {count} buildings")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Clear failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_InspectorZoomToSelection(Operator):
    """Zoom viewport to selected buildings in inspector"""
    bl_idname = "m1dc.inspector_zoom_to_selection"
    bl_label = "Zoom To Selection"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        
        try:
            from ... import ops
            _zoom_to_inspector_selection_impl = getattr(ops, "_zoom_to_inspector_selection_impl", None)
            if not _zoom_to_inspector_selection_impl:
                self.report({"ERROR"}, "Zoom logic not available")
                return {"CANCELLED"}
            
            _zoom_to_inspector_selection_impl(context, s)
            self.report({"INFO"}, "Zoomed to selection")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Zoom failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_InspectorLegendDecode(Operator):
    """Decode a single integer code via the combined legend CSV"""
    bl_idname = "m1dc.inspector_legend_decode"
    bl_label = "Decode"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        attr = s.legend_decode_attr  # e.g. "amenity_code"
        code = int(s.legend_decode_code)

        # Ensure legend caches are loaded
        try:
            from ...pipeline.diagnostics.legend_encoding import (
                legend_decode, init_legend_caches, _DECODE_CACHE,
            )
        except ImportError as ex:
            s.legend_decode_result = ""
            s.legend_decode_status = f"IMPORT_ERROR: {ex}"
            self.report({"ERROR"}, f"Legend module not available: {ex}")
            return {"CANCELLED"}

        # Auto-init caches from output_dir/legends/ if not loaded
        if not _DECODE_CACHE:
            import os
            output_dir = getattr(s, "output_dir", "").strip()
            if output_dir:
                legends_dir = os.path.join(output_dir, "legends")
                if os.path.isdir(legends_dir):
                    legend_files = sorted([
                        f for f in os.listdir(legends_dir)
                        if f.endswith("_legend.csv")
                    ])
                    if legend_files:
                        stem = legend_files[0].replace("_legend.csv", "")
                        try:
                            table_name, _ = stem.rsplit("__", 1)
                            init_legend_caches(legends_dir, table_name)
                        except ValueError:
                            pass

        if not _DECODE_CACHE:
            s.legend_decode_result = ""
            s.legend_decode_status = "CSV_MISSING"
            self.report({"WARNING"}, "Legend caches not loaded (no CSV found)")
            return {"FINISHED"}

        decoded = legend_decode(attr, code)
        if decoded == "":
            s.legend_decode_result = ""
            s.legend_decode_status = "NOT_FOUND"
            try:
                from ...utils.logging_system import log_info
            except ImportError:
                log_info = print
            log_info(f"[Inspector][LegendDecode] attr={attr} code={code} -> NOT_FOUND")
        else:
            s.legend_decode_result = decoded
            s.legend_decode_status = "OK"
            try:
                from ...utils.logging_system import log_info
            except ImportError:
                log_info = print
            log_info(f"[Inspector][LegendDecode] attr={attr} code={code} -> {decoded} (OK)")

        # Force UI redraw
        try:
            for area in context.screen.areas:
                area.tag_redraw()
        except Exception:
            pass

        self.report({"INFO"}, f"{attr} #{code} = {decoded or '(not found)'}")
        return {"FINISHED"}


class M1DC_OT_InspectorApplyDSL(Operator):
    """Apply DSL filter on materialized face attributes (no DB)"""
    bl_idname = "m1dc.inspector_apply_dsl"
    bl_label = "Apply DSL"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        try:
            from ... import ops
            _apply_dsl = getattr(ops, "_apply_dsl_filter_impl", None)
            if not _apply_dsl:
                self.report({"ERROR"}, "DSL filter logic not available")
                return {"CANCELLED"}

            matched = _apply_dsl(s)
            self.report({"INFO"}, f"DSL matched {matched} faces")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"DSL filter failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_InspectorExportReport(Operator):
    """Export inspector building list to CSV report"""
    bl_idname = "m1dc.inspector_export_report"
    bl_label = "Export Report"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        
        try:
            from ... import ops
            _export_inspector_report_impl = getattr(ops, "_export_inspector_report_impl", None)
            if not _export_inspector_report_impl:
                self.report({"ERROR"}, "Export logic not available")
                return {"CANCELLED"}
            
            filepath = _export_inspector_report_impl(s)
            self.report({"INFO"}, f"Exported to {filepath}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Export failed: {ex}")
            return {"CANCELLED"}
