#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
Link CityGML buildings (by centroid) to OSM buildings (by centroid) using two helper SQLite DBs.

INPUTS:
  1) GML centroid DB (from make_gml_centroids.py):
       db_GML_Kacheln_centroids.sqlite
       table: gml_building_centroids(source_tile, building_idx, gml_id, cx, cy, minx, miny, maxx, maxy)

  2) OSM centroid+semantics DB (from make_osm_centroids_semantics.py):
       koeln_linkdb.sqlite  (name can vary)
       table: osm_building_link (expected)
         columns include: osm_way_id, cx, cy, ... (plus chosen semantics)

OUTPUT:
  koeln_links.sqlite
  table: gml_osm_links
    source_tile, building_idx, osm_way_id, dist_m, confidence,
    cx_gml, cy_gml, cx_osm, cy_osm

MATCHING LOGIC (robust + simple):
  - Work per GML tile:
    - Load GML centroids for that tile
    - Compute tile bbox expanded by SEARCH_RADIUS_M
    - Pull OSM candidates in that bbox (single SQL query)
    - Build an in-memory grid index (cell size = GRID_CELL_M)
    - For each GML centroid: search neighbor cells, find nearest within radius
  - Confidence = max(0, 1 - dist / SEARCH_RADIUS_M)

WHY THIS IS STABLE:
  - No spatialite needed.
  - No Blender dependency.
  - No per-row SQL nearest-neighbor.
  - Scales: queries OSM in chunks (tile bbox), not whole city each time.

Assumes CRS units are meters (e.g., EPSG:25832). If CRS differs, do NOT run.
"""

import sqlite3
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ============================================================
# CONFIG (EDIT THESE)
# ============================================================

# Path to folder containing db_GML_Kacheln_centroids.sqlite
GML_DB_PATH = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\Kacheln\koeln_regbez\db_GML_Kacheln_centroids.sqlite"

# Path to OSM centroid+semantics DB (output of make_osm_centroids_semantics.py)
OSM_DB_PATH = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\GPKG\koeln_linkdb.sqlite"

# Output link DB
OUT_DB_PATH = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\GPKG\koeln_links.sqlite"

# Table names (change only if your scripts wrote different names)
GML_TABLE = "gml_building_centroids"
OSM_TABLE = "osm_building_link"
OUT_TABLE = "gml_osm_links"

# Matching parameters
SEARCH_RADIUS_M = 25.0          # max allowed centroid distance
GRID_CELL_M = 25.0              # grid cell size (usually same as radius)
MIN_CONFIDENCE = 0.0            # keep all matches >= this
MAX_DIST_FOR_WARN = 10.0        # warn if matched but distance larger than this

# Progress logging
LOG_EVERY_TILES = 10


# ============================================================
# Helpers
# ============================================================

def grid_key(x: float, y: float, cell: float) -> Tuple[int, int]:
    return (int(math.floor(x / cell)), int(math.floor(y / cell)))


def dist2(ax: float, ay: float, bx: float, by: float) -> float:
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy


def is_verbose_debug() -> bool:
    """
    [PHASE 13] Check if running under VSCode debugger.
    When True: full logging verbosity (no suppression).
    When False: "3 examples + progress + summary" policy.
    """
    return sys.gettrace() is not None


def ensure_schema(conn_out: sqlite3.Connection) -> None:
    cur = conn_out.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")

    cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE};")
    cur.execute(f"""
    CREATE TABLE {OUT_TABLE} (
        source_tile   TEXT NOT NULL,
        building_idx  INTEGER NOT NULL,
        osm_way_id    TEXT,
        dist_m        REAL,
        confidence    REAL,
        cx_gml        REAL NOT NULL,
        cy_gml        REAL NOT NULL,
        cx_osm        REAL,
        cy_osm        REAL,
        PRIMARY KEY (source_tile, building_idx)
    );
    """)
    cur.execute(f"CREATE INDEX idx_{OUT_TABLE}_osm ON {OUT_TABLE}(osm_way_id);")
    cur.execute(f"CREATE INDEX idx_{OUT_TABLE}_dist ON {OUT_TABLE}(dist_m);")
    conn_out.commit()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
        (name,)
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r[1] == col for r in rows)


def _pick_osm_table(conn: sqlite3.Connection, preferred: str = "osm_building_link_local", fallback: str = "osm_building_link") -> str:
    if table_exists(conn, preferred):
        return preferred
    if table_exists(conn, fallback):
        return fallback
    raise RuntimeError("No suitable OSM table found (expected osm_building_link_local or osm_building_link)")


def main(min_e: float = 0.0, min_n: float = 0.0):
    gml_db = Path(GML_DB_PATH)
    osm_db = Path(OSM_DB_PATH)
    out_db = Path(OUT_DB_PATH)

    if min_e != 0.0 or min_n != 0.0:
        print(f"[Link] Applying WORLD_ORIGIN offset to GML: E-={min_e:.3f}, N-={min_n:.3f}")

    if not gml_db.exists():
        raise SystemExit(f"[ERROR] GML DB not found: {gml_db}")
    if not osm_db.exists():
        raise SystemExit(f"[ERROR] OSM DB not found: {osm_db}")

    print("[1/6] Opening DBs …")
    conn_gml = sqlite3.connect(str(gml_db))
    conn_osm = sqlite3.connect(str(osm_db))
    if out_db.exists():
        out_db.unlink()
    conn_out = sqlite3.connect(str(out_db))

    print("[2/6] Validating schema …")
    if not table_exists(conn_gml, GML_TABLE):
        raise SystemExit(f"[ERROR] Missing table in GML DB: {GML_TABLE}")
    osm_table = _pick_osm_table(conn_osm)
    shift_gml = osm_table == "osm_building_link_local"
    print(f"[Link] Using OSM table: {osm_table}")
    if shift_gml:
        print(f"[Link] GML will be shifted by WORLD_ORIGIN offset: E-={min_e:.3f}, N-={min_n:.3f}")
    else:
        if min_e != 0.0 or min_n != 0.0:
            print("[Link] OSM is in CRS; GML will NOT be shifted (offset ignored).")

    if not table_exists(conn_osm, osm_table):
        raise SystemExit(f"[ERROR] Missing table in OSM DB: {osm_table}")

    for need_col in ("source_tile", "building_idx", "cx", "cy"):
        if not column_exists(conn_gml, GML_TABLE, need_col):
            raise SystemExit(f"[ERROR] GML table missing column: {need_col}")

    for need_col in ("osm_way_id", "cx", "cy"):
        if not column_exists(conn_osm, osm_table, need_col):
            raise SystemExit(f"[ERROR] OSM table missing column: {need_col}")

    print("[3/6] Creating output schema …")
    ensure_schema(conn_out)

    # Collect list of tiles present in GML DB
    tiles = conn_gml.execute(
        f"SELECT DISTINCT source_tile FROM {GML_TABLE} ORDER BY source_tile;"
    ).fetchall()
    tiles = [t[0] for t in tiles]
    if not tiles:
        raise SystemExit("[ERROR] No tiles found in GML centroid table.")

    total_tiles = len(tiles)
    print(f"[4/6] Found {total_tiles} tiles. Matching per tile …")
    print(f"      SEARCH_RADIUS_M={SEARCH_RADIUS_M}  GRID_CELL_M={GRID_CELL_M}")

    cur_out = conn_out.cursor()

    total_gml = 0
    matched = 0
    unmatched = 0
    suspicious = 0
    
    # [PHASE 13] Progress tracking: 3 examples + progress + summary
    verbose = is_verbose_debug()
    DETAIL_LIMIT = 3 if not verbose else total_tiles
    PROGRESS_INTERVAL = 10

    for ti, tile_name in enumerate(tiles, start=1):
        # Load all GML buildings for tile
        gml_rows = conn_gml.execute(
            f"SELECT building_idx, cx, cy FROM {GML_TABLE} WHERE source_tile=? ORDER BY building_idx;",
            (tile_name,)
        ).fetchall()

        if not gml_rows:
            continue

        total_gml += len(gml_rows)

        # Tile bbox from GML points (cheap)
        if shift_gml:
            xs = [(r[1] - min_e) for r in gml_rows]
            ys = [(r[2] - min_n) for r in gml_rows]
        else:
            xs = [r[1] for r in gml_rows]
            ys = [r[2] for r in gml_rows]
        minx = min(xs) - SEARCH_RADIUS_M
        maxx = max(xs) + SEARCH_RADIUS_M
        miny = min(ys) - SEARCH_RADIUS_M
        maxy = max(ys) + SEARCH_RADIUS_M

        # Pull OSM candidates in bbox (single query per tile)
        osm_rows = conn_osm.execute(
            f"""
            SELECT osm_way_id, cx, cy
                        FROM {osm_table}
            WHERE osm_way_id IS NOT NULL
              AND cx BETWEEN ? AND ?
              AND cy BETWEEN ? AND ?;
            """,
            (minx, maxx, miny, maxy)
        ).fetchall()

        # Build in-memory grid index for OSM candidates
        grid: Dict[Tuple[int, int], List[Tuple[str, float, float]]] = {}
        for osm_way_id, cx, cy in osm_rows:
            k = grid_key(cx, cy, GRID_CELL_M)
            grid.setdefault(k, []).append((osm_way_id, cx, cy))

        # Match each GML point against nearby grid cells
        r2 = SEARCH_RADIUS_M * SEARCH_RADIUS_M

        for building_idx, gx_raw, gy_raw in gml_rows:
            if shift_gml:
                # Apply WORLD_ORIGIN offset to convert from CRS to local coords
                gx = gx_raw - min_e
                gy = gy_raw - min_n
            else:
                # OSM is in CRS; use raw CRS GML coords
                gx = gx_raw
                gy = gy_raw
            gk = grid_key(gx, gy, GRID_CELL_M)

            best_id: Optional[str] = None
            best_cx = None
            best_cy = None
            best_d2 = None

            # search in neighbor cells (3x3 is usually enough when cell ~= radius)
            for dx_cell in (-1, 0, 1):
                for dy_cell in (-1, 0, 1):
                    cell = (gk[0] + dx_cell, gk[1] + dy_cell)
                    pts = grid.get(cell)
                    if not pts:
                        continue
                    for osm_way_id, ox, oy in pts:
                        d2 = dist2(gx, gy, ox, oy)
                        if d2 <= r2 and (best_d2 is None or d2 < best_d2):
                            best_d2 = d2
                            best_id = osm_way_id
                            best_cx = ox
                            best_cy = oy

            if best_id is None:
                # no match
                cur_out.execute(
                    f"INSERT INTO {OUT_TABLE} VALUES (?,?,?,?,?,?,?,?,?)",
                    (tile_name, building_idx, None, None, 0.0, gx, gy, None, None)
                )
                unmatched += 1
                continue

            d = math.sqrt(best_d2) if best_d2 is not None else None
            conf = max(0.0, 1.0 - (d / SEARCH_RADIUS_M)) if d is not None else 0.0

            if conf < MIN_CONFIDENCE:
                # treat as unmatched if below threshold
                cur_out.execute(
                    f"INSERT INTO {OUT_TABLE} VALUES (?,?,?,?,?,?,?,?,?)",
                    (tile_name, building_idx, None, None, 0.0, gx, gy, None, None)
                )
                unmatched += 1
                continue

            if d is not None and d > MAX_DIST_FOR_WARN:
                suspicious += 1

            cur_out.execute(
                f"INSERT INTO {OUT_TABLE} VALUES (?,?,?,?,?,?,?,?,?)",
                (tile_name, building_idx, best_id, float(d), float(conf), gx, gy, float(best_cx), float(best_cy))
            )
            matched += 1

        conn_out.commit()

        # [PHASE 13] Conditional logging: first 3 detailed, then progress every 10
        should_log_detail = verbose or (ti <= DETAIL_LIMIT)
        should_log_progress = (not verbose) and (ti > DETAIL_LIMIT) and ((ti % PROGRESS_INTERVAL) == 0)
        
        if should_log_detail or ti == total_tiles:
            print(f"  [{ti}/{total_tiles}] tiles done | GML buildings so far: {total_gml:,} | matched: {matched:,} | unmatched: {unmatched:,}")
        elif should_log_progress:
            print(f"[Linking] progress: tile {ti}/{total_tiles} | matched={matched:,} unmatched={unmatched:,}")

    print("[5/6] Final stats …")
    pct = (matched / total_gml * 100.0) if total_gml else 0.0
    print(f"  GML buildings total: {total_gml:,}")
    print(f"  Matched:            {matched:,} ({pct:.2f}%)")
    print(f"  Unmatched:          {unmatched:,}")
    print(f"  Suspicious (> {MAX_DIST_FOR_WARN} m): {suspicious:,}")

    print("[6/6] Output written:")
    print(f"  {out_db}")

    conn_gml.close()
    conn_osm.close()
    conn_out.close()


if __name__ == "__main__":
    main()
