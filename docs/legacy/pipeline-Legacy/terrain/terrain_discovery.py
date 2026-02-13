"""
Terrain Source Discovery Module

Discovers GeoTIFF rasters and metadata from a single terrain_source_dir.
Recursively searches for:
  - geotiff/ subfolder with *.tif files
  - dem_tiles/ subfolder with *.tif files  
  - Metadata CSV files (*.csv) at any level

Returns discovered paths without failing if subfolders are missing.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ...utils.logging_system import log_info, log_warn, log_error


def discover_terrain_sources(terrain_source_dir: str) -> Dict[str, object]:
    """
    Discover terrain raster files and metadata from terrain_source_dir.
    
    Args:
        terrain_source_dir: Root folder to search recursively
    
    Returns:
        Dictionary with keys:
        - 'geotiff_dir': Path to geotiff/ subfolder (or None if not found)
        - 'dem_tiles_dir': Path to dem_tiles/ subfolder (or None if not found)
        - 'metadata_csvs': List of discovered metadata CSV files
        - 'tif_files': List of discovered .tif/.tiff files (full paths)
        - 'status': Human-readable discovery summary
        - 'has_rasters': bool (True if any rasters found)
    """
    
    result = {
        'geotiff_dir': None,
        'dem_tiles_dir': None,
        'metadata_csvs': [],
        'tif_files': [],
        'status': 'not_checked',
        'has_rasters': False,
    }
    
    if not terrain_source_dir:
        result['status'] = 'missing'
        return result
    
    source_path = Path(terrain_source_dir)
    if not source_path.exists():
        result['status'] = 'missing'
        return result
    
    if not source_path.is_dir():
        result['status'] = 'not_a_directory'
        return result
    
    # Search for optional subfolders
    for item in source_path.iterdir():
        if not item.is_dir():
            continue
        
        name_lower = item.name.lower()
        if name_lower == 'geotiff':
            result['geotiff_dir'] = str(item)
        elif name_lower == 'dem_tiles':
            result['dem_tiles_dir'] = str(item)
    
    # Recursively collect all .tif/.tiff files
    tif_extensions = {'.tif', '.tiff'}
    for root, dirs, files in os.walk(terrain_source_dir):
        for filename in files:
            ext = Path(filename).suffix.lower()
            
            # Collect TIF files
            if ext in tif_extensions:
                full_path = os.path.join(root, filename)
                result['tif_files'].append(full_path)
            
            # Collect metadata CSVs
            if ext == '.csv':
                full_path = os.path.join(root, filename)
                result['metadata_csvs'].append(full_path)
    
    # Determine status
    result['has_rasters'] = len(result['tif_files']) > 0
    
    status_parts = []
    if result['geotiff_dir']:
        status_parts.append(f"geotiff_dir found")
    if result['dem_tiles_dir']:
        status_parts.append(f"dem_tiles_dir found")
    if result['tif_files']:
        status_parts.append(f"{len(result['tif_files'])} rasters")
    if result['metadata_csvs']:
        status_parts.append(f"{len(result['metadata_csvs'])} metadata CSVs")
    
    if status_parts:
        result['status'] = '; '.join(status_parts)
    else:
        result['status'] = 'no rasters or metadata found'
    
    return result


def log_terrain_discovery(discovery_result: Dict[str, object], prefix: str = "[Terrain]") -> None:
    """Log discovery results in a user-friendly format."""
    status = discovery_result.get('status', 'unknown')
    has_rasters = discovery_result.get('has_rasters', False)
    
    log_info(f"{prefix} Discovery: {status}")
    
    geotiff = discovery_result.get('geotiff_dir')
    dem_tiles = discovery_result.get('dem_tiles_dir')
    
    if geotiff:
        log_info(f"{prefix} ✓ geotiff_dir: {geotiff}")
    else:
        log_info(f"{prefix} - geotiff_dir: not found")
    
    if dem_tiles:
        log_info(f"{prefix} ✓ dem_tiles_dir: {dem_tiles}")
    else:
        log_info(f"{prefix} - dem_tiles_dir: not found")
    
    csvs = discovery_result.get('metadata_csvs', [])
    if csvs:
        log_info(f"{prefix} ✓ Metadata CSVs: {len(csvs)} found")
        for csv_path in csvs[:3]:  # Show first 3
            log_info(f"{prefix}   - {Path(csv_path).name}")
        if len(csvs) > 3:
            log_info(f"{prefix}   ... and {len(csvs) - 3} more")
    
    tifs = discovery_result.get('tif_files', [])
    if tifs:
        log_info(f"{prefix} ✓ GeoTIFF rasters: {len(tifs)} found")
        for tif_path in tifs[:3]:  # Show first 3
            log_info(f"{prefix}   - {Path(tif_path).name}")
        if len(tifs) > 3:
            log_info(f"{prefix}   ... and {len(tifs) - 3} more")
    else:
        log_info(f"{prefix} - No raster files (.tif/.tiff) found")
