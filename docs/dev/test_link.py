"""
Quick smoke test: run link_citygml_osm and dump diagnostic info.
Run inside Blender:  exec(open(r"c:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\M1_DC_V6\_test_link.py").read())
"""
import bpy, os, glob

s = bpy.context.scene.m1dc_settings

print("\n" + "=" * 80)
print("[TEST] ========== PRE-LINK DIAGNOSTICS ==========")
print(f"[TEST] gpkg_path          = {s.gpkg_path!r}")
print(f"[TEST] citygml_dir        = {s.citygml_dir!r}")
print(f"[TEST] output_dir         = {s.output_dir!r}")
print(f"[TEST] links_db_path      = {s.links_db_path!r}")
print(f"[TEST] citygml_dir isdir  = {os.path.isdir(s.citygml_dir) if s.citygml_dir else 'N/A'}")
print(f"[TEST] gpkg_path isfile   = {os.path.isfile(s.gpkg_path) if s.gpkg_path else 'N/A'}")

# Check existing SQLite files in output_dir
out_dir = s.output_dir.strip()
if out_dir and os.path.isdir(out_dir):
    cands = glob.glob(os.path.join(out_dir, "**", "*.sqlite"), recursive=True)
    print(f"[TEST] SQLite files in output_dir: {len(cands)}")
    for c in cands[:10]:
        sz = os.path.getsize(c)
        print(f"[TEST]   {c}  ({sz:,} bytes)")
else:
    print(f"[TEST] output_dir not valid: {out_dir!r}")

# Check CityGML meshes in scene
col = bpy.data.collections.get("CITYGML_TILES")
if col:
    mesh_objs = [o for o in col.objects if o.type == "MESH"]
    print(f"[TEST] CITYGML_TILES collection: {len(mesh_objs)} meshes")
    if mesh_objs:
        m0 = mesh_objs[0]
        print(f"[TEST]   sample: {m0.name}, faces={len(m0.data.polygons)}, source_tile={m0.get('source_tile','?')}")
        # Check for building_idx attribute
        for aname in ("gml_building_idx", "gml__building_idx", "building_idx"):
            a = m0.data.attributes.get(aname)
            if a:
                print(f"[TEST]   has {aname}: domain={a.domain} type={a.data_type} len={len(a.data)}")
else:
    print("[TEST] CITYGML_TILES collection: NOT FOUND")

print()
print("[TEST] ========== RUNNING link_citygml_osm ==========")

# Ensure we're in object mode
if bpy.context.mode != 'OBJECT':
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except:
        pass

# Run the operator
try:
    result = bpy.ops.m1dc.link_citygml_osm()
    print(f"[TEST] Operator result: {result}")
except Exception as ex:
    print(f"[TEST] Operator EXCEPTION: {ex}")
    import traceback
    traceback.print_exc()

print()
print("[TEST] ========== POST-LINK DIAGNOSTICS ==========")
print(f"[TEST] links_db_path      = {s.links_db_path!r}")
if s.links_db_path:
    exists = os.path.isfile(s.links_db_path)
    sz = os.path.getsize(s.links_db_path) if exists else 0
    print(f"[TEST] links_db exists    = {exists}")
    print(f"[TEST] links_db size      = {sz:,} bytes")
    if exists:
        import sqlite3
        try:
            conn = sqlite3.connect(s.links_db_path)
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            print(f"[TEST] tables: {tables}")
            if "gml_osm_links" in tables:
                cnt = cur.execute("SELECT COUNT(*) FROM gml_osm_links").fetchone()[0]
                print(f"[TEST] gml_osm_links rows: {cnt}")
                if cnt > 0:
                    sample = cur.execute("SELECT * FROM gml_osm_links LIMIT 3").fetchall()
                    cols = [d[0] for d in cur.description]
                    print(f"[TEST] columns: {cols}")
                    for row in sample:
                        print(f"[TEST]   {row}")
            conn.close()
        except Exception as ex:
            print(f"[TEST] DB query error: {ex}")
else:
    print("[TEST] links_db_path is EMPTY — linking did not persist the path")

# Check again for any new SQLite files
if out_dir and os.path.isdir(out_dir):
    cands2 = glob.glob(os.path.join(out_dir, "**", "*.sqlite"), recursive=True)
    new_files = set(cands2) - set(glob.glob(os.path.join(out_dir, "**", "*.sqlite"), recursive=True) if not out_dir else [])
    print(f"[TEST] SQLite files in output_dir after link: {len(cands2)}")
    for c in cands2[:15]:
        sz = os.path.getsize(c)
        print(f"[TEST]   {c}  ({sz:,} bytes)")

print("[TEST] ========== DONE ==========")
print("=" * 80)
