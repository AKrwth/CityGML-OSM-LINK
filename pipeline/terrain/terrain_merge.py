"""
Terrain raster merge via GDAL CLI (gdalbuildvrt + gdal_translate).

Merges DEM and RGB tile rasters into single GeoTIFF files in output/_Merged/.
Uses GDAL CLI (subprocess) for deterministic, platform-independent merging.

Imports: None (subprocess only, no GDAL Python bindings needed).
"""

import os
import glob
import subprocess
import shutil
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

# Use standard Python logging
log = logging.getLogger(__name__)


def find_gdal_exe() -> Optional[Dict[str, str]]:
    """
    Find GDAL CLI tools (gdalbuildvrt.exe, gdal_translate.exe).
    
    Prefers: tools in PATH
    Fallback: common Windows paths (OSGeo4W, QGIS)
    
    Returns:
        dict with keys 'buildvrt', 'translate' (full paths), or None if not found
    """
    # Try PATH first
    buildvrt = shutil.which("gdalbuildvrt")
    translate = shutil.which("gdal_translate")
    
    if buildvrt and translate:
        log.info(f"Found GDAL in PATH: buildvrt={buildvrt}, translate={translate}")
        return {"buildvrt": buildvrt, "translate": translate}
    
    # Fallback: common Windows paths
    fallback_paths = [
        r"C:\Program Files\OSGeo4W\bin",
        r"C:\Program Files (x86)\OSGeo4W\bin",
        r"C:\Users\Akhai\AppData\Local\Programs\OSGeo4W\bin",
        r"C:\Program Files\QGIS 3.36\bin",
        r"C:\Program Files\QGIS\bin",
    ]
    
    for base_path in fallback_paths:
        bv = os.path.join(base_path, "gdalbuildvrt.exe")
        tr = os.path.join(base_path, "gdal_translate.exe")
        if os.path.exists(bv) and os.path.exists(tr):
            log.info(f"Found GDAL in fallback path: {base_path}")
            return {"buildvrt": bv, "translate": tr}
    
    log.error(
        "GDAL CLI tools not found. Install QGIS or OSGeo4W and add to PATH, "
        "or set OSGeo4W root in M1DC settings."
    )
    return None


def ensure_merged_dir(output_dir: str) -> str:
    """
    Create output_dir/_Merged if it doesn't exist.
    
    Args:
        output_dir: Base output directory
    
    Returns:
        Path to _Merged subdirectory
    """
    merged_dir = os.path.join(output_dir, "_Merged")
    os.makedirs(merged_dir, exist_ok=True)
    log.info(f"Merged output directory: {merged_dir}")
    return merged_dir


def merge_rasters_to_geotiff(
    tile_glob: str,
    out_tif: str,
    tmp_vrt: str,
    gdal_exe: Dict[str, str],
    tile_type: str = "DEM",
) -> bool:
    """
    Merge raster tiles to single GeoTIFF using GDAL CLI.
    
    Steps:
    1. Discover tiles matching glob
    2. Build VRT with gdalbuildvrt
    3. Translate to GeoTIFF with gdal_translate (DEFLATE compression, tiled)
    
    Args:
        tile_glob: glob pattern to find tiles (e.g., "*.tif" in DGM folder)
        out_tif: output GeoTIFF path
        tmp_vrt: temporary VRT file path
        gdal_exe: dict with 'buildvrt' and 'translate' paths
        tile_type: name for logging ("DEM", "RGB", etc.)
    
    Returns:
        True if successful, False otherwise
    
    Raises:
        RuntimeError: if GDAL CLI fails
    """
    # Find tiles
    tiles = glob.glob(tile_glob)
    if not tiles:
        log.warning(f"No {tile_type} tiles found matching: {tile_glob}")
        return False
    
    log.info(f"Found {len(tiles)} {tile_type} tiles. Merging...")
    for t in tiles[:3]:
        log.info(f"  Sample: {os.path.basename(t)}")
    if len(tiles) > 3:
        log.info(f"  ... and {len(tiles) - 3} more")
    
    try:
        # Step 1: Build VRT
        buildvrt_cmd = [gdal_exe["buildvrt"], "-overwrite", tmp_vrt] + tiles
        log.info(f"Running: gdalbuildvrt -overwrite {tmp_vrt} <{len(tiles)} tiles>")
        subprocess.run(buildvrt_cmd, check=True, capture_output=True, text=True)
        log.info(f"VRT created: {tmp_vrt}")
        
        # Step 2: Translate to GeoTIFF
        translate_cmd = [
            gdal_exe["translate"],
            "-of", "GTiff",
            "-co", "COMPRESS=DEFLATE",
            "-co", "TILED=YES",
            tmp_vrt,
            out_tif,
        ]
        log.info(f"Running: gdal_translate -of GTiff -co COMPRESS=DEFLATE -co TILED=YES {tmp_vrt} {out_tif}")
        subprocess.run(translate_cmd, check=True, capture_output=True, text=True)
        log.info(f"GeoTIFF created: {out_tif}")
        
        # Cleanup VRT
        if os.path.exists(tmp_vrt):
            os.remove(tmp_vrt)
        
        return True
    
    except subprocess.CalledProcessError as e:
        log.error(f"GDAL merge failed for {tile_type}:")
        log.error(f"  Command: {' '.join(e.cmd)}")
        log.error(f"  stdout: {e.stdout}")
        log.error(f"  stderr: {e.stderr}")
        raise RuntimeError(f"GDAL merge failed for {tile_type}: {e.stderr}") from e


def run_terrain_merge(
    dgm_dir: str,
    rgb_dir: str,
    output_dir: str,
    force: bool = False,
) -> Dict[str, str]:
    """
    Orchestrate terrain merge: DEM + RGB tiles → _Merged/dem_merged.tif + rgb_merged.tif
    
    Workflow:
    1. Validate inputs (DGM, RGB folders exist)
    2. Create output_dir/_Merged
    3. If merged outputs exist and force=False: skip, return paths (cache reuse)
    4. Otherwise: run merge for both DEM and RGB
    
    Args:
        dgm_dir: folder with DEM/DGM GeoTIFF tiles
        rgb_dir: folder with RGB/DTK GeoTIFF tiles
        output_dir: base output folder (merged outputs → output_dir/_Merged)
        force: if True, re-merge even if outputs exist
    
    Returns:
        dict with keys:
        - merged_dir: path to _Merged folder
        - dem_merged_tif: path to dem_merged.tif
        - rgb_merged_tif: path to rgb_merged.tif
        - dem_vrt: path to DEM VRT (temporary, may be deleted)
        - rgb_vrt: path to RGB VRT (temporary, may be deleted)
        - dem_found: int count of DEM tiles
        - rgb_found: int count of RGB tiles
        - status: "MERGED" or "REUSED"
    
    Raises:
        ValueError: if inputs invalid or no tiles found
        RuntimeError: if GDAL merge fails
    """
    log.info("=== Terrain Merge Start ===")
    log.info(f"DGM source: {dgm_dir}")
    log.info(f"RGB source: {rgb_dir}")
    log.info(f"Output base: {output_dir}")
    
    # Validate inputs
    if not os.path.isdir(dgm_dir):
        raise ValueError(f"DGM directory not found: {dgm_dir}")
    if not os.path.isdir(rgb_dir):
        raise ValueError(f"RGB directory not found: {rgb_dir}")
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    
    # Ensure merged dir exists
    merged_dir = ensure_merged_dir(output_dir)
    
    dem_merged_tif = os.path.join(merged_dir, "dem_merged.tif")
    rgb_merged_tif = os.path.join(merged_dir, "rgb_merged.tif")
    dem_vrt = os.path.join(merged_dir, "_dem_temp.vrt")
    rgb_vrt = os.path.join(merged_dir, "_rgb_temp.vrt")
    
    # Check if already merged (cache reuse)
    if not force and os.path.exists(dem_merged_tif) and os.path.exists(rgb_merged_tif):
        log.info(f"Merged outputs already exist. Reusing (force=False).")
        log.info(f"  DEM: {dem_merged_tif}")
        log.info(f"  RGB: {rgb_merged_tif}")
        dem_count = len(glob.glob(os.path.join(dgm_dir, "*.tif")))
        rgb_count = len(glob.glob(os.path.join(rgb_dir, "*.tif")))
        return {
            "merged_dir": merged_dir,
            "dem_merged_tif": dem_merged_tif,
            "rgb_merged_tif": rgb_merged_tif,
            "dem_vrt": dem_vrt,
            "rgb_vrt": rgb_vrt,
            "dem_found": dem_count,
            "rgb_found": rgb_count,
            "status": "REUSED",
        }
    
    # Find GDAL tools
    gdal_exe = find_gdal_exe()
    if not gdal_exe:
        raise RuntimeError(
            "GDAL CLI tools not found. Install QGIS or OSGeo4W with GDAL support."
        )
    
    # Perform merge
    try:
        dem_glob = os.path.join(dgm_dir, "*.tif")
        rgb_glob = os.path.join(rgb_dir, "*.tif")
        
        dem_ok = merge_rasters_to_geotiff(dem_glob, dem_merged_tif, dem_vrt, gdal_exe, "DEM")
        rgb_ok = merge_rasters_to_geotiff(rgb_glob, rgb_merged_tif, rgb_vrt, gdal_exe, "RGB")
        
        if not (dem_ok and rgb_ok):
            raise RuntimeError("Merge completed but some outputs missing.")
        
        dem_count = len(glob.glob(dem_glob))
        rgb_count = len(glob.glob(rgb_glob))
        
        log.info(f"=== Terrain Merge Complete ===")
        log.info(f"DEM output: {dem_merged_tif} ({dem_count} tiles)")
        log.info(f"RGB output: {rgb_merged_tif} ({rgb_count} tiles)")
        
        return {
            "merged_dir": merged_dir,
            "dem_merged_tif": dem_merged_tif,
            "rgb_merged_tif": rgb_merged_tif,
            "dem_vrt": dem_vrt,
            "rgb_vrt": rgb_vrt,
            "dem_found": dem_count,
            "rgb_found": rgb_count,
            "status": "MERGED",
        }
    
    except Exception as e:
        log.error(f"Terrain merge failed: {e}")
        raise


def read_world_bounds_from_geotiff(path_tif: str) -> Optional[Tuple[float, float, float, float]]:
    """
    Read world bounds (min_e, min_n, max_e, max_n) from a GeoTIFF.

    Uses rasterio if available; otherwise falls back to GDAL.
    Returns None if neither is available or file unreadable.
    """
    if not path_tif or not os.path.isfile(path_tif):
        return None

    # Try rasterio first
    try:
        import rasterio  # type: ignore

        with rasterio.open(path_tif) as ds:
            b = ds.bounds  # left, bottom, right, top
            min_e = float(min(b.left, b.right))
            max_e = float(max(b.left, b.right))
            min_n = float(min(b.bottom, b.top))
            max_n = float(max(b.bottom, b.top))
            return (min_e, min_n, max_e, max_n)
    except Exception:
        pass

    # Fallback: GDAL
    try:
        from osgeo import gdal  # type: ignore

        ds = gdal.Open(path_tif)
        if ds is None:
            return None
        gt = ds.GetGeoTransform()
        width = ds.RasterXSize
        height = ds.RasterYSize

        xmin = gt[0]
        ymax = gt[3]
        xmax = gt[0] + width * gt[1]
        ymin = gt[3] + height * gt[5]

        min_e = float(min(xmin, xmax))
        max_e = float(max(xmin, xmax))
        min_n = float(min(ymin, ymax))
        max_n = float(max(ymin, ymax))
        return (min_e, min_n, max_e, max_n)
    except Exception:
        return None
