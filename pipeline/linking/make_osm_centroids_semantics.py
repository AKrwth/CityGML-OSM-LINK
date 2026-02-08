#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
Build a runtime-friendly SQLite DB for Blender linking:
- Centroid (from GPKG RTree extents)
- Selected semantic columns (from feature table)
- No geometry blobs

Output file:
  C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\GPKG\koeln_linkdb.sqlite
"""

import sqlite3
from pathlib import Path

try:
    from ...utils.common import open_db_readonly
except ImportError:
    open_db_readonly = None

# =========================
# CONFIG
# =========================
SRC_GPKG = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\GPKG\koeln_regbez_buildings_READONLY.gpkg"
OUT_DB  = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\GPKG\koeln_linkdb.sqlite"

FEATURE_TABLE = "koelnregbez251213osm__multipolygons"
RTREE_TABLE   = "rtree_koelnregbez251213osm__multipolygons_geom"

ID_FIELD = "osm_way_id"  # primary key for linking (works in your data)

# Pick ONLY what you actually want to carry into Blender
# (You can add more; avoid 'geom' and usually avoid 'other_tags' unless needed)
SEMANTIC_COLUMNS = [
    "name",
    "type",
    "building",
    "amenity",
    "shop",
    "office",
    "landuse",
    "leisure",
    "historic",
    "tourism",
    "man_made",
    "natural",
    "military",
    "craft",
    "aeroway",
    "barrier",
    "boundary",
    "admin_level",
    # "other_tags",  # <-- huge; enable only if you really need it
]

OUT_TABLE = "osm_building_link"
BATCH_SIZE = 50_000


def die(msg: str):
    raise SystemExit(f"[ERROR] {msg}")


def main():
    src_path = Path(SRC_GPKG)
    out_path = Path(OUT_DB)

    if not src_path.exists():
        die(f"Source GPKG not found: {src_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    # Open source read-only
    if open_db_readonly:
        src = open_db_readonly(str(src_path), log_open=False)
    else:
        src_uri = f"file:{src_path.as_posix()}?mode=ro"
        src = sqlite3.connect(src_uri, uri=True)
        src.execute("PRAGMA busy_timeout=5000;")
        src.execute("PRAGMA query_only=ON;")
    src.row_factory = sqlite3.Row

    # Open output
    out = sqlite3.connect(str(out_path))
    out_cur = out.cursor()

    out_cur.execute("PRAGMA journal_mode=WAL;")
    out_cur.execute("PRAGMA synchronous=NORMAL;")
    out_cur.execute("PRAGMA temp_store=MEMORY;")

    # Validate tables
    if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (FEATURE_TABLE,)).fetchone():
        die(f"Feature table not found: {FEATURE_TABLE}")
    if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (RTREE_TABLE,)).fetchone():
        die(f"RTree table not found: {RTREE_TABLE}")

    # Validate columns
    fcols = [r["name"] for r in src.execute(f"PRAGMA table_info('{FEATURE_TABLE}');").fetchall()]
    if ID_FIELD not in fcols:
        die(f"ID field '{ID_FIELD}' not found in {FEATURE_TABLE}")

    missing = [c for c in SEMANTIC_COLUMNS if c not in fcols]
    if missing:
        print(f"[WARN] Some semantic columns not found and will be skipped: {missing}")
        semantic_cols = [c for c in SEMANTIC_COLUMNS if c in fcols]
    else:
        semantic_cols = SEMANTIC_COLUMNS[:]

    # Build output schema
    out_cur.execute(f"DROP TABLE IF EXISTS {OUT_TABLE};")

    # Build CREATE TABLE dynamically
    semantic_schema = ",\n            ".join([f"{c} TEXT" for c in semantic_cols])

    out_cur.execute(
        f"""
        CREATE TABLE {OUT_TABLE} (
            {ID_FIELD} TEXT NOT NULL,
            cx REAL NOT NULL,
            cy REAL NOT NULL,
            minx REAL NOT NULL,
            miny REAL NOT NULL,
            maxx REAL NOT NULL,
            maxy REAL NOT NULL,
            {semantic_schema}
        );
        """
    )

    out_cur.execute(f"CREATE INDEX idx_{OUT_TABLE}_id ON {OUT_TABLE}({ID_FIELD});")
    out_cur.execute(f"CREATE INDEX idx_{OUT_TABLE}_xy ON {OUT_TABLE}(cx, cy);")
    out.commit()

    # Count
    n_with_id = src.execute(
        f"SELECT COUNT(*) AS n FROM {FEATURE_TABLE} WHERE {ID_FIELD} IS NOT NULL;"
    ).fetchone()["n"]
    print(f"[INFO] Rows to export (with {ID_FIELD}): {n_with_id:,}")

    # Build SELECT
    semantic_select = ", ".join([f"b.{c}" for c in semantic_cols]) if semantic_cols else ""
    if semantic_select:
        semantic_select = ", " + semantic_select

    select_sql = f"""
        SELECT
            b.{ID_FIELD} AS id_primary,
            (r.minx + r.maxx) / 2.0 AS cx,
            (r.miny + r.maxy) / 2.0 AS cy,
            r.minx, r.miny, r.maxx, r.maxy
            {semantic_select}
        FROM {FEATURE_TABLE} b
        JOIN {RTREE_TABLE} r
            ON b.rowid = r.id
        WHERE b.{ID_FIELD} IS NOT NULL
    """

    # Build INSERT
    insert_cols = [ID_FIELD, "cx", "cy", "minx", "miny", "maxx", "maxy"] + semantic_cols
    placeholders = ",".join(["?"] * len(insert_cols))
    insert_sql = f"INSERT INTO {OUT_TABLE} ({','.join(insert_cols)}) VALUES ({placeholders});"

    src_cur = src.cursor()
    src_cur.execute(select_sql)

    inserted = 0
    while True:
        rows = src_cur.fetchmany(BATCH_SIZE)
        if not rows:
            break

        batch = []
        for r in rows:
            values = [
                r["id_primary"],
                r["cx"], r["cy"],
                r["minx"], r["miny"], r["maxx"], r["maxy"],
            ]
            for c in semantic_cols:
                values.append(r[c])
            batch.append(tuple(values))

        out_cur.executemany(insert_sql, batch)
        out.commit()
        inserted += len(batch)
        print(f"  Inserted: {inserted:,} / {n_with_id:,}")

    final_n = out.execute(f"SELECT COUNT(*) FROM {OUT_TABLE};").fetchone()[0]
    print("\n[DONE]")
    print(f"  Output DB: {out_path}")
    print(f"  Table '{OUT_TABLE}' rows: {final_n:,}")

    src.close()
    out.close()


if __name__ == "__main__":
    main()
