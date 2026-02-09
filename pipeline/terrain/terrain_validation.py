"""
Terrain Validation & Auto-Correction Module

Purpose:
    Validate scene consistency BEFORE/AFTER terrain import:
    - Scene units (METRIC, scale=1.0)
    - Terrain scale (anisotropic detection)
    - CityGML Z-offset (local Z ~0 vs terrain Z >100m)
    - Geo-referencing state (BlenderGIS/Geoscene)

Decision outputs:
    - CLEAN: No corrections needed
    - FIX_SCALE_Z: Apply terrain scale fix + CityGML Z offset
    - BLOCKED: Critical issues (missing objects, wrong units)

Findings Context (Aachen Dataset):
    - Terrain was anisotropic: scale=(0.8183, 0.7145, 1.0) → caused XY drift
    - CityGML had z_min~0, terrain had z_median~239m → Z mismatch
    - BlenderGIS Web Mercator caused nonlinear drift (distance-dependent)
    - Solution: Force terrain scale=(1,1,1) + apply scale, shift CityGML Z by dz

NOT included (future/optional):
    - XY fine positioning (MinCorner/Center match) - helpers provided for debug
"""

import statistics
from typing import Optional, List, Tuple, Dict

try:
    import bpy
    from mathutils import Vector
except ImportError:
    pass

from ...utils.logging_system import log_info, log_warn, log_error

# Constants
CITYGML_COLLECTION = "CITYGML_TILES"
TERRAIN_DEM_NAME = "dem_merged"
TERRAIN_RGB_NAME = "rgb_merged"
ANISOTROPIC_TOLERANCE = 1e-4
Z_THRESHOLD_METERS = 50.0  # If dz > 50m AND gml_minZ ~ 0, assume Z mismatch


# ============================================================================
# HELPER FUNCTIONS: Geometry & Measurements
# ============================================================================

def bbox_world(obj):
    """
    Transform object's bounding box to world space.

    Args:
        obj: Blender object with bound_box

    Returns:
        List of world-space Vector coordinates (8 corners)
    """
    if not obj or not hasattr(obj, 'bound_box'):
        return []
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def extent_xy_minmax(obj) -> Tuple[float, float, float, float]:
    """
    Calculate XY extent (min/max) of object in world space.

    Args:
        obj: Blender object

    Returns:
        Tuple (min_x, max_x, min_y, max_y)
    """
    bb = bbox_world(obj)
    if not bb:
        return (0.0, 0.0, 0.0, 0.0)

    xs = [v.x for v in bb]
    ys = [v.y for v in bb]

    return (min(xs), max(xs), min(ys), max(ys))


def extent_xy(obj) -> Tuple[float, float]:
    """
    Calculate XY extent (width, height) of object in world space.

    Args:
        obj: Blender object

    Returns:
        Tuple (width, height) in meters
    """
    min_x, max_x, min_y, max_y = extent_xy_minmax(obj)
    return (max_x - min_x, max_y - min_y)


def median_bbox_z(obj) -> Optional[float]:
    """
    Calculate median Z coordinate from object's bounding box (world space).

    Args:
        obj: Blender object

    Returns:
        Median Z value in meters, or None if invalid
    """
    bb = bbox_world(obj)
    if not bb:
        return None

    zs = [v.z for v in bb]
    return statistics.median(zs) if zs else None


def median_bbox_z_many(objs: List) -> Optional[float]:
    """
    Calculate median Z coordinate across many objects (world space).

    Args:
        objs: List of Blender objects

    Returns:
        Median Z value across all objects, or None if no valid data
    """
    all_zs = []
    for obj in objs:
        if obj.type != 'MESH' or not hasattr(obj, 'bound_box'):
            continue
        bb = bbox_world(obj)
        all_zs.extend([v.z for v in bb])

    return statistics.median(all_zs) if all_zs else None


def is_anisotropic_scale(scale, tol=ANISOTROPIC_TOLERANCE) -> bool:
    """
    Check if scale is anisotropic (non-uniform) or not (1,1,1).

    Args:
        scale: Vector or tuple (sx, sy, sz)
        tol: Tolerance for float comparison

    Returns:
        True if scale is anisotropic or not uniform 1.0
    """
    sx, sy, sz = scale[0], scale[1], scale[2]

    # Check if any axis differs from 1.0
    not_one = (abs(sx - 1.0) > tol) or (abs(sy - 1.0) > tol) or (abs(sz - 1.0) > tol)

    # Check if axes differ from each other (anisotropic)
    aniso = (abs(sx - sy) > tol) or (abs(sy - sz) > tol) or (abs(sx - sz) > tol)

    return not_one or aniso


# ============================================================================
# SCENE & OBJECT DISCOVERY
# ============================================================================

def get_terrain_object():
    """
    Get terrain object with fallback detection strategy.
    
    Priority (in order):
    1. Any object with custom property m1dc_role="terrain"
    2. First mesh in TERRAIN collection (if exists)
    3. Legacy names: dem_merged (preferred) or rgb_merged (fallback)
    4. None if not found

    Returns:
        Blender Object or None
    """
    # Strategy 1: Property-based detection (m1dc_role="terrain")
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.get("m1dc_role") == "terrain":
            log_info(f"[VALIDATION] Terrain found via m1dc_role property: {obj.name}")
            return obj
    
    # Strategy 2: TERRAIN collection
    terrain_col = bpy.data.collections.get("TERRAIN")
    if terrain_col:
        for obj in terrain_col.objects:
            if obj.type == 'MESH':
                log_info(f"[VALIDATION] Terrain found in TERRAIN collection: {obj.name}")
                return obj
    
    # Strategy 3: Legacy hardcoded names
    terrain = bpy.data.objects.get(TERRAIN_DEM_NAME)
    if terrain:
        log_info(f"[VALIDATION] Terrain found via legacy name: {terrain.name}")
        return terrain

    terrain = bpy.data.objects.get(TERRAIN_RGB_NAME)
    if terrain:
        log_info(f"[VALIDATION] Terrain found via legacy RGB name: {terrain.name}")
        return terrain
    
    # Not found
    log_warn(f"[VALIDATION] Terrain not found (searched: m1dc_role='terrain', TERRAIN collection, legacy names)")
    return None


def collect_gml_objects() -> List:
    """
    Collect all CityGML mesh objects from CITYGML_TILES collection.

    Primary: CITYGML_TILES collection
    Fallback: Name contains "lod", "gml", or "LoD2_"

    Returns:
        List of mesh objects
    """
    col = bpy.data.collections.get(CITYGML_COLLECTION)
    if col:
        return [o for o in col.all_objects if o.type == 'MESH']

    # Fallback: search by name pattern
    log_warn(f"[Validation] Collection '{CITYGML_COLLECTION}' not found, using name fallback")
    objs = []
    for o in bpy.data.objects:
        if o.type != 'MESH':
            continue
        name_lower = o.name.lower()
        if any(kw in name_lower for kw in ["lod", "gml", "lod2_"]):
            objs.append(o)

    return objs


# ============================================================================
# SCENE VALIDATION
# ============================================================================

def validate_scene_units() -> Tuple[bool, Dict]:
    """
    Validate that scene units are METRIC and scale_length=1.0.

    Returns:
        (is_valid, diagnostics_dict)
    """
    scene = bpy.context.scene
    unit_system = scene.unit_settings.system
    scale_length = scene.unit_settings.scale_length

    is_metric = (unit_system == 'METRIC')
    is_scale_one = (abs(scale_length - 1.0) < 1e-6)
    is_valid = is_metric and is_scale_one

    diag = {
        "unit_system": unit_system,
        "scale_length": scale_length,
        "is_valid": is_valid,
    }

    return (is_valid, diag)


# ============================================================================
# GEO-REFERENCING ENFORCEMENT
# ============================================================================

def enforce_disable_georef() -> Dict:
    """
    Best-effort: Disable BlenderGIS/Geoscene georeferencing if available.

    This prevents Web Mercator or other nonlinear projections from causing
    distance-dependent drift in XY coordinates.

    Returns:
        Diagnostics dict with 'georef_disabled' status
    """
    diag = {
        "georef_module_available": False,
        "georef_disabled": False,
        "georef_note": "Module not found (OK for pipeline)",
    }

    try:
        # Attempt to import BlenderGIS/Geoscene modules
        # Note: This is best-effort and may fail if addon not installed
        scene = bpy.context.scene

        # Check for common BlenderGIS/Geoscene properties
        if hasattr(scene, 'BL_geodata'):
            # BlenderGIS style
            if hasattr(scene.BL_geodata, 'georefEnabled'):
                diag["georef_module_available"] = True
                if scene.BL_geodata.georefEnabled:
                    scene.BL_geodata.georefEnabled = False
                    diag["georef_disabled"] = True
                    diag["georef_note"] = "BlenderGIS georef was enabled, now disabled"
                    log_info("[Validation] BlenderGIS georeferencing disabled for pipeline")
                else:
                    diag["georef_note"] = "BlenderGIS georef already disabled"

        elif hasattr(scene, 'geoscene'):
            # Geoscene style
            diag["georef_module_available"] = True
            if hasattr(scene.geoscene, 'isGeoref'):
                if scene.geoscene.isGeoref:
                    scene.geoscene.isGeoref = False
                    diag["georef_disabled"] = True
                    diag["georef_note"] = "Geoscene georef was enabled, now disabled"
                    log_info("[Validation] Geoscene georeferencing disabled for pipeline")
                else:
                    diag["georef_note"] = "Geoscene georef already disabled"

        else:
            # No georef module detected
            diag["georef_note"] = "No georef module detected (BlenderGIS/Geoscene)"
            log_info("[Validation] No georeferencing module found (OK for pipeline)")

    except Exception as ex:
        diag["georef_note"] = f"Error checking georef: {ex}"
        log_warn(f"[Validation] Could not check/disable georef: {ex}")

    return diag


# ============================================================================
# VALIDATION & DECISION LOGIC
# ============================================================================

def validate_and_decide() -> Tuple[str, Dict]:
    """
    Main validation and decision function.

    Validates:
        - Scene units (METRIC, scale=1.0)
        - Terrain and CityGML objects present
        - Terrain scale (anisotropic detection)
        - Z-offset between terrain and CityGML
        - Extent plausibility

    Returns:
        (decision, diagnostics)

        decision: "CLEAN" | "FIX_SCALE_Z" | "BLOCKED"
        diagnostics: Dict with all measured values and flags
    """
    log_info("[VALIDATION] ═══════════════════════════════════")
    log_info("[VALIDATION] PIPELINE VALIDATION START")
    log_info("[VALIDATION] ═══════════════════════════════════")

    diag = {}

    # 1. Validate scene units
    units_ok, units_diag = validate_scene_units()
    diag.update(units_diag)

    log_info(f"[VALIDATION] unit_system={units_diag['unit_system']} scale_length={units_diag['scale_length']}")

    if not units_ok:
        log_error("[VALIDATION] Scene units invalid! Must be METRIC with scale_length=1.0")
        diag["decision"] = "BLOCKED"
        diag["reason"] = "Scene units wrong (not METRIC or scale_length != 1.0)"
        return ("BLOCKED", diag)

    # 2. Get terrain object
    terrain = get_terrain_object()
    if not terrain:
        log_error(f"[VALIDATION] Terrain not found (searched: {TERRAIN_DEM_NAME}, {TERRAIN_RGB_NAME})")
        diag["terrain_found"] = False
        diag["decision"] = "BLOCKED"
        diag["reason"] = "Terrain object missing"
        return ("BLOCKED", diag)

    diag["terrain_found"] = True
    diag["terrain_name"] = terrain.name
    diag["terrain_scale"] = tuple(terrain.scale)

    terrain_w, terrain_h = extent_xy(terrain)
    diag["terrain_extent_wh"] = (terrain_w, terrain_h)

    log_info(f"[VALIDATION] terrain={terrain.name} scale={diag['terrain_scale']} extent_wh=({terrain_w:.2f}m, {terrain_h:.2f}m)")

    # 3. Get CityGML objects
    gml_objs = collect_gml_objects()
    if not gml_objs:
        log_error("[VALIDATION] No CityGML objects found")
        diag["gml_count"] = 0
        diag["decision"] = "BLOCKED"
        diag["reason"] = "CityGML objects missing"
        return ("BLOCKED", diag)

    diag["gml_count"] = len(gml_objs)

    # Compute GML extent
    gml_minx = gml_miny = 1e18
    gml_maxx = gml_maxy = -1e18
    for o in gml_objs:
        minx, maxx, miny, maxy = extent_xy_minmax(o)
        gml_minx = min(gml_minx, minx)
        gml_maxx = max(gml_maxx, maxx)
        gml_miny = min(gml_miny, miny)
        gml_maxy = max(gml_maxy, maxy)

    gml_w = gml_maxx - gml_minx
    gml_h = gml_maxy - gml_miny
    diag["gml_extent_wh"] = (gml_w, gml_h)

    # Compute Z statistics
    gml_all_zs = []
    for o in gml_objs:
        bb = bbox_world(o)
        gml_all_zs.extend([v.z for v in bb])

    gml_minZ = min(gml_all_zs) if gml_all_zs else None
    gml_medianZ = statistics.median(gml_all_zs) if gml_all_zs else None

    terrain_bb = bbox_world(terrain)
    terrain_zs = [v.z for v in terrain_bb]
    terrain_medianZ = statistics.median(terrain_zs) if terrain_zs else None

    diag["gml_minZ"] = gml_minZ
    diag["gml_medianZ"] = gml_medianZ
    diag["terrain_medianZ"] = terrain_medianZ

    log_info(f"[VALIDATION] gml_count={len(gml_objs)} extent_wh=({gml_w:.2f}m, {gml_h:.2f}m)")
    log_info(f"[VALIDATION] gml_minZ={gml_minZ:.2f}m gml_medianZ={gml_medianZ:.2f}m")
    log_info(f"[VALIDATION] terrain_medianZ={terrain_medianZ:.2f}m")

    # 4. Compute dz (potential Z offset)
    if terrain_medianZ is not None and gml_medianZ is not None:
        dz = terrain_medianZ - gml_medianZ
        diag["dz"] = dz
        log_info(f"[VALIDATION] dz (terrain - gml) = {dz:.2f}m")
    else:
        diag["dz"] = None
        log_warn("[VALIDATION] Cannot compute dz (missing Z data)")

    # 4b. Compute XY intersection and coverage
    t_minx, t_maxx, t_miny, t_maxy = extent_xy_minmax(terrain)
    diag["dem_bbox_xy"] = ((t_minx, t_miny), (t_maxx, t_maxy))
    diag["gml_bbox_xy"] = ((gml_minx, gml_miny), (gml_maxx, gml_maxy))

    # Check XY intersection
    def _bbox_intersects(a_min, a_max, b_min, b_max):
        """Check if two 2D bboxes intersect. a/b are (x,y) tuples."""
        return not (a_max[0] < b_min[0] or a_min[0] > b_max[0] or
                    a_max[1] < b_min[1] or a_min[1] > b_max[1])

    intersection_xy = _bbox_intersects(
        (t_minx, t_miny), (t_maxx, t_maxy),
        (gml_minx, gml_miny), (gml_maxx, gml_maxy)
    )
    diag["intersection_xy"] = intersection_xy

    # Compute center distance
    dem_cx = (t_minx + t_maxx) / 2.0
    dem_cy = (t_miny + t_maxy) / 2.0
    gml_cx = (gml_minx + gml_maxx) / 2.0
    gml_cy = (gml_miny + gml_maxy) / 2.0
    center_dist_xy = ((dem_cx - gml_cx)**2 + (dem_cy - gml_cy)**2)**0.5
    diag["center_dist_xy"] = center_dist_xy

    # Compute coverage ratios
    cover_x = terrain_w / max(gml_w, 1e-9)
    cover_y = terrain_h / max(gml_h, 1e-9)
    diag["cover_x"] = cover_x
    diag["cover_y"] = cover_y

    log_info(f"[VALIDATION] DEM extent_wh=({terrain_w:.2f}m, {terrain_h:.2f}m) vs gml_extent_wh=({gml_w:.2f}m, {gml_h:.2f}m)")
    log_info(f"[VALIDATION] INTERSECTION_XY: {'YES' if intersection_xy else 'NO'}")
    log_info(f"[VALIDATION] center_dist_xy={center_dist_xy:.2f}m")
    log_info(f"[VALIDATION] coverage: cover_x={cover_x:.3f}, cover_y={cover_y:.3f}")

    # HARD FAIL: No XY overlap
    if not intersection_xy:
        log_error(f"[VALIDATION] FAIL: No XY overlap between DEM and CityGML!")
        diag["decision"] = "FAIL"
        diag["reason"] = f"No XY overlap: DEM vs CityGML (center_dist={center_dist_xy:.1f}m)"
        return ("FAIL", diag)

    # HARD FAIL: DEM is implausibly small vs CityGML
    MIN_COVERAGE = 0.6
    if cover_x < MIN_COVERAGE or cover_y < MIN_COVERAGE:
        log_error(f"[VALIDATION] FAIL: DEM too small vs CityGML (cover_x={cover_x:.3f}, cover_y={cover_y:.3f})")
        diag["decision"] = "FAIL"
        diag["reason"] = f"DEM too small vs CityGML (cover_x={cover_x:.3f}, cover_y={cover_y:.3f}, min={MIN_COVERAGE})"
        return ("FAIL", diag)

    # 5. Enforce georef disable (always, even if CLEAN)
    georef_diag = enforce_disable_georef()
    diag.update(georef_diag)

    # 6. Decision logic
    terrain_anisotropic = is_anisotropic_scale(terrain.scale)
    diag["anisotropic_scale"] = terrain_anisotropic

    # Check if GML has local Z (minZ ~ 0) and terrain has high Z
    is_gml_localZ = (gml_minZ is not None and abs(gml_minZ) < 10.0 and gml_medianZ is not None and gml_medianZ < 100.0)
    is_terrain_highZ = (terrain_medianZ is not None and terrain_medianZ > Z_THRESHOLD_METERS)
    needs_z_shift = (is_gml_localZ and is_terrain_highZ and diag.get("dz", 0) > Z_THRESHOLD_METERS)

    diag["needs_z_shift"] = needs_z_shift

    if terrain_anisotropic or needs_z_shift:
        decision = "FIX_SCALE_Z"
        diag["decision"] = decision
        diag["reason"] = []
        if terrain_anisotropic:
            diag["reason"].append(f"Terrain scale anisotropic: {diag['terrain_scale']}")
        if needs_z_shift:
            diag["reason"].append(f"Z mismatch detected: dz={diag.get('dz', 0):.2f}m")
        diag["reason"] = " | ".join(diag["reason"])
    else:
        decision = "CLEAN"
        diag["decision"] = decision
        diag["reason"] = "No corrections needed"

    log_info(f"[VALIDATION] ═══════════════════════════════════")
    log_info(f"[VALIDATION] decision={decision}")
    log_info(f"[VALIDATION] reason={diag['reason']}")
    log_info(f"[VALIDATION] ═══════════════════════════════════")
    
    # [PHASE 6] Acceptance signal for terrain validation
    print(f"[ACCEPT] terrain_validation_ok=True decision={decision}")

    return (decision, diag)


# ============================================================================
# CORRECTION ACTIONS
# ============================================================================

def apply_terrain_scale_fix(terrain):
    """
    Apply terrain scale fix: set scale to (1,1,1) and apply scale transform.

    Only applies if scale is anisotropic or not (1,1,1).

    Args:
        terrain: Blender terrain object

    Side effects:
        - Modifies terrain.scale
        - Applies scale transform (baked into mesh)
        - Logs actions
    """
    if not terrain:
        log_warn("[Validation] apply_terrain_scale_fix: terrain is None")
        return

    # Check if fix needed
    if not is_anisotropic_scale(terrain.scale):
        log_info(f"[Validation] Terrain scale already uniform (1,1,1), skipping scale fix")
        return

    log_info(f"[Validation] ╔═══════════════════════════════════╗")
    log_info(f"[Validation] ║ TERRAIN SCALE FIX                 ║")
    log_info(f"[Validation] ╚═══════════════════════════════════╝")
    log_info(f"[Validation] Before: {terrain.name} scale={tuple(terrain.scale)}")

    # Deselect all, select terrain, set active
    bpy.ops.object.select_all(action='DESELECT')
    terrain.select_set(True)
    bpy.context.view_layer.objects.active = terrain

    # Set scale to (1,1,1)
    terrain.scale = (1.0, 1.0, 1.0)

    # Apply scale transform
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    log_info(f"[Validation] After: {terrain.name} scale={tuple(terrain.scale)} (applied)")
    log_info(f"[Validation] Terrain scale fix complete")


def apply_gml_z_offset(gml_objs: List, dz: float):
    """
    Apply Z offset to all CityGML objects.

    Args:
        gml_objs: List of CityGML mesh objects
        dz: Z offset to apply (meters)

    Side effects:
        - Modifies location.z for all objects
        - Logs actions
    """
    if not gml_objs:
        log_warn("[Validation] apply_gml_z_offset: no objects provided")
        return

    if abs(dz) < 1e-6:
        log_info(f"[Validation] Z offset negligible (dz={dz:.6f}m), skipping")
        return

    log_info(f"[Validation] ╔═══════════════════════════════════╗")
    log_info(f"[Validation] ║ CITYGML Z-OFFSET                  ║")
    log_info(f"[Validation] ╚═══════════════════════════════════╝")
    log_info(f"[Validation] Applying dz={dz:.2f}m to {len(gml_objs)} objects")

    count = 0
    for obj in gml_objs:
        if obj.type != 'MESH':
            continue

        obj.location.z += dz
        count += 1

    log_info(f"[Validation] Applied Z offset to {count} buildings")


# ============================================================================
# OPTIONAL XY POSITIONING HELPERS (for future/debug use)
# ============================================================================

def compute_xy_shift_min_corner(terrain, gml_objs: List) -> Tuple[float, float]:
    """
    Compute XY shift to align terrain min-corner with CityGML min-corner.

    NOT USED IN MAIN PIPELINE (optional debug/future use).

    Args:
        terrain: Terrain object
        gml_objs: List of CityGML objects

    Returns:
        (dx, dy) shift in meters
    """
    if not terrain or not gml_objs:
        return (0.0, 0.0)

    t_minx, t_maxx, t_miny, t_maxy = extent_xy_minmax(terrain)

    g_minx = g_miny = 1e18
    g_maxx = g_maxy = -1e18
    for o in gml_objs:
        minx, maxx, miny, maxy = extent_xy_minmax(o)
        g_minx = min(g_minx, minx)
        g_maxx = max(g_maxx, maxx)
        g_miny = min(g_miny, miny)
        g_maxy = max(g_maxy, maxy)

    dx = g_minx - t_minx
    dy = g_miny - t_miny

    log_info(f"[Debug] XY shift (min-corner match): dx={dx:.2f}m, dy={dy:.2f}m")
    return (dx, dy)


def compute_xy_shift_center(terrain, gml_objs: List) -> Tuple[float, float]:
    """
    Compute XY shift to align terrain center with CityGML center.

    NOT USED IN MAIN PIPELINE (optional debug/future use).

    Args:
        terrain: Terrain object
        gml_objs: List of CityGML objects

    Returns:
        (dx, dy) shift in meters
    """
    if not terrain or not gml_objs:
        return (0.0, 0.0)

    t_minx, t_maxx, t_miny, t_maxy = extent_xy_minmax(terrain)
    t_cx = (t_minx + t_maxx) / 2.0
    t_cy = (t_miny + t_maxy) / 2.0

    g_minx = g_miny = 1e18
    g_maxx = g_maxy = -1e18
    for o in gml_objs:
        minx, maxx, miny, maxy = extent_xy_minmax(o)
        g_minx = min(g_minx, minx)
        g_maxx = max(g_maxx, maxx)
        g_miny = min(g_miny, miny)
        g_maxy = max(g_maxy, maxy)

    g_cx = (g_minx + g_maxx) / 2.0
    g_cy = (g_miny + g_maxy) / 2.0

    dx = g_cx - t_cx
    dy = g_cy - t_cy

    log_info(f"[Debug] XY shift (center match): dx={dx:.2f}m, dy={dy:.2f}m")
    return (dx, dy)


def apply_terrain_xy_offset(terrain, dx: float, dy: float):
    """
    Apply XY offset to terrain object to align with CityGML.

    OPTION A: Terrain recenters to CityGML local frame.

    Args:
        terrain: Terrain object
        dx: X offset to apply (meters)
        dy: Y offset to apply (meters)

    Side effects:
        - Modifies terrain.location.x and terrain.location.y
        - Logs actions
    """
    if not terrain:
        log_warn("[Validation] apply_terrain_xy_offset: terrain is None")
        return

    # Skip if negligible shift
    if abs(dx) < 0.01 and abs(dy) < 0.01:
        log_info(f"[Validation] XY offset negligible (dx={dx:.3f}m, dy={dy:.3f}m), skipping")
        return

    log_info(f"[Validation] ╔═══════════════════════════════════╗")
    log_info(f"[Validation] ║ TERRAIN XY-OFFSET (ALIGNMENT)     ║")
    log_info(f"[Validation] ╚═══════════════════════════════════╝")
    log_info(f"[Validation] Before: {terrain.name} location=({terrain.location.x:.2f}, {terrain.location.y:.2f}, {terrain.location.z:.2f})")
    log_info(f"[Validation] Applying delta: dx={dx:.2f}m, dy={dy:.2f}m")

    # Apply offset
    terrain.location.x += dx
    terrain.location.y += dy

    # CRITICAL: Update depsgraph so bound_box reflects new location
    # Without this, subsequent bbox queries return stale data
    bpy.context.view_layer.update()

    log_info(f"[Validation] After: {terrain.name} location=({terrain.location.x:.2f}, {terrain.location.y:.2f}, {terrain.location.z:.2f})")
    log_info(f"[Validation] Terrain XY alignment complete")


def log_alignment_diagnostics(terrain, gml_objs: List) -> Dict:
    """
    Log comprehensive diagnostics for terrain/CityGML spatial alignment.

    This is a FORENSIC function to prove the coordinate frames of both datasets.

    Args:
        terrain: Terrain object
        gml_objs: List of CityGML objects

    Returns:
        Dict with diagnostic info:
            terrain_loc: terrain.location as tuple
            terrain_bbox_min/max: world-space bbox corners
            terrain_center_xy: (cx, cy) in world space
            gml_bbox_min/max: combined bbox corners
            gml_center_xy: (cx, cy) in world space
            delta_xy: (dx, dy) shift needed
            center_dist_xy: distance between centers
    """
    diag = {}

    log_info("[ALIGNMENT] ═════════════════════════════════════════════")
    log_info("[ALIGNMENT] TERRAIN ↔ CITYGML ALIGNMENT DIAGNOSTICS")
    log_info("[ALIGNMENT] ═════════════════════════════════════════════")

    if not terrain:
        log_error("[ALIGNMENT] Terrain object is None!")
        diag["error"] = "terrain_missing"
        return diag

    # ──── TERRAIN FORENSICS ────
    log_info("[ALIGNMENT] ─── TERRAIN FORENSICS ───")
    log_info(f"[ALIGNMENT] terrain.name = {terrain.name}")
    log_info(f"[ALIGNMENT] terrain.location = ({terrain.location.x:.3f}, {terrain.location.y:.3f}, {terrain.location.z:.3f})")
    log_info(f"[ALIGNMENT] terrain.scale = ({terrain.scale.x:.6f}, {terrain.scale.y:.6f}, {terrain.scale.z:.6f})")

    t_minx, t_maxx, t_miny, t_maxy = extent_xy_minmax(terrain)
    t_cx = (t_minx + t_maxx) / 2.0
    t_cy = (t_miny + t_maxy) / 2.0
    t_w = t_maxx - t_minx
    t_h = t_maxy - t_miny

    diag["terrain_loc"] = (terrain.location.x, terrain.location.y, terrain.location.z)
    diag["terrain_bbox_min"] = (t_minx, t_miny)
    diag["terrain_bbox_max"] = (t_maxx, t_maxy)
    diag["terrain_center_xy"] = (t_cx, t_cy)
    diag["terrain_extent_wh"] = (t_w, t_h)

    log_info(f"[ALIGNMENT] terrain WORLD bbox_min = ({t_minx:.2f}, {t_miny:.2f})")
    log_info(f"[ALIGNMENT] terrain WORLD bbox_max = ({t_maxx:.2f}, {t_maxy:.2f})")
    log_info(f"[ALIGNMENT] terrain WORLD center = ({t_cx:.2f}, {t_cy:.2f})")
    log_info(f"[ALIGNMENT] terrain WORLD extent = ({t_w:.2f}m x {t_h:.2f}m)")

    if not gml_objs:
        log_error("[ALIGNMENT] No CityGML objects provided!")
        diag["error"] = "gml_missing"
        return diag

    # ──── CITYGML FORENSICS ────
    log_info("[ALIGNMENT] ─── CITYGML FORENSICS ───")
    log_info(f"[ALIGNMENT] gml_count = {len(gml_objs)}")

    # Sample first tile
    sample = gml_objs[0]
    log_info(f"[ALIGNMENT] sample_tile.name = {sample.name}")
    log_info(f"[ALIGNMENT] sample_tile.location = ({sample.location.x:.3f}, {sample.location.y:.3f}, {sample.location.z:.3f})")

    # Compute combined GML bbox
    g_minx = g_miny = 1e18
    g_maxx = g_maxy = -1e18
    for o in gml_objs:
        minx, maxx, miny, maxy = extent_xy_minmax(o)
        g_minx = min(g_minx, minx)
        g_maxx = max(g_maxx, maxx)
        g_miny = min(g_miny, miny)
        g_maxy = max(g_maxy, maxy)

    g_cx = (g_minx + g_maxx) / 2.0
    g_cy = (g_miny + g_maxy) / 2.0
    g_w = g_maxx - g_minx
    g_h = g_maxy - g_miny

    diag["gml_bbox_min"] = (g_minx, g_miny)
    diag["gml_bbox_max"] = (g_maxx, g_maxy)
    diag["gml_center_xy"] = (g_cx, g_cy)
    diag["gml_extent_wh"] = (g_w, g_h)

    log_info(f"[ALIGNMENT] gml WORLD bbox_min = ({g_minx:.2f}, {g_miny:.2f})")
    log_info(f"[ALIGNMENT] gml WORLD bbox_max = ({g_maxx:.2f}, {g_maxy:.2f})")
    log_info(f"[ALIGNMENT] gml WORLD center = ({g_cx:.2f}, {g_cy:.2f})")
    log_info(f"[ALIGNMENT] gml WORLD extent = ({g_w:.2f}m x {g_h:.2f}m)")

    # ──── COMPUTE DELTA ────
    log_info("[ALIGNMENT] ─── DELTA COMPUTATION ───")
    dx = g_cx - t_cx
    dy = g_cy - t_cy
    center_dist = (dx**2 + dy**2)**0.5

    diag["delta_xy"] = (dx, dy)
    diag["center_dist_xy"] = center_dist

    log_info(f"[ALIGNMENT] delta_xy = (dx={dx:.2f}m, dy={dy:.2f}m)")
    log_info(f"[ALIGNMENT] center_dist = {center_dist:.2f}m")

    # Verdict
    if center_dist < 10.0:
        log_info("[ALIGNMENT] VERDICT: ALIGNED (centers within 10m)")
        diag["aligned"] = True
    else:
        log_warn(f"[ALIGNMENT] VERDICT: MISALIGNED (centers {center_dist:.2f}m apart)")
        diag["aligned"] = False

    log_info("[ALIGNMENT] ═════════════════════════════════════════════")

    return diag


# ============================================================================
# PREPARED TERRAIN DATASET VALIDATION (NEW - Phase 1)
# ============================================================================

def validate_prepared_terrain_dataset(terrain_dir) -> Dict:
    """
    Validate a prepared terrain dataset (external pre-processing workflow).

    Expected structure:
        Terrain/
          DGM_Tiles/
            dgm1_32_<E>_<N>_1_*.tif
          RGB_Tiles/
            derived/
              dop_rgb_32_<E>_<N>_*m.tif   (preferred, from WCS download)
              OR dop10rgbi_32_<E>_<N>_1_*.tif (fallback, old naming)
            raw/ (IGNORED - may contain JP2/J2W, must NOT be consumed)

    Validation policy:
        - DGM_Tiles/ must exist and contain at least one valid DGM tile
        - RGB_Tiles/derived/ must exist and contain at least one valid RGB tile
        - DGM and RGB must have at least some overlap in tile keys (E_km, N_km)
        - Pipeline MUST ignore RGB_Tiles/raw/ and any JP2/J2W files

    Args:
        terrain_dir: Path to terrain root directory (str or Path-like)

    Returns:
        dict with keys:
            ok (bool): Overall validation pass/fail
            errors (list[str]): Critical errors (prevent import)
            warnings (list[str]): Non-critical warnings
            dgm_count (int): Number of DGM tiles found
            rgb_count (int): Number of RGB derived tiles found
            overlap_count (int): Number of tiles with both DGM and RGB
            dgm_range (dict): E/N min/max for DGM tiles
            rgb_range (dict): E/N min/max for RGB tiles
            missing_rgb_for_dgm_sample (list[tuple]): Sample of DGM tiles missing RGB (max 20)
            missing_dgm_for_rgb_sample (list[tuple]): Sample of RGB tiles missing DGM (max 20)
            rgb_expected_hint (str): Hint for user about RGB preparation
    """
    import re
    from pathlib import Path

    # Normalize input
    terrain_path = Path(terrain_dir) if not isinstance(terrain_dir, Path) else terrain_dir

    result = {
        "ok": False,
        "errors": [],
        "warnings": [],
        "dgm_count": 0,
        "rgb_count": 0,
        "overlap_count": 0,
        "dgm_range": {"e_min": None, "e_max": None, "n_min": None, "n_max": None},
        "rgb_range": {"e_min": None, "e_max": None, "n_min": None, "n_max": None},
        "missing_rgb_for_dgm_sample": [],
        "missing_dgm_for_rgb_sample": [],
        "rgb_expected_hint": "Use RGB_Tiles/derived with 1.0m/pixel GeoTIFF (~1000x1000 per 1km tile). Run prepare_terrain_rgb_wcs.py to download from NRW WCS.",
    }

    log_info("[TerrainValidation] ═══════════════════════════════════")
    log_info("[TerrainValidation] PREPARED TERRAIN DATASET VALIDATION")
    log_info("[TerrainValidation] ═══════════════════════════════════")
    log_info(f"[TerrainValidation] Terrain root: {terrain_path.resolve()}")

    # Check terrain root exists
    if not terrain_path.exists():
        result["errors"].append(f"Terrain root directory does not exist: {terrain_path.resolve()}")
        log_error(f"[TerrainValidation] Terrain root not found: {terrain_path.resolve()}")
        return result

    if not terrain_path.is_dir():
        result["errors"].append(f"Terrain root is not a directory: {terrain_path.resolve()}")
        log_error(f"[TerrainValidation] Terrain root is not a directory: {terrain_path.resolve()}")
        return result

    # Check required subdirectories
    dgm_dir = terrain_path / "DGM_Tiles"
    rgb_derived_dir = terrain_path / "RGB_Tiles" / "derived"
    rgb_raw_dir = terrain_path / "RGB_Tiles" / "raw"

    if not dgm_dir.exists() or not dgm_dir.is_dir():
        result["errors"].append(f"Missing DGM_Tiles/: {dgm_dir.resolve()}")
        log_error(f"[TerrainValidation] Missing DGM_Tiles/: {dgm_dir.resolve()}")
        return result

    if not rgb_derived_dir.exists() or not rgb_derived_dir.is_dir():
        result["errors"].append(f"Missing RGB_Tiles/derived/: {rgb_derived_dir.resolve()}")
        log_error(f"[TerrainValidation] Missing RGB_Tiles/derived/: {rgb_derived_dir.resolve()}")
        result["warnings"].append(result["rgb_expected_hint"])
        return result

    # Warn if raw/ exists (pipeline must ignore it)
    if rgb_raw_dir.exists() and rgb_raw_dir.is_dir():
        raw_files = list(rgb_raw_dir.glob("*"))
        if raw_files:
            result["warnings"].append(
                f"RGB_Tiles/raw/ exists with {len(raw_files)} files. Pipeline will IGNORE this folder (JP2/J2W not supported)."
            )
            log_warn(f"[TerrainValidation] RGB_Tiles/raw/ exists ({len(raw_files)} files) - will be ignored by pipeline")

    # DGM tile regex: dgm1_32_<E>_<N>_1_*.tif
    dgm_pattern = re.compile(r"^dgm1_32_(\d+)_(\d+)_1_.*\.tif$", re.IGNORECASE)

    # RGB tile regex (accept both naming conventions):
    # Preferred: dop_rgb_32_<E>_<N>_*m.tif (from WCS download script)
    # Fallback: dop10rgbi_32_<E>_<N>_1_*.tif (old naming, still acceptable in derived/)
    rgb_pattern_preferred = re.compile(r"^dop_rgb_32_(\d+)_(\d+)_.*m\.tif$", re.IGNORECASE)
    rgb_pattern_fallback = re.compile(r"^dop10rgbi_32_(\d+)_(\d+)_1_.*\.tiff?$", re.IGNORECASE)

    # Scan DGM tiles
    dgm_tiles = {}  # {(E_km, N_km): filename}
    for tif_file in dgm_dir.glob("*.tif"):
        m = dgm_pattern.match(tif_file.name)
        if m:
            e_km = int(m.group(1))
            n_km = int(m.group(2))
            dgm_tiles[(e_km, n_km)] = tif_file.name

    result["dgm_count"] = len(dgm_tiles)
    log_info(f"[TerrainValidation] DGM tiles found: {result['dgm_count']}")

    if result["dgm_count"] == 0:
        result["errors"].append(
            f"No DGM tiles found in {dgm_dir.resolve()}. "
            f"Expected filename pattern: dgm1_32_<E>_<N>_1_*.tif"
        )
        log_error("[TerrainValidation] No DGM tiles found")
        return result

    # Compute DGM range
    dgm_e_vals = sorted({e for e, n in dgm_tiles.keys()})
    dgm_n_vals = sorted({n for e, n in dgm_tiles.keys()})
    result["dgm_range"] = {
        "e_min": dgm_e_vals[0],
        "e_max": dgm_e_vals[-1],
        "n_min": dgm_n_vals[0],
        "n_max": dgm_n_vals[-1],
    }
    log_info(
        f"[TerrainValidation] DGM range: "
        f"E=[{result['dgm_range']['e_min']}..{result['dgm_range']['e_max']}] "
        f"N=[{result['dgm_range']['n_min']}..{result['dgm_range']['n_max']}]"
    )

    # Scan RGB derived tiles
    rgb_tiles = {}  # {(E_km, N_km): filename}
    for tif_file in rgb_derived_dir.glob("*.tif"):
        # Try preferred pattern first
        m = rgb_pattern_preferred.match(tif_file.name)
        if m:
            e_km = int(m.group(1))
            n_km = int(m.group(2))
            rgb_tiles[(e_km, n_km)] = tif_file.name
            continue

        # Try fallback pattern
        m = rgb_pattern_fallback.match(tif_file.name)
        if m:
            e_km = int(m.group(1))
            n_km = int(m.group(2))
            rgb_tiles[(e_km, n_km)] = tif_file.name

    # Also check .tiff extension
    for tif_file in rgb_derived_dir.glob("*.tiff"):
        m = rgb_pattern_fallback.match(tif_file.name)
        if m:
            e_km = int(m.group(1))
            n_km = int(m.group(2))
            if (e_km, n_km) not in rgb_tiles:  # Don't overwrite .tif
                rgb_tiles[(e_km, n_km)] = tif_file.name

    result["rgb_count"] = len(rgb_tiles)
    log_info(f"[TerrainValidation] RGB derived tiles found: {result['rgb_count']}")

    if result["rgb_count"] == 0:
        result["errors"].append(
            f"No RGB tiles found in {rgb_derived_dir.resolve()}. "
            f"Expected patterns: dop_rgb_32_<E>_<N>_*m.tif OR dop10rgbi_32_<E>_<N>_1_*.tif"
        )
        result["warnings"].append(result["rgb_expected_hint"])
        log_error("[TerrainValidation] No RGB derived tiles found")
        return result

    # Compute RGB range
    rgb_e_vals = sorted({e for e, n in rgb_tiles.keys()})
    rgb_n_vals = sorted({n for e, n in rgb_tiles.keys()})
    result["rgb_range"] = {
        "e_min": rgb_e_vals[0],
        "e_max": rgb_e_vals[-1],
        "n_min": rgb_n_vals[0],
        "n_max": rgb_n_vals[-1],
    }
    log_info(
        f"[TerrainValidation] RGB range: "
        f"E=[{result['rgb_range']['e_min']}..{result['rgb_range']['e_max']}] "
        f"N=[{result['rgb_range']['n_min']}..{result['rgb_range']['n_max']}]"
    )

    # Compute overlap
    dgm_keys = set(dgm_tiles.keys())
    rgb_keys = set(rgb_tiles.keys())
    overlap_keys = dgm_keys & rgb_keys
    result["overlap_count"] = len(overlap_keys)

    log_info(f"[TerrainValidation] Overlap tiles (DGM ∩ RGB): {result['overlap_count']}")

    if result["overlap_count"] == 0:
        result["errors"].append(
            "No overlapping tiles found between DGM and RGB. "
            "DGM and RGB tile grids must match (same E_km, N_km values)."
        )
        log_error("[TerrainValidation] No overlap between DGM and RGB tiles")
        return result

    # Compute missing tiles (for diagnostics)
    missing_rgb_for_dgm = sorted(dgm_keys - rgb_keys)
    missing_dgm_for_rgb = sorted(rgb_keys - dgm_keys)

    result["missing_rgb_for_dgm_sample"] = missing_rgb_for_dgm[:20]
    result["missing_dgm_for_rgb_sample"] = missing_dgm_for_rgb[:20]

    if missing_rgb_for_dgm:
        result["warnings"].append(
            f"{len(missing_rgb_for_dgm)} DGM tiles have no matching RGB tile. "
            f"Sample: {result['missing_rgb_for_dgm_sample'][:5]}"
        )
        log_warn(
            f"[TerrainValidation] {len(missing_rgb_for_dgm)} DGM tiles missing RGB. "
            f"Sample: {result['missing_rgb_for_dgm_sample'][:5]}"
        )

    if missing_dgm_for_rgb:
        result["warnings"].append(
            f"{len(missing_dgm_for_rgb)} RGB tiles have no matching DGM tile. "
            f"Sample: {result['missing_dgm_for_rgb_sample'][:5]}"
        )
        log_warn(
            f"[TerrainValidation] {len(missing_dgm_for_rgb)} RGB tiles missing DGM. "
            f"Sample: {result['missing_dgm_for_rgb_sample'][:5]}"
        )

    # Validation PASS
    result["ok"] = True
    log_info("[TerrainValidation] ✓ Validation PASSED")
    log_info(f"[TerrainValidation]   DGM tiles: {result['dgm_count']}")
    log_info(f"[TerrainValidation]   RGB tiles: {result['rgb_count']}")
    log_info(f"[TerrainValidation]   Overlap: {result['overlap_count']}")
    log_info("[TerrainValidation] ═══════════════════════════════════")

    return result
