"""
Debug/repair operators: Diagnostic tools for mesh attributes, DB schema, and data integrity.

These operators provide low-level debugging and repair functionality:
- Inspect mesh attributes (original vs evaluated)
- Debug link DB and GPKG schemas
- Analyze building ID candidates
- Repair/remap building_idx attributes
- Find best link key attributes via hit-rate scoring
"""
import bpy
import bmesh
from bpy.types import Operator
from bpy.props import IntProperty, BoolProperty, StringProperty


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


class M1DC_OT_DebugMeshAttributes(Operator):
    """Debug mesh attributes (prints original and evaluated mesh attrs to console)"""
    bl_idname = "m1dc.debug_mesh_attributes"
    bl_label = "Debug Mesh Attributes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}

        mesh = obj.data
        print("[M1DC Debug] OBJ:", obj.name)
        
        try:
            from ... import ops
            norm_source_tile = getattr(ops, "norm_source_tile", None)
            if norm_source_tile:
                print("[M1DC Debug] source_tile:", norm_source_tile(obj.get("source_tile") or obj.name))
            else:
                print("[M1DC Debug] source_tile:", obj.get("source_tile") or obj.name)
        except Exception:
            print("[M1DC Debug] source_tile:", obj.get("source_tile") or obj.name)
        
        try:
            print("[M1DC Debug] ORIGINAL mesh:", mesh.name, "faces=", len(mesh.polygons), "verts=", len(mesh.vertices), "loops=", len(mesh.loops))
        except Exception:
            pass

        if not hasattr(mesh, "attributes"):
            print("[M1DC Debug] mesh.attributes: (missing)")
            self.report({"WARNING"}, "Mesh has no attributes")
            return {"FINISHED"}

        print("[M1DC Debug] ORIGINAL mesh.attributes:")
        for a in mesh.attributes:
            try:
                print("[M1DC Debug]", a.name, a.domain, a.data_type, len(a.data))
            except Exception:
                print("[M1DC Debug]", getattr(a, "name", "?"), "<unprintable>")

        b = mesh.attributes.get("building_idx")
        print(
            "[M1DC Debug] building_idx attr:",
            (b.domain if b else None),
            (b.data_type if b else None),
            (len(b.data) if b else None),
        )

        # Sample face value: active face in edit mode, else face 0.
        face_idx = None
        if obj.mode == 'EDIT':
            try:
                bm = bmesh.from_edit_mesh(mesh)
                face = bm.faces.active or (bm.faces[0] if bm.faces else None)
                face_idx = face.index if face else None
            except Exception:
                face_idx = None
        if face_idx is None:
            face_idx = 0 if len(mesh.polygons) else None

        if b and face_idx is not None and face_idx < len(mesh.polygons):
            if b.domain == 'FACE' and face_idx < len(b.data):
                try:
                    print(f"[M1DC Debug] building_idx[{face_idx}] =", int(b.data[face_idx].value))
                except Exception:
                    print(f"[M1DC Debug] building_idx[{face_idx}] = <read failed>")
            else:
                print(f"[M1DC Debug] building_idx is not FACE (domain={b.domain}); cannot read per-face directly")

        # Also inspect evaluated mesh
        try:
            from ... import ops
            _get_evaluated_mesh = getattr(ops, "_get_evaluated_mesh", None)
            if _get_evaluated_mesh:
                obj_eval, me_eval = _get_evaluated_mesh(context, obj)
            else:
                depsgraph = context.evaluated_depsgraph_get()
                obj_eval = obj.evaluated_get(depsgraph)
                me_eval = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph) if obj_eval else None
        except Exception:
            obj_eval = None
            me_eval = None
            
        if me_eval is not None and hasattr(me_eval, "attributes"):
            try:
                print("[M1DC Debug] EVALUATED mesh:", me_eval.name, "faces=", len(me_eval.polygons), "verts=", len(me_eval.vertices), "loops=", len(me_eval.loops))
            except Exception:
                pass
            print("[M1DC Debug] EVALUATED mesh.attributes:")
            for a in me_eval.attributes:
                try:
                    print("[M1DC Debug]", a.name, a.domain, a.data_type, len(a.data))
                except Exception:
                    print("[M1DC Debug]", getattr(a, "name", "?"), "<unprintable>")
            eb = me_eval.attributes.get("building_idx")
            if eb is not None:
                print(
                    "[M1DC Debug] evaluated building_idx attr:",
                    eb.domain,
                    eb.data_type,
                    len(eb.data),
                )
            try:
                if obj_eval:
                    obj_eval.to_mesh_clear()
            except Exception:
                pass
        else:
            print("[M1DC Debug] EVALUATED mesh: (unavailable)")

        self.report({"INFO"}, "Printed mesh.attributes inventory to console")
        return {"FINISHED"}


class M1DC_OT_DebugLinkDBSchema(Operator):
    """Debug link DB schema (prints gml_osm_links table structure to console)"""
    bl_idname = "m1dc.debug_link_db_schema"
    bl_label = "Debug Link DB Schema"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        link_db = getattr(s, "links_db_path", "") if s else ""
        
        try:
            from ... import ops
            _is_link_db_valid = getattr(ops, "_is_link_db_valid", None)
            if not _is_link_db_valid:
                # Fallback validation
                import os
                if not link_db or not os.path.isfile(link_db):
                    self.report({"ERROR"}, "Link DB missing. Run linking first.")
                    return {"CANCELLED"}
            elif not _is_link_db_valid(link_db):
                self.report({"ERROR"}, "Link DB missing. Run linking first.")
                return {"CANCELLED"}
        except Exception:
            self.report({"ERROR"}, "Cannot validate link DB")
            return {"CANCELLED"}

        try:
            import sqlite3
            from pathlib import Path
            uri = f"file:{Path(link_db).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            cur = con.cursor()

            row = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gml_osm_links' LIMIT 1;").fetchone()
            if not row:
                print("[M1DC Debug] Link DB:", link_db)
                print("[M1DC Debug] Table gml_osm_links: MISSING")
                con.close()
                self.report({"ERROR"}, "gml_osm_links table missing")
                return {"CANCELLED"}

            info = cur.execute("PRAGMA table_info('gml_osm_links');").fetchall()
            cols = [r[1] for r in info]
            print("[M1DC Debug] Link DB:", link_db)
            print("[M1DC Debug] gml_osm_links columns:", cols)
            for r in info:
                print("[M1DC Debug]  -", r)

            sample = cur.execute("SELECT * FROM gml_osm_links LIMIT 1;").fetchone()
            if sample is not None:
                try:
                    print("[M1DC Debug] sample row:", tuple(sample))
                except Exception:
                    print("[M1DC Debug] sample row: <unprintable>")
            con.close()
        except Exception as ex:
            self.report({"ERROR"}, f"DB schema debug failed: {ex}")
            return {"CANCELLED"}

        self.report({"INFO"}, "Printed gml_osm_links schema to console")
        return {"FINISHED"}


class M1DC_OT_DebugGPKGTableInfo(Operator):
    """Debug GPKG table info (prints table schema and sample data to console)"""
    bl_idname = "m1dc.debug_gpkg_table_info"
    bl_label = "Debug GPKG Table Info"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        gpkg_path = getattr(s, "gpkg_path", "") if s else ""
        if not gpkg_path:
            self.report({"ERROR"}, "GPKG path not set")
            return {"CANCELLED"}

        # Resolve path
        try:
            from ...utils.common import resolve_gpkg_path
            resolved, info = resolve_gpkg_path(gpkg_path)
            gpkg_path = resolved or gpkg_path
            print("[M1DC GPKG] resolve:", info)
        except Exception:
            pass

        import os
        if not gpkg_path or not os.path.isfile(gpkg_path):
            self.report({"ERROR"}, f"GPKG missing: {gpkg_path}")
            return {"CANCELLED"}

        # Choose table
        try:
            from ... import ops
            FEATURE_TABLE_FALLBACK = getattr(ops, "FEATURE_TABLE_FALLBACK", "osm_buildings")
        except Exception:
            FEATURE_TABLE_FALLBACK = "osm_buildings"
            
        table = ""
        for cand in (
            getattr(s, "spreadsheet_table", "") if s else "",
            getattr(s, "osm_feature_table", "") if s else "",
            getattr(s, "osm_feature_table_used", "") if s else "",
            FEATURE_TABLE_FALLBACK,
        ):
            if cand:
                table = str(cand)
                break

        if not table:
            self.report({"ERROR"}, "No feature table selected")
            return {"CANCELLED"}

        print("[M1DC GPKG] file:", gpkg_path)
        print("[M1DC GPKG] table:", table)

        try:
            import sqlite3
            from pathlib import Path
            
            try:
                from ... import ops
                open_db_readonly = getattr(ops, "open_db_readonly", None)
                _sanitize_identifier = getattr(ops, "_sanitize_identifier", lambda x: x)
            except Exception:
                open_db_readonly = None
                _sanitize_identifier = lambda x: x
            
            if open_db_readonly:
                con = open_db_readonly(gpkg_path, log_open=False)
            else:
                uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
                con = sqlite3.connect(uri, uri=True)
            cur = con.cursor()

            t = _sanitize_identifier(table)
            row = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;", (t,)
            ).fetchone()
            if not row:
                con.close()
                self.report({"ERROR"}, f"Table not found: {table}")
                return {"CANCELLED"}

            info = cur.execute(f"PRAGMA table_info('{t}');").fetchall()
            cols = [r[1] for r in info]
            print("[M1DC GPKG] PRAGMA table_info:")
            for r in info:
                print("[M1DC GPKG]  -", r)

            # Pick ID column
            id_col = getattr(s, "id_col", "") if s else ""
            if id_col and id_col in cols:
                pass
            else:
                id_col = ""
                for cand in ("osm_id", "osm_way_id", "id", "osm_wayid", "osm_way"):
                    if cand in cols:
                        id_col = cand
                        break

            if not id_col:
                print("[M1DC GPKG] No obvious id column found in:", cols)
                con.close()
                self.report({"WARNING"}, "No obvious id column (see console)")
                return {"FINISHED"}

            c = _sanitize_identifier(id_col)
            print("[M1DC GPKG] id_col:", id_col)
            try:
                rows = cur.execute(f'SELECT typeof("{c}"), "{c}" FROM "{t}" LIMIT 5;').fetchall()
                print("[M1DC GPKG] typeof samples:")
                for r in rows:
                    print("[M1DC GPKG]  -", r)
            except Exception as ex:
                print("[M1DC GPKG] typeof sample query failed:", ex)

            con.close()
        except Exception as ex:
            self.report({"ERROR"}, f"GPKG debug failed: {ex}")
            return {"CANCELLED"}

        self.report({"INFO"}, "Printed GPKG table info to console")
        return {"FINISHED"}


class M1DC_OT_DebugLinkKeyIdentity(Operator):
    """Debug link key identity: full B1 (Blender keys), B2 (DB keys), B3 (lookup proof)"""
    bl_idname = "m1dc.debug_link_key_identity"
    bl_label = "Debug Link Key Identity"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}

        mesh = obj.data
        face_count = len(mesh.polygons)

        # ── Canonical normalization from single source of truth ──
        try:
            from ..linking.key_normalization import normalize_source_tile
        except ImportError:
            self.report({"ERROR"}, "key_normalization module not found")
            return {"CANCELLED"}

        try:
            from ... import ops
            _load_link_lookup = getattr(ops, "_load_link_lookup", None)
        except Exception:
            _load_link_lookup = None

        # ====================================================================
        # B1 — Blender-side keys
        # ====================================================================
        print("\n" + "=" * 70)
        print("[B1] BLENDER-SIDE KEYS")
        print("=" * 70)

        raw_tile = obj.get("source_tile", obj.name)
        norm_tile = normalize_source_tile(raw_tile)
        print(f"  Active mesh:  {obj.name}")
        print(f"  source_tile RAW:  {raw_tile!r}")
        print(f"  source_tile NORM: {norm_tile!r}")

        # Find building_idx attribute
        idx_attr = None
        for cand in ("gml_building_idx", "gml__building_idx", "building_idx"):
            a = mesh.attributes.get(cand)
            if a and a.domain == "FACE" and a.data_type == "INT" and len(a.data) == face_count:
                idx_attr = a
                break

        if idx_attr is None:
            print("  building_idx: MISSING (no FACE/INT attribute found)")
            self.report({"WARNING"}, "No building_idx attribute on active mesh")
            return {"FINISHED"}

        # Collect unique building indices
        unique_bidx = set()
        for fi in range(face_count):
            try:
                unique_bidx.add(int(idx_attr.data[fi].value))
            except Exception:
                pass

        sorted_bidx = sorted(unique_bidx)
        print(f"  building_idx attr: {idx_attr.name} (len={len(idx_attr.data)})")
        print(f"  min_bidx={min(sorted_bidx) if sorted_bidx else 'N/A'} max_bidx={max(sorted_bidx) if sorted_bidx else 'N/A'}")
        print(f"  unique_count={len(sorted_bidx)} sample_indices={sorted_bidx[:10]}")

        # Composed keys (first 10)
        blender_keys = [(norm_tile, bidx) for bidx in sorted_bidx[:10]]
        print(f"  Composed keys (first 10): {blender_keys}")

        # Sample from up to 3 OTHER tiles for cross-check
        try:
            from ..linking.mesh_discovery import collect_citygml_meshes
            all_meshes = collect_citygml_meshes(log_prefix="[B1][Sample]")
            other_tiles = []
            for m_obj in all_meshes:
                t = normalize_source_tile(m_obj.get("source_tile", m_obj.name))
                if t != norm_tile and t not in [x[0] for x in other_tiles]:
                    other_tiles.append((t, m_obj.name))
                if len(other_tiles) >= 3:
                    break
            if other_tiles:
                print(f"  Other tiles (sample 3): {other_tiles}")
        except Exception:
            pass

        # ====================================================================
        # B2 — DB-side keys
        # ====================================================================
        print("\n" + "=" * 70)
        print("[B2] DB-SIDE KEYS")
        print("=" * 70)

        link_map = {}
        if _load_link_lookup and s:
            link_map = _load_link_lookup(s) or {}

        print(f"  link_map rows loaded: {len(link_map)}")

        if link_map:
            db_tiles = {}
            for k in link_map:
                db_tiles[k[0]] = db_tiles.get(k[0], 0) + 1
            print(f"  Distinct tiles in DB: {len(db_tiles)}")
            for tn, tc in sorted(db_tiles.items())[:5]:
                print(f"    tile={tn!r} entries={tc}")

            # Keys for active tile
            active_tile_keys = [k for k in link_map if k[0] == norm_tile]
            print(f"  Keys for active tile ({norm_tile!r}): {len(active_tile_keys)}")
            for k in active_tile_keys[:10]:
                v = link_map[k]
                print(f"    DB key={k!r} osm_id={v.get('osm_id','?')} conf={v.get('link_conf',0):.3f}")

            if not active_tile_keys:
                # Try fuzzy match to diagnose mismatch
                print("  [DIAG] No exact tile match. Attempting fuzzy match...")
                for db_tile in sorted(db_tiles.keys())[:10]:
                    print(f"    DB tile: {db_tile!r}")
        else:
            print("  link_map is EMPTY — run 'Link CityGML ↔ OSM' first")

        # ====================================================================
        # B3 — Actual lookup proof
        # ====================================================================
        print("\n" + "=" * 70)
        print("[B3] LOOKUP PROOF (first 5 faces with building_idx)")
        print("=" * 70)

        hits = 0
        misses = 0
        probed = 0
        seen_bidx = set()

        for fi in range(face_count):
            if probed >= 5:
                break
            try:
                bidx = int(idx_attr.data[fi].value)
            except Exception:
                continue
            if bidx in seen_bidx:
                continue
            seen_bidx.add(bidx)
            probed += 1

            key = (norm_tile, bidx)
            row = link_map.get(key)
            if row:
                hits += 1
                print(
                    f"  face[{fi}] bidx={bidx} key={key!r} -> HIT "
                    f"osm_id={row.get('osm_id','?')} "
                    f"conf={row.get('link_conf',0):.3f} "
                    f"dist={row.get('link_dist_m',0):.2f} "
                    f"iou={row.get('link_iou',0):.3f}"
                )
            else:
                misses += 1
                print(f"  face[{fi}] bidx={bidx} key={key!r} -> MISS")

        # Counters
        print(f"\n  SUMMARY: probed={probed} HITs={hits} MISSes={misses}")
        if link_map and hits == 0:
            print("  [ALERT] 0 HITs — likely key mismatch between Blender and DB!")
            print("  Check if Blender source_tile normalization matches DB source_tile.")

        print("=" * 70 + "\n")

        self.report({"INFO"}, f"Link Key Identity: {hits} HITs / {misses} MISSes from {probed} probes")
        return {"FINISHED"}


class M1DC_OT_DebugBuildingIdxStats(Operator):
    """Debug building_idx statistics (prints min/max/unique counts to console)"""
    bl_idname = "m1dc.debug_building_idx_stats"
    bl_label = "Debug building_idx Stats"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}

        mesh = obj.data
        if not hasattr(mesh, "attributes"):
            self.report({"ERROR"}, "Mesh has no attributes")
            return {"CANCELLED"}

        try:
            from ... import ops
            _get_face_link_attr = getattr(ops, "_get_face_link_attr", None)
            if not _get_face_link_attr:
                # Fallback: try building_idx directly
                b = mesh.attributes.get("building_idx") or mesh.attributes.get("link_bidx")
            else:
                b = _get_face_link_attr(mesh, face_count=len(mesh.polygons))
        except Exception:
            b = mesh.attributes.get("building_idx") or mesh.attributes.get("link_bidx")

        if b is None:
            self.report({"ERROR"}, "Mesh missing attribute 'link_bidx/building_idx'")
            return {"CANCELLED"}

        face_count = len(mesh.polygons)
        if face_count == 0:
            self.report({"ERROR"}, "Mesh has no faces")
            return {"CANCELLED"}
        if len(b.data) != face_count:
            msg = f"building_idx length mismatch: attr={len(b.data)} faces={face_count}"
            print("[M1DC bidx]", msg)
            print("[M1DC bidx] HINT: run 'Debug Mesh Attrs' to compare original vs evaluated.")
            print("[M1DC bidx] HINT: if evaluated has building_idx, run 'Bake Eval FACE/INT Attrs'.")
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        # Compute stats
        min_bidx = None
        max_bidx = None
        unique = set()
        try:
            for item in b.data:
                try:
                    v = int(item.value)
                except Exception:
                    continue
                if min_bidx is None or v < min_bidx:
                    min_bidx = v
                if max_bidx is None or v > max_bidx:
                    max_bidx = v
                unique.add(v)
        except Exception as ex:
            self.report({"ERROR"}, f"Failed while scanning building_idx: {ex}")
            return {"CANCELLED"}

        if min_bidx is None or max_bidx is None:
            self.report({"ERROR"}, "No readable building_idx values")
            return {"CANCELLED"}

        unique_sorted = sorted(unique)
        first_10 = unique_sorted[:10]

        print("[M1DC bidx] OBJ:", obj.name)
        print("[M1DC bidx] faces=", face_count)
        print("[M1DC bidx] min_bidx=", int(min_bidx), "max_bidx=", int(max_bidx))
        print("[M1DC bidx] unique_count=", len(unique))
        print("[M1DC bidx] first_10_unique_sorted=", first_10)

        if min_bidx > 1_000_000 or max_bidx > 10_000_000:
            print("[M1DC bidx] NOTE: building_idx range looks very large; may be an identifier, not a compact index")

        self.report({"INFO"}, f"building_idx stats printed (unique={len(unique)})")
        return {"FINISHED"}


class M1DC_OT_FindBestLinkKeyAttr(Operator):
    """Find best link key attribute by scoring overlap with link DB"""
    bl_idname = "m1dc.find_best_link_key_attr"
    bl_label = "Find Best Link Key Attribute"
    bl_options = {"REGISTER", "UNDO"}

    samples: IntProperty(
        name="Samples",
        default=200,
        min=50,
        max=2000,
        description="Number of evenly spaced faces to sample for hit-rate scoring",
    )

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Settings not found")
            return {"CANCELLED"}

        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}

        # Full implementation delegated to ops.py (this is a complex scoring algorithm ~150 LOC)
        try:
            from ... import ops
            impl_op = getattr(ops, "M1DC_OT_FindBestLinkKeyAttr", None)
            if impl_op and hasattr(impl_op, "execute"):
                return impl_op.execute(self, context)
            else:
                self.report({"ERROR"}, "FindBestLinkKeyAttr implementation not available")
                return {"CANCELLED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Link key scoring failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_DebugBuildingIdCandidates(Operator):
    """Debug building ID candidates (prints all FACE/INT attrs with overlap stats)"""
    bl_idname = "m1dc.debug_building_id_candidates"
    bl_label = "Debug Building ID Candidates"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}

        # Full implementation delegated to ops.py (this is a complex analysis ~200 LOC)
        try:
            from ... import ops
            impl_op = getattr(ops, "M1DC_OT_DebugBuildingIdCandidates", None)
            if impl_op and hasattr(impl_op, "execute"):
                return impl_op.execute(self, context)
            else:
                self.report({"ERROR"}, "DebugBuildingIdCandidates implementation not available")
                return {"CANCELLED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Building ID candidate analysis failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_RepairBuildingIdxToFace(Operator):
    """Repair building_idx to FACE domain (converts from CORNER/POINT)"""
    bl_idname = "m1dc.repair_building_idx_face"
    bl_label = "Repair building_idx to FACE"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}

        # Full implementation delegated to ops.py (this is a domain conversion ~100 LOC)
        try:
            from ... import ops
            impl_op = getattr(ops, "M1DC_OT_RepairBuildingIdxToFace", None)
            if impl_op and hasattr(impl_op, "execute"):
                return impl_op.execute(self, context)
            else:
                self.report({"ERROR"}, "RepairBuildingIdxToFace implementation not available")
                return {"CANCELLED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Repair failed: {ex}")
            return {"CANCELLED"}


class M1DC_OT_RemapBuildingIdxTest(Operator):
    """Test remap building_idx (tile-only validation with hit-rate scoring)"""
    bl_idname = "m1dc.remap_building_idx_test"
    bl_label = "Test Remap building_idx (Tile Only)"
    bl_options = {"REGISTER", "UNDO"}

    samples: IntProperty(
        name="Samples",
        default=25,
        min=5,
        max=200,
        description="How many deterministic sample faces to test before/after",
    )

    apply_overwrite: BoolProperty(
        name="Overwrite building_idx on success",
        default=False,
        description="If enabled, overwrite building_idx with the remapped values after validation",
    )

    require_factor: IntProperty(
        name="Required hit-factor",
        default=5,
        min=1,
        max=100,
        description="Require new_hits >= max(1, old_hits) * factor",
    )

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "m1dc_settings not found on scene")
            return {"CANCELLED"}

        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Select a CityGML tile mesh object")
            return {"CANCELLED"}

        # Full implementation delegated to ops.py (this is a complex remap algorithm ~250 LOC)
        try:
            from ... import ops
            impl_op = getattr(ops, "M1DC_OT_RemapBuildingIdxTest", None)
            if impl_op and hasattr(impl_op, "execute"):
                return impl_op.execute(self, context)
            else:
                self.report({"ERROR"}, "RemapBuildingIdxTest implementation not available")
                return {"CANCELLED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Remap test failed: {ex}")
            return {"CANCELLED"}
