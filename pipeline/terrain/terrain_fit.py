"""
Terrain BBox-Fit: Scale + position terrain to exactly match CityGML tile extent.

Contract:
- Input: terrain object (DEM mesh) + list of CityGML tile mesh objects
- Output: terrain scaled + positioned so its XY bounding box matches the union
  bounding box of all CityGML tiles (pixel-perfect in local coordinate space)
- Invariant: CityGML objects are NEVER moved or scaled
- Tripwire: If post-fit error > eps (default 0.05m), raise RuntimeError

Algorithm:
1. Compute union bounding box of all CityGML tile objects (world space XY)
2. Compute terrain bounding box (world space XY)
3. Compute non-uniform scale factors: sx = target_w / src_w, sy = target_h / src_h
4. Apply scale to terrain (XY only, Z unchanged)
5. Recompute terrain bbox; translate min corner to match CityGML min corner
6. Validate: all 4 corners must match within eps
7. Tag terrain with M1DC_TERRAIN_FIT=True to prevent double-fitting

Usage from pipeline:
    from pipeline.terrain.terrain_fit import fit_terrain_to_citygml
    info = fit_terrain_to_citygml(terrain_obj, citygml_objs, eps=0.05)
"""

import logging

try:
    import bpy
    from mathutils import Vector
except ImportError:
    pass

log = logging.getLogger(__name__)


def _fit_log(msg: str) -> None:
    """Log to both console and Python logger."""
    print(msg)
    log.info(msg)
    try:
        from ...utils.logging_system import log_info
        log_info(msg)
    except Exception:
        pass


def _fit_err(msg: str) -> None:
    """Log error to both console and Python logger."""
    print(msg)
    log.error(msg)
    try:
        from ...utils.logging_system import log_error
        log_error(msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def bbox_world(obj):
    """
    Compute world-space axis-aligned bounding box of an object.

    Returns:
        ((min_x, min_y, min_z), (max_x, max_y, max_z))
    """
    mw = obj.matrix_world
    corners = [mw @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def bbox_union(objs):
    """
    Compute the union bounding box of multiple objects (world space).

    Returns:
        ((min_x, min_y, min_z), (max_x, max_y, max_z))

    Raises:
        ValueError if objs is empty
    """
    if not objs:
        raise ValueError("[TERRAIN][FIT] No objects provided for bbox union")

    all_min = []
    all_max = []
    for obj in objs:
        mn, mx = bbox_world(obj)
        all_min.append(mn)
        all_max.append(mx)

    return (
        (min(m[0] for m in all_min), min(m[1] for m in all_min), min(m[2] for m in all_min)),
        (max(m[0] for m in all_max), max(m[1] for m in all_max), max(m[2] for m in all_max)),
    )


# ---------------------------------------------------------------------------
# Core fit function
# ---------------------------------------------------------------------------

def fit_terrain_to_citygml(terrain_obj, citygml_objs, eps=0.05, rgb_obj=None):
    """
    Scale and position terrain to exactly match the CityGML tile union bounding box.

    This is a deterministic, idempotent operation. The terrain's XY footprint
    will match the CityGML tiles' XY footprint after this call.

    Args:
        terrain_obj: Blender mesh object (DEM / terrain)
        citygml_objs: List of Blender mesh objects (CityGML tiles)
        eps: Maximum acceptable error in meters (default 0.05 = 5cm)
        rgb_obj: Optional RGB basemap object to transform identically

    Returns:
        dict with diagnostics:
            - status: 'OK' or error description
            - target_bbox: ((min_x, min_y), (max_x, max_y))
            - target_size: (width, height)
            - terrain_bbox_before: ((min_x, min_y), (max_x, max_y))
            - terrain_size_before: (width, height)
            - terrain_bbox_after: ((min_x, min_y), (max_x, max_y))
            - terrain_size_after: (width, height)
            - scale_x, scale_y: applied scale factors
            - dx, dy: applied translation
            - error: max corner deviation in meters

    Raises:
        RuntimeError if post-fit error > eps
        ValueError if inputs are invalid
    """
    import bpy

    info = {}

    # ── Validate inputs ──
    if terrain_obj is None or not hasattr(terrain_obj, 'bound_box'):
        raise ValueError("[TERRAIN][FIT] terrain_obj is None or has no bound_box")
    if not citygml_objs:
        raise ValueError("[TERRAIN][FIT] citygml_objs is empty — need CityGML tiles")

    # Filter to meshes only
    gml_meshes = [o for o in citygml_objs if o.type == 'MESH']
    if not gml_meshes:
        raise ValueError("[TERRAIN][FIT] No MESH objects in citygml_objs")

    _fit_log("[TERRAIN][FIT] === TERRAIN BBOX-FIT START ===")
    _fit_log(f"[TERRAIN][FIT] terrain={terrain_obj.name} | citygml_tiles={len(gml_meshes)}")

    # ── Step 1: Target bbox from CityGML union ──
    tgt_min, tgt_max = bbox_union(gml_meshes)
    tgt_w = tgt_max[0] - tgt_min[0]
    tgt_h = tgt_max[1] - tgt_min[1]

    info['target_bbox'] = ((tgt_min[0], tgt_min[1]), (tgt_max[0], tgt_max[1]))
    info['target_size'] = (tgt_w, tgt_h)

    _fit_log(f"[TERRAIN][FIT] CityGML union bbox: min=({tgt_min[0]:.2f}, {tgt_min[1]:.2f}) max=({tgt_max[0]:.2f}, {tgt_max[1]:.2f})")
    _fit_log(f"[TERRAIN][FIT] CityGML extent: {tgt_w:.2f} x {tgt_h:.2f} meters")

    if tgt_w <= 0 or tgt_h <= 0:
        raise ValueError(f"[TERRAIN][FIT] CityGML extent invalid: {tgt_w:.2f} x {tgt_h:.2f}")

    # ── Step 2: Current terrain bbox ──
    src_min, src_max = bbox_world(terrain_obj)
    src_w = src_max[0] - src_min[0]
    src_h = src_max[1] - src_min[1]

    info['terrain_bbox_before'] = ((src_min[0], src_min[1]), (src_max[0], src_max[1]))
    info['terrain_size_before'] = (src_w, src_h)

    _fit_log(f"[TERRAIN][FIT] Terrain bbox BEFORE: min=({src_min[0]:.2f}, {src_min[1]:.2f}) max=({src_max[0]:.2f}, {src_max[1]:.2f})")
    _fit_log(f"[TERRAIN][FIT] Terrain extent BEFORE: {src_w:.2f} x {src_h:.2f} meters")

    if src_w <= 1e-6 or src_h <= 1e-6:
        raise ValueError(f"[TERRAIN][FIT] Terrain bbox too small: {src_w:.6f} x {src_h:.6f}")

    # ── Step 3: Compute non-uniform XY scale factors ──
    sx = tgt_w / src_w
    sy = tgt_h / src_h

    info['scale_x'] = sx
    info['scale_y'] = sy

    _fit_log(f"[TERRAIN][FIT] Scale factors: sx={sx:.6f}, sy={sy:.6f}")

    # ── Step 4: Apply scale (XY only, Z unchanged) ──
    terrain_obj.scale.x *= sx
    terrain_obj.scale.y *= sy
    if rgb_obj:
        rgb_obj.scale.x *= sx
        rgb_obj.scale.y *= sy

    bpy.context.view_layer.update()

    # ── Step 5: Translate min corner to match CityGML min corner ──
    src_min2, _ = bbox_world(terrain_obj)
    dx = tgt_min[0] - src_min2[0]
    dy = tgt_min[1] - src_min2[1]

    info['dx'] = dx
    info['dy'] = dy

    terrain_obj.location.x += dx
    terrain_obj.location.y += dy
    if rgb_obj:
        rgb_obj.location.x += dx
        rgb_obj.location.y += dy

    bpy.context.view_layer.update()

    _fit_log(f"[TERRAIN][FIT] Applied translate: dx={dx:.3f}, dy={dy:.3f}")

    # ── Step 6: Validate — all 4 corners must match within eps ──
    src_min3, src_max3 = bbox_world(terrain_obj)
    src_w_after = src_max3[0] - src_min3[0]
    src_h_after = src_max3[1] - src_min3[1]

    info['terrain_bbox_after'] = ((src_min3[0], src_min3[1]), (src_max3[0], src_max3[1]))
    info['terrain_size_after'] = (src_w_after, src_h_after)

    err_min_x = abs(src_min3[0] - tgt_min[0])
    err_min_y = abs(src_min3[1] - tgt_min[1])
    err_max_x = abs(src_max3[0] - tgt_max[0])
    err_max_y = abs(src_max3[1] - tgt_max[1])
    err = max(err_min_x, err_min_y, err_max_x, err_max_y)

    info['error'] = err

    _fit_log(f"[TERRAIN][FIT] Terrain bbox AFTER: min=({src_min3[0]:.3f}, {src_min3[1]:.3f}) max=({src_max3[0]:.3f}, {src_max3[1]:.3f})")
    _fit_log(f"[TERRAIN][FIT] Terrain extent AFTER: {src_w_after:.2f} x {src_h_after:.2f} meters")
    _fit_log(f"[TERRAIN][FIT] Corner errors: minX={err_min_x:.4f} minY={err_min_y:.4f} maxX={err_max_x:.4f} maxY={err_max_y:.4f}")
    _fit_log(f"[TERRAIN][FIT] Max error: {err:.4f}m (eps={eps}m)")

    if err > eps:
        info['status'] = f'FAIL: err={err:.4f}m > eps={eps}m'
        _fit_err(f"[TERRAIN][FIT][FAIL] bbox mismatch err={err:.3f}m (eps={eps}m)")
        raise RuntimeError(f"[TERRAIN][FIT][FAIL] bbox mismatch err={err:.3f}m (eps={eps}m)")

    # ── Step 7: Tag terrain as fitted ──
    terrain_obj["M1DC_TERRAIN_FIT"] = True
    terrain_obj["M1DC_TERRAIN_CALIBRATED"] = True  # compat with old calibration flag
    if rgb_obj:
        rgb_obj["M1DC_TERRAIN_FIT"] = True
        rgb_obj["M1DC_TERRAIN_CALIBRATED"] = True

    info['status'] = 'OK'
    _fit_log(f"[TERRAIN][FIT][OK] err={err:.3f}m scale_x={sx:.6f} scale_y={sy:.6f} dx={dx:.3f} dy={dy:.3f}")
    _fit_log(f"[TERRAIN][FIT] terrain.location=({terrain_obj.location.x:.3f}, {terrain_obj.location.y:.3f}, {terrain_obj.location.z:.3f})")
    _fit_log(f"[TERRAIN][FIT] terrain.scale=({terrain_obj.scale.x:.6f}, {terrain_obj.scale.y:.6f}, {terrain_obj.scale.z:.6f})")
    _fit_log("[TERRAIN][FIT] === TERRAIN BBOX-FIT COMPLETE ===")

    return info
