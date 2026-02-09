"""
Z-Alignment (DHHN / Höhenbezug) for M1_DC_V6 Pipeline

PHASE 4: Vertical alignment of CityGML to DGM terrain

Requirements:
- NO changes to XY (location.x, location.y remain unchanged)
- NO scaling, NO rotation
- ONLY location.z may be modified
- Support GLOBAL_OFFSET and PER_BUILDING_SNAP modes
- Preserve all XY alignment from Phases 1-3

Algorithm:
1. ANALYSIS: Sample buildings, compute ΔZ = Z_building_base - Z_terrain
2. CLASSIFICATION: Determine if ΔZ is constant (global) or variable (per-building)
3. ADJUSTMENT: Apply global offset OR per-building terrain snap
"""

import random
from typing import List, Tuple, Optional

try:
    import bpy
    import bmesh
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
except ImportError:
    pass

from ...utils.logging_system import log_info, log_warn, log_error

# Constants
CITYGML_COLLECTION = "CITYGML_TILES"
TERRAIN_COLLECTION = "M1DC_TERRAIN_DGM"
# DEPRECATED: TERRAIN_OBJECT = "dem_merged"  # Legacy fallback only
TERRAIN_OBJECT_LEGACY_DEM = "dem_merged"
TERRAIN_OBJECT_LEGACY_RGB = "rgb_merged"

# Thresholds
GLOBAL_OFFSET_THRESHOLD = 0.5  # meters - if std < 0.5m, use global offset
SAMPLE_SIZE = 10  # Number of buildings to sample for analysis


def get_building_base_z(obj) -> Optional[float]:
    """
    Get building base Z (minimum Z of bounding box in world space).

    Args:
        obj: CityGML building object

    Returns:
        Minimum Z coordinate in world space, or None if object invalid
    """
    if not obj or obj.type != 'MESH' or not hasattr(obj, 'bound_box'):
        return None

    try:
        coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        z_values = [v.z for v in coords]
        return min(z_values) if z_values else None
    except Exception:
        return None


def get_terrain_object():
    """
    Get terrain object with robust fallback detection strategy.
    
    Priority (in order):
    1. Any object with custom property m1dc_role="terrain"
    2. First mesh in TERRAIN collection (if exists)
    3. Legacy names: dem_merged (preferred) or rgb_merged (fallback)
    4. None if not found

    Returns:
        Blender Object (MESH) or None
    """
    # Strategy 1: Property-based detection (m1dc_role="terrain")
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.get("m1dc_role") == "terrain":
            log_info(f"[Z-Align] Terrain found via m1dc_role property: {obj.name}")
            return obj
    
    # Strategy 2: TERRAIN collection
    terrain_col = bpy.data.collections.get(TERRAIN_COLLECTION)
    if terrain_col:
        for obj in terrain_col.objects:
            if obj.type == 'MESH':
                log_info(f"[Z-Align] Terrain found in TERRAIN collection: {obj.name}")
                return obj
    
    # Strategy 3: Legacy hardcoded names
    terrain = bpy.data.objects.get(TERRAIN_OBJECT_LEGACY_DEM)
    if terrain:
        log_info(f"[Z-Align] Terrain found via legacy DEM name: {terrain.name}")
        return terrain

    terrain = bpy.data.objects.get(TERRAIN_OBJECT_LEGACY_RGB)
    if terrain:
        log_info(f"[Z-Align] Terrain found via legacy RGB name: {terrain.name}")
        return terrain
    
    # Not found
    log_warn(f"[Z-Align] Terrain not found (searched: m1dc_role='terrain', TERRAIN collection, legacy names)")
    return None


def raycast_terrain_at_xy(terrain_obj, x: float, y: float, max_height: float = 10000.0) -> Optional[float]:
    """
    Raycast down from (x, y) to find terrain Z.

    Args:
        terrain_obj: DGM terrain mesh object
        x: World X coordinate
        y: World Y coordinate
        max_height: Starting height for raycast (default 10km above)

    Returns:
        Z coordinate of terrain surface, or None if no hit
    """
    if not terrain_obj or terrain_obj.type != 'MESH':
        return None

    try:
        # Create BVH tree from terrain mesh
        bm = bmesh.new()
        bm.from_mesh(terrain_obj.data)
        bm.transform(terrain_obj.matrix_world)
        bvh = BVHTree.FromBMesh(bm)

        # Raycast straight down
        origin = Vector((x, y, max_height))
        direction = Vector((0, 0, -1))

        location, normal, index, distance = bvh.ray_cast(origin, direction)

        bm.free()

        if location:
            return location.z
        return None
    except Exception as ex:
        log_warn(f"[Z-Align] Raycast failed at ({x:.1f}, {y:.1f}): {ex}")
        return None


def analyze_z_offset(
    citygml_objects: List,
    terrain_obj,
    sample_size: int = SAMPLE_SIZE
) -> Tuple[List[Tuple[str, float, float, float]], dict]:
    """
    Analyze Z offset between CityGML buildings and terrain.

    STEP 1: Pure analysis (no modifications).

    Args:
        citygml_objects: List of CityGML building objects
        terrain_obj: DGM terrain object
        sample_size: Number of buildings to sample

    Returns:
        (samples, statistics)
        samples: [(building_name, z_building, z_terrain, delta_z), ...]
        statistics: {mean, std, min, max, mode}
    """
    if not citygml_objects:
        log_error("[Z-Align] No CityGML objects provided for analysis")
        return [], {}

    if not terrain_obj:
        log_error("[Z-Align] No terrain object provided for analysis")
        return [], {}

    # Sample random buildings
    sample_count = min(sample_size, len(citygml_objects))
    sampled = random.sample(citygml_objects, sample_count)

    log_info(f"[Z-Align] ╔═══════════════════════════════════╗")
    log_info(f"[Z-Align] ║ ANALYSIS START                    ║")
    log_info(f"[Z-Align] ╚═══════════════════════════════════╝")
    log_info(f"[Z-Align] Sampling {sample_count} buildings")

    samples = []
    delta_z_values = []

    for obj in sampled:
        # Get building base Z
        z_building = get_building_base_z(obj)
        if z_building is None:
            continue

        # Get terrain Z at building XY position
        x = obj.matrix_world.translation.x
        y = obj.matrix_world.translation.y
        z_terrain = raycast_terrain_at_xy(terrain_obj, x, y)

        if z_terrain is None:
            log_warn(f"[Z-Align] No terrain hit for {obj.name} at ({x:.1f}, {y:.1f})")
            continue

        # Compute delta
        delta_z = z_building - z_terrain

        samples.append((obj.name, z_building, z_terrain, delta_z))
        delta_z_values.append(delta_z)

        # Log individual sample
        log_info(f"[Z-Align] Building {obj.name} | Z_building={z_building:.2f} | Z_terrain={z_terrain:.2f} | ΔZ={delta_z:.2f}")

    if not delta_z_values:
        log_error("[Z-Align] No valid samples (no terrain hits)")
        return samples, {}

    # Compute statistics
    import statistics as stats

    mean_dz = stats.mean(delta_z_values)
    std_dz = stats.stdev(delta_z_values) if len(delta_z_values) > 1 else 0.0
    min_dz = min(delta_z_values)
    max_dz = max(delta_z_values)

    statistics = {
        "mean": mean_dz,
        "std": std_dz,
        "min": min_dz,
        "max": max_dz,
        "count": len(delta_z_values),
    }

    log_info(f"[Z-Align] ╔═══════════════════════════════════╗")
    log_info(f"[Z-Align] ║ ANALYSIS COMPLETE                 ║")
    log_info(f"[Z-Align] ╚═══════════════════════════════════╝")
    log_info(f"[Z-Align] Building stats:")
    log_info(f"[Z-Align]   mean_ΔZ: {mean_dz:.2f}m")
    log_info(f"[Z-Align]   std_ΔZ: {std_dz:.2f}m")
    log_info(f"[Z-Align]   min_ΔZ: {min_dz:.2f}m")
    log_info(f"[Z-Align]   max_ΔZ: {max_dz:.2f}m")
    log_info(f"[Z-Align]   samples: {len(delta_z_values)}")

    return samples, statistics


def classify_alignment_mode(statistics: dict, threshold: float = GLOBAL_OFFSET_THRESHOLD) -> str:
    """
    Classify alignment mode based on ΔZ statistics.

    STEP 2: Classification.

    Args:
        statistics: Statistics dict from analyze_z_offset
        threshold: Std deviation threshold (meters) for global vs per-building

    Returns:
        "GLOBAL_OFFSET" or "PER_BUILDING_SNAP"
    """
    if not statistics:
        return "UNKNOWN"

    std_dz = statistics.get("std", float('inf'))

    if std_dz < threshold:
        mode = "GLOBAL_OFFSET"
    else:
        mode = "PER_BUILDING_SNAP"

    log_info(f"[Z-Align] ╔═══════════════════════════════════╗")
    log_info(f"[Z-Align] ║ CLASSIFICATION                    ║")
    log_info(f"[Z-Align] ╚═══════════════════════════════════╝")
    log_info(f"[Z-Align] Detected mode: {mode}")
    log_info(f"[Z-Align] Threshold: {threshold:.2f}m")
    log_info(f"[Z-Align] Actual std: {std_dz:.2f}m")

    if mode == "GLOBAL_OFFSET":
        log_info(f"[Z-Align] → ΔZ is consistent across buildings (std < {threshold}m)")
        log_info(f"[Z-Align] → Will apply single global Z offset to all buildings")
    else:
        log_info(f"[Z-Align] → ΔZ varies significantly (std ≥ {threshold}m)")
        log_info(f"[Z-Align] → Will apply per-building terrain snap")

    return mode


def apply_global_z_offset(citygml_objects: List, z_offset: float) -> int:
    """
    Apply global Z offset to all CityGML buildings.

    STEP 3A: Global offset adjustment.

    Args:
        citygml_objects: List of CityGML building objects
        z_offset: Z offset to apply (meters)

    Returns:
        Number of objects adjusted
    """
    log_info(f"[Z-Align] ╔═══════════════════════════════════╗")
    log_info(f"[Z-Align] ║ GLOBAL Z-OFFSET                   ║")
    log_info(f"[Z-Align] ╚═══════════════════════════════════╝")
    log_info(f"[Z-Align] Applying offset: {z_offset:.2f}m to {len(citygml_objects)} buildings")

    count = 0
    for obj in citygml_objects:
        if obj.type != 'MESH':
            continue

        # Store original XY for verification
        original_x = obj.location.x
        original_y = obj.location.y

        # Apply Z offset ONLY
        obj.location.z += z_offset

        # Verify XY unchanged
        assert abs(obj.location.x - original_x) < 1e-6, f"[Z-Align] ERROR: X changed for {obj.name}"
        assert abs(obj.location.y - original_y) < 1e-6, f"[Z-Align] ERROR: Y changed for {obj.name}"

        count += 1

    log_info(f"[Z-Align] Applied global offset to {count} buildings")
    return count


def apply_per_building_snap(citygml_objects: List, terrain_obj) -> int:
    """
    Apply per-building terrain snap.

    STEP 3B: Per-building adjustment.

    Args:
        citygml_objects: List of CityGML building objects
        terrain_obj: DGM terrain object

    Returns:
        Number of objects adjusted
    """
    log_info(f"[Z-Align] ╔═══════════════════════════════════╗")
    log_info(f"[Z-Align] ║ PER-BUILDING TERRAIN SNAP         ║")
    log_info(f"[Z-Align] ╚═══════════════════════════════════╝")
    log_info(f"[Z-Align] Adjusting {len(citygml_objects)} buildings individually")

    count = 0
    skipped = 0

    for obj in citygml_objects:
        if obj.type != 'MESH':
            continue

        # Store original XY for verification
        original_x = obj.location.x
        original_y = obj.location.y

        # Get building base Z
        z_building = get_building_base_z(obj)
        if z_building is None:
            skipped += 1
            continue

        # Raycast to terrain
        x = obj.matrix_world.translation.x
        y = obj.matrix_world.translation.y
        z_terrain = raycast_terrain_at_xy(terrain_obj, x, y)

        if z_terrain is None:
            skipped += 1
            continue

        # Compute and apply adjustment
        delta_z = z_terrain - z_building
        obj.location.z += delta_z

        # Store debug info
        obj["terrain_z"] = z_terrain
        obj["delta_z"] = delta_z

        # Verify XY unchanged
        assert abs(obj.location.x - original_x) < 1e-6, f"[Z-Align] ERROR: X changed for {obj.name}"
        assert abs(obj.location.y - original_y) < 1e-6, f"[Z-Align] ERROR: Y changed for {obj.name}"

        count += 1

    log_info(f"[Z-Align] Adjusted {count} buildings")
    if skipped > 0:
        log_warn(f"[Z-Align] Skipped {skipped} buildings (no terrain hit or invalid geometry)")

    return count


def align_citygml_to_terrain_z(
    threshold: float = GLOBAL_OFFSET_THRESHOLD,
    sample_size: int = SAMPLE_SIZE
) -> Tuple[bool, str, dict]:
    """
    Main Z-alignment workflow.

    PHASE 4 Implementation: Align CityGML buildings to DGM terrain in Z.

    Args:
        threshold: Std threshold for global vs per-building mode
        sample_size: Number of buildings to sample for analysis

    Returns:
        (success, message, statistics)
    """
    # Get collections
    citygml_col = bpy.data.collections.get(CITYGML_COLLECTION)
    if not citygml_col:
        msg = f"[Z-Align] CityGML collection not found: {CITYGML_COLLECTION}"
        log_error(msg)
        return False, msg, {}

    terrain_obj = get_terrain_object()
    if not terrain_obj:
        msg = "[Z-Align] Terrain object not found (checked: m1dc_role property, TERRAIN collection, legacy names dem_merged/rgb_merged)"
        log_error(msg)
        return False, msg, {}

    # Get CityGML buildings
    citygml_objects = [o for o in citygml_col.objects if o.type == 'MESH']
    if not citygml_objects:
        msg = "[Z-Align] No CityGML buildings found"
        log_error(msg)
        return False, msg, {}

    log_info(f"[Z-Align] ═══════════════════════════════════")
    log_info(f"[Z-Align] PHASE 4: Z-ALIGNMENT START")
    log_info(f"[Z-Align] ═══════════════════════════════════")
    log_info(f"[Z-Align] CityGML buildings: {len(citygml_objects)}")
    log_info(f"[Z-Align] Terrain object: {terrain_obj.name}")

    # STEP 1: ANALYSIS
    samples, statistics = analyze_z_offset(citygml_objects, terrain_obj, sample_size)

    if not statistics:
        msg = "[Z-Align] Analysis failed (no valid samples)"
        log_error(msg)
        return False, msg, {}

    # STEP 2: CLASSIFICATION
    mode = classify_alignment_mode(statistics, threshold)

    # STEP 3: ADJUSTMENT
    count = 0
    if mode == "GLOBAL_OFFSET":
        # Apply global offset (negative of mean to lower buildings to terrain)
        z_offset = -statistics["mean"]
        count = apply_global_z_offset(citygml_objects, z_offset)
        statistics["z_offset_applied"] = z_offset
    elif mode == "PER_BUILDING_SNAP":
        count = apply_per_building_snap(citygml_objects, terrain_obj)
        statistics["z_offset_applied"] = "per-building (variable)"
    else:
        msg = f"[Z-Align] Unknown mode: {mode}"
        log_error(msg)
        return False, msg, statistics

    # FINAL SUMMARY
    log_info(f"[Z-Align] ═══════════════════════════════════")
    log_info(f"[Z-Align] ALIGNMENT COMPLETE")
    log_info(f"[Z-Align] ═══════════════════════════════════")
    log_info(f"[Z-Align] Mode: {mode}")
    log_info(f"[Z-Align] Buildings adjusted: {count}")
    log_info(f"[Z-Align] XY unchanged ✓")
    log_info(f"[Z-Align] Scale unchanged ✓")

    msg = f"Z-alignment complete: {mode}, {count} buildings adjusted"
    return True, msg, statistics
