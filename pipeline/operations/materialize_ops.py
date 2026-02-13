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
    
    Implements the complete materialization pipeline:
    - Phase 3: Core link data (osm_id, confidence, distance)
    - Phase 4: OSM feature columns (building, amenity, etc.)
    - Phase 5: Legend code materialization
    - Phase 6: Final validation
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

        try:
            from ... import ops
            from ...utils.logging_system import log_info as _log_info
            
            log_info("[Materialize] Starting materialization pipeline...")
            
            # Get active mesh (if working in mesh context)
            active_obj = getattr(context, "object", None)
            
            # Collect CityGML meshes from scene
            _log_info("[Materialize] Collecting CityGML meshes...")
            citygml_col = bpy.data.collections.get("CITYGML_TILES")
            if citygml_col:
                mesh_objs = [o for o in citygml_col.objects if o.type == "MESH"]
            else:
                mesh_objs = [o for o in bpy.data.objects if o.type == "MESH" and o.get("source_tile")]
            
            if not mesh_objs:
                log_warn("[Materialize] No CityGML meshes found")
                self.report({"WARNING"}, "No CityGML meshes found in scene")
                return {"FINISHED"}
            
            log_info(f"[Materialize] Found {len(mesh_objs)} CityGML meshes")
            
            # ── Phase 3: Write core link data (osm_way_id, confidence, dist, iou)
            #    to FACE attributes from link DB. Required BEFORE Phase 4/5. ──
            _load_link_lookup = getattr(ops, "_load_link_lookup", None)
            _get_source_tile = getattr(ops, "_get_source_tile", None)
            ensure_face_attr_fn = getattr(ops, "ensure_face_attr", None)
            _normalize_osm_id_fn = getattr(ops, "_normalize_osm_id", None)

            if not all([_load_link_lookup, _get_source_tile, ensure_face_attr_fn]):
                log_warn("[Materialize] P3: Required link-writeback functions not available in ops")
            else:
                link_map = _load_link_lookup(s)
                if not link_map:
                    log_warn("[Materialize] P3: link_map empty (no link DB or no entries)")
                else:
                    _log_info(f"[Materialize] P3: Loaded {len(link_map)} link entries")
                    # [PROOF][LINK_KEYS] Sample keys from link_map for canon verification
                    _sample_keys = list(link_map.keys())[:3]
                    for _sk in _sample_keys:
                        _sv = link_map[_sk]
                        _log_info(f"[PROOF][LINK_KEYS] sample key={_sk!r} osm_id={_sv.get('osm_id','?')} conf={_sv.get('link_conf',0):.3f}")
                    total_linked_faces = 0
                    meshes_written = []

                    for mesh_obj in mesh_objs:
                        mesh = mesh_obj.data
                        source_tile = _get_source_tile(mesh_obj)
                        face_count = len(mesh.polygons)
                        if face_count == 0:
                            continue

                        # Resolve building_idx attribute (same fallback chain as _collect_unique_osm_keys_from_meshes)
                        idx_attr = None
                        for candidate in ("gml_building_idx", "gml__building_idx", "building_idx"):
                            a = mesh.attributes.get(candidate)
                            if a and a.domain == 'FACE' and a.data_type == 'INT' and len(a.data) == face_count:
                                idx_attr = a
                                break

                        if idx_attr is None:
                            log_warn(f"[Materialize] P3: {mesh_obj.name} has no building_idx attr, skipping")
                            continue

                        # Ensure target FACE attributes
                        osm_attr = ensure_face_attr_fn(mesh, "osm_way_id", "INT")
                        conf_attr = ensure_face_attr_fn(mesh, "link_conf", "FLOAT")
                        dist_attr = ensure_face_attr_fn(mesh, "link_dist_m", "FLOAT")
                        iou_attr = ensure_face_attr_fn(mesh, "link_iou", "FLOAT")
                        has_attr = ensure_face_attr_fn(mesh, "has_link", "INT")

                        if not osm_attr:
                            log_warn(f"[Materialize] P3: Cannot create osm_way_id for {mesh_obj.name}")
                            continue

                        linked_here = 0
                        for fi in range(face_count):
                            try:
                                bidx = int(idx_attr.data[fi].value)
                            except Exception:
                                continue
                            row = link_map.get((source_tile, bidx))
                            if row:
                                osm_id_str = row.get("osm_id", "")
                                try:
                                    osm_int = int(osm_id_str) if osm_id_str and osm_id_str not in ("\u2014", "") else 0
                                except (ValueError, OverflowError):
                                    osm_int = 0
                                osm_attr.data[fi].value = osm_int
                                if conf_attr and fi < len(conf_attr.data):
                                    conf_attr.data[fi].value = float(row.get("link_conf", 0.0))
                                if dist_attr and fi < len(dist_attr.data):
                                    dist_attr.data[fi].value = float(row.get("link_dist_m", 0.0))
                                if iou_attr and fi < len(iou_attr.data):
                                    iou_attr.data[fi].value = float(row.get("link_iou", 0.0))
                                if has_attr and fi < len(has_attr.data):
                                    has_attr.data[fi].value = 1
                                if osm_int != 0:
                                    linked_here += 1

                        total_linked_faces += linked_here
                        meshes_written.append(mesh_obj.name)
                        mesh.update()
                        # [PROOF][LINK_KEYS] Per-mesh hit/miss for canon verification
                        _log_info(f"[PROOF][LINK_KEYS] mesh={mesh_obj.name} source_tile={source_tile!r} faces={face_count} linked={linked_here} miss={face_count - linked_here}")

                    _log_info(f"[Materialize] P3 PROOF: {len(meshes_written)} meshes, {total_linked_faces} linked faces")
                    if meshes_written:
                        _log_info(f"[Materialize] P3 first meshes: {meshes_written[:3]}")
                    
                    # [PROOF][ATTR_SCHEMA] Validate that written attrs have correct domain/type
                    for mesh_obj in mesh_objs[:2]:  # Sample first 2
                        m = mesh_obj.data
                        fc = len(m.polygons)
                        schema_lines = []
                        for aname in ("osm_way_id", "link_conf", "link_dist_m", "link_iou", "has_link"):
                            a = m.attributes.get(aname)
                            if a:
                                nz = sum(1 for i in range(min(len(a.data), fc)) if a.data[i].value not in (0, 0.0, b"", ""))
                                schema_lines.append(f"{aname}:{a.data_type}/{a.domain} len={len(a.data)} nz={nz}")
                            else:
                                schema_lines.append(f"{aname}:MISSING")
                        _log_info(f"[PROOF][ATTR_SCHEMA] {mesh_obj.name} fc={fc} | {' | '.join(schema_lines)}")

            # Phase 4: Materialize OSM features (building, amenity, name, etc.)
            p4_total = 0
            if self.include_features and s.gpkg_path:
                _log_info(f"[Materialize] P4: Materializing OSM features from {s.gpkg_path}")
                phase4_complete = False
                for mesh_obj in mesh_objs:
                    try:
                        from ... import ops as ops_module
                        materialize_osm_features = getattr(ops_module, "_materialize_osm_features", None)
                        if materialize_osm_features and callable(materialize_osm_features):
                            written_count = materialize_osm_features(
                                mesh_obj.data, 
                                osm_id_attr=None,  # Will detect internally
                                gpkg_path=s.gpkg_path
                            )
                            p4_total += (written_count or 0)
                            log_info(f"[Materialize] Phase 4: {mesh_obj.name} wrote {written_count} features")
                            phase4_complete = True
                    except Exception as ex:
                        log_warn(f"[Materialize] Phase 4 for {mesh_obj.name}: {ex}")
                        continue
                _log_info(f"[PROOF][P4_READBACK] total_features_written={p4_total} meshes={len(mesh_objs)}")
            
            # Phase 5: Materialize legend codes (building_code, amenity_code, etc.)
            _log_info(f"[Materialize] P5: Materializing legend codes")
            p5_total = 0
            try:
                from ... import ops as ops_module
                output_dir = s.output_dir or str(ops.get_output_dir())
                materialize_legend_codes = getattr(ops_module, "_materialize_legend_codes", None)
                
                for mesh_obj in mesh_objs:
                    if materialize_legend_codes and callable(materialize_legend_codes):
                        try:
                            codes_written = materialize_legend_codes(
                                mesh_obj.data,
                                gpkg_path=s.gpkg_path,
                                output_dir=output_dir
                            )
                            p5_total += (codes_written or 0)
                            log_info(f"[Materialize] Phase 5: {mesh_obj.name} wrote {codes_written} codes")
                        except Exception as ex:
                            log_warn(f"[Materialize] Phase 5 for {mesh_obj.name}: {ex}")
                            continue
            except Exception as ex:
                log_warn(f"[Materialize] Phase 5 setup failed: {ex}")
            _log_info(f"[PROOF][P5_READBACK] total_legend_codes_written={p5_total} meshes={len(mesh_objs)}")
            
            log_info("[Materialize] ✓ Materialization pipeline complete")
            self.report({"INFO"}, "Materialization complete")
            return {"FINISHED"}
                
        except Exception as ex:
            log_error(f"[Materialize] FAILED: {ex}")
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
            from ... import ops
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
            from ... import ops
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
            from ... import ops
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
