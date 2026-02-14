"""Quick check: source_tile keys in link DB vs scene mesh names"""
import sqlite3, os, sys

DB = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\Test_Set\Output\koeln_regbez_buildings_READONLY_links.sqlite"

if not os.path.isfile(DB):
    print(f"DB not found: {DB}")
    sys.exit(1)

conn = sqlite3.connect(DB)
cur = conn.cursor()

# Get distinct source_tile values
tiles = [r[0] for r in cur.execute("SELECT DISTINCT source_tile FROM gml_osm_links ORDER BY source_tile").fetchall()]
print(f"Distinct source_tile values in link DB: {len(tiles)}")
for t in tiles[:10]:
    cnt = cur.execute("SELECT COUNT(*) FROM gml_osm_links WHERE source_tile=?", (t,)).fetchone()[0]
    print(f"  '{t}'  ({cnt} rows)")

print(f"\n... (showing first 10 of {len(tiles)})")

# Check if any tiles have path-like names
path_tiles = [t for t in tiles if "/" in t or "\\" in t or "." in t]
print(f"\nTiles with path separators or dots: {len(path_tiles)}")
for t in path_tiles[:5]:
    print(f"  '{t}'")

# Show tile naming pattern
print(f"\nFirst 5 tiles: {tiles[:5]}")
print(f"Last 5 tiles: {tiles[-5:]}")

# Compare with expected Blender mesh names
expected = [
    "LoD2_32_291_5625_1_NW",
    "LoD2_32_291_5626_1_NW",  
    "LoD2_32_292_5627_1_NW",
]
for e in expected:
    if e in tiles:
        print(f"\n'{e}' FOUND in link DB")
    else:
        # Try to find similar
        matches = [t for t in tiles if e[:20] in t]
        print(f"\n'{e}' NOT found in link DB. Similar: {matches[:3]}")

conn.close()
