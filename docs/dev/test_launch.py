"""
Blender launcher + link test.
Run: blender --background <blend_file> --python _test_launch.py
Or:  blender <blend_file> --python _test_launch.py
"""
import sys, os

# Ensure parent of M1_DC_V6 is on path so `import M1_DC_V6` works
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(ADDON_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

import bpy

# ── Step 1: Register addon ──
addon_name = os.path.basename(ADDON_DIR)  # "M1_DC_V6"
print(f"\n[LAUNCH] Addon dir: {ADDON_DIR}")
print(f"[LAUNCH] Addon name: {addon_name}")

# Check if already registered
if hasattr(bpy.types.Scene, "m1dc_settings"):
    print("[LAUNCH] Addon already registered (m1dc_settings exists)")
else:
    # Try to register
    try:
        mod = __import__(addon_name)
        mod.register()
        print("[LAUNCH] Addon registered via direct import + register()")
    except Exception as ex:
        print(f"[LAUNCH] Direct register failed: {ex}")
        # Try via preferences
        try:
            bpy.ops.preferences.addon_enable(module=addon_name)
            print("[LAUNCH] Addon enabled via preferences.addon_enable")
        except Exception as ex2:
            print(f"[LAUNCH] addon_enable also failed: {ex2}")
            print("[LAUNCH] FATAL: Cannot register addon. Aborting.")
            sys.exit(1)

# ── Step 2: Check scene has data ──
s = getattr(bpy.context.scene, "m1dc_settings", None)
if s is None:
    print("[LAUNCH] FATAL: m1dc_settings not found on scene")
    sys.exit(1)

print(f"\n[LAUNCH] ========== SCENE STATE ==========")
print(f"[LAUNCH] Objects: {len(bpy.data.objects)}")
print(f"[LAUNCH] Meshes: {len([o for o in bpy.data.objects if o.type == 'MESH'])}")
print(f"[LAUNCH] Collections: {[c.name for c in bpy.data.collections]}")

col = bpy.data.collections.get("CITYGML_TILES")
if col:
    gml_meshes = [o for o in col.objects if o.type == "MESH"]
    print(f"[LAUNCH] CITYGML_TILES: {len(gml_meshes)} meshes")
    if gml_meshes:
        m0 = gml_meshes[0]
        print(f"[LAUNCH]   sample: {m0.name}, faces={len(m0.data.polygons)}")
        for aname in ("gml_building_idx", "building_idx", "source_tile"):
            a = m0.data.attributes.get(aname)
            if a:
                print(f"[LAUNCH]   attr: {aname} domain={a.domain} type={a.data_type}")
            elif aname in m0:
                print(f"[LAUNCH]   prop: {aname}={m0[aname]!r}")
else:
    print("[LAUNCH] CITYGML_TILES: NOT FOUND")
    # Check for LoD2_ objects
    lod2 = [o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("LoD2_")]
    print(f"[LAUNCH] LoD2_* objects: {len(lod2)}")

# ── Step 3: Check/set paths ──
print(f"\n[LAUNCH] ========== SETTINGS ==========")
print(f"[LAUNCH] gpkg_path     = {s.gpkg_path!r}")
print(f"[LAUNCH] citygml_dir   = {s.citygml_dir!r}")
print(f"[LAUNCH] output_dir    = {s.output_dir!r}")
print(f"[LAUNCH] links_db_path = {s.links_db_path!r}")

# Validate paths
paths_ok = True
if not s.gpkg_path or not os.path.isfile(s.gpkg_path):
    print(f"[LAUNCH] WARNING: gpkg_path invalid: {s.gpkg_path!r}")
    paths_ok = False
if not s.citygml_dir or not os.path.isdir(s.citygml_dir):
    print(f"[LAUNCH] WARNING: citygml_dir invalid: {s.citygml_dir!r}")
    # Not fatal if meshes are in scene
if not s.output_dir:
    print(f"[LAUNCH] WARNING: output_dir empty")
    paths_ok = False

import glob
if s.output_dir and os.path.isdir(s.output_dir):
    sqlites = glob.glob(os.path.join(s.output_dir, "**", "*.sqlite"), recursive=True)
    print(f"[LAUNCH] SQLite files in output_dir: {len(sqlites)}")
    for f in sqlites[:10]:
        print(f"[LAUNCH]   {f} ({os.path.getsize(f):,} bytes)")

# ── Step 4: Run linking ──
print(f"\n[LAUNCH] ========== RUNNING LINK ==========")

# Ensure object mode
if bpy.context.mode != 'OBJECT':
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except:
        pass

try:
    result = bpy.ops.m1dc.link_citygml_osm()
    print(f"[LAUNCH] *** Operator result: {result} ***")
except Exception as ex:
    print(f"[LAUNCH] *** Operator EXCEPTION: {ex} ***")
    import traceback
    traceback.print_exc()

# ── Step 5: Post-link diagnostics ──
print(f"\n[LAUNCH] ========== POST-LINK ==========")
print(f"[LAUNCH] links_db_path = {s.links_db_path!r}")

if s.links_db_path and os.path.isfile(s.links_db_path):
    sz = os.path.getsize(s.links_db_path)
    print(f"[LAUNCH] ✓ Link DB EXISTS, size={sz:,} bytes")
    
    import sqlite3
    try:
        conn = sqlite3.connect(s.links_db_path)
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"[LAUNCH] tables: {tables}")
        
        if "gml_osm_links" in tables:
            cnt = cur.execute("SELECT COUNT(*) FROM gml_osm_links").fetchone()[0]
            print(f"[LAUNCH] gml_osm_links: {cnt} rows")
            if cnt > 0:
                cols = [d[0] for d in cur.execute("SELECT * FROM gml_osm_links LIMIT 1").description]
                print(f"[LAUNCH] columns: {cols}")
                for row in cur.execute("SELECT * FROM gml_osm_links LIMIT 5").fetchall():
                    print(f"[LAUNCH]   {row}")
        conn.close()
    except Exception as ex:
        print(f"[LAUNCH] DB query error: {ex}")
else:
    print(f"[LAUNCH] ✗ Link DB NOT FOUND at {s.links_db_path!r}")
    # Search for any new SQLite files
    if s.output_dir and os.path.isdir(s.output_dir):
        sqlites2 = glob.glob(os.path.join(s.output_dir, "**", "*.sqlite"), recursive=True)
        print(f"[LAUNCH] SQLite scan after link: {len(sqlites2)} files")
        for f in sqlites2[:15]:
            print(f"[LAUNCH]   {f} ({os.path.getsize(f):,} bytes)")

print(f"\n[LAUNCH] ========== ACCEPTANCE ==========")
db_ok = bool(s.links_db_path and os.path.isfile(s.links_db_path))
print(f"[LAUNCH] links_db_path non-empty : {bool(s.links_db_path)}")
print(f"[LAUNCH] links_db file exists    : {db_ok}")
print(f"[LAUNCH] ACCEPTANCE              : {'PASS' if db_ok else 'FAIL'}")
print(f"[LAUNCH] ==============================\n")
