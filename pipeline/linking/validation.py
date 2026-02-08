"""
M1_DC_V6 Geometry Validation & Diagnostics
Checks for common import issues: scale, BBox, CRS units, rotation problems.
"""

from math import sqrt
from typing import Tuple, Dict, Any
try:
    import bpy
except ImportError:
    pass

from ...utils.logging_system import log_info, log_warn, log_error


def get_bbox_dimensions(obj: "bpy.types.Object") -> Tuple[float, float, float]:
    """
    Get bounding box dimensions in local coordinates (X, Y, Z).
    Returns (width_x, depth_y, height_z) in Blender units.
    """
    if not obj or not hasattr(obj, "bound_box"):
        return 0.0, 0.0, 0.0
    
    bbox = obj.bound_box
    if not bbox:
        return 0.0, 0.0, 0.0
    
    xs = [v[0] for v in bbox]
    ys = [v[1] for v in bbox]
    zs = [v[2] for v in bbox]
    
    width_x = max(xs) - min(xs)
    depth_y = max(ys) - min(ys)
    height_z = max(zs) - min(zs)
    
    return width_x, depth_y, height_z


def check_tile_plausibility(
    obj: "bpy.types.Object",
    expected_tile_size_m: float,
    tolerance_pct: float = 20.0,
) -> Dict[str, Any]:
    """
    Check if imported tile is plausible given expected tile size.
    
    Args:
        obj: Blender object (tile mesh)
        expected_tile_size_m: Expected tile size in meters (e.g. 1000, 2000)
        tolerance_pct: Tolerance in percent (default 20%)
    
    Returns:
        dict with keys:
        - 'ok': bool (plausible or not)
        - 'bbox_xy': (width, depth) in local coords
        - 'scale': (sx, sy, sz)
        - 'aspect': max_dim / min_dim
        - 'issue': str or None (description of problem)
        - 'suggestion': str or None (what to check/fix)
    """
    
    result = {
        'ok': False,
        'bbox_xy': (0.0, 0.0),
        'scale': (1.0, 1.0, 1.0),
        'aspect': 1.0,
        'issue': None,
        'suggestion': None,
    }
    
    if not obj:
        result['issue'] = "Object is None"
        return result
    
    # Get dimensions
    width_x, depth_y, height_z = get_bbox_dimensions(obj)
    result['bbox_xy'] = (width_x, depth_y)
    
    # Get scale
    if hasattr(obj, 'scale'):
        result['scale'] = tuple(obj.scale)
    
    # Check scale ≠ 1
    sx, sy, sz = result['scale']
    if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01 or abs(sz - 1.0) > 0.01:
        result['issue'] = f"Object scale not (1,1,1): ({sx:.2f}, {sy:.2f}, {sz:.2f})"
        result['suggestion'] = "Apply scale via Ctrl+A > Apply Scale in Blender"
        return result
    
    # Check XY dimensions plausible
    xy_dims = [width_x, depth_y]
    xy_aspect = max(xy_dims) / min(xy_dims) if min(xy_dims) > 0 else 1.0
    result['aspect'] = xy_aspect
    
    # Aspect should be close to 1 (square-ish tile)
    if xy_aspect > 2.0:
        result['issue'] = f"Unusual XY aspect ratio: {xy_aspect:.1f}:1 (width={width_x:.1f}, depth={depth_y:.1f})"
        result['suggestion'] = "Check if tile is rotated or not square (might be Y-up vs Z-up confusion)"
        return result
    
    # Check if dimensions match expected tile size (within tolerance)
    avg_xy = (width_x + depth_y) / 2.0
    tolerance_abs = expected_tile_size_m * (tolerance_pct / 100.0)
    lower = expected_tile_size_m - tolerance_abs
    upper = expected_tile_size_m + tolerance_abs
    
    if avg_xy < lower * 0.1:  # 10x too small
        result['issue'] = f"Tile XY size suspiciously small: avg={avg_xy:.1f}m (expected ~{expected_tile_size_m:.1f}m)"
        result['suggestion'] = "Check for scale/unit conversion: OBJ might be in different units (cm, inches, etc.)"
        return result
    
    if avg_xy > upper * 10.0:  # 10x too large
        result['issue'] = f"Tile XY size suspiciously large: avg={avg_xy:.1f}m (expected ~{expected_tile_size_m:.1f}m)"
        result['suggestion'] = "Check if multiplier (e_mult/n_mult) is wrong or tile_size_m miscalculated"
        return result
    
    if not (lower <= avg_xy <= upper):
        result['issue'] = f"Tile XY size mismatch: avg={avg_xy:.1f}m (expected {expected_tile_size_m:.1f}±{tolerance_abs:.1f}m)"
        result['suggestion'] = "Check CRS units (meters vs degrees?), scale factor, or tile_size_m calculation"
        return result
    
    result['ok'] = True
    return result


def log_tile_import_summary(
    tile_name: str,
    obj: "bpy.types.Object",
    expected_tile_size_m: float,
    expected_local_pos: Tuple[float, float] = None,
) -> None:
    """
    Log summary of tile import for diagnostics.
    
    Args:
        tile_name: Name of tile (e.g. 'Aachen_tile_001')
        obj: Imported Blender object
        expected_tile_size_m: Expected size in meters
        expected_local_pos: Expected local position (x, y) or None
    """
    
    check = check_tile_plausibility(obj, expected_tile_size_m)
    
    width_x, depth_y = check['bbox_xy']
    sx, sy, sz = check['scale']
    aspect = check['aspect']
    
    log_info(f"[Import] Tile: {tile_name}")
    log_info(f"  BBox XY: {width_x:.1f} × {depth_y:.1f} m (aspect {aspect:.2f}:1)")
    log_info(f"  Scale: ({sx:.2f}, {sy:.2f}, {sz:.2f})")
    
    if hasattr(obj, 'location'):
        log_info(f"  Location: ({obj.location.x:.1f}, {obj.location.y:.1f}, {obj.location.z:.1f})")
    
    if expected_local_pos:
        expected_x, expected_y = expected_local_pos
        log_info(f"  Expected pos: ({expected_x:.1f}, {expected_y:.1f})")
    
    if check['issue']:
        log_warn(f"  ⚠ ISSUE: {check['issue']}")
        if check['suggestion']:
            log_warn(f"  💡 Suggestion: {check['suggestion']}")
    else:
        log_info(f"  ✓ Plausibility check OK")


def is_likely_wrong_units(bbox_size_m: float) -> Tuple[bool, str]:
    """
    Heuristic check if object is in wrong units (e.g. degrees vs meters).
    
    Assumes:
    - Normal tile size: 500 – 10000 m
    - Degree tiles: 0.01 – 1.0 degrees
    
    Returns:
        (is_wrong, description)
    """
    if bbox_size_m < 0.01:
        return True, "Size < 0.01m (maybe mm/cm scale?)"
    if bbox_size_m < 1.0:
        return True, "Size < 1m (suspicious; check units)"
    if 0.01 < bbox_size_m < 1.0:
        return True, "Size 0.01–1m; might be degree-based CRS (EPSG:4326) by mistake"
    if bbox_size_m > 100000.0:
        return True, "Size > 100km (very large; check multiplier)"
    
    return False, "Units look OK"


def check_crs_units(crs_code: str) -> Tuple[bool, str]:
    """
    Check if CRS is meter-based.
    
    Returns:
        (is_meter_based, description)
    """
    
    # Known meter-based CRS codes
    meter_crses = {
        "EPSG:25832",  # ETRS89 / UTM 32N
        "EPSG:25833",  # ETRS89 / UTM 33N
        "EPSG:31256",  # MGI / Austria GK M28
        "EPSG:31257",  # MGI / Austria GK M31
        "EPSG:31258",  # MGI / Austria GK M34
        "EPSG:2154",   # Lambert 93 (France)
        "EPSG:32632",  # WGS 84 / UTM zone 32N
    }
    
    # Known degree-based CRS codes
    degree_crses = {
        "EPSG:4326",   # WGS 84 (Lat/Lon)
        "EPSG:4258",   # ETRS89 (Lat/Lon)
    }
    
    if crs_code in meter_crses:
        return True, f"{crs_code} is meter-based ✓"
    
    if crs_code in degree_crses:
        return False, f"{crs_code} is DEGREE-based (Lat/Lon) ✗ – expect units mismatch"
    
    # Heuristic: if it starts with EPSG and ends with a number 25000+, likely UTM (meters)
    if crs_code.startswith("EPSG:") and int(crs_code.split(":")[1]) >= 25000:
        return True, f"{crs_code} (likely UTM = meters)"
    
    return None, f"{crs_code} (unknown; assume meters if local coords)"
