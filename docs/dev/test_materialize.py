"""
Blender test: Materialize Links + verify face attributes.
Run: blender --background <blend_file> --python _test_materialize.py

PREREQUISITE: Run _test_launch.py first so links_db_path is set and DB exists.
"""
import sys, os

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(ADDON_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

import bpy

# ── Addon check ──
s = getattr(bpy.context.scene, "m1dc_settings", None)
if s is None:
    print("[TEST] FATAL: m1dc_settings not found")
    sys.exit(1)

print(f"\n{'='*80}")
print(f"[TEST] ========== PRE-MATERIALIZE ==========")
print(f"[TEST] links_db_path = {s.links_db_path!r}")
print(f"[TEST] links_db exists = {os.path.isfile(s.links_db_path) if s.links_db_path else False}")
print(f"[TEST] gpkg_path = {s.gpkg_path!r}")
print(f"[TEST] output_dir = {s.output_dir!r}")

# Check if links_db_path still set from previous run
# If this is a fresh .blend load, links_db_path might be empty.
# Try to set it if we can find the DB
if not s.links_db_path or not os.path.isfile(s.links_db_path):
    import glob
    out_dir = s.output_dir.strip()
    if out_dir and os.path.isdir(out_dir):
        cands = glob.glob(os.path.join(out_dir, "*_links.sqlite"))
        print(f"[TEST] Auto-detecting link DB from {out_dir}: {len(cands)} candidates")
        if cands:
            # Take the largest one
            cands.sort(key=lambda f: os.path.getsize(f), reverse=True)
            s.links_db_path = cands[0]
            print(f"[TEST] Set links_db_path = {s.links_db_path!r} ({os.path.getsize(cands[0]):,} bytes)")

# Check pre-existing face attributes on first mesh
col = bpy.data.collections.get("CITYGML_TILES")
if col:
    mesh_objs = [o for o in col.objects if o.type == "MESH"]
    if mesh_objs:
        m0 = mesh_objs[0]
        print(f"\n[TEST] Pre-materialize attrs on {m0.name}:")
        for aname in ("osm_way_id", "osm_id_int", "has_link", "link_conf", "link_dist_m", "link_iou"):
            a = m0.data.attributes.get(aname)
            if a:
                fc = min(len(a.data), 5)
                vals = [a.data[i].value for i in range(fc)]
                nz = sum(1 for i in range(len(a.data)) if a.data[i].value not in (0, 0.0))
                print(f"[TEST]   {aname}: domain={a.domain} type={a.data_type} nz={nz}/{len(a.data)} first5={vals}")
            else:
                print(f"[TEST]   {aname}: NOT PRESENT")

# ── Run Materialize ──
print(f"\n[TEST] ========== RUNNING MATERIALIZE ==========")

if bpy.context.mode != 'OBJECT':
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except:
        pass

try:
    result = bpy.ops.m1dc.materialize_links()
    print(f"[TEST] *** Operator result: {result} ***")
except Exception as ex:
    print(f"[TEST] *** Operator EXCEPTION: {ex} ***")
    import traceback
    traceback.print_exc()

# ── Post-materialize: verify face attributes ──
print(f"\n[TEST] ========== POST-MATERIALIZE ==========")

if col:
    mesh_objs = [o for o in col.objects if o.type == "MESH"]
    total_meshes_with_link = 0
    total_linked_faces = 0
    
    for i, m_obj in enumerate(mesh_objs[:5]):  # Check first 5 meshes
        m = m_obj.data
        fc = len(m.polygons)
        print(f"\n[TEST] Mesh {i}: {m_obj.name} ({fc} faces)")
        
        for aname in ("osm_way_id", "osm_id_int", "has_link", "link_conf", "link_dist_m", "link_iou"):
            a = m.attributes.get(aname)
            if a:
                nz = sum(1 for j in range(min(len(a.data), fc)) if a.data[j].value not in (0, 0.0))
                first5 = [a.data[j].value for j in range(min(5, fc))]
                print(f"[TEST]   {aname}: {a.data_type}/{a.domain} nz={nz}/{fc} first5={first5}")
                if aname == "osm_id_int" and nz > 0:
                    total_linked_faces += nz
                    total_meshes_with_link += 1
            else:
                print(f"[TEST]   {aname}: MISSING")
    
    # Also check for *_code attributes (Phase 5)
    if mesh_objs:
        m0 = mesh_objs[0].data
        code_attrs = [a.name for a in m0.attributes if a.name.endswith("_code")]
        print(f"\n[TEST] *_code attributes on {mesh_objs[0].name}: {code_attrs}")
    
    # Summary across ALL meshes
    print(f"\n[TEST] ========== FULL SUMMARY ==========")
    all_linked = 0
    all_meshes_linked = 0
    for m_obj in mesh_objs:
        m = m_obj.data
        a = m.attributes.get("osm_id_int")
        if a:
            fc = len(m.polygons)
            nz = sum(1 for j in range(min(len(a.data), fc)) if a.data[j].value != 0)
            if nz > 0:
                all_linked += nz
                all_meshes_linked += 1
    print(f"[TEST] Total meshes: {len(mesh_objs)}")
    print(f"[TEST] Meshes with osm_id_int>0: {all_meshes_linked}")
    print(f"[TEST] Total faces with osm_id_int>0: {all_linked}")

print(f"\n[TEST] ========== ACCEPTANCE ==========")
has_osm_id = False
has_link_conf = False
if col:
    mesh_objs = [o for o in col.objects if o.type == "MESH"]
    if mesh_objs:
        m0 = mesh_objs[0].data
        a1 = m0.attributes.get("osm_id_int")
        a2 = m0.attributes.get("link_conf")
        has_osm_id = a1 is not None and sum(1 for j in range(min(len(a1.data), len(m0.polygons))) if a1.data[j].value != 0) > 0
        has_link_conf = a2 is not None and sum(1 for j in range(min(len(a2.data), len(m0.polygons))) if a2.data[j].value != 0.0) > 0

print(f"[TEST] osm_id_int present + nonzero : {has_osm_id}")
print(f"[TEST] link_conf present + nonzero  : {has_link_conf}")
print(f"[TEST] ACCEPTANCE                   : {'PASS' if (has_osm_id and has_link_conf) else 'FAIL'}")
print(f"{'='*80}\n")
