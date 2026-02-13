#!/usr/bin/env python3
"""
prepare_rgb_tiles_only.py — Prepare NRW RGB DOP tiles for Blender (RGB_Tiles only)

⚠️ NOT PART OF BLENDER ADD-ON PIPELINE ⚠️
Standalone preprocessing. Run BEFORE Blender.

Folder (your current real structure):
  RGB_Tiles/
    dop10rgbi_32_<E>_<N>_1_nw_2025.jp2
    dop_nw.csv
    dop_j2w/*.j2w

Target structure (after script):
  RGB_Tiles/
    dop_nw.csv                    # stays in root (only CSV in root)
    raw/
      *.jp2
      dop_j2w/*.j2w
      _old/                       # optional: any unexpected leftovers
    derived/
      dop_rgb_32_<E>_<N>_1m.tif    # GeoTIFF downscaled to 1.0 m/pixel

Why:
- JP2 at 0.1m/px is huge.
- 1.0m/px is ~1000x1000px per 1km tile → lightweight and fast in Blender.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple, List


JP2_PATTERN = re.compile(r"^dop10rgbi_32_(\d+)_(\d+)_1_.*\.jp2$", re.IGNORECASE)
CSV_PATTERN = re.compile(r".*\.csv$", re.IGNORECASE)
J2W_PATTERN = re.compile(r".*\.j2w$", re.IGNORECASE)

DEFAULT_RESOLUTION_M = 1.0
DEFAULT_COMPRESSION = "DEFLATE"


def human_mb(num_bytes: int) -> float:
    return num_bytes / (1024 * 1024)


def folder_size_bytes(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def find_gdal_translate(explicit_exe: Optional[str], gdal_bin: Optional[str]) -> Optional[str]:
    """
    Find gdal_translate.exe in a robust way.
    Priority:
      1) --gdal_translate explicit path
      2) --gdal_bin folder (contains gdal_translate(.exe))
      3) PATH lookup
      4) common QGIS install locations
    """
    # 1) explicit
    if explicit_exe:
        p = Path(explicit_exe)
        if p.exists():
            return str(p)

    # 2) bin folder
    if gdal_bin:
        binp = Path(gdal_bin)
        cand = binp / ("gdal_translate.exe" if os.name == "nt" else "gdal_translate")
        if cand.exists():
            return str(cand)

    # 3) PATH
    exe_name = "gdal_translate.exe" if os.name == "nt" else "gdal_translate"
    found = shutil.which(exe_name)
    if found:
        return found

    # 4) common Windows QGIS locations
    if os.name == "nt":
        program_files = Path(r"C:\Program Files")
        for p in program_files.glob(r"QGIS */bin/gdal_translate.exe"):
            if p.exists():
                return str(p)

    return None


def move_into(src: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        # if same file already moved, skip
        return
    shutil.move(str(src), str(dst))


def organize_rgb_tiles(rgb_dir: Path, move_leftovers_to_old: bool) -> Tuple[int, int, int, int]:
    """
    Organize files inside RGB_Tiles:
      - move *.jp2 to raw/
      - move dop_j2w/*.j2w (or any *.j2w anywhere under RGB_Tiles) to raw/dop_j2w/
      - keep *.csv in root (dop_nw.csv)
      - optionally move other leftover files/folders from root into raw/_old/
    Returns counts: (jp2_moved, j2w_moved, csv_kept, leftovers_moved)
    """
    raw_dir = rgb_dir / "raw"
    raw_j2w_dir = raw_dir / "dop_j2w"
    raw_old_dir = raw_dir / "_old"

    raw_dir.mkdir(exist_ok=True)
    raw_j2w_dir.mkdir(parents=True, exist_ok=True)

    jp2_moved = 0
    j2w_moved = 0
    csv_kept = 0
    leftovers_moved = 0

    # Move JP2 from root
    for f in rgb_dir.glob("*.jp2"):
        move_into(f, raw_dir)
        jp2_moved += 1

    # Move J2W from anywhere under RGB_Tiles (including dop_j2w/)
    for f in rgb_dir.rglob("*.j2w"):
        # already in the right place?
        if f.parent == raw_j2w_dir:
            continue
        move_into(f, raw_j2w_dir)
        j2w_moved += 1

    # Keep CSV in root (count them)
    for f in rgb_dir.glob("*.csv"):
        csv_kept += 1

    # Optional cleanup: move unexpected leftovers from root into raw/_old/
    if move_leftovers_to_old:
        allowed_in_root = {"raw", "derived"}
        allowed_ext = {".csv"}  # keep CSV only
        for item in rgb_dir.iterdir():
            if item.name in allowed_in_root:
                continue
            if item.is_file() and item.suffix.lower() in allowed_ext:
                continue
            # anything else in root gets moved
            move_into(item, raw_old_dir)
            leftovers_moved += 1

    # Also: if original folder "dop_j2w" is now empty, move it into _old or remove
    dop_j2w = rgb_dir / "dop_j2w"
    if dop_j2w.exists() and dop_j2w.is_dir():
        try:
            # if empty -> remove
            if not any(dop_j2w.iterdir()):
                dop_j2w.rmdir()
        except OSError:
            pass

    return jp2_moved, j2w_moved, csv_kept, leftovers_moved


def convert_one(
    jp2_path: Path,
    derived_dir: Path,
    resolution_m: float,
    compression: str,
    gdal_translate: str,
) -> Tuple[bool, float]:
    m = JP2_PATTERN.match(jp2_path.name)
    if not m:
        return False, 0.0

    e_km = int(m.group(1))
    n_km = int(m.group(2))
    out_name = f"dop_rgb_32_{e_km}_{n_km}_{resolution_m:g}m.tif"
    out_path = derived_dir / out_name

    if out_path.exists() and out_path.stat().st_size > 0:
        return True, human_mb(out_path.stat().st_size)

    derived_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        gdal_translate,
        "-tr", str(resolution_m), str(resolution_m),
        "-r", "average",
        "-co", f"COMPRESS={compression}",
        "-co", "TILED=YES",
        "-co", "BIGTIFF=IF_SAFER",
        str(jp2_path),
        str(out_path),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600)
        return True, human_mb(out_path.stat().st_size)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore") if e.stderr else "Unknown GDAL error"
        print(f"  ✗ GDAL failed for {jp2_path.name}: {err}")
        return False, 0.0
    except subprocess.TimeoutExpired:
        print(f"  ✗ TIMEOUT for {jp2_path.name}")
        return False, 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rgb_tiles_dir", type=Path, required=True, help="Path to RGB_Tiles folder (ONLY this folder is touched)")
    ap.add_argument("--resolution", type=float, default=DEFAULT_RESOLUTION_M, help="Target meters/pixel (default 1.0)")
    ap.add_argument("--compression", type=str, default=DEFAULT_COMPRESSION, choices=["DEFLATE", "LZW", "JPEG", "NONE"])
    ap.add_argument("--max_mb", type=float, default=12.0, help="Warn if a derived tile is bigger than this MB")
    ap.add_argument("--skip_conversion", action="store_true", help="Only organize, do not convert")
    ap.add_argument("--move_leftovers_to_old", action="store_true",
                    help="Move any unexpected leftovers from RGB_Tiles root into raw/_old/ (keeps root clean)")
    ap.add_argument("--gdal_translate", type=str, default=None, help="Explicit path to gdal_translate.exe")
    ap.add_argument("--gdal_bin", type=str, default=None, help="Folder containing gdal_translate.exe (e.g. QGIS ...\\bin)")

    args = ap.parse_args()

    rgb_dir = args.rgb_tiles_dir.resolve()

    print("=" * 72)
    print("Prepare RGB_Tiles (ONLY this folder is touched)")
    print("=" * 72)
    print(f"RGB_Tiles:   {rgb_dir}")
    print(f"Resolution:  {args.resolution} m/pixel")
    print(f"Compression: {args.compression}")
    print(f"Clean root:  {bool(args.move_leftovers_to_old)} (move leftovers -> raw/_old/)")
    print()

    if not rgb_dir.exists():
        print(f"ERROR: folder not found: {rgb_dir}")
        return 1

    # Step 1: organize
    jp2_moved, j2w_moved, csv_kept, leftovers_moved = organize_rgb_tiles(rgb_dir, args.move_leftovers_to_old)

    print("Organization result:")
    print(f"  JP2 moved to raw/:        {jp2_moved}")
    print(f"  J2W moved to raw/dop_j2w/:{j2w_moved}")
    print(f"  CSV kept in root:         {csv_kept}  (dop_nw.csv expected)")
    if args.move_leftovers_to_old:
        print(f"  Leftovers moved to _old/: {leftovers_moved}")
    print()

    raw_dir = rgb_dir / "raw"
    derived_dir = rgb_dir / "derived"

    if args.skip_conversion:
        raw_mb = human_mb(folder_size_bytes(raw_dir))
        der_mb = human_mb(folder_size_bytes(derived_dir))
        print("Conversion skipped (--skip_conversion).")
        print(f"raw/ size:     {raw_mb:.2f} MB")
        print(f"derived/ size: {der_mb:.2f} MB")
        print("✓ Done.")
        return 0

    # Step 2: conversion
    gdal_translate = find_gdal_translate(args.gdal_translate, args.gdal_bin)
    if not gdal_translate:
        print("GDAL not found -> conversion skipped.")
        print("Provide one of:")
        print('  --gdal_translate "C:\\Program Files\\QGIS ...\\bin\\gdal_translate.exe"')
        print('  --gdal_bin "C:\\Program Files\\QGIS ...\\bin"')
        print("Or install QGIS/OSGeo4W so gdal_translate is on PATH.")
        return 0

    print(f"Using GDAL: {gdal_translate}")
    print()

    jp2_files = list((raw_dir).glob("*.jp2"))
    if not jp2_files:
        print("No JP2 files found in raw/. Nothing to convert.")
        return 0

    derived_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    total_mb = 0.0
    too_big: List[str] = []

    print(f"Converting {len(jp2_files)} JP2 -> derived/ GeoTIFF ...")
    for jp2 in jp2_files:
        success, size_mb = convert_one(jp2, derived_dir, args.resolution, args.compression, gdal_translate)
        if success:
            ok += 1
            total_mb += size_mb
            if size_mb > args.max_mb:
                too_big.append(f"{jp2.name} -> {size_mb:.2f} MB")

    print()
    print("=" * 72)
    print("Summary")
    print("=" * 72)
    print(f"Converted tiles: {ok}/{len(jp2_files)}")
    print(f"derived/ total:  {total_mb:.2f} MB")
    if too_big:
        print()
        print("Warning: Some derived tiles exceed max_mb:")
        for s in too_big:
            print(f"  - {s}")

    print()
    print("Output structure:")
    print(f"  {rgb_dir}\\")
    print("    dop_nw.csv              (kept in root)")
    print("    raw\\                    (pipeline IGNORES)")
    print("    derived\\                (pipeline USES)")
    print()
    print("✓ Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
