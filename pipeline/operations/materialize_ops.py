"""
Materialize operators: Write link data and OSM features to face attributes.

WARNING: M1DC_OT_MaterializeLinks is ~2200 LOC and implements complex multi-phase
materialization:
- Phase 1: Matching proof
- Phase 2: Writeback proof
- Phase 3: Real materialize (write core cols)
- Phase 4: OSM feature writeback
- Phase 4.5: Build MKDB
- Phase 5: Legend code writeback
- Phase 6: Tile ID assignment

This is a candidate for further modularization into a pipeline module if the file
size becomes problematic.
"""
import bpy
import bmesh
from bpy.types import Operator
from bpy.props import BoolProperty


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


class M1DC_OT_MaterializeLinks(Operator):
    """
    Materialize link data (osm_id, confidence, features) onto mesh FACE attributes.
    
    This is the LARGE operator (~2200 LOC) that orchestrates the full materialization pipeline.
    Implements 6 phases of data writeback and validation.
    
    NOTE: Due to its size and complexity, this operator is a prime candidate for refactoring
    into a dedicated materialization pipeline module if ops.py becomes unwieldy.
    """
    bl_idname = "m1dc.materialize_links"
    bl_label = "Materialize Links"
    bl_options = {"REGISTER", "UNDO"}

    include_features: BoolProperty(
        name="Include OSM Columns",
        description="Also materialize selected OSM columns as face attributes",
        default=True,
    )

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings missing")
            return {"CANCELLED"}

        # NOTE: The full MaterializeLinks implementation is ~2200 LOC and lives in ops.py
        # This is a minimal stub that delegates to the main implementation
        # For the full extraction to work, the supporting functions need to be refactored:
        # - _load_link_lookup()
        # - _collect_citygml_meshes()
        # - _collect_unique_osm_keys_from_meshes()
        # - _materialize_osm_features()
        # - _materialize_legend_codes()
        # - ensure_face_storage_ready()
        # - build_mkdb_from_linkdb()
        
        try:
            import ops
            # Try to use the full implementation from ops.py
            materialize_impl = None
            for attr_name in dir(ops):
                if attr_name == "M1DC_OT_MaterializeLinks":
                    materialize_impl = getattr(ops, attr_name)
                    break
            
            if materialize_impl and hasattr(materialize_impl, 'execute'):
                # Delegate to full implementation
                return materialize_impl.execute(self, context)
            else:
                self.report({"ERROR"}, "Materialize implementation not available")
                return {"CANCELLED"}
                
        except Exception as ex:
            log_error(f"[Materialize] Failed: {ex}")
            self.report({"ERROR"}, f"Materialize failed: {ex}")
            import traceback
            traceback.print_exc()
            return {"CANCELLED"}


class M1DC_OT_ReloadOSMTables(Operator):
    bl_idname = "m1dc.reload_osm_tables"
    bl_label = "Reload OSM Tables"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings missing")
            return {"CANCELLED"}
        
        try:
            import ops
            refresh_osm_feature_tables = getattr(ops, "refresh_osm_feature_tables", None)
            if not refresh_osm_feature_tables:
                self.report({"ERROR"}, "OSM table refresh logic not available")
                return {"CANCELLED"}
            
            tables = refresh_osm_feature_tables(s, reset_selection=True)
            if not tables:
                self.report({"WARNING"}, "No feature tables found in GeoPackage")
                return {"CANCELLED"}
            self.report({"INFO"}, f"Loaded {len(tables)} feature tables")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Table reload failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_ReloadOSMColumns(Operator):
    bl_idname = "m1dc.reload_osm_columns"
    bl_label = "Reload Columns"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings missing")
            return {"CANCELLED"}
        
        try:
            import ops
            refresh_osm_feature_tables = getattr(ops, "refresh_osm_feature_tables", None)
            refresh_osm_feature_columns = getattr(ops, "refresh_osm_feature_columns", None)
            
            if not all([refresh_osm_feature_tables, refresh_osm_feature_columns]):
                self.report({"ERROR"}, "OSM column refresh logic not available")
                return {"CANCELLED"}
            
            if not getattr(s, "osm_feature_table", ""):
                refresh_osm_feature_tables(s, reset_selection=True)
            
            cols = refresh_osm_feature_columns(s, reset_selection=True)
            if not cols:
                self.report({"WARNING"}, "No columns found for selected table")
                return {"CANCELLED"}
            self.report({"INFO"}, f"Loaded {len(cols)} columns (max 8 selectable)")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Column reload failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_SelectBuildingCluster(Operator):
    bl_idname = "m1dc.select_building_cluster"
    bl_label = "Select Building Cluster"
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
            import ops
            _sync_edit_mesh = getattr(ops, "_sync_edit_mesh", None)
            _get_active_face_poly_index = getattr(ops, "_get_active_face_poly_index", None)
            _get_face_link_attr = getattr(ops, "_get_face_link_attr", None)
            _ensure_face_int_attr_repair = getattr(ops, "_ensure_face_int_attr_repair", None)
            _read_face_int_attr_checked = getattr(ops, "_read_face_int_attr_checked", None)
            
            if not all([_sync_edit_mesh, _get_active_face_poly_index, _get_face_link_attr]):
                self.report({"ERROR"}, "Required functions not available")
                return {"CANCELLED"}
            
            _sync_edit_mesh(obj)
            mesh = obj.data
            poly_idx = _get_active_face_poly_index(obj)
            if poly_idx is None:
                self.report({"WARNING"}, "Select a face in Edit Mode")
                return {"CANCELLED"}

            face_count = len(mesh.polygons)
            b_attr = _get_face_link_attr(mesh, face_count=face_count)
            if b_attr is None and _ensure_face_int_attr_repair:
                b_attr, _ = _ensure_face_int_attr_repair(obj, mesh, "building_idx", "[Cluster] ")
            if b_attr is None:
                self.report({"WARNING"}, "building_idx/link_bidx attribute missing")
                return {"CANCELLED"}

            active_bidx, bidx_err = _read_face_int_attr_checked(mesh, b_attr.name, poly_idx) if _read_face_int_attr_checked else (None, None)
            active_face_index = poly_idx
            
            if active_bidx is None:
                bm = bmesh.from_edit_mesh(mesh)
                bm.faces.ensure_lookup_table()
                for f in bm.faces:
                    if not f.select:
                        continue
                    val, _ = _read_face_int_attr_checked(mesh, b_attr.name, f.index) if _read_face_int_attr_checked else (None, None)
                    if val is not None and val >= 0:
                        active_bidx = val
                        active_face_index = f.index
                        break
            
            if active_bidx is None:
                msg = bidx_err or "Active face has no building_idx"
                self.report({"WARNING"}, msg)
                log_info(f"[Cluster] building_idx read issue: {msg}")
                return {"CANCELLED"}

            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            selected_count = 0
            for f in bm.faces:
                try:
                    val = int(b_attr.data[f.index].value)
                except Exception:
                    val = None
                match = (val == active_bidx)
                f.select = bool(match)
                if match:
                    selected_count += 1

            try:
                bm.faces.active = bm.faces[active_face_index]
            except Exception:
                pass

            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            self.report({"INFO"}, f"Selected building_idx={active_bidx} faces={selected_count}")
            log_info(f"[Cluster] poly_idx={poly_idx}, building_idx={active_bidx}, faces_selected={selected_count}")
            return {"FINISHED"}
            
        except Exception as ex:
            self.report({"ERROR"}, f"Cluster selection failed: {ex}")
            return {"CANCELLED"}
