#!/usr/bin/env python3
"""
prepare_terrain_rgb_wcs.py - External Terrain RGB Preparation Tool

⚠️  NOT PART OF BLENDER ADD-ON PIPELINE ⚠️
This is a standalone preprocessing script. Run it BEFORE opening Blender.

Purpose:
--------
Downloads NRW DOP RGB orthophotos via WCS service, downsampled to 1.0m/pixel
(instead of native 0.1m/pixel). Creates lightweight GeoTIFF tiles suitable for
Blender UV textures.

Why separate from Blender pipeline:
- Native DOP tiles are huge (JP2, 0.1m/pixel, multi-GB total)
- For Blender textures, 1.0m/pixel is sufficient and massively smaller
- Keeps Blender pipeline fast and deterministic (consumes pre-prepared data)

Output structure:
-----------------
Terrain/
  DGM_Tiles/              (scanned to determine tile grid)
    dgm1_32_<E>_<N>_1_*.tif
  RGB_Tiles/
    derived/              (WCS outputs written here)
      dop_rgb_32_<E>_<N>_1m.tif
    rgb_manifest.json     (metadata for validation)

Recommended settings:
- RESOLUTION: 1.0 m/pixel → ~1000x1000 px per 1x1 km tile
- Typical file size: 300 KB - 2 MB per tile (depends on imagery complexity)
- Total for 20-30 tiles: usually ~10-20 MB (laptop-friendly)

Usage:
------
Windows PowerShell:
  cd path\\to\\Terrain
  python ..\\M1_DC_V6\\pipeline\\Data_Set_Tools\\prepare_terrain_rgb_wcs.py --terrain_dir .

Linux/Mac:
  python3 prepare_terrain_rgb_wcs.py --terrain_dir /path/to/Terrain

Options:
  --terrain_dir PATH    Path to Terrain root folder (required)
  --resolution METERS   Resolution in meters/pixel (default: 1.0)
  --skip_existing       Skip tiles that already exist in derived/

Dependencies:
  pip install requests

After running:
  Set terrain_root_dir in Blender to this Terrain folder and run pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple

import requests


# ----------------------------
# Configuration
# ----------------------------
CRS_EPSG = "EPSG:25832"
TILE_SIZE_M = 1000  # 1x1 km tiles
DEFAULT_RESOLUTION_M = 1.0  # 1.0m/pixel (recommended for Blender)

# NRW WCS endpoint (DOP RGB)
WCS_BASE_URL = "https://www.wcs.nrw.de/geobasis/wcs_nw_dop"
COVERAGE_ID = "nw_dop_rgb"  # RGB only (no infrared)


# ----------------------------
# Data structures
# ----------------------------
@dataclass(frozen=True)
class TileKM:
    """Represents a 1x1 km tile by its km coordinates."""
    e_km: int
    n_km: int


# ----------------------------
# DGM tile scanning (derive RGB tile list from DGM)
# ----------------------------
DGM_RE = re.compile(r"^dgm1_32_(\d+)_(\d+)_1_.*\.tif$", re.IGNORECASE)


def scan_dgm_tiles(dgm_dir: Path) -> List[TileKM]:
    """Scan DGM_Tiles/ folder and extract tile coordinates from filenames."""
    if not dgm_dir.exists():
        raise FileNotFoundError(f"DGM_Tiles folder not found: {dgm_dir.resolve()}")

    tiles: Set[TileKM] = set()
    for tif_file in dgm_dir.glob("*.tif"):
        m = DGM_RE.match(tif_file.name)
        if not m:
            continue
        e_km = int(m.group(1))
        n_km = int(m.group(2))
        tiles.add(TileKM(e_km, n_km))

    if not tiles:
        raise RuntimeError(
            f"No DGM tiles found in {dgm_dir.resolve()}\n"
            f"Expected filename pattern: dgm1_32_<E_KM>_<N_KM>_1_*.tif"
        )

    return sorted(tiles, key=lambda t: (t.e_km, t.n_km))


# ----------------------------
# WCS download
# ----------------------------
def build_wcs_url(e_km: int, n_km: int, resolution_m: float) -> str:
    """Build WCS GetCoverage request URL for one 1x1 km tile."""
    x_min = e_km * 1000
    x_max = x_min + TILE_SIZE_M
    y_min = n_km * 1000
    y_max = y_min + TILE_SIZE_M

    # Native DOP resolution is ~0.1m/pixel; we request server-side downsampling
    # Scale factor = requested_resolution / native_resolution
    scale_factor = resolution_m / 0.1

    # WCS 2.0.1 GetCoverage request
    url = (
        f"{WCS_BASE_URL}?"
        f"VERSION=2.0.1"
        f"&SERVICE=wcs"
        f"&REQUEST=GetCoverage"
        f"&COVERAGEID={COVERAGE_ID}"
        f"&FORMAT=image/tiff"
        f"&SUBSET=x({x_min},{x_max})"
        f"&SUBSET=y({y_min},{y_max})"
        f"&SCALEFACTOR={scale_factor}"
        f"&SUBSETTINGCRS={CRS_EPSG}"
        f"&OUTPUTCRS={CRS_EPSG}"
    )
    return url


def download_tile(
    tile: TileKM,
    out_dir: Path,
    resolution_m: float,
    session: requests.Session,
    skip_existing: bool = False,
) -> Tuple[bool, float, Path]:
    """
    Download one RGB tile via WCS.

    Returns:
        (success: bool, size_mb: float, filepath: Path)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dop_rgb_32_{tile.e_km}_{tile.n_km}_{int(resolution_m)}m.tif"

    # Skip if already exists
    if skip_existing and out_path.exists() and out_path.stat().st_size > 0:
        size_mb = out_path.stat().st_size / (1024 * 1024)
        return True, size_mb, out_path

    url = build_wcs_url(tile.e_km, tile.n_km, resolution_m)

    try:
        print(f"  Downloading {tile.e_km}_{tile.n_km}...", end=" ", flush=True)
        r = session.get(url, timeout=120)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        size_mb = len(r.content) / (1024 * 1024)
        print(f"✓ {size_mb:.2f} MB")
        return True, size_mb, out_path
    except requests.RequestException as e:
        print(f"✗ FAILED: {e}")
        return False, 0.0, out_path


# ----------------------------
# Manifest generation
# ----------------------------
def write_manifest(tiles: List[TileKM], out_dir: Path, resolution_m: float):
    """Write rgb_manifest.json with metadata for pipeline validation."""
    e_vals = sorted({t.e_km for t in tiles})
    n_vals = sorted({t.n_km for t in tiles})

    manifest = {
        "crs": CRS_EPSG,
        "tile_size_m": TILE_SIZE_M,
        "resolution_m_per_pixel": resolution_m,
        "expected_pixels_per_tile": int(TILE_SIZE_M / resolution_m),
        "coverage_id": COVERAGE_ID,
        "wcs_base_url": WCS_BASE_URL,
        "tile_grid": {
            "easting_km_min": e_vals[0],
            "easting_km_max": e_vals[-1],
            "northing_km_min": n_vals[0],
            "northing_km_max": n_vals[-1],
            "tile_count": len(tiles),
        },
        "note": (
            "Generated via WCS server-side downsampling. "
            "This dataset is optimized for Blender UV/basemap usage (lightweight), "
            "not for photogrammetry/measurement at native DOP resolution."
        ),
    }

    manifest_path = out_dir / "rgb_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n✓ Manifest written: {manifest_path.resolve()}")


# ----------------------------
# Main workflow
# ----------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download NRW DOP RGB tiles via WCS (external preprocessing tool)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--terrain_dir",
        type=Path,
        required=True,
        help="Path to Terrain root folder (must contain DGM_Tiles/)",
    )
    parser.add_argument(
        "--resolution",
        type=float,
        default=DEFAULT_RESOLUTION_M,
        help=f"Resolution in meters/pixel (default: {DEFAULT_RESOLUTION_M})",
    )
    parser.add_argument(
        "--skip_existing",
        action="store_true",
        help="Skip tiles that already exist in RGB_Tiles/derived/",
    )

    args = parser.parse_args()

    print("=" * 72)
    print("Prepare Terrain RGB via NRW WCS (External Preprocessing Tool)")
    print("=" * 72)
    print(f"Terrain root:  {args.terrain_dir.resolve()}")
    print(f"CRS:           {CRS_EPSG}")
    print(f"Tile size:     {TILE_SIZE_M} m")
    print(f"Resolution:    {args.resolution} m/pixel  (~{int(TILE_SIZE_M/args.resolution)}×{int(TILE_SIZE_M/args.resolution)} px/tile)")
    print()

    # Validate terrain root
    if not args.terrain_dir.exists():
        print(f"ERROR: Terrain root not found: {args.terrain_dir.resolve()}")
        return 1

    # Scan DGM tiles to determine tile grid
    dgm_dir = args.terrain_dir / "DGM_Tiles"
    try:
        tiles = scan_dgm_tiles(dgm_dir)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}")
        return 1

    print(f"Found {len(tiles)} DGM tiles")
    print(f"Tile range: E_km={min(t.e_km for t in tiles)}..{max(t.e_km for t in tiles)}, N_km={min(t.n_km for t in tiles)}..{max(t.n_km for t in tiles)}")
    print()

    # Ensure output directory exists
    rgb_derived_dir = args.terrain_dir / "RGB_Tiles" / "derived"
    rgb_derived_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {rgb_derived_dir.resolve()}")
    print()

    # Download tiles
    ok_count = 0
    fail_count = 0
    total_mb = 0.0

    print(f"Downloading {len(tiles)} RGB tiles...")
    print()

    with requests.Session() as session:
        for i, tile in enumerate(tiles, 1):
            print(f"[{i:03d}/{len(tiles)}]", end=" ")
            success, size_mb, _ = download_tile(
                tile,
                rgb_derived_dir,
                args.resolution,
                session,
                skip_existing=args.skip_existing,
            )
            if success:
                ok_count += 1
                total_mb += size_mb
            else:
                fail_count += 1

    # Write manifest
    write_manifest(tiles, rgb_derived_dir, args.resolution)

    # Summary
    print()
    print("=" * 72)
    print("Summary")
    print("=" * 72)
    print(f"Success:     {ok_count}")
    print(f"Failed:      {fail_count}")
    print(f"Total size:  {total_mb:.2f} MB")
    print()

    if fail_count > 0:
        print("⚠  Some tiles failed to download. Re-run with --skip_existing to retry.")
        print()
        return 2

    print("✓ All tiles downloaded successfully!")
    print()
    print("Next step:")
    print(f"  1. Open Blender")
    print(f"  2. Set 'Terrain (Prepared Dataset)' to: {args.terrain_dir.resolve()}")
    print(f"  3. Run pipeline")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
