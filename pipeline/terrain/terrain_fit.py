"""
Terrain BBox-Fit: Scale + position terrain to exactly match CityGML tile-grid extent.

Contract:
- Input: terrain object (DEM mesh) + list of CityGML tile mesh objects
- Output: terrain scaled + positioned so its XY bounding box matches the
  CityGML tile *grid* extent (pixel-perfect in local coordinate space)
- CityGML extent is computed from tile locations + tile_size, NOT from
  mesh-vertex bounding boxes (which only cover building geometry, much
  smaller than the full tile grid)
- Terrain bbox is computed from obj.data.vertices + matrix_world (NOT from
  obj.bound_box which can be stale/cached in Blender)
- Invariant: CityGML objects are NEVER moved or scaled
- Tripwire: If post-fit error > eps (default 0.05m), raise RuntimeError

Algorithm:
1. Compute CityGML tile-grid extent from tile locations + tile_size
2. Compute terrain bounding box via vertices + matrix_world
3. Compute non-uniform scale factors: sx = target_w / src_w, sy = target_h / src_h
4. Apply scale as tuple (atomic write, Z unchanged)
5. Flush depsgraph; recompute terrain bbox via vertices; translate to align min corner
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


# ---------------------------------------------------------------------------
# Tripwires & helpers
# ---------------------------------------------------------------------------

def _require_terrain_objects(terrain_col, ctx=""):
    """Hard tripwire: terrain collection must contain at least 1 MESH."""
    terrain_meshes = [o for o in terrain_col.objects if o.type == "MESH"]
    if len(terrain_meshes) == 0:
        raise RuntimeError(
            f"[TERRAIN] Import produced 0 mesh objects. ctx={ctx}. "
            f"Check terrain path/mode, importer errors, and deletion/cleanup steps."
        )
    return terrain_meshes


def _neutralize_rotation(obj, log_fn=None):
    """Ensure rotation is zero. Apply if non-zero so vertex coords are correct."""
    import bpy
    from math import radians
    rot = obj.rotation_euler
    threshold = radians(0.01)  # ~0.01 degree
    if abs(rot.x) > threshold or abs(rot.y) > threshold or abs(rot.z) > threshold:
        msg = (f"[TERRAIN][FIT] Non-zero rotation on '{obj.name}': "
               f"({rot.x:.4f}, {rot.y:.4f}, {rot.z:.4f}) rad — applying rotation first")
        if log_fn:
            log_fn(msg)
        # Apply rotation into mesh
        prev_active = bpy.context.view_layer.objects.active
        prev_selected = [o for o in bpy.context.selected_objects]
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        obj.select_set(False)
        for o in prev_selected:
            try:
                o.select_set(True)
            except Exception:
                pass
        bpy.context.view_layer.objects.active = prev_active


def _detect_axis_swap(span_x, span_y, span_z, log_fn=None):
    """Detect if terrain is in X/Z instead of X/Y (common OBJ import issue).

    Returns True if the Z-span is much larger than Y-span, suggesting the
    terrain lies in the X/Z plane rather than X/Y.
    """
    # If Z is large and Y is tiny, terrain is "standing" in X/Z
    if span_z > 10 * span_y and span_z > 100:
        msg = (f"[TERRAIN][FIT] AXIS SWAP DETECTED: span_x={span_x:.1f}, "
               f"span_y={span_y:.1f}, span_z={span_z:.1f}. "
               f"Terrain appears to lie in X/Z plane, not X/Y.")
        if log_fn:
            log_fn(msg)
        return True
    return False


def _object_world_span(obj):
    """Return (span_vector, center_vector, world_min, world_max) from obj vertices."""
    bb_min, bb_max = world_bbox_from_vertices(obj)
    from mathutils import Vector
    mn = Vector(bb_min)
    mx = Vector(bb_max)
    span = mx - mn
    center = (mx + mn) * 0.5
    return span, center, mn, mx


def terrain_acceptance_proof(terrain_obj, city_span_xy, city_center_xy, log_fn=None):
    """Log terrain acceptance proof: spans, center delta, scale, relative error.

    Returns dict with diagnostics.
    """
    _log = log_fn or print
    span, center, mn, mx = _object_world_span(terrain_obj)
    city_sx, city_sy = city_span_xy
    city_cx, city_cy = city_center_xy

    err_x = abs(span.x - city_sx) / city_sx if city_sx > 0 else 999
    err_y = abs(span.y - city_sy) / city_sy if city_sy > 0 else 999
    d_cx = abs(center.x - city_cx)
    d_cy = abs(center.y - city_cy)

    _log(f"[TERRAIN][ACCEPT] city_span=({city_sx:.1f}, {city_sy:.1f})")
    _log(f"[TERRAIN][ACCEPT] terrain_span=({span.x:.1f}, {span.y:.1f})")
    _log(f"[TERRAIN][ACCEPT] rel_err=({err_x:.4f}, {err_y:.4f})")
    _log(f"[TERRAIN][ACCEPT] city_center=({city_cx:.1f}, {city_cy:.1f})")
    _log(f"[TERRAIN][ACCEPT] terrain_center=({center.x:.1f}, {center.y:.1f})")
    _log(f"[TERRAIN][ACCEPT] center_delta=({d_cx:.1f}, {d_cy:.1f})")
    _log(f"[TERRAIN][ACCEPT] terrain.scale={tuple(terrain_obj.scale)}")
    _log(f"[TERRAIN][ACCEPT] terrain.rotation={tuple(terrain_obj.rotation_euler)}")
    _log(f"[TERRAIN][ACCEPT] terrain.location={tuple(terrain_obj.location)}")

    return {
        'terrain_span': (span.x, span.y),
        'city_span': (city_sx, city_sy),
        'err_rel': (err_x, err_y),
        'center_delta': (d_cx, d_cy),
    }


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
# Geometry helpers — vertex-based (NOT obj.bound_box)
# ---------------------------------------------------------------------------

def world_bbox_from_vertices(obj):
    """
    Compute world-space axis-aligned bounding box from mesh vertices.

    Uses ``obj.data.vertices`` transformed by ``obj.matrix_world``.
    This is immune to the bound_box caching issues in Blender where
    ``obj.bound_box`` may return stale data after scale changes even
    after ``view_layer.update()``.

    Returns:
        ((min_x, min_y, min_z), (max_x, max_y, max_z))

    Raises:
        ValueError if mesh has no vertices
    """
    mesh = obj.data
    if not mesh.vertices:
        raise ValueError(f"[TERRAIN][FIT] Object '{obj.name}' has no vertices")

    mw = obj.matrix_world
    # Process first vertex to seed min/max
    first = mw @ mesh.vertices[0].co
    min_x = max_x = first.x
    min_y = max_y = first.y
    min_z = max_z = first.z

    for v in mesh.vertices:
        wco = mw @ v.co
        if wco.x < min_x:
            min_x = wco.x
        elif wco.x > max_x:
            max_x = wco.x
        if wco.y < min_y:
            min_y = wco.y
        elif wco.y > max_y:
            max_y = wco.y
        if wco.z < min_z:
            min_z = wco.z
        elif wco.z > max_z:
            max_z = wco.z

    return (min_x, min_y, min_z), (max_x, max_y, max_z)


def citygml_grid_extent(tile_objs):
    """
    Compute CityGML tile-grid extent from tile object locations + tile_size.

    The tile-grid extent is the full area covered by ALL tiles, computed as:
        grid_min = min(tile_locations) - tile_size / 2
        grid_max = max(tile_locations) + tile_size / 2

    The tile_size is inferred from the minimum spacing between adjacent tile
    locations (which equals tile_size for a regular grid). Falls back to
    the scene ``tile_size_m`` / ``tile_size_m_ref`` property on M1DC_WORLD_ORIGIN,
    or a default of 1000m.

    This is CRITICAL because mesh-vertex bounding boxes only cover actual
    building geometry (~1150x1176m in the Cologne dataset) while the real
    tile grid covers 8000x7000m.

    Args:
        tile_objs: List of CityGML tile mesh objects (from CITYGML_TILES collection)

    Returns:
        ((min_x, min_y), (max_x, max_y)) in world(local) coordinates

    Raises:
        ValueError if fewer than 1 tile provided
    """
    if not tile_objs:
        raise ValueError("[TERRAIN][FIT] No tile objects for grid extent computation")

    # Collect tile center locations (XY only)
    locs_x = [obj.location.x for obj in tile_objs]
    locs_y = [obj.location.y for obj in tile_objs]

    min_loc_x = min(locs_x)
    max_loc_x = max(locs_x)
    min_loc_y = min(locs_y)
    max_loc_y = max(locs_y)

    _fit_log(f"[TERRAIN][FIT][GRID] tile_count={len(tile_objs)} "
             f"loc_x=[{min_loc_x:.1f}..{max_loc_x:.1f}] "
             f"loc_y=[{min_loc_y:.1f}..{max_loc_y:.1f}]")

    # Infer tile_size from spacing between adjacent unique tile locations
    tile_size = _infer_tile_size(locs_x, locs_y)
    half = tile_size / 2.0

    grid_min_x = min_loc_x - half
    grid_max_x = max_loc_x + half
    grid_min_y = min_loc_y - half
    grid_max_y = max_loc_y + half

    _fit_log(f"[TERRAIN][FIT][GRID] tile_size={tile_size:.1f}m "
             f"grid=({grid_min_x:.1f},{grid_min_y:.1f})..({grid_max_x:.1f},{grid_max_y:.1f}) "
             f"extent={grid_max_x - grid_min_x:.1f} x {grid_max_y - grid_min_y:.1f} m")

    return (grid_min_x, grid_min_y), (grid_max_x, grid_max_y)


def _infer_tile_size(locs_x, locs_y):
    """
    Infer tile_size from minimum spacing between unique sorted tile locations.

    Strategy (in priority order):
    1. Compute min positive step from unique sorted X and Y locations
    2. Fall back to M1DC_WORLD_ORIGIN['tile_size_m'] or ['tile_size_m_ref']
    3. Fall back to 1000.0 m (standard 1km CityGML tiles)

    Returns:
        float — tile size in meters
    """
    # Strategy 1: spacing from sorted unique locations
    spacings = []
    for locs in (locs_x, locs_y):
        uniq = sorted(set(locs))
        if len(uniq) >= 2:
            steps = [uniq[i + 1] - uniq[i] for i in range(len(uniq) - 1)]
            pos_steps = [s for s in steps if s > 1.0]  # filter noise
            if pos_steps:
                spacings.append(min(pos_steps))

    if spacings:
        tile_size = min(spacings)
        _fit_log(f"[TERRAIN][FIT][GRID] tile_size inferred from spacing: {tile_size:.1f}m")
        return tile_size

    # Strategy 2: scene property
    try:
        world = bpy.data.objects.get("M1DC_WORLD_ORIGIN")
        if world:
            for key in ("tile_size_m", "tile_size_m_ref"):
                val = world.get(key)
                if val and float(val) > 0:
                    tile_size = float(val)
                    _fit_log(f"[TERRAIN][FIT][GRID] tile_size from WORLD_ORIGIN['{key}']: {tile_size:.1f}m")
                    return tile_size
    except Exception:
        pass

    # Strategy 3: default
    _fit_log("[TERRAIN][FIT][GRID] tile_size fallback: 1000.0m (default)")
    return 1000.0


# ---------------------------------------------------------------------------
# Core fit function
# ---------------------------------------------------------------------------

def fit_terrain_to_citygml(terrain_obj, citygml_objs, eps=0.05, rgb_obj=None):
    """
    Scale and position terrain to exactly match the CityGML tile-grid extent.

    CityGML extent is computed from tile locations + tile_size (the full grid),
    NOT from mesh-vertex bounding boxes (which only cover building geometry).
    Terrain bbox is measured via obj.data.vertices + matrix_world to avoid
    stale bound_box caching.

    This is a deterministic, idempotent operation. The terrain's XY footprint
    will match the CityGML tile grid after this call.

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
    if terrain_obj is None or terrain_obj.type != 'MESH':
        raise ValueError("[TERRAIN][FIT] terrain_obj is None or not a MESH")
    if not citygml_objs:
        raise ValueError("[TERRAIN][FIT] citygml_objs is empty — need CityGML tiles")

    # Filter to meshes only
    gml_meshes = [o for o in citygml_objs if o.type == 'MESH']
    if not gml_meshes:
        raise ValueError("[TERRAIN][FIT] No MESH objects in citygml_objs")

    _fit_log("[TERRAIN][FIT] === TERRAIN BBOX-FIT START ===")
    _fit_log(f"[TERRAIN][FIT] terrain={terrain_obj.name} | citygml_tiles={len(gml_meshes)}")

    # ── Step 0a: Neutralize rotation (prevents axis-skewed scaling) ──
    _neutralize_rotation(terrain_obj, log_fn=_fit_log)
    if rgb_obj:
        _neutralize_rotation(rgb_obj, log_fn=_fit_log)

    # ── Step 0b: Check for X/Z axis swap ──
    bpy.context.view_layer.update()
    _pre_min, _pre_max = world_bbox_from_vertices(terrain_obj)
    _pre_span_x = _pre_max[0] - _pre_min[0]
    _pre_span_y = _pre_max[1] - _pre_min[1]
    _pre_span_z = _pre_max[2] - _pre_min[2]
    if _detect_axis_swap(_pre_span_x, _pre_span_y, _pre_span_z, log_fn=_fit_err):
        _fit_err(f"[TERRAIN][FIT] Terrain spans: X={_pre_span_x:.1f} Y={_pre_span_y:.1f} Z={_pre_span_z:.1f}")
        _fit_err("[TERRAIN][FIT] ⚠  Terrain may need a 90° X-rotation before fitting. "
                 "Proceeding anyway — verify result visually.")

    # ── Step 1: Target extent from CityGML tile grid ──
    # Uses tile locations + tile_size (NOT mesh vertex bboxes)
    (tgt_min_x, tgt_min_y), (tgt_max_x, tgt_max_y) = citygml_grid_extent(gml_meshes)
    tgt_w = tgt_max_x - tgt_min_x
    tgt_h = tgt_max_y - tgt_min_y

    info['target_bbox'] = ((tgt_min_x, tgt_min_y), (tgt_max_x, tgt_max_y))
    info['target_size'] = (tgt_w, tgt_h)

    _fit_log(f"[TERRAIN][FIT] CityGML grid extent: min=({tgt_min_x:.2f}, {tgt_min_y:.2f}) "
             f"max=({tgt_max_x:.2f}, {tgt_max_y:.2f})")
    _fit_log(f"[TERRAIN][FIT] CityGML grid size: {tgt_w:.2f} x {tgt_h:.2f} meters")

    if tgt_w <= 0 or tgt_h <= 0:
        raise ValueError(f"[TERRAIN][FIT] CityGML grid extent invalid: {tgt_w:.2f} x {tgt_h:.2f}")

    # ── Step 2: Current terrain bbox (vertex-based) ──
    # Flush depsgraph before measuring
    bpy.context.view_layer.update()

    src_min, src_max = world_bbox_from_vertices(terrain_obj)
    src_w = src_max[0] - src_min[0]
    src_h = src_max[1] - src_min[1]

    info['terrain_bbox_before'] = ((src_min[0], src_min[1]), (src_max[0], src_max[1]))
    info['terrain_size_before'] = (src_w, src_h)

    _fit_log(f"[TERRAIN][FIT] Terrain bbox BEFORE: min=({src_min[0]:.2f}, {src_min[1]:.2f}) "
             f"max=({src_max[0]:.2f}, {src_max[1]:.2f})")
    _fit_log(f"[TERRAIN][FIT] Terrain extent BEFORE: {src_w:.2f} x {src_h:.2f} meters")
    _fit_log(f"[TERRAIN][FIT] Terrain scale BEFORE: ({terrain_obj.scale.x:.6f}, "
             f"{terrain_obj.scale.y:.6f}, {terrain_obj.scale.z:.6f})")

    if src_w <= 1e-6 or src_h <= 1e-6:
        raise ValueError(f"[TERRAIN][FIT] Terrain bbox too small: {src_w:.6f} x {src_h:.6f}")

    # ── Step 3: Compute non-uniform XY scale factors ──
    sx = tgt_w / src_w
    sy = tgt_h / src_h

    info['scale_x'] = sx
    info['scale_y'] = sy

    _fit_log(f"[TERRAIN][FIT] Scale factors: sx={sx:.6f}, sy={sy:.6f}")

    # ── Step 4: Apply scale + BAKE into vertices via transform_apply ──
    # Setting obj.scale alone leaves a "ghost transform" — Blender's
    # bound_box and sometimes even matrix_world @ vertex don't fully
    # reflect non-uniform scale until the depsgraph is evaluated.  The
    # bulletproof approach: set scale, then bake it via
    # bpy.ops.object.transform_apply(scale=True).  After that, scale
    # is back to (1,1,1) and vertex coords ARE physically stretched.
    terrain_obj.scale = (
        terrain_obj.scale.x * sx,
        terrain_obj.scale.y * sy,
        terrain_obj.scale.z,
    )

    _fit_log(f"[TERRAIN][FIT] Terrain scale set: ({terrain_obj.scale.x:.6f}, "
             f"{terrain_obj.scale.y:.6f}, {terrain_obj.scale.z:.6f})")

    # Bake scale into vertices (transform_apply)
    _apply_scale(terrain_obj)
    if rgb_obj:
        rgb_obj.scale = (
            rgb_obj.scale.x * sx,
            rgb_obj.scale.y * sy,
            rgb_obj.scale.z,
        )
        _apply_scale(rgb_obj)

    # After apply, scale is (1,1,1) and vertices are stretched
    bpy.context.view_layer.update()

    _fit_log(f"[TERRAIN][FIT] Terrain scale AFTER apply: ({terrain_obj.scale.x:.6f}, "
             f"{terrain_obj.scale.y:.6f}, {terrain_obj.scale.z:.6f})")

    # ── Step 5: Translate min corner to match CityGML grid min corner ──
    # Remeasure via vertices — now that scale is baked, local == world for XY
    src_min2, _ = world_bbox_from_vertices(terrain_obj)
    dx = tgt_min_x - src_min2[0]
    dy = tgt_min_y - src_min2[1]

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
    # Final measurement via evaluated depsgraph to ensure we read the
    # fully-committed vertex positions.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    terrain_eval = terrain_obj.evaluated_get(depsgraph)
    src_min3, src_max3 = world_bbox_from_vertices(terrain_eval)
    src_w_after = src_max3[0] - src_min3[0]
    src_h_after = src_max3[1] - src_min3[1]

    info['terrain_bbox_after'] = ((src_min3[0], src_min3[1]), (src_max3[0], src_max3[1]))
    info['terrain_size_after'] = (src_w_after, src_h_after)

    err_min_x = abs(src_min3[0] - tgt_min_x)
    err_min_y = abs(src_min3[1] - tgt_min_y)
    err_max_x = abs(src_max3[0] - tgt_max_x)
    err_max_y = abs(src_max3[1] - tgt_max_y)
    err = max(err_min_x, err_min_y, err_max_x, err_max_y)

    info['error'] = err

    _fit_log(f"[TERRAIN][FIT] Terrain bbox AFTER: min=({src_min3[0]:.3f}, {src_min3[1]:.3f}) "
             f"max=({src_max3[0]:.3f}, {src_max3[1]:.3f})")
    _fit_log(f"[TERRAIN][FIT] Terrain extent AFTER: {src_w_after:.2f} x {src_h_after:.2f} meters")
    _fit_log(f"[TERRAIN][FIT] Corner errors: minX={err_min_x:.4f} minY={err_min_y:.4f} "
             f"maxX={err_max_x:.4f} maxY={err_max_y:.4f}")
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
    _fit_log(f"[TERRAIN][FIT][OK] err={err:.3f}m scale_applied=({sx:.6f},{sy:.6f}) "
             f"translate=({dx:.3f},{dy:.3f}) scale_baked=True")
    _fit_log(f"[TERRAIN][FIT] terrain.location=({terrain_obj.location.x:.3f}, "
             f"{terrain_obj.location.y:.3f}, {terrain_obj.location.z:.3f})")
    _fit_log(f"[TERRAIN][FIT] terrain.scale=({terrain_obj.scale.x:.6f}, "
             f"{terrain_obj.scale.y:.6f}, {terrain_obj.scale.z:.6f})  (should be ~1,1,1 after apply)")
    _fit_log(f"[TERRAIN][FIT] target  = {tgt_w:.1f} x {tgt_h:.1f} m")
    _fit_log(f"[TERRAIN][FIT] terrain = {src_w_after:.1f} x {src_h_after:.1f} m")
    _fit_log("[TERRAIN][FIT] === TERRAIN BBOX-FIT COMPLETE ===")

    return info


def _apply_scale(obj):
    """
    Bake the object's scale into its mesh vertex data.

    After this call obj.scale == (1,1,1) and vertex coords are physically
    stretched to match the previous scale.  This eliminates "ghost
    transform" issues where Blender's bound_box / matrix_world don't
    fully reflect a non-uniform scale.
    """
    import bpy

    # Save current selection/active state
    prev_active = bpy.context.view_layer.objects.active
    prev_selected = [o for o in bpy.context.selected_objects]

    # Deselect all, then select + activate target
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Bake scale into vertices
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    _fit_log(f"[TERRAIN][FIT] transform_apply(scale) on '{obj.name}' → "
             f"scale now ({obj.scale.x:.4f}, {obj.scale.y:.4f}, {obj.scale.z:.4f})")

    # Restore selection state
    obj.select_set(False)
    for o in prev_selected:
        try:
            o.select_set(True)
        except Exception:
            pass
    bpy.context.view_layer.objects.active = prev_active
