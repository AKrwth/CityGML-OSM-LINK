"""
Spreadsheet operators: Building table/spreadsheet UI operations.

NOTE: These operators require supporting functions from ops.py that handle
spreadsheet data management. This is a minimal extraction - full functionality
requires refactoring spreadsheet state management into a separate module.
"""
import bpy
from bpy.types import Operator
from bpy.props import IntProperty, BoolProperty, StringProperty, EnumProperty


def _settings(context):
    """Get scene settings"""
    return getattr(context.scene, "m1dc_settings", None)


class M1DC_OT_SpreadsheetReload(Operator):
    bl_idname = "m1dc_spreadsheet.reload"
    bl_label = "Reload Building Table"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings missing")
            return {"CANCELLED"}
        
        # NOTE: _build_spreadsheet_rows is defined in ops.py
        # This would need to be refactored into a separate spreadsheet module
        try:
            from ... import ops
            _build_spreadsheet_rows = getattr(ops, "_build_spreadsheet_rows", None)
            if not _build_spreadsheet_rows:
                self.report({"ERROR"}, "Spreadsheet logic not available")
                return {"CANCELLED"}
            
            ok = _build_spreadsheet_rows(context, s)
            if not ok:
                self.report({"ERROR"}, s.spreadsheet_last_error or "Reload failed")
                return {"CANCELLED"}
            self.report({"INFO"}, f"Loaded {len(s.spreadsheet_rows)} buildings")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Spreadsheet reload failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_SpreadsheetColumnsSelect(Operator):
    bl_idname = "m1dc_spreadsheet.columns_select"
    bl_label = "Set Column Selection"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="Mode",
        items=(
            ("ALL", "All", "Select all columns"),
            ("NONE", "None", "Deselect all columns"),
        ),
        default="ALL",
    )

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        for opt in s.spreadsheet_columns_available:
            opt.selected = self.mode == "ALL"

        # Rebuild rows so dynamic columns reflect the new selection
        try:
            from ... import ops
            _build_spreadsheet_rows = getattr(ops, "_build_spreadsheet_rows", None)
            if _build_spreadsheet_rows:
                _build_spreadsheet_rows(context, s)
        except Exception:
            pass

        return {"FINISHED"}


class M1DC_OT_SpreadsheetSyncFromSelection(Operator):
    bl_idname = "m1dc_spreadsheet.sync_from_selection"
    bl_label = "Sync From Selection"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        try:
            from ... import ops
            _perform_face_sync = getattr(ops, "_perform_face_sync", None)
            if not _perform_face_sync:
                self.report({"WARNING"}, "Face sync logic not available")
                return {"CANCELLED"}
            
            if not _perform_face_sync(context, s):
                self.report({"WARNING"}, "No active/selected face with building_idx")
                return {"CANCELLED"}
            self.report({"INFO"}, "Synced from selection")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Sync failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_SpreadsheetSelectRow(Operator):
    bl_idname = "m1dc_spreadsheet.select_row"
    bl_label = "Select Building Row"
    bl_options = {"REGISTER", "UNDO"}

    building_idx: IntProperty()
    source_tile: StringProperty()
    value: BoolProperty(default=True)

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        try:
            from ... import ops
            _get_active_mesh = getattr(ops, "_get_active_mesh", None)
            _build_spreadsheet_rows = getattr(ops, "_build_spreadsheet_rows", None)
            _select_faces_by_building_idx = getattr(ops, "_select_faces_by_building_idx", None)
            
            if not all([_get_active_mesh, _build_spreadsheet_rows, _select_faces_by_building_idx]):
                self.report({"ERROR"}, "Required spreadsheet functions not available")
                return {"CANCELLED"}
            
            obj, _ = _get_active_mesh(context)
            if obj is None:
                return {"CANCELLED"}

            cached_obj = getattr(s, "spreadsheet_cached_obj", "")
            if cached_obj != obj.name or len(s.spreadsheet_rows) == 0:
                _build_spreadsheet_rows(context, s)

            if self.value:
                _select_faces_by_building_idx(context, obj, self.building_idx)

            s.spreadsheet_silent = True
            try:
                for i, row in enumerate(s.spreadsheet_rows):
                    is_target = (row.building_idx == self.building_idx and row.source_tile == (self.source_tile or row.source_tile))
                    row.selected = is_target if self.value else False
                    if is_target:
                        s.spreadsheet_row_index = i
            finally:
                s.spreadsheet_silent = False

            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Row selection failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_SpreadsheetDeferredSync(Operator):
    """Deferred sync operator: performs face-to-row sync outside draw context."""
    bl_idname = "m1dc_spreadsheet.deferred_sync"
    bl_label = "Spreadsheet Deferred Sync"
    bl_options = {"REGISTER"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        
        try:
            from ... import ops
            _perform_face_sync = getattr(ops, "_perform_face_sync", None)
            if _perform_face_sync:
                _perform_face_sync(context, s)
        except Exception:
            pass
        
        return {"FINISHED"}
