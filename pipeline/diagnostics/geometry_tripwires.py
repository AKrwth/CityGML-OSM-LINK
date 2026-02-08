"""
Geometry Tripwires for M1_DC_V6 Pipeline

PHASE 5: Post-import consistency checks to prevent regressions.

Purpose:
- Detect scale violations (must be 1,1,1)
- Detect CityGML tile drift (irregular spacing)
- NO automatic fixes - fail fast and loud

Usage:
    from pipeline.diagnostics import geometry_tripwires
    geometry_tripwires.run_geometry_tripwires()
"""

try:
    import bpy
    from mathutils import Vector
except ImportError:
    pass

from ...utils.logging_system import log_info, log_warn, log_error

# Collection names
CITYGML_COLLECTION = "CITYGML_TILES"
RGB_COLLECTION = "M1DC_RGB_BASEMAP"
DGM_COLLECTION = "M1DC_TERRAIN_DGM"

# Tolerances
SCALE_TOLERANCE = 1e-6  # Scale must be 1.0 ± epsilon
SPACING_TOLERANCE = 0.05  # 5% tolerance for tile spacing regularity


def check_scale_unity(obj, collection_name: str):
    """
    Check that object has scale=(1,1,1).

    Raises RuntimeError if scale violation detected.
    """
    scale = obj.scale
    if any(abs(s - 1.0) > SCALE_TOLERANCE for s in scale):
        raise RuntimeError(
            f"[TRIPWIRE] Object '{obj.name}' in '{collection_name}' has non-unit scale: {tuple(scale)}\n"
            f"Expected: (1.0, 1.0, 1.0)\n"
            f"Actual: ({scale.x:.6f}, {scale.y:.6f}, {scale.z:.6f})"
        )


def check_citygml_scale():
    """
    TRIPWIRE 1: CityGML objects must have scale=(1,1,1).

    Raises RuntimeError if any object has non-unit scale.
    """
    col = bpy.data.collections.get(CITYGML_COLLECTION)
    if not col:
        log_warn(f"[TRIPWIRE] Collection '{CITYGML_COLLECTION}' not found, skipping scale check")
        return

    mesh_objs = [o for o in col.objects if o.type == 'MESH']
    if not mesh_objs:
        log_warn(f"[TRIPWIRE] No mesh objects in '{CITYGML_COLLECTION}', skipping scale check")
        return

    for obj in mesh_objs:
        check_scale_unity(obj, CITYGML_COLLECTION)

    log_info(f"[TRIPWIRE] CityGML scale OK ({len(mesh_objs)} objects checked)")


def check_citygml_tile_spacing():
    """
    TRIPWIRE 2: CityGML tiles must be regularly spaced (no drift).

    Algorithm:
    1. Compute bounding box centers for all tiles
    2. Compute pairwise distances
    3. Check for regular grid pattern (distances clustered)

    Raises RuntimeError if spacing is irregular.
    """
    col = bpy.data.collections.get(CITYGML_COLLECTION)
    if not col:
        log_warn(f"[TRIPWIRE] Collection '{CITYGML_COLLECTION}' not found, skipping spacing check")
        return

    mesh_objs = [o for o in col.objects if o.type == 'MESH']
    if len(mesh_objs) < 2:
        log_warn(f"[TRIPWIRE] Only {len(mesh_objs)} CityGML tile(s), skipping spacing check")
        return

    # Compute bounding box centers
    centers = []
    for obj in mesh_objs:
        bbox = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        center = sum(bbox, Vector()) / len(bbox)
        centers.append((obj.name, center))

    # Compute pairwise distances (sample up to 50 pairs to avoid O(n²) explosion)
    distances = []
    max_pairs = 50
    pair_count = 0

    for i, (name_a, center_a) in enumerate(centers):
        for j, (name_b, center_b) in enumerate(centers):
            if i >= j:
                continue

            dist = (center_a - center_b).length
            if dist > 0.1:  # Ignore near-zero distances (same tile)
                distances.append(dist)
                pair_count += 1

                if pair_count >= max_pairs:
                    break
        if pair_count >= max_pairs:
            break

    if not distances:
        log_warn("[TRIPWIRE] No tile spacing data (all tiles at same position?)")
        return

    # Check minimum distance (must be > 0 to avoid overlap)
    min_dist = min(distances)
    if min_dist < 0.1:
        raise RuntimeError(
            f"[TRIPWIRE] CityGML tiles too close or overlapping\n"
            f"Minimum spacing: {min_dist:.2f}m (expected > 0.1m)"
        )

    # Check spacing regularity (coefficient of variation)
    # CV = std / mean - should be small for regular grid
    import statistics
    mean_dist = statistics.mean(distances)
    std_dist = statistics.stdev(distances) if len(distances) > 1 else 0.0
    cv = std_dist / mean_dist if mean_dist > 0 else 0.0

    # Relaxed threshold: allow some variation (tiles may not form perfect grid)
    # Focus on detecting major drift (CV > 0.3 indicates irregular spacing)
    if cv > 0.3:
        raise RuntimeError(
            f"[TRIPWIRE] CityGML tile spacing inconsistent (possible drift or scaling)\n"
            f"Mean spacing: {mean_dist:.1f}m, StdDev: {std_dist:.1f}m, CV: {cv:.2f}\n"
            f"Expected CV < 0.3 for regular grid"
        )

    log_info(f"[TRIPWIRE] CityGML spacing OK ({len(mesh_objs)} tiles, mean={mean_dist:.1f}m, CV={cv:.2f})")


def check_terrain_rgb_scale():
    """
    TRIPWIRE 3: Terrain and RGB basemap must have scale=(1,1,1).

    Raises RuntimeError if any object has non-unit scale.
    """
    collections_to_check = [
        (DGM_COLLECTION, "DGM Terrain"),
        (RGB_COLLECTION, "RGB Basemap"),
    ]

    total_checked = 0

    for col_name, display_name in collections_to_check:
        col = bpy.data.collections.get(col_name)
        if not col:
            continue  # Collection not created yet (may not be imported)

        mesh_objs = [o for o in col.objects if o.type == 'MESH']
        for obj in mesh_objs:
            check_scale_unity(obj, col_name)
            total_checked += 1

    if total_checked > 0:
        log_info(f"[TRIPWIRE] Terrain/RGB scale OK ({total_checked} objects checked)")
    else:
        log_warn("[TRIPWIRE] No terrain/RGB objects found, skipping scale check")


def run_geometry_tripwires():
    """
    Run all geometry tripwire checks.

    Raises RuntimeError if any check fails.
    """
    log_info("[TRIPWIRE] ╔═══════════════════════════════════╗")
    log_info("[TRIPWIRE] ║ GEOMETRY CHECKS START             ║")
    log_info("[TRIPWIRE] ╚═══════════════════════════════════╝")

    try:
        # TRIPWIRE 1: CityGML scale
        check_citygml_scale()

        # TRIPWIRE 2: CityGML tile spacing
        check_citygml_tile_spacing()

        # TRIPWIRE 3: Terrain/RGB scale
        check_terrain_rgb_scale()

        # Success
        log_info("[TRIPWIRE] ╔═══════════════════════════════════╗")
        log_info("[TRIPWIRE] ║ GEOMETRY CHECKS PASSED            ║")
        log_info("[TRIPWIRE] ╚═══════════════════════════════════╝")
        log_info("[TRIPWIRE] Geometry checks passed:")
        log_info("[TRIPWIRE]   ✓ CityGML scale OK")
        log_info("[TRIPWIRE]   ✓ CityGML spacing OK")
        log_info("[TRIPWIRE]   ✓ Terrain/RGB scale OK")

    except RuntimeError as e:
        log_error(f"[TRIPWIRE] GEOMETRY CHECK FAILED")
        log_error(f"[TRIPWIRE] {str(e)}")
        raise  # Re-raise to abort pipeline
