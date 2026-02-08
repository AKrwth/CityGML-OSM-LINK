"""
Face attribute operators: Manage and manipulate mesh face custom data layers.

These operators provide low-level face attribute management:
- Debug/inspect face attributes
- Bake evaluated attributes
- Create presentation attributes
- Commit evaluated to original
- List/view face attribute values
"""
import bpy
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty


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


class M1DC_OT_DebugFaceAttrs(Operator):
    """Debug face attributes on active mesh"""
    bl_idname = "m1dc.debug_face_attrs"
    bl_label = "Debug Face Attrs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"WARNING"}, "Select a mesh object")
            return {"CANCELLED"}
        
        try:
            import ops
            _debug_face_attributes = getattr(ops, "_debug_face_attributes", None)
            if not _debug_face_attributes:
                self.report({"ERROR"}, "Debug logic not available")
                return {"CANCELLED"}
            
            report = _debug_face_attributes(obj)
            log_info(f"[FaceAttr Debug] {report}")
            self.report({"INFO"}, f"Face attrs: {report}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Debug failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_BakeEvalFaceIntAttrs(Operator):
    """
    Bake evaluated integer face attributes to original mesh.
    
    Some modifiers (e.g., Geometry Nodes) may create evaluated face int attributes that
    don't exist on the original mesh. This operator copies those from evaluated to original.
    """
    bl_idname = "m1dc.bake_eval_face_int_attrs"
    bl_label = "Bake Evaluated Face Int Attrs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH":
            self.report({"WARNING"}, "Select a mesh object")
            return {"CANCELLED"}
        
        try:
            import ops
            _bake_eval_face_int_attrs = getattr(ops, "_bake_eval_face_int_attrs", None)
            if not _bake_eval_face_int_attrs:
                self.report({"ERROR"}, "Bake logic not available")
                return {"CANCELLED"}
            
            count = _bake_eval_face_int_attrs(obj)
            self.report({"INFO"}, f"Baked {count} evaluated attributes")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Bake failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_MakePresentationAttrs(Operator):
    """
    Create presentation face attributes for visualization.
    
    Generates color ramps or categorical colors based on face attribute values
    for visual debugging/presentation purposes.
    """
    bl_idname = "m1dc.make_presentation_attrs"
    bl_label = "Make Presentation Attrs"
    bl_options = {"REGISTER", "UNDO"}

    attr_name: StringProperty(
        name="Attribute",
        description="Face attribute to visualize",
        default=""
    )

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"WARNING"}, "Select a mesh object")
            return {"CANCELLED"}
        
        try:
            import ops
            _make_presentation_attrs = getattr(ops, "_make_presentation_attrs", None)
            if not _make_presentation_attrs:
                self.report({"ERROR"}, "Presentation logic not available")
                return {"CANCELLED"}
            
            result = _make_presentation_attrs(obj, self.attr_name)
            self.report({"INFO"}, f"Created presentation attrs: {result}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Presentation attr creation failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_CommitEvaluatedToOriginal(Operator):
    """
    Commit evaluated mesh geometry back to original mesh.
    
    Applies all modifiers (destructive) and commits the result to the original mesh.
    USE WITH CAUTION - this is destructive!
    """
    bl_idname = "m1dc.commit_evaluated_to_original"
    bl_label = "Commit Evaluated to Original"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH":
            self.report({"WARNING"}, "Select a mesh object")
            return {"CANCELLED"}
        
        try:
            import ops
            _commit_evaluated_to_original = getattr(ops, "_commit_evaluated_to_original", None)
            if not _commit_evaluated_to_original:
                self.report({"ERROR"}, "Commit logic not available")
                return {"CANCELLED"}
            
            _commit_evaluated_to_original(context, obj)
            self.report({"INFO"}, "Committed evaluated mesh to original")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Commit failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_FaceAttrList(Operator):
    """List all face attributes on active mesh"""
    bl_idname = "m1dc.face_attr_list"
    bl_label = "List Face Attrs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"WARNING"}, "Select a mesh object")
            return {"CANCELLED"}
        
        try:
            mesh = obj.data
            attrs = mesh.attributes
            if not attrs:
                self.report({"INFO"}, "No face attributes")
                return {"FINISHED"}
            
            face_attrs = [a.name for a in attrs if a.domain == 'FACE']
            if not face_attrs:
                self.report({"INFO"}, "No FACE domain attributes")
                return {"FINISHED"}
            
            log_info(f"[FaceAttr List] {len(face_attrs)} attributes: {', '.join(face_attrs)}")
            self.report({"INFO"}, f"{len(face_attrs)} face attributes")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"List failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_FaceAttrValues(Operator):
    """Display unique values of a face attribute"""
    bl_idname = "m1dc.face_attr_values"
    bl_label = "Face Attr Values"
    bl_options = {"REGISTER", "UNDO"}

    attr_name: StringProperty(
        name="Attribute",
        description="Face attribute to inspect",
        default=""
    )
    max_unique: bpy.props.IntProperty(
        name="Max Unique",
        description="Maximum unique values to display",
        default=50,
        min=1,
        max=1000
    )

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"WARNING"}, "Select a mesh object")
            return {"CANCELLED"}
        
        if not self.attr_name:
            self.report({"WARNING"}, "Specify attribute name")
            return {"CANCELLED"}
        
        try:
            mesh = obj.data
            attr = mesh.attributes.get(self.attr_name)
            if attr is None or attr.domain != 'FACE':
                self.report({"WARNING"}, f"No FACE attribute '{self.attr_name}'")
                return {"CANCELLED"}
            
            unique_vals = set()
            for poly in mesh.polygons:
                try:
                    val = attr.data[poly.index].value
                    if isinstance(val, (int, float)):
                        unique_vals.add(val)
                    if len(unique_vals) > self.max_unique:
                        break
                except Exception:
                    continue
            
            sorted_vals = sorted(unique_vals)
            truncated = " (truncated)" if len(unique_vals) > self.max_unique else ""
            log_info(f"[FaceAttr Values] {self.attr_name}: {sorted_vals}{truncated}")
            self.report({"INFO"}, f"{len(sorted_vals)} unique values{truncated}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Value inspection failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_FaceAttrSelect(Operator):
    """Select faces by face attribute value"""
    bl_idname = "m1dc.face_attr_select"
    bl_label = "Select By Face Attr"
    bl_options = {"REGISTER", "UNDO"}

    attr_name: StringProperty(
        name="Attribute",
        description="Face attribute to filter by",
        default=""
    )
    value: bpy.props.IntProperty(
        name="Value",
        description="Value to match",
        default=0
    )

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"WARNING"}, "Select a mesh in Edit Mode")
            return {"CANCELLED"}
        if obj.mode != 'EDIT':
            self.report({"WARNING"}, "Switch to Edit Mode")
            return {"CANCELLED"}
        
        if not self.attr_name:
            self.report({"WARNING"}, "Specify attribute name")
            return {"CANCELLED"}
        
        try:
            import bmesh
            mesh = obj.data
            attr = mesh.attributes.get(self.attr_name)
            if attr is None or attr.domain != 'FACE':
                self.report({"WARNING"}, f"No FACE attribute '{self.attr_name}'")
                return {"CANCELLED"}
            
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            
            selected_count = 0
            for f in bm.faces:
                try:
                    val = int(attr.data[f.index].value)
                    match = (val == self.value)
                    f.select = bool(match)
                    if match:
                        selected_count += 1
                except Exception:
                    f.select = False
            
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            self.report({"INFO"}, f"Selected {selected_count} faces with {self.attr_name}={self.value}")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Selection failed: {ex}")
            return {"CANCELLED"}
