#!/usr/bin/env python3
"""
prepare_rgb_tiles_only.py — Prepare NRW DOP RGB_Tiles for Blender usage (RGB_Tiles-only)

⚠️  SAFETY RULE: This script ONLY touches the folder you pass via --rgb_tiles_dir.
It will NOT traverse outside of that directory.

Goal end-state inside RGB_Tiles/:
  RGB_Tiles/
    dop_nw.csv               (kept in root; any *.csv kept in root)
    derived/                 (small GeoTIFFs used by pipeline)
      dop_rgb_32_<E>_<N>_1m.tif   (optional conversion result)
    raw/                     (everything heavy/irrelevant for pipeline)
      *.jp2
      dop_j2w/
        *.j2w

What it does:
1) Create raw/, raw/dop_j2w/, derived/ (if missing)
2) Move all *.jp2 from RGB_Tiles root -> raw/
3) Move dop_j2w/ folder -> raw/dop_j2w/  (merge-safe; preserves files)
4) Keep all *.csv in RGB_Tiles root (moves any nested CSVs up to root)
5) Optional: Convert JP2 -> GeoTIFF into derived/ at target resolution using gdal_translate
   - If GDAL missing: organization still runs; conversion is skipped

Usage (PowerShell):
  cd "...\Test_Set\Terrain\RGB_Tiles"
  python prepare_rgb_tiles_only.py --rgb_tiles_dir . --resolution 1.0

Only organize (no conversion):
  python prepare_rgb_tiles_only.py --rgb_tiles_dir . --skip_conversion
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

JP2_PATTERN = re.compile(r"^dop10rgbi_32_(\d+)_(\d+)_1_.*\.jp2$", re.IGNORECASE)

DEFAULT_RESOLUTION_M = 1.0
DEFAULT_COMPRESSION = "DEFLATE"
DEFAULT_MAX_MB = 12.0


def find_gdal_translate() -> Optional[str]:
    exe = shutil.which("gdal_translate")
    if exe:
        return exe

    if sys.platform == "win32":
        fallback_paths = [
            r"C:\Program Files\OSGeo4W\bin\gdal_translate.exe",
            r"C:\Program Files (x86)\OSGeo4W\bin\gdal_translate.exe",
            r"C:\OSGeo4W64\bin\gdal_translate.exe",
            r"C:\Program Files\QGIS\bin\gdal_translate.exe",
            r"C:\Program Files\QGIS 3.34.0\bin\gdal_translate.exe",
            r"C:\Program Files\QGIS 3.36.0\bin\gdal_translate.exe",
        ]
        for p in fallback_paths:
            if Path(p).exists():
                return p
    return None


def safe_move(src: Path, dst: Path) -> Path:
    """Move src -> dst; if dst exists, add __dupN suffix."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        stem = dst.stem
        suf = dst.suffix
        i = 1
        while True:
            cand = dst.with_name(f"{stem}__dup{i}{suf}")
            if not cand.exists():
                dst = cand
                break
            i += 1
    shutil.move(str(src), str(dst))
    return dst


def ensure_structure(rgb_tiles_dir: Path) -> Tuple[Path, Path, Path]:
    raw_dir = rgb_tiles_dir / "raw"
    j2w_dir = raw_dir / "dop_j2w"
    derived_dir = rgb_tiles_dir / "derived"
    raw_dir.mkdir(exist_ok=True)
    j2w_dir.mkdir(exist_ok=True)
    derived_dir.mkdir(exist_ok=True)
    return raw_dir, j2w_dir, derived_dir


def pull_nested_csvs_to_root(rgb_tiles_dir: Path) -> int:
    """Move any *.csv found in subfolders up into RGB_Tiles root."""
    count = 0
    for csv in rgb_tiles_dir.rglob("*.csv"):
        if csv.parent == rgb_tiles_dir:
            continue
        safe_move(csv, rgb_tiles_dir / csv.name)
        count += 1
    return count


def move_root_jp2_to_raw(rgb_tiles_dir: Path, raw_dir: Path) -> int:
    """Move *.jp2 files in RGB_Tiles root into raw/."""
    moved = 0
    for jp2 in rgb_tiles_dir.glob("*.jp2"):
        safe_move(jp2, raw_dir / jp2.name)
        moved += 1
    return moved


def move_dop_j2w_folder_into_raw(rgb_tiles_dir: Path, j2w_dir: Path) -> int:
    """
    Move contents of RGB_Tiles/dop_j2w -> RGB_Tiles/raw/dop_j2w
    If the folder exists, merge file-by-file safely.
    Returns number of j2w moved.
    """
    src_dir = rgb_tiles_dir / "dop_j2w"
    if not src_dir.exists() or not src_dir.is_dir():
        return 0

    moved = 0
    for f in src_dir.glob("*.j2w"):
        safe_move(f, j2w_dir / f.name)
        moved += 1

    # Remove empty src_dir if possible
    try:
        next(src_dir.iterdir())
    except StopIteration:
        src_dir.rmdir()

    return moved


def convert_one_jp2(
    jp2_path: Path,
    derived_dir: Path,
    resolution_m: float,
    compression: str,
    max_mb: float,
    gdal_translate: str,
) -> Tuple[bool, float, Optional[str]]:
    m = JP2_PATTERN.match(jp2_path.name)
    if not m:
        return False, 0.0, f"Skip (bad filename): {jp2_path.name}"

    e_km = int(m.group(1))
    n_km = int(m.group(2))
    out_path = derived_dir / f"dop_rgb_32_{e_km}_{n_km}_{int(resolution_m)}m.tif"

    if out_path.exists() and out_path.stat().st_size > 0:
        size_mb = out_path.stat().st_size / (1024 * 1024)
        warn = f"WARNING: {out_path.name} is {size_mb:.2f} MB > {max_mb} MB" if size_mb > max_mb else None
        return True, size_mb, warn

    comp_opt = f"COMPRESS={compression}" if compression != "NONE" else "COMPRESS=NONE"
    cmd = [
        gdal_translate,
        "-tr", str(resolution_m), str(resolution_m),
        "-r", "average",
        "-co", comp_opt,
        "-co", "TILED=YES",
        "-co", "BIGTIFF=IF_SAFER",
        str(jp2_path),
        str(out_path),
    ]

    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=600,
        )
        size_mb = out_path.stat().st_size / (1024 * 1024)
        warn = f"WARNING: {out_path.name} is {size_mb:.2f} MB > {max_mb} MB" if size_mb > max_mb else None
        return True, size_mb, warn
    except subprocess.CalledProcessError as e:
        msg = e.stderr.decode(errors="ignore") if e.stderr else "Unknown GDAL error"
        return False, 0.0, f"FAILED {jp2_path.name}: {msg}"
    except subprocess.TimeoutExpired:
        return False, 0.0, f"TIMEOUT {jp2_path.name}"


def folder_size_mb(p: Path) -> float:
    s = 0
    for f in p.rglob("*"):
        if f.is_file():
            s += f.stat().st_size
    return s / (1024 * 1024)


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare NRW DOP RGB_Tiles (RGB_Tiles-only).")
    ap.add_argument("--rgb_tiles_dir", type=Path, required=True, help="Path to RGB_Tiles folder ONLY.")
    ap.add_argument("--resolution", type=float, default=DEFAULT_RESOLUTION_M, help="Target meters/pixel (default 1.0).")
    ap.add_argument("--compression", type=str, default=DEFAULT_COMPRESSION,
                    choices=["DEFLATE", "JPEG", "LZW", "NONE"], help="GeoTIFF compression.")
    ap.add_argument("--max_mb", type=float, default=DEFAULT_MAX_MB, help="Warn if derived tile exceeds this size.")
    ap.add_argument("--skip_conversion", action="store_true", help="Only organize, do not convert.")
    args = ap.parse_args()

    rgb_tiles_dir = args.rgb_tiles_dir.resolve()

    print("=" * 72)
    print("Prepare RGB_Tiles (ONLY this folder is touched)")
    print("=" * 72)
    print(f"RGB_Tiles:    {rgb_tiles_dir}")
    print(f"Resolution:   {args.resolution} m/pixel")
    print(f"Compression:  {args.compression}")
    print(f"Warn > size:  {args.max_mb} MB")
    print()

    if not rgb_tiles_dir.exists() or not rgb_tiles_dir.is_dir():
        print(f"ERROR: RGB_Tiles dir not found: {rgb_tiles_dir}")
        return 1

    # Ensure structure
    raw_dir, j2w_dir, derived_dir = ensure_structure(rgb_tiles_dir)

    # CSVs: keep in root; pull nested up
    csv_pulled = pull_nested_csvs_to_root(rgb_tiles_dir)

    # Move JP2 from root to raw
    jp2_moved = move_root_jp2_to_raw(rgb_tiles_dir, raw_dir)

    # Move dop_j2w/* into raw/dop_j2w
    j2w_moved = move_dop_j2w_folder_into_raw(rgb_tiles_dir, j2w_dir)

    print("Organization result:")
    print(f"  CSV pulled to root:   {csv_pulled}")
    print(f"  JP2 moved to raw/:    {jp2_moved}")
    print(f"  J2W moved to raw/:    {j2w_moved}")
    print()

    if args.skip_conversion:
        print("Conversion skipped (--skip_conversion).")
        print("✓ Done. Root now should contain: *.csv + raw/ + derived/")
        return 0

    gdal_translate = find_gdal_translate()
    if not gdal_translate:
        print("GDAL not found -> conversion skipped.")
        print("✓ Done. Install QGIS/OSGeo4W to enable JP2 -> GeoTIFF conversion.")
        return 0

    jp2_files = sorted(raw_dir.glob("*.jp2"))
    if not jp2_files:
        print("No JP2 files found in raw/ -> nothing to convert.")
        return 0

    print(f"Converting {len(jp2_files)} JP2 -> GeoTIFF using: {gdal_translate}")
    ok = 0
    fail = 0
    warns = 0
    total_mb = 0.0

    for i, jp2 in enumerate(jp2_files, 1):
        print(f"[{i:03d}/{len(jp2_files)}] {jp2.name} ... ", end="", flush=True)
        success, size_mb, warn = convert_one_jp2(
            jp2_path=jp2,
            derived_dir=derived_dir,
            resolution_m=args.resolution,
            compression=args.compression,
            max_mb=args.max_mb,
            gdal_translate=gdal_translate,
        )
        if success:
            ok += 1
            total_mb += size_mb
            print(f"✓ {size_mb:.2f} MB")
            if warn:
                warns += 1
                print(f"    {warn}")
        else:
            fail += 1
            print("✗")
            if warn:
                print(f"    {warn}")

    print()
    print("=" * 72)
    print("Summary")
    print("=" * 72)
    print(f"Converted:     {ok} ok, {fail} failed, {warns} size warnings")
    print(f"derived/ size: {folder_size_mb(derived_dir):.2f} MB")
    print(f"raw/ size:     {folder_size_mb(raw_dir):.2f} MB")
    print()
    print("Final RGB_Tiles root should contain ONLY:")
    print("  - *.csv")
    print("  - derived/")
    print("  - raw/")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
