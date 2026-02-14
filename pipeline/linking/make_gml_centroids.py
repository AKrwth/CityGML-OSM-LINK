#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
CityGML Tile → per-building centroid + bbox → SQLite helper DB

OUTPUT (in the same folder as the GML tiles):
  db_GML_Kacheln_centroids.sqlite

TABLE:
  gml_building_centroids(
    source_tile TEXT,
    building_idx INTEGER,
    gml_id TEXT,
    cx REAL, cy REAL,
    minx REAL, miny REAL, maxx REAL, maxy REAL
  )

NOTES
- Efficient parsing using iterparse (streaming) to handle many tiles.
- Groups coordinates by each <bldg:Building> (or BuildingPart if desired).
- Computes centroid from bbox center (robust even if geometry is complex).
- Assumes coordinates are in the CRS of the GML dataset (often EPSG:25832).
"""

import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator, Optional, Tuple

# =========================
# CONFIG
# =========================
GML_DIR = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\Kacheln\koeln_regbez"  # <-- your tile folder
OUT_DB_NAME = "db_GML_Kacheln_centroids.sqlite"

# If True, also treat bldg:BuildingPart as a separate "building unit"
INCLUDE_BUILDINGPARTS = False

# Progress logging
LOG_EVERY_N_BUILDINGS = 2000


# -------------------------
# Helpers
# -------------------------
_FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def iter_coords_from_text(text: str) -> Iterator[Tuple[float, float]]:
  """
  Extract (x, y) pairs from mixed numeric text, ignoring z values when present.
  """
  if not text:
    return
  nums = [float(m.group(0)) for m in _FLOAT_RE.finditer(text)]
  step = 3 if (len(nums) % 3 == 0 and len(nums) >= 3) else 2
  for i in range(0, len(nums) - 1, step):
    yield nums[i], nums[i + 1]


def update_bbox(bbox: Optional[Tuple[float, float, float, float]], x: float, y: float) -> Tuple[float, float, float, float]:
  if bbox is None:
    return (x, y, x, y)
  minx, miny, maxx, maxy = bbox
  return (min(minx, x), min(miny, y), max(maxx, x), max(maxy, y))


def localname(tag: str) -> str:
  if "}" in tag:
    return tag.split("}", 1)[1]



def is_building_tag(tag: str) -> bool:
  name = localname(tag)
  if name == "Building":
    return True
  if INCLUDE_BUILDINGPARTS and name == "BuildingPart":
    return True
  return False


def iter_building_bboxes(gml_path: Path) -> Iterator[Tuple[int, Optional[str], Tuple[float, float, float, float]]]:
  building_idx = 0
  context = ET.iterparse(str(gml_path), events=("start", "end"))
  next(context)  # prime the iterator

  for event, elem in context:
    if event != "end":
      continue
    if not is_building_tag(elem.tag):
      continue

    bbox = None
    for child in elem.iter():
      lname = localname(child.tag)
      if lname not in ("posList", "pos"):
        continue
      bbox_text = child.text or ""
      for x, y in iter_coords_from_text(bbox_text):
        bbox = update_bbox(bbox, x, y)

    if bbox is not None:
      gml_id = None
      for key in ("{http://www.opengis.net/gml}id", "gml:id", "id"):
        if key in elem.attrib:
          gml_id = elem.attrib.get(key)
          break
      if gml_id is None:
        for k in elem.attrib:
          if localname(k) == "id":
            gml_id = elem.attrib.get(k)
            break
      yield building_idx, gml_id, bbox
      building_idx += 1

    elem.clear()


def ensure_schema(conn: sqlite3.Connection) -> None:
  cur = conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL;")
  cur.execute("PRAGMA synchronous=NORMAL;")
  cur.execute("DROP TABLE IF EXISTS gml_building_centroids;")
  cur.execute(
    """
    CREATE TABLE gml_building_centroids(
      source_tile TEXT NOT NULL,
      building_idx INTEGER NOT NULL,
      gml_id TEXT,
      cx REAL NOT NULL,
      cy REAL NOT NULL,
      minx REAL NOT NULL,
      miny REAL NOT NULL,
      maxx REAL NOT NULL,
      maxy REAL NOT NULL,
      PRIMARY KEY (source_tile, building_idx)
    );
    """
  )
  conn.commit()


def process_tile(gml_path: Path, conn: sqlite3.Connection) -> int:
  # Canonical key normalization (single source of truth)
  try:
      from .key_normalization import normalize_source_tile
      tile_key = normalize_source_tile(gml_path.name)
  except ImportError:
      tile_key = gml_path.stem

  cur = conn.cursor()
  inserted = 0
  for bidx, gml_id, bbox in iter_building_bboxes(gml_path):
    minx, miny, maxx, maxy = bbox
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    cur.execute(
      """
      INSERT INTO gml_building_centroids(source_tile, building_idx, gml_id, cx, cy, minx, miny, maxx, maxy)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
      """,
      (tile_key, bidx, gml_id, cx, cy, minx, miny, maxx, maxy),
    )
    inserted += 1
    if inserted and (inserted % LOG_EVERY_N_BUILDINGS == 0):
      print(f"  [{gml_path.name}] buildings processed: {inserted}")
  conn.commit()
  return inserted


def main():
  gml_dir = Path(GML_DIR)
  if not gml_dir.exists() or not gml_dir.is_dir():
    raise SystemExit(f"[ERROR] GML_DIR not found: {gml_dir}")

  out_db = gml_dir / OUT_DB_NAME
  if out_db.exists():
    out_db.unlink()

  conn = sqlite3.connect(str(out_db))
  ensure_schema(conn)

  total = 0
  gml_files = sorted([p for p in gml_dir.iterdir() if p.suffix.lower() == ".gml"])
  if not gml_files:
    raise SystemExit(f"[ERROR] No .gml files found in {gml_dir}")

  print(f"[GML] Writing centroids to {out_db}")
  for path in gml_files:
    total += process_tile(path, conn)
  conn.close()
  print(f"[GML] Done. Buildings processed: {total}")


if __name__ == "__main__":
  main()
