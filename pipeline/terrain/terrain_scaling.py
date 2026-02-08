"""
Terrain Scaling & Localization Module

Automates the proven manual fix sequence for terrain scaling and positioning:
1. Measure current bounding box size
2. Calculate uniform scale factor to match world coordinates
3. Apply scale
4. Position at center of world bounds via world_to_local()

This ensures terrain mesh:
- Has correct physical dimensions (in meters, matching EPSG:25832)
- Is positioned at the correct location (overlaying with CityGML tiles)
- Uses the same coordinate system (shared M1DC_WORLD_ORIGIN)

INVARIANT:
- CityGML tiles MUST NOT be modified
- Only terrain mesh is scaled and positioned
- Scaling is UNIFORM (sx = sy, to preserve aspect ratio)
- No automatic "zentrieren" or heuristic guessing
"""

import os
import logging

try:
    import bpy
    from mathutils import Vector
except ImportError:
    pass

from ...utils.logging_system import log_info, log_warn, log_error
from ...utils.common import world_to_local as api_world_to_local, get_world_origin_minmax, SCENE_KEY_MIN_E, SCENE_KEY_MIN_N, SCENE_KEY_MAX_E, SCENE_KEY_MAX_N

# Use standard Python logging
log = logging.getLogger(__name__)


def _bbox_size_xy_world(obj):
    """
    Measure bounding box size of object in world space (XY only).
    
    Args:
        obj: Blender object with bound_box
    
    Returns:
        Tuple (width_x_meters, height_y_meters) in world space
    
    Formula:
    - Transform all 8 bound_box corners to world space
    - Find min/max X and Y
    - Return (max_x - min_x, max_y - min_y)
    """
    if not obj or not hasattr(obj, 'bound_box'):
        return (0, 0)
    
    coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    xs = [v.x for v in coords]
    ys = [v.y for v in coords]
    
    if not xs or not ys:
        return (0, 0)
    
    width_x = max(xs) - min(xs)
    height_y = max(ys) - min(ys)
    
    return (width_x, height_y)


def world_to_local(scene, e, n, z=0.0):
    """
    Convert world coordinates (EPSG:25832 easting/northing) to local Blender coords.
    
    Must be IDENTICAL to CityGML conversion logic.
    
    Args:
        scene: Blender scene object
        e: World easting (EPSG:25832 meters)
        n: World northing (EPSG:25832 meters)
        z: World elevation (optional, default 0.0)
    
    Returns:
        Tuple (local_x, local_y, local_z) for Blender object placement
    
    Raises:
        RuntimeError: If world origin not set on scene
    """
    world_min_e = scene.get(SCENE_KEY_MIN_E)
    world_min_n = scene.get(SCENE_KEY_MIN_N)
    
    if world_min_e is None or world_min_n is None:
        raise RuntimeError(
            f"[Terrain] M1DC world origin not set. "
            f"Scene must have {SCENE_KEY_MIN_E} and {SCENE_KEY_MIN_N}. "
            f"Solution: Run CityGML validation first (auto-sets world origin)."
        )
    
    local_x = float(e) - float(world_min_e)
    local_y = float(n) - float(world_min_n)
    local_z = float(z)
    
    return (local_x, local_y, local_z)


def scale_and_place_terrain(
    obj_name,
    world_min_e,
    world_min_n,
    world_max_e,
    world_max_n,
    debug_log=True,
):
    """
    Scale terrain mesh to match world bounds and place at center.
    
    CRITICAL SEQUENCE (order is not negotiable):
    1. Reset scale to (1, 1, 1) — must be done FIRST
    2. Measure current bounding box size (in model space)
    3. Calculate target dimensions from world bounds
    4. Calculate uniform scale factor
    5. Apply scale
    6. Calculate center position in world coords
    7. Convert to local coords via world_to_local()
    8. Apply location
    9. Log diagnostics (bbox, scale, location)
    
    Args:
        obj_name: Name of terrain object to scale/place (e.g., "dem_merged", "rgb_merged")
        world_min_e: World origin easting (EPSG:25832 meters)
        world_min_n: World origin northing (EPSG:25832 meters)
        world_max_e: World bound max easting (EPSG:25832 meters)
        world_max_n: World bound max northing (EPSG:25832 meters)
        debug_log: If True, log detailed diagnostics (default True)
    
    Raises:
        RuntimeError: If object not found, bbox invalid, or world_to_local fails
    
    Logic:
    - Target dimensions = (world_max_e - world_min_e, world_max_n - world_min_n) in real meters
    - Current dimensions = measured from BBox in model space (may be arbitrary units)
    - Uniform scale = average of (target_x / current_x, target_y / current_y)
    - Center = ((world_min_e + world_max_e)/2, (world_min_n + world_max_n)/2)
    - Local center = world_to_local(center_e, center_n, z=0)
    - Apply: obj.location = local_center
    """
    import bpy
    
    scene = bpy.context.scene
    obj = bpy.data.objects.get(obj_name)
    
    if not obj:
        raise RuntimeError(f"[Terrain] Object '{obj_name}' not found in scene")
    
    if obj.type != "MESH":
        raise RuntimeError(f"[Terrain] Object '{obj_name}' is not a MESH (type={obj.type})")
    
    try:
        # ========== STEP 1: Reset scale ==========
        obj.scale = (1.0, 1.0, 1.0)
        bpy.context.view_layer.update()
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: Reset scale to (1, 1, 1)")
        
        # ========== STEP 2: Measure current bounding box ==========
        cur_x, cur_y = _bbox_size_xy_world(obj)
        
        if cur_x <= 1e-6 or cur_y <= 1e-6:
            raise RuntimeError(
                f"[Terrain] {obj_name}: Invalid bounding box size "
                f"cur_x={cur_x:.6f}, cur_y={cur_y:.6f} (must be > 0)"
            )
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: Current BBox size: ({cur_x:.2f}m, {cur_y:.2f}m)")
        
        # ========== STEP 3: Calculate target dimensions ==========
        target_x = float(world_max_e - world_min_e)
        target_y = float(world_max_n - world_min_n)
        
        if target_x <= 0 or target_y <= 0:
            raise RuntimeError(
                f"[Terrain] Invalid world bounds: "
                f"E=[{world_min_e:.0f}, {world_max_e:.0f}], "
                f"N=[{world_min_n:.0f}, {world_max_n:.0f}]"
            )
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: Target dimensions: ({target_x:.2f}m, {target_y:.2f}m)")
        
        # ========== STEP 4: Calculate uniform scale factor ==========
        sx = target_x / cur_x
        sy = target_y / cur_y
        scale = 0.5 * (sx + sy)  # Uniform average
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: Scale factors: sx={sx:.6f}, sy={sy:.6f} → uniform={scale:.6f}")
        
        # ========== STEP 5: Apply scale ==========
        obj.scale = (scale, scale, scale)
        bpy.context.view_layer.update()
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: Applied scale ({scale:.6f}, {scale:.6f}, {scale:.6f})")
        
        # ========== STEP 6: Calculate center position in world coords ==========
        center_e = 0.5 * (world_min_e + world_max_e)
        center_n = 0.5 * (world_min_n + world_max_n)
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: World center: ({center_e:.2f}, {center_n:.2f})")
        
        # ========== STEP 7: Convert to local coords via world_to_local ==========
        lx, ly, lz = world_to_local(scene, center_e, center_n, 0.0)
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: Local center: ({lx:.2f}, {ly:.2f}, {lz:.2f})")
        
        # ========== STEP 8: Apply location ==========
        obj.location = (lx, ly, lz)
        bpy.context.view_layer.update()
        
        if debug_log:
            log_info(f"[Terrain] {obj_name}: Applied location ({lx:.2f}, {ly:.2f}, {lz:.2f})")
        
        # ========== STEP 9: Log final diagnostics ==========
        new_x, new_y = _bbox_size_xy_world(obj)
        
        log_info(
            f"[Terrain] {obj_name} FINALIZED: "
            f"scale={scale:.6f} bbox_xy=({new_x:.2f}m, {new_y:.2f}m) "
            f"target=({target_x:.2f}m, {target_y:.2f}m) "
            f"loc={obj.location}"
        )
        
        return True
    
    except Exception as e:
        log_error(f"[Terrain] {obj_name}: scale_and_place_terrain failed: {e}")
        raise


def scale_and_place_terrain_pair(
    dem_obj_name="dem_merged",
    rgb_obj_name="rgb_merged",
    world_min_e=None,
    world_min_n=None,
    world_max_e=None,
    world_max_n=None,
    debug_log=True,
):
    """
    Scale and place both DEM and RGB terrain meshes using the same world bounds.
    
    Convenience wrapper for applying scale_and_place_terrain to multiple objects
    (typically dem_merged and rgb_merged) with identical world bounds.
    
    Args:
        dem_obj_name: Name of DEM mesh object
        rgb_obj_name: Name of RGB mesh object
        world_min_e/n, world_max_e/n: World bounds (if None, read from scene)
        debug_log: If True, enable detailed logging
    
    Returns:
        Tuple (dem_success: bool, rgb_success: bool, errors: list)
    
    Side effects:
        - Modifies DEM and RGB object scale and location in Blender scene
        - Logs diagnostics to log_info, log_warn, log_error
    """
    import bpy
    
    scene = bpy.context.scene
    errors = []
    
    # Resolve world bounds from scene if not provided
    if world_min_e is None or world_min_n is None or world_max_e is None or world_max_n is None:
        min_e, min_n, max_e, max_n = get_world_origin_minmax()
        if min_e is None or min_n is None or max_e is None or max_n is None:
            msg = "[Terrain] World bounds not provided and not found in scene. Cannot proceed."
            log_error(msg)
            return (False, False, [msg])
        
        world_min_e = world_min_e or min_e
        world_min_n = world_min_n or min_n
        world_max_e = world_max_e or max_e
        world_max_n = world_max_n or max_n
    
    if debug_log:
        log_info(
            f"[Terrain] === SCALE & PLACE TERRAIN PAIR ===\n"
            f"          World bounds: E=[{world_min_e:.0f}, {world_max_e:.0f}], "
            f"N=[{world_min_n:.0f}, {world_max_n:.0f}]"
        )
    
    # Process DEM
    dem_ok = False
    try:
        scale_and_place_terrain(
            dem_obj_name,
            world_min_e, world_min_n, world_max_e, world_max_n,
            debug_log=debug_log,
        )
        dem_ok = True
    except Exception as e:
        errors.append(f"DEM ({dem_obj_name}): {e}")
        log_error(f"[Terrain] DEM scaling failed: {e}")
    
    # Process RGB
    rgb_ok = False
    try:
        scale_and_place_terrain(
            rgb_obj_name,
            world_min_e, world_min_n, world_max_e, world_max_n,
            debug_log=debug_log,
        )
        rgb_ok = True
    except Exception as e:
        errors.append(f"RGB ({rgb_obj_name}): {e}")
        log_error(f"[Terrain] RGB scaling failed: {e}")
    
    if debug_log:
        log_info(
            f"[Terrain] === SCALE & PLACE COMPLETE ===\n"
            f"          DEM: {'✓' if dem_ok else '✗'}, RGB: {'✓' if rgb_ok else '✗'}"
        )
    
    return (dem_ok, rgb_ok, errors)
