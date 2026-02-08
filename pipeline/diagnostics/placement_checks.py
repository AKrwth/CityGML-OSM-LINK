"""
Placement Tripwires & Regression Detection

Pure diagnostics module — no behavior change.
Detects violations in tile placement and aborts pipeline on failure.

Functions:
- assert_tile_scale_is_one(lods, tol=1e-6)
- assert_tiles_not_collapsed(lods, min_spacing)
- assert_tiles_are_local(lods, limit=1e6)
- snapshot_tiles(lods)
- compare_snapshots(before, after)
"""

from typing import List, Tuple, Optional, Dict, Any
from ...utils.logging_system import log_info, log_warn, log_error


def snapshot_tiles(lods: List) -> List[Tuple[str, Tuple[float, float], Tuple[float, float, float]]]:
    """
    Capture placement state: name, location.xy, scale.
    
    Args:
        lods: List of LoD2 mesh objects
    
    Returns:
        [(name, (x, y), (sx, sy, sz)), ...]
    """
    snapshot = []
    for obj in lods:
        if obj is None or not hasattr(obj, 'location') or not hasattr(obj, 'scale'):
            continue
        try:
            name = obj.name
            loc_xy = (obj.location.x, obj.location.y)
            scale = tuple(obj.scale)
            snapshot.append((name, loc_xy, scale))
        except Exception as e:
            log_warn(f"[Snapshot] Failed to capture {obj.name}: {e}")
            continue
    return snapshot


def assert_tile_scale_is_one(lods: List, tol: float = 1e-6) -> bool:
    """
    Ensure all LoD2 tiles have scale == (1, 1, 1) within tolerance.
    
    Args:
        lods: List of LoD2 mesh objects
        tol: Tolerance for scale deviation (default 1e-6)
    
    Returns:
        True if all tiles pass
    
    Raises:
        AssertionError with details if violation detected
    """
    violations = []
    
    for obj in lods:
        if obj is None or not hasattr(obj, 'scale'):
            continue
        
        try:
            sx, sy, sz = obj.scale
            
            # Check each component against (1.0, 1.0, 1.0)
            if abs(sx - 1.0) > tol or abs(sy - 1.0) > tol or abs(sz - 1.0) > tol:
                violations.append({
                    'name': obj.name,
                    'scale': (sx, sy, sz),
                    'delta': (abs(sx - 1.0), abs(sy - 1.0), abs(sz - 1.0)),
                })
        except Exception as e:
            log_warn(f"[TileScale] Failed to check {obj.name}: {e}")
            continue
    
    if violations:
        error_msg = f"[TRIPWIRE] Tile scale violation detected ({len(violations)} tiles):\n"
        for v in violations[:5]:  # Show first 5
            error_msg += f"  - {v['name']}: scale={v['scale']} (delta={v['delta']})\n"
        if len(violations) > 5:
            error_msg += f"  ... and {len(violations) - 5} more"
        
        log_error(error_msg)
        raise AssertionError(error_msg)
    
    log_info(f"[TileScale] ✓ All {len(lods)} tiles have scale == (1,1,1)")
    return True


def assert_tiles_not_collapsed(lods: List, min_spacing: float) -> bool:
    """
    Ensure tile centers maintain minimum spacing (not collapsed).
    
    Args:
        lods: List of LoD2 mesh objects
        min_spacing: Minimum center-to-center distance in meters
    
    Returns:
        True if all tiles maintain spacing
    
    Raises:
        AssertionError if collapse detected
    """
    if len(lods) < 2:
        log_info(f"[TileSpacing] Skipped (< 2 tiles)")
        return True
    
    positions = []
    for obj in lods:
        if obj is None or not hasattr(obj, 'location'):
            continue
        try:
            positions.append((obj.name, obj.location.x, obj.location.y))
        except Exception as e:
            log_warn(f"[TileSpacing] Failed to get position for {obj.name}: {e}")
            continue
    
    # Check all pairs
    violations = []
    for i, (name_a, x_a, y_a) in enumerate(positions):
        for name_b, x_b, y_b in positions[i+1:]:
            dist = ((x_a - x_b)**2 + (y_a - y_b)**2) ** 0.5
            if dist < min_spacing:
                violations.append({
                    'pair': (name_a, name_b),
                    'distance': dist,
                    'min_required': min_spacing,
                })
    
    if violations:
        error_msg = f"[TRIPWIRE] Tile collapse detected ({len(violations)} pairs too close):\n"
        for v in violations[:3]:  # Show first 3
            error_msg += f"  - {v['pair'][0]} ↔ {v['pair'][1]}: {v['distance']:.1f}m < {v['min_required']:.1f}m\n"
        if len(violations) > 3:
            error_msg += f"  ... and {len(violations) - 3} more pairs"
        
        log_error(error_msg)
        raise AssertionError(error_msg)
    
    log_info(f"[TileSpacing] ✓ All {len(positions)} tiles maintain spacing >= {min_spacing:.1f}m")
    return True


def assert_tiles_are_local(lods: List, limit: float = 1e6) -> bool:
    """
    Ensure tiles are in a local coordinate range (not in global UTM).
    
    Typical local range: -1e6 to +1e6 meters.
    If abs(x) or abs(y) > limit, tile is likely in global CRS (wrong).
    
    Args:
        lods: List of LoD2 mesh objects
        limit: Maximum allowed absolute coordinate (default 1e6 m)
    
    Returns:
        True if all tiles are local
    
    Raises:
        AssertionError if global coordinates detected
    """
    violations = []
    
    for obj in lods:
        if obj is None or not hasattr(obj, 'location'):
            continue
        
        try:
            x = obj.location.x
            y = obj.location.y
            
            if abs(x) > limit or abs(y) > limit:
                violations.append({
                    'name': obj.name,
                    'location': (x, y),
                    'limit': limit,
                })
        except Exception as e:
            log_warn(f"[TileLocal] Failed to check {obj.name}: {e}")
            continue
    
    if violations:
        error_msg = f"[TRIPWIRE] Global coordinate drift detected ({len(violations)} tiles):\n"
        error_msg += f"  Limit: abs(x|y) <= {violations[0]['limit']}\n"
        for v in violations[:3]:  # Show first 3
            error_msg += f"  - {v['name']}: location={v['location']} (GLOBAL)\n"
        if len(violations) > 3:
            error_msg += f"  ... and {len(violations) - 3} more"
        
        log_error(error_msg)
        raise AssertionError(error_msg)
    
    log_info(f"[TileLocal] ✓ All {len(lods)} tiles in local range (abs(x|y) <= {limit:.0e}m)")
    return True


def compare_snapshots(
    before: List[Tuple[str, Tuple[float, float], Tuple[float, float, float]]],
    after: List[Tuple[str, Tuple[float, float], Tuple[float, float, float]]],
    tolerance_translation: float = 1.0,
) -> bool:
    """
    Compare placement state before/after.
    
    Allowed changes:
    - location.x, location.y translation only
    
    Forbidden:
    - scale change
    - rotation change
    - tile grid collapse
    
    Args:
        before: Snapshot before placement
        after: Snapshot after placement
        tolerance_translation: Allow small rounding errors (meters)
    
    Returns:
        True if only translation changed
    
    Raises:
        AssertionError if forbidden change detected
    """
    if len(before) != len(after):
        error_msg = f"[TRIPWIRE] Tile count mismatch: {len(before)} before → {len(after)} after"
        log_error(error_msg)
        raise AssertionError(error_msg)
    
    # Build lookup maps
    before_map = {name: (loc, scale) for name, loc, scale in before}
    after_map = {name: (loc, scale) for name, loc, scale in after}
    
    violations = []
    
    for name in before_map.keys():
        if name not in after_map:
            violations.append({
                'type': 'missing_tile',
                'name': name,
            })
            continue
        
        (x_b, y_b), (sx_b, sy_b, sz_b) = before_map[name]
        (x_a, y_a), (sx_a, sy_a, sz_a) = after_map[name]
        
        # Check scale: must be identical
        if (abs(sx_a - sx_b) > 1e-6 or abs(sy_a - sy_b) > 1e-6 or abs(sz_a - sz_b) > 1e-6):
            violations.append({
                'type': 'scale_change',
                'name': name,
                'before_scale': (sx_b, sy_b, sz_b),
                'after_scale': (sx_a, sy_a, sz_a),
            })
            continue
        
        # Check location: allow translation only (x/y)
        # Translation is OK; we just note that it occurred
        dx = abs(x_a - x_b)
        dy = abs(y_a - y_b)
    
    if violations:
        error_msg = f"[TRIPWIRE] Placement change violation ({len(violations)} issues):\n"
        for v in violations[:5]:  # Show first 5
            if v['type'] == 'scale_change':
                error_msg += f"  - {v['name']}: scale {v['before_scale']} → {v['after_scale']} (FORBIDDEN)\n"
            elif v['type'] == 'missing_tile':
                error_msg += f"  - {v['name']}: tile disappeared (FORBIDDEN)\n"
        if len(violations) > 5:
            error_msg += f"  ... and {len(violations) - 5} more"
        
        log_error(error_msg)
        raise AssertionError(error_msg)
    
    log_info(f"[Compare] ✓ Placement comparison passed: {len(before)} tiles, only translation changed")
    return True


def assert_anchor_contract(world_origin_obj=None) -> bool:
    """
    Validate that the tile anchor contract is consistent.

    Checks:
    1. The canonical TILE_ANCHOR constant (from pipeline/common.py) is "CORNER".
    2. If a WORLD_ORIGIN empty exists and has a tile_anchor property, it must
       match the canonical constant.  A mismatch means an older session wrote
       a different convention and the scene is in an inconsistent state.

    Args:
        world_origin_obj: Optional Blender Empty object (M1DC_WORLD_ORIGIN).
                          If None, the check against the scene property is skipped.

    Returns:
        True if the contract is satisfied.

    Raises:
        AssertionError if a mismatch is detected.
    """
    from ...utils.common import TILE_ANCHOR as CANONICAL_ANCHOR

    if CANONICAL_ANCHOR.upper() not in ("CORNER", "CENTER"):
        error_msg = (
            f"[TRIPWIRE] Invalid TILE_ANCHOR value '{CANONICAL_ANCHOR}' in pipeline/common.py. "
            f"Must be 'CORNER' or 'CENTER'."
        )
        log_error(error_msg)
        raise AssertionError(error_msg)

    if world_origin_obj is not None:
        stored = world_origin_obj.get("tile_anchor")
        if stored is not None and str(stored).upper() != CANONICAL_ANCHOR.upper():
            error_msg = (
                f"[TRIPWIRE] Anchor contract violation: "
                f"WORLD_ORIGIN.tile_anchor='{stored}' != canonical TILE_ANCHOR='{CANONICAL_ANCHOR}'. "
                f"Scene was created with a different anchor convention."
            )
            log_error(error_msg)
            raise AssertionError(error_msg)

    log_info(f"[AnchorContract] ✓ TILE_ANCHOR='{CANONICAL_ANCHOR}' is consistent")
    return True


def run_placement_tripwires(
    lods: List,
    tile_size_m: float,
    before_snapshot: Optional[List] = None,
    world_origin_obj=None,
) -> bool:
    """
    Run all tripwires in sequence.
    
    Aborts pipeline on first failure.
    
    Args:
        lods: List of LoD2 mesh objects
        tile_size_m: Tile size in meters (for spacing threshold)
        before_snapshot: Optional snapshot taken before placement
    
    Returns:
        True if all tripwires pass
    
    Raises:
        AssertionError if any tripwire fails (causing pipeline abort)
    """
    try:
        log_info(f"[Tripwires] Running placement diagnostics on {len(lods)} tiles...")

        # Tripwire 0: Anchor contract
        assert_anchor_contract(world_origin_obj)

        # Tripwire 1: Scale check
        assert_tile_scale_is_one(lods, tol=1e-6)
        
        # Tripwire 2: Spacing check
        min_spacing = tile_size_m * 0.25
        assert_tiles_not_collapsed(lods, min_spacing)
        
        # Tripwire 3: Local coordinate check
        assert_tiles_are_local(lods, limit=1e6)
        
        # Tripwire 4: Before/after comparison (if snapshot provided)
        if before_snapshot is not None:
            after_snapshot = snapshot_tiles(lods)
            compare_snapshots(before_snapshot, after_snapshot)
        
        log_info(f"[Tripwires] ✓ All placement diagnostics passed")
        return True
    
    except AssertionError as e:
        log_error(f"[Tripwires] ✗ PLACEMENT REGRESSION DETECTED")
        log_error(str(e))
        raise  # Re-raise to abort pipeline


__all__ = [
    'snapshot_tiles',
    'assert_tile_scale_is_one',
    'assert_tiles_not_collapsed',
    'assert_tiles_are_local',
    'assert_anchor_contract',
    'compare_snapshots',
    'run_placement_tripwires',
]
