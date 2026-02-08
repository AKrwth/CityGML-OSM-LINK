"""
Terrain World Bounds Calibration Module

Deterministic scaling and placement of terrain (DEM/RGB) to match WORLD_BOUNDS.

Contract:
- Input: dem_merged, rgb_merged objects (post-BlenderGIS import, typically at arbitrary scale/position)
- Input: Scene WORLD_BOUNDS (M1DC_WORLD_MIN_E, M1DC_WORLD_MIN_N, M1DC_WORLD_MAX_E, M1DC_WORLD_MAX_N)
- Output: dem_merged & rgb_merged scaled + positioned so bbox matches target bounds
- Invariant: CityGML objects are NEVER used in scaling calculations

Algorithm:
1. Validate WORLD_BOUNDS exist in scene
2. Extract target dimensions: target_w = MAX_E - MIN_E, target_h = MAX_N - MIN_N
3. Measure current dem_merged bbox_size_xy (world space)
4. Compute uniform scale: s = 0.5 * (target_w / current_w + target_h / current_h)
5. Apply scale to dem_merged and rgb_merged
6. Compute target bbox center in local space: (target_w/2, target_h/2, 0)
7. Move dem/rgb so their bbox center matches target center
8. Validate: after scaling, bbox must match target within tolerance (default 2%)
9. Return (success, diagnostics)
"""

import logging
from pathlib import Path

try:
    import bpy
    from mathutils import Vector
except ImportError:
    pass

# Use local logger
log = logging.getLogger(__name__)


def _cal_log(msg: str) -> None:
    print(msg)
    log.info(msg)


def _cal_err(msg: str) -> None:
    print(msg)
    log.error(msg)

# Scene property keys (must match pipeline/common.py)
SCENE_KEY_MIN_E = "M1DC_WORLD_MIN_E"
SCENE_KEY_MIN_N = "M1DC_WORLD_MIN_N"
SCENE_KEY_MAX_E = "M1DC_WORLD_MAX_E"
SCENE_KEY_MAX_N = "M1DC_WORLD_MAX_N"


def _bbox_size_xy_world(obj):
    """
    Compute bounding box size in world space (XY only).
    
    Args:
        obj: Blender object with bound_box
    
    Returns:
        (width_x, height_y) in Blender world units (1 unit = 1 meter in local space)
    """
    if not obj or not hasattr(obj, 'bound_box'):
        return (0, 0)
    
    coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    xs = [v.x for v in coords]
    ys = [v.y for v in coords]
    
    if not xs or not ys:
        return (0, 0)
    
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    return (width, height)


def bbox_size_xy_world(obj):
    return _bbox_size_xy_world(obj)


def _bbox_center_xy_world(obj):
    """
    Get bounding box center in world space (XY only).
    
    Returns:
        (center_x, center_y) in Blender world units
    """
    if not obj or not hasattr(obj, 'bound_box'):
        return (0, 0)
    
    coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    xs = [v.x for v in coords]
    ys = [v.y for v in coords]
    
    if not xs or not ys:
        return (0, 0)
    
    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    return (center_x, center_y)


def calibrate_terrain_to_world_bounds(scene, dem_obj, rgb_obj=None, tol_rel=0.02):
    """
    Scale and position terrain to match world bounds.
    
    This is the core calibration routine. It:
    1. Validates WORLD_BOUNDS are set on scene
    2. Measures current terrain bbox
    3. Computes uniform scale factor to match target bounds
    4. Applies scale to dem and rgb objects
    5. Positions so bbox center aligns with target center
    6. Validates result within tolerance
    
    Args:
        scene: Blender scene (must contain WORLD_BOUNDS properties)
        dem_obj: DEM mesh object (e.g. dem_merged)
        rgb_obj: Optional RGB object (e.g. rgb_merged). If provided, scaled/positioned identically.
        tol_rel: Relative tolerance for validation (default 0.02 = 2%)
    
    Returns:
        dict with diagnostics (raises RuntimeError on failure)
        info dict contains:
        - 'status': 'OK' or failure reason
        - 'world_bounds': (min_e, min_n, max_e, max_n)
        - 'target_size': (target_w, target_h)
        - 'dem_bbox_before': (w, h)
        - 'dem_bbox_after': (w, h)
        - 'scale_applied': float
        - 'dem_location_final': (x, y, z)
        - 'rgb_location_final': (x, y, z) if rgb_obj provided
        - 'validation_error': relative error % if validation failed
    
    Side effects:
        - Modifies dem_obj.scale and dem_obj.location
        - Modifies rgb_obj.scale and rgb_obj.location if rgb_obj provided
    """
    import bpy
    
    info = {}
    
    # Step 1: Validate WORLD_BOUNDS
    min_e = scene.get(SCENE_KEY_MIN_E)
    min_n = scene.get(SCENE_KEY_MIN_N)
    max_e = scene.get(SCENE_KEY_MAX_E)
    max_n = scene.get(SCENE_KEY_MAX_N)
    
    if min_e is None or min_n is None or max_e is None or max_n is None:
        info['status'] = 'WORLD_BOUNDS missing'
        info['world_bounds'] = (min_e, min_n, max_e, max_n)
        _cal_err("[TerrainCal] WORLD_BOUNDS not set on scene. Cannot calibrate.")
        raise RuntimeError("[TerrainCal] WORLD_BOUNDS missing")
    
    min_e = float(min_e)
    min_n = float(min_n)
    max_e = float(max_e)
    max_n = float(max_n)
    info['world_bounds'] = (min_e, min_n, max_e, max_n)
    
    # Step 2: Validate DEM object
    if not dem_obj or dem_obj.type != 'MESH':
        info['status'] = f'Invalid DEM object: {dem_obj}'
        _cal_err("[TerrainCal] DEM object invalid or not MESH")
        raise RuntimeError("[TerrainCal] Invalid DEM object")
    
    try:
        # Step 3: Extract target dimensions
        target_w = max_e - min_e
        target_h = max_n - min_n
        info['target_size'] = (target_w, target_h)
        
        if target_w <= 0 or target_h <= 0:
            info['status'] = f'Invalid WORLD_BOUNDS: W={target_w}, H={target_h}'
            _cal_err(f"[TerrainCal] Invalid WORLD_BOUNDS: {info['world_bounds']}")
            raise RuntimeError("[TerrainCal] Invalid WORLD_BOUNDS")
        
        _cal_log("[TerrainCal] === TERRAIN CALIBRATION START ===")
        _cal_log(f"[TerrainCal] WORLD_BOUNDS: E=[{min_e:.1f}, {max_e:.1f}], N=[{min_n:.1f}, {max_n:.1f}]")
        _cal_log(f"[TerrainCal] Target bbox size: {target_w:.2f} x {target_h:.2f} meters")
        
        # Step 4: Measure current DEM bbox
        dem_w, dem_h = _bbox_size_xy_world(dem_obj)
        info['dem_bbox_before'] = (dem_w, dem_h)
        
        if dem_w <= 1e-6 or dem_h <= 1e-6:
            info['status'] = f'DEM bbox invalid: W={dem_w}, H={dem_h}'
            _cal_err(f"[TerrainCal] DEM bbox too small: {dem_w:.6f} x {dem_h:.6f}")
            raise RuntimeError("[TerrainCal] DEM bbox invalid")
        
        _cal_log(f"[TerrainCal] DEM bbox before: {dem_w:.2f} x {dem_h:.2f} units")
        
        # Step 5: Compute non-uniform scale factors
        scale_x = target_w / dem_w
        scale_y = target_h / dem_h
        info['scale_x_applied'] = scale_x
        info['scale_y_applied'] = scale_y
        _cal_log(f"[TerrainCal] Scale factors: X={scale_x:.6f}, Y={scale_y:.6f} (non-uniform)")

        # Step 6: Apply non-uniform scale to DEM and RGB (Z unchanged)
        dem_obj.scale.x *= scale_x
        dem_obj.scale.y *= scale_y
        # scale.z unchanged (documented)
        if rgb_obj:
            rgb_obj.scale.x *= scale_x
            rgb_obj.scale.y *= scale_y
        bpy.context.view_layer.update()
        _cal_log(f"[TerrainCal] Applied non-uniform scale: X={scale_x:.6f}, Y={scale_y:.6f} to DEM" + (f" and RGB" if rgb_obj else ""))
        
        # Step 7: Compute target center in local space
        target_c = Vector((target_w / 2.0, target_h / 2.0, dem_obj.location.z))
        _cal_log(f"[TerrainCal] Target bbox center (local): ({target_c.x:.2f}, {target_c.y:.2f})")
        
        # Step 8: Get current DEM bbox center and move to target
        current_cx, current_cy = _bbox_center_xy_world(dem_obj)
        cur_c = Vector((current_cx, current_cy, target_c.z))
        delta = target_c - cur_c
        
        dem_obj.location.x += delta.x
        dem_obj.location.y += delta.y
        
        if rgb_obj:
            rgb_obj.location.x += delta.x
            rgb_obj.location.y += delta.y
        
        bpy.context.view_layer.update()
        _cal_log(f"[TerrainCal] Applied location delta: ({delta.x:.2f}, {delta.y:.2f})")
        _cal_log(f"[TerrainCal] DEM location final: {tuple(dem_obj.location)}")
        if rgb_obj:
            _cal_log(f"[TerrainCal] RGB location final: {tuple(rgb_obj.location)}")
        
        info['dem_location_final'] = tuple(dem_obj.location)
        if rgb_obj:
            info['rgb_location_final'] = tuple(rgb_obj.location)
        
        # Step 9: Validate result
        dem_w_after, dem_h_after = _bbox_size_xy_world(dem_obj)
        info['dem_bbox_after'] = (dem_w_after, dem_h_after)
        
        err_w = abs(dem_w_after - target_w) / target_w
        err_h = abs(dem_h_after - target_h) / target_h
        err_max = max(err_w, err_h)
        info['validation_error'] = err_max * 100  # as percentage
        
        _cal_log(f"[TerrainCal] DEM bbox after: {dem_w_after:.2f} x {dem_h_after:.2f} units")
        _cal_log(f"[TerrainCal] Validation: target {target_w:.2f}x{target_h:.2f} vs actual {dem_w_after:.2f}x{dem_h_after:.2f}")
        _cal_log(f"[TerrainCal] Max relative error: {err_max*100:.2f}% (tolerance: {tol_rel*100:.2f}%)")
        
        if err_max > tol_rel:
            info['status'] = f'VALIDATION FAILED: {err_max*100:.2f}% error > {tol_rel*100:.2f}% tolerance'
            _cal_err(f"[TerrainCal] {info['status']}")
            _cal_err("[TerrainCal] HARD TRIPWIRE: Terrain calibration failed. Operator will fail.")
            raise RuntimeError(f"[TerrainCal] {info['status']}")
        
        _cal_log(f"[TerrainCal] ✓ VALIDATION PASSED (error {err_max*100:.2f}% within {tol_rel*100:.2f}% tolerance)")

        # === XY ALIGNMENT FIX: Enforce terrain min corner at world (0, 0) ===
        # After calibration (which sets scale and centers bbox), adjust location so min corner is at (0, 0)
        # This matches CityGML local coordinate system (local = world - WORLD_MIN)
        _cal_log("[TerrainCal] === XY ALIGNMENT: Aligning terrain min corner to (0, 0) ===")

        # Compute current world bbox min (accounting for scale and current location)
        coords = [dem_obj.matrix_world @ Vector(corner) for corner in dem_obj.bound_box]
        current_world_min_x = min(v.x for v in coords)
        current_world_min_y = min(v.y for v in coords)

        _cal_log(f"[TerrainCal][XY-ALIGN] Current world bbox min: ({current_world_min_x:.2f}, {current_world_min_y:.2f})")
        _cal_log(f"[TerrainCal][XY-ALIGN] Current location: ({dem_obj.location.x:.2f}, {dem_obj.location.y:.2f})")

        # Adjust location to shift min corner to (0, 0)
        dem_obj.location.x -= current_world_min_x
        dem_obj.location.y -= current_world_min_y
        # Z unchanged

        if rgb_obj:
            rgb_obj.location.x -= current_world_min_x
            rgb_obj.location.y -= current_world_min_y

        bpy.context.view_layer.update()

        # Verify result
        coords_after = [dem_obj.matrix_world @ Vector(corner) for corner in dem_obj.bound_box]
        world_min_x_after = min(v.x for v in coords_after)
        world_min_y_after = min(v.y for v in coords_after)

        _cal_log(f"[TerrainCal][XY-ALIGN] Final location: ({dem_obj.location.x:.2f}, {dem_obj.location.y:.2f})")
        _cal_log(f"[TerrainCal][XY-ALIGN] Final world bbox min: ({world_min_x_after:.2f}, {world_min_y_after:.2f})")

        if abs(world_min_x_after) > 100 or abs(world_min_y_after) > 100:
            _cal_log(f"[TerrainCal][XY-ALIGN] ⚠️  World min not at origin! Expected ~(0,0), got ({world_min_x_after:.2f}, {world_min_y_after:.2f})")
        else:
            _cal_log(f"[TerrainCal][XY-ALIGN] ✓ Terrain aligned to local origin")

        _cal_log("[TerrainCal] === TERRAIN CALIBRATION COMPLETE ===")

        dem_obj["M1DC_TERRAIN_CALIBRATED"] = True
        if rgb_obj:
            rgb_obj["M1DC_TERRAIN_CALIBRATED"] = True
            # --- Begin: Remove RGB plane after calibration ---
            rgb_name = getattr(rgb_obj, 'name', None)
            # Only delete if object is named like 'rgb_merged' (not DEM)
            if rgb_name and rgb_name.lower().startswith('rgb'):
                # Extract material/image references if needed (already done by pipeline)
                try:
                    import bpy
                    bpy.data.objects.remove(rgb_obj, do_unlink=True)
                    _cal_log(f"[Terrain] Deleted RGB plane object: {rgb_name}")
                except Exception as ex:
                    _cal_log(f"[Terrain] Failed to delete RGB plane object: {rgb_name}: {ex}")
            # --- End: Remove RGB plane after calibration ---
        info['status'] = 'OK'
        return info
    
    except Exception as e:
        info['status'] = f'Exception: {e}'
        _cal_err(f"[TerrainCal] Calibration failed with exception: {e}")
        raise


def localize_citygml_by_world_min(scene, collection_name="CITYGML_TILES"):
    """
    Optional helper: Localize CityGML tiles by subtracting WORLD_MIN if they are in global coords.
    
    This is a SEPARATE operation from terrain calibration. Call only if needed.
    
    Args:
        scene: Blender scene
        collection_name: Name of collection containing CityGML tiles
    
    Returns:
        (count_localized, info_dict)
    """
    import bpy
    
    min_e = scene.get(SCENE_KEY_MIN_E)
    min_n = scene.get(SCENE_KEY_MIN_N)
    
    if min_e is None or min_n is None:
        log.warning(f"[TerrainCal] WORLD_MIN not set; skipping CityGML localization")
        return (0, {'status': 'WORLD_MIN missing'})
    
    min_e = float(min_e)
    min_n = float(min_n)
    
    try:
        coll = bpy.data.collections.get(collection_name)
        if not coll:
            log.warning(f"[TerrainCal] Collection '{collection_name}' not found")
            return (0, {'status': 'Collection not found'})
        
        count = 0
        for obj in coll.objects:
            if obj.type != 'MESH':
                continue
            
            # Check if object is in global coordinates (heuristic: location magnitude > 1e5)
            mag_sq = obj.location.x ** 2 + obj.location.y ** 2
            mag = mag_sq ** 0.5
            
            if mag > 1e5:
                obj.location.x -= min_e
                obj.location.y -= min_n
                log.info(f"[TerrainCal] Localized CityGML tile '{obj.name}': delta=({-min_e:.1f}, {-min_n:.1f})")
                count += 1
        
        log.info(f"[TerrainCal] Localized {count} CityGML tiles by WORLD_MIN")
        return (count, {'status': 'OK', 'count': count})
    
    except Exception as e:
        log.exception(f"[TerrainCal] CityGML localization failed")
        return (0, {'status': f'Exception: {e}'})
