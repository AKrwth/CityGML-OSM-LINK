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
            from ..linking.mesh_discovery import collect_citygml_meshes

            log_info("[Materialize] Starting materialization pipeline...")

            # ── D1: Hard precondition — link DB must exist ──
            link_db = getattr(s, "links_db_path", "").strip()
            import os
            if not link_db or not os.path.isfile(link_db):
                # Try to auto-detect from output_dir/links/ (TASK C: canonical location)
                from pathlib import Path
                out_dir = Path(getattr(s, "output_dir", "").strip() or "")
                gpkg_stem = Path(getattr(s, "gpkg_path", "")).stem if getattr(s, "gpkg_path", "") else ""
                # Check links/ subdirectory first (canonical), then root (legacy)
                candidates = []
                if gpkg_stem and out_dir.is_dir():
                    candidates.append(out_dir / "links" / f"{gpkg_stem}_links.sqlite")
                    candidates.append(out_dir / f"{gpkg_stem}_links.sqlite")
                found = None
                for candidate in candidates:
                    if candidate.is_file():
                        found = candidate
                        break
                if found:
                    s.links_db_path = str(found.resolve())
                    link_db = s.links_db_path
                    log_info(f"[Materialize] Auto-detected link DB: {link_db}")
                else:
                    msg = (
                        f"[Materialize] CANCELLED: No link DB found. "
                        f"links_db_path={link_db!r}, searched={[str(c) for c in candidates]}. "
                        f"Run 'Link CityGML ↔ OSM' first."
                    )
                    log_error(msg)
                    self.report({"ERROR"}, "No link DB found. Run linking first.")
                    return {"CANCELLED"}

            log_info(f"[Materialize] Link DB: {link_db} (size={os.path.getsize(link_db)} bytes)")

            # Collect CityGML meshes via centralised discovery
            mesh_objs = collect_citygml_meshes(log_prefix="[Materialize][Discovery]")

            if not mesh_objs:
                log_warn("[Materialize] No CityGML meshes found")
                self.report({"WARNING"}, "No CityGML meshes found in scene")
                return {"CANCELLED"}

            log_info(f"[Materialize] Found {len(mesh_objs)} CityGML meshes")

            # ── OBJECT MODE GUARD: foreach_set/attribute writes fail silently in EDIT mode ──
            active_obj = bpy.context.view_layer.objects.active
            if active_obj and active_obj.mode != 'OBJECT':
                log_warn(f"[Materialize] Active object '{active_obj.name}' in {active_obj.mode} mode — switching to OBJECT")
                try:
                    bpy.ops.object.mode_set(mode='OBJECT')
                except Exception as mode_ex:
                    log_warn(f"[Materialize] mode_set failed: {mode_ex} — proceeding anyway")

            # ── Phase 3: Write core link data (osm_way_id, confidence, dist, iou)
            #    to FACE attributes from link DB. Required BEFORE Phase 4/5. ──
            _load_link_lookup = getattr(ops, "_load_link_lookup", None)
            ensure_face_attr_fn = getattr(ops, "ensure_face_attr", None)
            _normalize_osm_id_fn = getattr(ops, "_normalize_osm_id", None)

            # Use canonical normalization (single source of truth)
            try:
                from ..linking.key_normalization import normalize_source_tile
            except ImportError:
                normalize_source_tile = getattr(ops, "norm_source_tile", None)

            if not all([_load_link_lookup, normalize_source_tile, ensure_face_attr_fn]):
                log_error("[Materialize] P3: Required link-writeback functions not available")
                self.report({"ERROR"}, "Required functions missing from ops module")
                return {"CANCELLED"}

            # D1: Hard precondition — link_map must be non-empty
            link_map = _load_link_lookup(s)
            if not link_map:
                log_error("[Materialize] P3: CANCELLED — link_map empty (no link DB or no entries). Run linking first.")
                self.report({"ERROR"}, "Link map is empty — no links found. Run linking first.")
                return {"CANCELLED"}

            _log_info(f"[Materialize] P3: Loaded {len(link_map)} link entries")
            # [PROOF] Show tile distribution in link_map
            _tile_counts = {}
            for _k in link_map:
                _tile_counts[_k[0]] = _tile_counts.get(_k[0], 0) + 1
            _log_info(f"[PROOF][LINKMAP_TILES] distinct tiles in link_map: {len(_tile_counts)}")
            for _tn, _tc in sorted(_tile_counts.items())[:5]:
                _log_info(f"[PROOF][LINKMAP_TILES]   '{_tn}' -> {_tc} entries")
            # [PROOF][LINK_KEYS] Sample keys from link_map for canon verification
            _sample_keys = list(link_map.keys())[:3]
            for _sk in _sample_keys:
                _sv = link_map[_sk]
                _log_info(f"[PROOF][LINK_KEYS] sample key={_sk!r} osm_id={_sv.get('osm_id','?')} conf={_sv.get('link_conf',0):.3f}")
            total_linked_faces = 0
            meshes_written = []

            for mesh_obj in mesh_objs:
                mesh = mesh_obj.data
                source_tile = normalize_source_tile(mesh_obj.get("source_tile", mesh_obj.name))
                face_count = len(mesh.polygons)
                if face_count == 0:
                    continue

                # Resolve building_idx attribute (same fallback chain)
                idx_attr = None
                for candidate in ("gml_building_idx", "gml__building_idx", "building_idx"):
                    a = mesh.attributes.get(candidate)
                    if a and a.domain == 'FACE' and a.data_type == 'INT' and len(a.data) == face_count:
                        idx_attr = a
                        break

                if idx_attr is None:
                    log_warn(f"[Materialize] P3: {mesh_obj.name} has no building_idx attr, skipping")
                    continue

                # D2: Ensure target FACE attributes with correct type/domain
                # Schema: osm_way_id(INT), osm_id_int(INT), link_conf(FLOAT),
                #         link_dist_m(FLOAT), link_iou(FLOAT), has_link(INT)
                _p3_schema = [
                    ("osm_way_id", "INT"),
                    ("osm_id_int", "INT"),
                    ("link_conf", "FLOAT"),
                    ("link_dist_m", "FLOAT"),
                    ("link_iou", "FLOAT"),
                    ("has_link", "INT"),
                ]
                for attr_name, attr_type in _p3_schema:
                    # Check if existing attr has wrong type/domain -> remove + recreate
                    existing = mesh.attributes.get(attr_name)
                    if existing and (existing.domain != "FACE" or existing.data_type != attr_type):
                        _log_info(f"[D2][SCHEMA] {mesh_obj.name}.{attr_name}: wrong type/domain "
                                  f"(got {existing.data_type}/{existing.domain}, want {attr_type}/FACE) — removing + recreating")
                        try:
                            mesh.attributes.remove(existing)
                        except Exception:
                            pass

                osm_attr = ensure_face_attr_fn(mesh, "osm_way_id", "INT")
                osm_id_int_attr = ensure_face_attr_fn(mesh, "osm_id_int", "INT")
                conf_attr = ensure_face_attr_fn(mesh, "link_conf", "FLOAT")
                dist_attr = ensure_face_attr_fn(mesh, "link_dist_m", "FLOAT")
                iou_attr = ensure_face_attr_fn(mesh, "link_iou", "FLOAT")
                has_attr = ensure_face_attr_fn(mesh, "has_link", "INT")

                # ─── CRITICAL: Full RE-RESOLVE of ALL handles after creation ───
                # mesh.attributes.new() invalidates ALL previously returned
                # bpy_prop_collection references (Blender API caveat).
                # After the ensure_face_attr_fn calls above, EVERY handle
                # (including idx_attr and all output attrs) may be stale.
                # We must re-resolve via mesh.attributes.get() BEFORE any
                # data access.

                # Re-resolve idx_attr (input)
                idx_attr = None
                for candidate in ("gml_building_idx", "gml__building_idx", "building_idx"):
                    a = mesh.attributes.get(candidate)
                    if a and a.domain == 'FACE' and a.data_type == 'INT' and len(a.data) == face_count:
                        idx_attr = a
                        break
                if idx_attr is None:
                    log_warn(f"[Materialize] P3: {mesh_obj.name} building_idx invalidated after attr creation, skipping")
                    continue

                # Re-resolve ALL output handles
                osm_attr = mesh.attributes.get("osm_way_id")
                osm_id_int_attr = mesh.attributes.get("osm_id_int")
                conf_attr = mesh.attributes.get("link_conf")
                dist_attr = mesh.attributes.get("link_dist_m")
                iou_attr = mesh.attributes.get("link_iou")
                has_attr = mesh.attributes.get("has_link")

                if not osm_attr:
                    log_warn(f"[Materialize] P3: Cannot create/resolve osm_way_id for {mesh_obj.name}")
                    continue

                # PROOF: verify every output handle has len(data) == face_count
                _p3_handles = {
                    "osm_way_id": osm_attr,
                    "osm_id_int": osm_id_int_attr,
                    "link_conf": conf_attr,
                    "link_dist_m": dist_attr,
                    "link_iou": iou_attr,
                    "has_link": has_attr,
                }
                _p3_ok = True
                for _hn, _hv in _p3_handles.items():
                    if _hv is None:
                        _log_info(f"[PROOF][P3_RESOLVE] {mesh_obj.name}.{_hn} = None (optional)")
                        continue
                    _hlen = len(_hv.data)
                    if _hlen != face_count:
                        _log_info(
                            f"[PROOF][P3_RESOLVE][FAIL] {mesh_obj.name}.{_hn} "
                            f"len(data)={_hlen} != face_count={face_count}"
                        )
                        _p3_ok = False
                if not _p3_ok:
                    log_warn(
                        f"[Materialize] P3: {mesh_obj.name} output attr data length mismatch "
                        f"after re-resolve — skipping (bpy_prop_collection invalidation)"
                    )
                    continue

                # [PROOF] Pre-loop: count how many link_map entries match this tile
                _tile_entry_count = sum(1 for k in link_map if k[0] == source_tile)
                _first_bidx = int(idx_attr.data[0].value) if face_count > 0 else -1
                _probe0 = (source_tile, _first_bidx)
                _log_info(
                    f"[PROOF][P3_PRE] mesh={mesh_obj.name} source_tile={source_tile!r} "
                    f"tile_entries_in_map={_tile_entry_count} "
                    f"first_bidx={_first_bidx} probe0_in_map={_probe0 in link_map}"
                )

                linked_here = 0
                _first_miss_logged = False
                for fi in range(face_count):
                    try:
                        bidx = int(idx_attr.data[fi].value)
                    except Exception:
                        continue
                    row = link_map.get((source_tile, bidx))
                    if not row and not _first_miss_logged:
                        # Diagnostic: log the first miss — scan ALL keys
                        _probe_key = (source_tile, bidx)
                        _near_keys = [k for k in link_map if k[0] == source_tile]
                        _log_info(
                            f"[PROOF][P3_MISS] mesh={mesh_obj.name} fi={fi} "
                            f"key={_probe_key!r} key_type=({type(source_tile).__name__},{type(bidx).__name__}) "
                            f"in_map={_probe_key in link_map} "
                            f"near_keys_same_tile={len(_near_keys)} sample={_near_keys[:3]!r}"
                        )
                        if not _near_keys:
                            # Broader search: find keys with similar tile name
                            _similar = [k for k in link_map if source_tile[:15] in str(k[0])]
                            _log_info(f"[PROOF][P3_MISS] no exact tile match; similar={_similar[:3]!r}")
                        _first_miss_logged = True
                    if row:
                        osm_id_str = row.get("osm_id", "")
                        try:
                            osm_int = int(osm_id_str) if osm_id_str and osm_id_str not in ("\u2014", "") else 0
                        except (ValueError, OverflowError):
                            osm_int = 0
                        osm_attr.data[fi].value = osm_int
                        # Write osm_id_int as duplicate for Phase 4/5 canonical lookups
                        if osm_id_int_attr and fi < len(osm_id_int_attr.data):
                            osm_id_int_attr.data[fi].value = osm_int
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
                # Force mesh + depsgraph update so Spreadsheet/Viewer shows data
                mesh.update()
                mesh_obj.update_tag()
                # D3: Per-mesh proof log
                _log_info(
                    f"[D3][P3] mesh={mesh_obj.name} source_tile={source_tile!r} "
                    f"faces_total={face_count} faces_linked={linked_here} faces_missed={face_count - linked_here}"
                )
                # D3: Print first 5 faces with (building_idx, osm_id_int, link_conf)
                _proof_count = min(5, face_count)
                for _pi in range(_proof_count):
                    _p_bidx = int(idx_attr.data[_pi].value) if _pi < len(idx_attr.data) else -1
                    _p_osm = osm_id_int_attr.data[_pi].value if osm_id_int_attr and _pi < len(osm_id_int_attr.data) else 0
                    _p_conf = conf_attr.data[_pi].value if conf_attr and _pi < len(conf_attr.data) else 0.0
                    _log_info(
                        f"[D3][P3]   face[{_pi}] building_idx={_p_bidx} osm_id_int={_p_osm} link_conf={_p_conf:.3f}"
                    )

            _log_info(f"[Materialize] P3 PROOF: {len(meshes_written)} meshes, {total_linked_faces} linked faces")
            if meshes_written:
                _log_info(f"[Materialize] P3 first meshes: {meshes_written[:3]}")

            # Readback proof: first 5 faces of first 2 meshes
            for mesh_obj in mesh_objs[:2]:
                m = mesh_obj.data
                fc = min(len(m.polygons), 5)
                osm_a = m.attributes.get("osm_id_int")
                conf_a = m.attributes.get("link_conf")
                if osm_a and conf_a:
                    for fi in range(fc):
                        _log_info(
                            f"[ACCEPTANCE][P3] {mesh_obj.name} face[{fi}] "
                            f"osm_id_int={osm_a.data[fi].value} link_conf={conf_a.data[fi].value:.3f}"
                        )

            # [PROOF][ATTR_SCHEMA] Validate that written attrs have correct domain/type
            for mesh_obj in mesh_objs[:2]:  # Sample first 2
                m = mesh_obj.data
                fc = len(m.polygons)
                schema_lines = []
                for aname in ("osm_way_id", "osm_id_int", "link_conf", "link_dist_m", "link_iou", "has_link"):
                    a = m.attributes.get(aname)
                    if a:
                        nz = sum(1 for i in range(min(len(a.data), fc)) if a.data[i].value not in (0, 0.0, b"", ""))
                        schema_lines.append(f"{aname}:{a.data_type}/{a.domain} len={len(a.data)} nz={nz}")
                    else:
                        schema_lines.append(f"{aname}:MISSING")
                _log_info(f"[PROOF][ATTR_SCHEMA] {mesh_obj.name} fc={fc} | {' | '.join(schema_lines)}")

            # Force global depsgraph update so Spreadsheet viewer refreshes
            bpy.context.view_layer.update()

            # ── NO-SILENT-SUCCESS GATE: Phase 3 must have linked at least 1 face ──
            if total_linked_faces == 0 and len(link_map) > 0:
                msg = (
                    f"[Materialize] CANCELLED: Phase 3 wrote 0 linked faces despite "
                    f"{len(link_map)} link_map entries. This indicates a key mismatch "
                    f"(source_tile normalization or building_idx). Check proof logs above."
                )
                log_error(msg)
                self.report({"ERROR"}, "0 linked faces written — key mismatch. Check console.")
                return {"CANCELLED"}

            # Phase 4: Materialize OSM features (building, amenity, name, etc.)
            p4_total = 0
            p4_hits_total = 0
            p4_miss_total = 0
            if self.include_features and s.gpkg_path:
                _log_info(f"[Materialize] P4: Materializing OSM features from {s.gpkg_path}")
                if total_linked_faces == 0:
                    _log_info("[Materialize] P4: 0 linked faces from P3 — skipping Phase 4/5")
                else:
                    # ── KEY PROOF: Compare 3 sample keys from link_map vs 3 from scene ──
                    _sample_db_keys = list(link_map.keys())[:3]
                    _log_info(f"[PH4][KEYS][DB] sample keys from link_map: {_sample_db_keys}")
                    for _mk in _sample_db_keys:
                        _mv = link_map[_mk]
                        _log_info(f"[PH4][KEYS][DB]   (tile={_mk[0]!r}, idx={_mk[1]}) -> osm_id={_mv.get('osm_id','?')}")

                    _sample_scene_keys = []
                    for _mo in mesh_objs[:3]:
                        _ms_tile = normalize_source_tile(_mo.get("source_tile", _mo.name))
                        _ms_idx_attr = _mo.data.attributes.get("gml_building_idx") or _mo.data.attributes.get("building_idx")
                        _ms_first_idx = int(_ms_idx_attr.data[0].value) if _ms_idx_attr and len(_ms_idx_attr.data) > 0 else -1
                        _sample_scene_keys.append((_ms_tile, _ms_first_idx))
                    _log_info(f"[PH4][KEYS][SCENE] sample keys from meshes: {_sample_scene_keys}")

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
                                # Ensure depsgraph knows this object changed (Phase 4 only updates mesh data)
                                mesh_obj.data.update()
                                mesh_obj.update_tag()
                        except Exception as ex:
                            log_warn(f"[Materialize] Phase 4 for {mesh_obj.name}: {ex}")
                            import traceback
                            traceback.print_exc()
                            continue

                    # ── P4 ACCEPTANCE: Verify face attributes are domain=FACE with real values ──
                    _p4_proof_cols = ["building", "amenity", "name", "has_feature"]
                    for _mo in mesh_objs[:2]:
                        _m = _mo.data
                        _fc = len(_m.polygons)
                        _proof_parts = []
                        for _pcol in _p4_proof_cols:
                            _pa = _m.attributes.get(_pcol)
                            if _pa:
                                _nz = 0
                                for _pi in range(min(len(_pa.data), _fc)):
                                    _v = _pa.data[_pi].value
                                    if _v and str(_v).strip() and _v not in (0, 0.0):
                                        _nz += 1
                                _proof_parts.append(f"{_pcol}:{_pa.data_type}/{_pa.domain} nz={_nz}")
                            else:
                                _proof_parts.append(f"{_pcol}:MISSING")
                        _log_info(f"[PH4][ATTR_PROOF] {_mo.name} fc={_fc} | {' | '.join(_proof_parts)}")

                _log_info(f"[PROOF][P4_READBACK] total_features_written={p4_total} meshes={len(mesh_objs)}")
            else:
                _log_info("[Materialize] P4: Skipped (include_features=False or no gpkg_path)")

            # Phase 5: Materialize legend codes (building_code, amenity_code, etc.)
            _log_info("[Materialize] P5: Materializing legend codes")
            p5_total = 0
            p5_nonzero_per_mesh = {}
            try:
                from ... import ops as ops_module
                from pathlib import Path as _Path

                # E: Resolve legends_dir EXACTLY as output_dir/legends
                output_dir = getattr(s, "output_dir", "").strip()
                if not output_dir:
                    try:
                        output_dir = str(ops.get_output_dir())
                    except Exception:
                        output_dir = ""
                legends_dir = os.path.join(output_dir, "legends") if output_dir else ""

                # E: Proof logging — show resolved path
                _log_info(f"[Materialize] P5: legends_dir resolved = {legends_dir!r}")
                _log_info(f"[Materialize] P5: output_dir = {output_dir!r}")

                if not legends_dir or not os.path.isdir(legends_dir):
                    _log_info(
                        f"[Materialize] P5: CANCELLED — No legends directory found at {legends_dir!r}. "
                        f"Run 'Build Legends' first."
                    )
                else:
                    legend_files = sorted([f for f in os.listdir(legends_dir) if f.endswith("_legend.csv")])
                    # E: Proof — show number and sample filenames
                    _log_info(f"[Materialize] P5: Found {len(legend_files)} legend files")
                    for _lf in legend_files[:5]:
                        _log_info(f"[Materialize] P5:   {_lf}")

                    if not legend_files:
                        _log_info("[Materialize] P5: No legend CSV files found — run 'Build Legends' first")
                    else:
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
                                    if codes_written:
                                        p5_nonzero_per_mesh[mesh_obj.name] = codes_written
                                        log_info(f"[Materialize] P5: {mesh_obj.name} wrote {codes_written} codes")
                                    # Ensure depsgraph knows this object changed (Phase 5 only updates mesh data)
                                    mesh_obj.data.update()
                                    mesh_obj.update_tag()
                                except Exception as ex:
                                    log_warn(f"[Materialize] P5 for {mesh_obj.name}: {ex}")
                                    continue

                        # E: Proof — summarize non-zero codes written
                        _log_info(f"[E][P5] Non-zero codes per mesh: {p5_nonzero_per_mesh}")
                        _log_info(f"[E][P5] Total legend codes across all meshes: {p5_total}")

            except Exception as ex:
                log_warn(f"[Materialize] P5 setup failed: {ex}")
            _log_info(f"[PROOF][P5_READBACK] total_legend_codes_written={p5_total} meshes={len(mesh_objs)}")

            # ── FINAL DEPSGRAPH FLUSH: Ensure ALL phases are visible in Spreadsheet ──
            try:
                bpy.context.view_layer.update()
                _log_info("[Materialize] Final view_layer.update() done — Spreadsheet should reflect all attributes")
            except Exception:
                pass

            # ── ACCEPTANCE LOGGING: Transparent summary for verification ──
            total_faces_all = sum(len(o.data.polygons) for o in mesh_objs if o.data)
            total_unlinked = total_faces_all - total_linked_faces
            log_info("=" * 60)
            log_info("[ACCEPTANCE] === MATERIALIZE SUMMARY ===")
            log_info(f"[MATERIALIZE] {total_linked_faces}/{total_faces_all} faces linked")
            log_info(f"[MATERIALIZE] {total_unlinked} faces unlinked")
            log_info(f"[MATERIALIZE] {len(meshes_written)} meshes written: {meshes_written[:5]}{'...' if len(meshes_written) > 5 else ''}")
            if p4_total > 0:
                log_info(f"[MATERIALIZE] {p4_total} OSM feature values written (Phase 4)")
            else:
                log_info(f"[MATERIALIZE] 0 OSM features written (Phase 4) — check GPKG feature tables")
            log_info(f"[MATERIALIZE] {p5_total} legend codes written (Phase 5)")

            # Per-mesh hit/miss breakdown
            for mesh_obj in mesh_objs[:5]:
                m = mesh_obj.data
                fc = len(m.polygons)
                has_link_attr = m.attributes.get("has_link")
                if has_link_attr:
                    linked_count = sum(1 for i in range(min(len(has_link_attr.data), fc)) if has_link_attr.data[i].value != 0)
                    log_info(f"[MATERIALIZE]   {mesh_obj.name}: {linked_count}/{fc} linked")

            # Link distance statistics
            dist_values = []
            for mesh_obj in mesh_objs:
                m = mesh_obj.data
                dist_a = m.attributes.get("link_dist_m")
                if dist_a:
                    for i in range(min(len(dist_a.data), len(m.polygons))):
                        v = dist_a.data[i].value
                        if v > 0:
                            dist_values.append(v)
            if dist_values:
                avg_dist = sum(dist_values) / len(dist_values)
                max_dist = max(dist_values)
                min_dist = min(dist_values)
                log_info(f"[LINKING] avg distance {avg_dist:.1f}m, min {min_dist:.1f}m, max {max_dist:.1f}m ({len(dist_values)} links)")

            # Legend code count per column
            if p5_nonzero_per_mesh:
                log_info(f"[LEGEND] codes per mesh: {dict(list(p5_nonzero_per_mesh.items())[:5])}")
            log_info("=" * 60)

            # Final summary
            summary = (
                f"P3: {total_linked_faces} linked faces across {len(meshes_written)} meshes | "
                f"P4: {p4_total} features | P5: {p5_total} legend codes"
            )
            log_info(f"[Materialize] ✓ Complete: {summary}")
            self.report({"INFO"}, f"Materialization complete: {summary}")
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
