"""
Deep diagnosis: compare building_idx values in link DB vs what scene meshes have.
Run: blender --background Test3.blend --python _check_bidx.py
"""
import sys, os

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(ADDON_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

import bpy
import sqlite3

s = getattr(bpy.context.scene, "m1dc_settings", None)
if s is None:
    print("[FATAL] m1dc_settings not found")
    sys.exit(1)

# Open link DB
db_path = s.links_db_path
if not db_path or not os.path.isfile(db_path):
    # Auto-detect
    import glob
    out_dir = s.output_dir.strip()
    cands = glob.glob(os.path.join(out_dir, "*_links.sqlite")) if out_dir else []
    if cands:
        db_path = max(cands, key=os.path.getsize)
        print(f"[AUTO] Using link DB: {db_path}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Get link_map per tile
from pathlib import Path
import re

def norm_source_tile(v):
    if v is None:
        return ""
    s = str(v).strip().replace("\\", "/").split("/")[-1]
    s = Path(s).stem
    s = re.sub(r"\.\d{3}$", "", s)
    return s

# Build link_map
link_map = {}
for row in cur.execute("SELECT source_tile, building_idx, osm_way_id FROM gml_osm_links"):
    key = (norm_source_tile(row[0]), int(row[1]))
    link_map[key] = row[2]

print(f"[DB] Total link_map entries: {len(link_map)}")

# Get DB building_idx per tile
db_bidx_per_tile = {}
for row in cur.execute("SELECT source_tile, building_idx FROM gml_osm_links ORDER BY source_tile, building_idx"):
    tile = norm_source_tile(row[0])
    db_bidx_per_tile.setdefault(tile, set()).add(int(row[1]))

conn.close()

# Now check scene meshes
col = bpy.data.collections.get("CITYGML_TILES")
if col:
    mesh_objs = [o for o in col.objects if o.type == "MESH"]
else:
    mesh_objs = []

print(f"\n[SCENE] {len(mesh_objs)} CityGML meshes")

# Compare first 5 meshes
mismatches = 0
for mesh_obj in mesh_objs[:10]:
    m = mesh_obj.data
    fc = len(m.polygons)
    source_tile = norm_source_tile(mesh_obj.get("source_tile", mesh_obj.name))
    
    # Find building_idx attr
    idx_attr = None
    attr_name_used = None
    for cand in ("gml_building_idx", "gml__building_idx", "building_idx"):
        a = m.attributes.get(cand)
        if a and a.domain == 'FACE' and a.data_type == 'INT' and len(a.data) == fc:
            idx_attr = a
            attr_name_used = cand
            break
    
    if idx_attr is None:
        print(f"\n[MESH] {mesh_obj.name} -> NO building_idx attr!")
        continue
    
    # Collect unique building_idx values from mesh
    mesh_bidx = set()
    for fi in range(fc):
        try:
            bidx = int(idx_attr.data[fi].value)
            mesh_bidx.add(bidx)
        except:
            pass
    
    # Compare with DB
    db_bidx = db_bidx_per_tile.get(source_tile, set())
    
    in_both = mesh_bidx & db_bidx
    only_mesh = mesh_bidx - db_bidx
    only_db = db_bidx - mesh_bidx
    
    # Count how many faces would match
    match_faces = 0
    for fi in range(fc):
        try:
            bidx = int(idx_attr.data[fi].value)
            key = (source_tile, bidx)
            if key in link_map:
                match_faces += 1
        except:
            pass
    
    print(f"\n[MESH] {mesh_obj.name}  source_tile='{source_tile}'  faces={fc}  attr={attr_name_used}")
    print(f"  mesh building_idx unique: {sorted(mesh_bidx)[:10]} ... (total {len(mesh_bidx)})")
    print(f"  DB   building_idx unique: {sorted(db_bidx)[:10]} ... (total {len(db_bidx)})")
    print(f"  intersection: {len(in_both)}  only_mesh: {len(only_mesh)}  only_db: {len(only_db)}")
    print(f"  face match count: {match_faces}/{fc}")
    
    if match_faces == 0 and len(db_bidx) > 0 and len(mesh_bidx) > 0:
        mismatches += 1
        # Show samples
        print(f"  *** MISMATCH: mesh bidx sample={sorted(mesh_bidx)[:5]}, DB bidx sample={sorted(db_bidx)[:5]}")

print(f"\n[SUMMARY] Tiles with 0 face matches despite DB entries: {mismatches}")
