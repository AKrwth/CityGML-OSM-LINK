#!/usr/bin/env python3
"""
terrain_postprocess.py - Terrain Post-Processing (NaN Repair, UV, Z-Align)

Post-import processing to make terrain mesh production-ready:
1. Repair NaN/Inf vertices (make raycastable)
2. Generate UV mapping from WORLD-SPACE XY coordinates
3. Compute Z-offset via raycast sampling (low-vertices strategy)

Policy:
- Runs immediately after DEM/RGB import
- Read/write mesh data, no changes to import logic
- Robust fallback if CityGML not present

Improvements (2026-01-24):
- FIX 1: UV mapping uses world-space XY (avoids scale skew)
- FIX 2: Efficient NaN repair (sample first, full scan only if needed)
- FIX 3: Z-alignment with safety clearance
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

try:
    import bpy
    from mathutils import Vector
except ImportError:
    bpy = None
    Vector = None

from ...utils.logging_system import log_info, log_warn, log_error


# ============================================================================
# Helper: Check for NaN/Inf
# ============================================================================

def _is_bad(v):
    """Check if vector has NaN or Inf components."""
    return (math.isnan(v.x) or math.isnan(v.y) or math.isnan(v.z) or
            math.isinf(v.x) or math.isinf(v.y) or math.isinf(v.z))


# ============================================================================
# A1) NaN/Inf Repair (with sample → full scan only when needed)
# ============================================================================

def repair_nan_vertices(obj, sample_only=False, sample_n=2000) -> Dict:
    """
    Repair NaN/Inf vertices so ray_cast works.

    Strategy:
    1. Quick sample check (2000 vertices default)
    2. If none bad → exit (no repair needed)
    3. If bad found → full scan repair (unless sample_only=True)

    Args:
        obj: Blender Object (MESH type)
        sample_only: If True, only do sample check (don't repair)
        sample_n: Number of vertices to sample (default: 2000)

    Returns:
        dict with:
            ok: bool
            bad: int (number of bad vertices found/repaired)
            mode: str ("sample_clean", "sample_detected_only", or "full_repair")
            z_fallback: float (fallback Z value used for repair)
    """
    if not obj or obj.type != 'MESH' or not obj.data:
        log_warn("[TerrainRepair] Object is not a mesh, skipping NaN repair")
        return {"ok": False, "warnings": ["invalid obj"]}

    mesh = obj.data
    if not mesh.vertices:
        log_info("[TerrainRepair] Mesh has no vertices, skipping NaN repair")
        return {"ok": True, "bad": 0, "mode": "no_verts"}

    vert_count = len(mesh.vertices)
    log_info(f"[TerrainRepair] Scanning {vert_count:,} vertices for NaN/Inf...")

    # Compute local z fallback from bbox (sample for performance)
    sample_size = min(5000, vert_count)
    z_samples = []
    for _ in range(sample_size):
        v = mesh.vertices[random.randrange(vert_count)].co
        if math.isfinite(v.z):
            z_samples.append(v.z)

    z_fallback = min(z_samples) if z_samples else 0.0
    log_info(f"[TerrainRepair] Computed z_fallback from bbox: {z_fallback:.2f}m")

    # Sample check (quick pass to see if repair is needed)
    bad_sample = 0
    sample_count = min(sample_n, vert_count)
    for _ in range(sample_count):
        v = mesh.vertices[random.randrange(vert_count)].co
        if _is_bad(v):
            bad_sample += 1

    if bad_sample == 0:
        log_info(f"[TerrainRepair] Sample check ({sample_count} vertices): No NaN/Inf found ✓")
        return {"ok": True, "bad": 0, "mode": "sample_clean"}

    log_warn(f"[TerrainRepair] Sample check: Found {bad_sample}/{sample_count} bad vertices")

    if sample_only:
        log_info("[TerrainRepair] sample_only=True, skipping full repair")
        return {"ok": True, "bad": bad_sample, "mode": "sample_detected_only"}

    # Full repair
    log_info("[TerrainRepair] Running full scan and repair...")
    bad = 0
    for vert in mesh.vertices:
        co = vert.co
        if _is_bad(co):
            bad += 1
            x = 0.0 if (math.isnan(co.x) or math.isinf(co.x)) else co.x
            y = 0.0 if (math.isnan(co.y) or math.isinf(co.y)) else co.y
            z = z_fallback if (math.isnan(co.z) or math.isinf(co.z)) else co.z
            vert.co = (x, y, z)

    mesh.update()
    obj["M1DC_NAN_REPAIRED_COUNT"] = int(bad)

    log_info(f"[TerrainRepair] ✓ NaN/Inf repaired: {bad:,} vertices (z_fallback={z_fallback:.2f}m)")

    return {"ok": True, "bad": bad, "mode": "full_repair", "z_fallback": z_fallback}


# ============================================================================
# A2) UV Mapping from WORLD-SPACE XY
# ============================================================================

def ensure_uv_xy(obj, uv_name="M1DC_UV", flip_v=True) -> Dict:
    """
    Write deterministic UVs from WORLD-SPACE XY extents.

    This avoids skew when obj.scale is used for meter sizing.
    Uses matrix_world to get true world coordinates, ensuring UV mapping
    is independent of object scale/rotation.

    Args:
        obj: Blender Object (MESH type)
        uv_name: Name for UV layer (default: "M1DC_UV")
        flip_v: If True, flip V coordinate (v = 1-v)

    Returns:
        dict with:
            ok: bool
            uv: str (layer name)
            bounds_world: dict with minx, maxx, miny, maxy (world coordinates)
            flip_v: bool
            loop_count: int
    """
    if not obj or obj.type != 'MESH' or not obj.data:
        log_warn("[TerrainUV] Object is not a mesh, skipping UV generation")
        return {"ok": False, "warnings": ["invalid obj"]}

    mesh = obj.data
    if not mesh.vertices or not mesh.loops:
        log_warn("[TerrainUV] Mesh has no vertices/loops, skipping UV generation")
        return {"ok": False, "warnings": ["no vertices/loops"]}

    log_info(f"[TerrainUV] Generating UV layer '{uv_name}' from WORLD-SPACE XY coordinates...")

    # Get or create UV layer
    if uv_name not in mesh.uv_layers:
        mesh.uv_layers.new(name=uv_name)
        log_info(f"[TerrainUV] Created new UV layer: {uv_name}")
    else:
        log_info(f"[TerrainUV] Using existing UV layer: {uv_name}")

    uv_layer = mesh.uv_layers[uv_name]

    # WORLD bounds from vertices (robust even if obj.scale != 1)
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    mw = obj.matrix_world
    for v in mesh.vertices:
        w = mw @ v.co
        if w.x < min_x:
            min_x = w.x
        if w.x > max_x:
            max_x = w.x
        if w.y < min_y:
            min_y = w.y
        if w.y > max_y:
            max_y = w.y

    dx = (max_x - min_x) or 1.0
    dy = (max_y - min_y) or 1.0

    log_info(f"[TerrainUV] World bounds: x=[{min_x:.2f}, {max_x:.2f}], y=[{min_y:.2f}, {max_y:.2f}]")
    log_info(f"[TerrainUV] Extents: dx={dx:.2f}m, dy={dy:.2f}m")

    # Write UV per loop (using world coordinates)
    loop_count = 0
    for li, loop in enumerate(mesh.loops):
        v = mesh.vertices[loop.vertex_index]
        w = mw @ v.co
        u = (w.x - min_x) / dx
        vv = (w.y - min_y) / dy
        if flip_v:
            vv = 1.0 - vv
        uv_layer.data[li].uv = (u, vv)
        loop_count += 1

    mesh.update()

    log_info(f"[TerrainUV] ✓ Wrote UV '{uv_name}': {loop_count:,} loops (world-space), flip_v={flip_v}")

    return {
        "ok": True,
        "uv": uv_name,
        "bounds_world": {"minx": min_x, "maxx": max_x, "miny": min_y, "maxy": max_y},
        "flip_v": flip_v,
        "loop_count": loop_count,
    }


# ============================================================================
# A3) Z-Offset Computation (Low-Vertices Raycast Strategy with Safety Clearance)
# ============================================================================

def compute_z_offset_raycast_low_vertices(
    terrain_obj,
    gml_objs: List,
    N: int = 400,
    low_fraction: float = 0.08,
    origin_up: float = 5000.0,
    max_dist: float = 20000.0,
    safety_clearance: float = 0.5,
) -> Dict:
    """
    Compute Z-offset via raycast from low CityGML vertices to terrain.

    Strategy:
    1. Sample N random vertices from CityGML objects
    2. Filter to lowest low_fraction (e.g., 8% = ground-level vertices)
    3. Raycast each down onto terrain (local space)
    4. Compute median dz = (gml_vertex_world_z - terrain_hit_world_z)
    5. Add safety_clearance to final dz

    Args:
        terrain_obj: Terrain mesh object (dem_merged)
        gml_objs: List of CityGML mesh objects
        N: Number of vertices to sample (default: 400)
        low_fraction: Fraction of lowest vertices to use (default: 0.08 = 8%)
        origin_up: Ray origin offset above vertex (meters, default: 5000)
        max_dist: Max ray distance (meters, default: 20000)
        safety_clearance: Safety offset added to dz_median (meters, default: 0.5)

    Returns:
        dict with:
            ok: bool
            hits: int (successful raycasts)
            misses: int (failed raycasts)
            dz_median: float (median Z offset, or None if no hits)
            dz_with_clearance: float (dz_median + safety_clearance)
            sample_count: int (total vertices sampled)
            low_threshold_z: float (Z threshold for "low" vertices)
            safety_clearance: float
    """
    if not terrain_obj or terrain_obj.type != 'MESH':
        log_warn("[TerrainZ] Terrain object is not a mesh, cannot compute Z-offset")
        return {"ok": False, "hits": 0, "misses": 0, "dz_median": None}

    if not gml_objs:
        log_warn("[TerrainZ] No CityGML objects provided, cannot compute Z-offset")
        return {"ok": False, "hits": 0, "misses": 0, "dz_median": None}

    log_info(f"[TerrainZ] ═══════════════════════════════════")
    log_info(f"[TerrainZ] COMPUTE Z-OFFSET (LOW-VERTICES)")
    log_info(f"[TerrainZ] ═══════════════════════════════════")
    log_info(f"[TerrainZ] Terrain: {terrain_obj.name}")
    log_info(f"[TerrainZ] CityGML objects: {len(gml_objs)}")
    log_info(f"[TerrainZ] Sample size: {N}, low_fraction: {low_fraction:.2%}")

    # Use evaluated depsgraph for raycast
    deps = bpy.context.evaluated_depsgraph_get()
    terr_eval = terrain_obj.evaluated_get(deps)

    inv = terr_eval.matrix_world.inverted()
    dir_local = Vector((0, 0, -1))

    dzs = []
    hits = 0
    misses = 0

    for _ in range(N):
        # Pick random CityGML object
        o = random.choice(gml_objs)
        me = o.data
        if not me.vertices:
            misses += 1
            continue

        # Threshold for lowest fraction of vertices in WORLD Z
        # (sample subset for speed)
        sample_count = min(3000, len(me.vertices))
        idxs = [random.randrange(len(me.vertices)) for __ in range(sample_count)]
        zs = [(o.matrix_world @ me.vertices[i].co).z for i in idxs]
        zs.sort()
        z_thresh = zs[max(0, int(len(zs) * low_fraction) - 1)]

        # Pick a vertex below threshold (try a few attempts)
        picked = None
        for __ in range(20):
            v = me.vertices[random.randrange(len(me.vertices))]
            pz = (o.matrix_world @ v.co).z
            if pz <= z_thresh:
                picked = v
                break

        if picked is None:
            misses += 1
            continue

        # Raycast from above vertex down to terrain
        p_world = o.matrix_world @ picked.co
        origin_world = p_world + Vector((0, 0, origin_up))
        origin_local = inv @ origin_world

        hit, loc_local, n, fi = terr_eval.ray_cast(origin_local, dir_local, distance=max_dist)
        if hit:
            loc_world = terr_eval.matrix_world @ loc_local
            dzs.append(p_world.z - loc_world.z)
            hits += 1
        else:
            misses += 1

    # Compute median dz
    dz_median = None
    dz_with_clearance = None

    if not dzs:
        log_warn("[TerrainZ] No successful raycasts, cannot compute dz_median")
        return {"ok": False, "hits": hits, "misses": misses, "dz_median": None}

    # Use statistics.median for cleaner code
    from statistics import median
    dz_median = float(median(dzs))
    dz_with_clearance = dz_median + safety_clearance

    hit_rate = hits / (hits + misses) if (hits + misses) > 0 else 0.0

    log_info(f"[TerrainZ] Raycast results: hits={hits}/{hits+misses} ({hit_rate:.1%})")
    log_info(f"[TerrainZ] ✓ dz_low_median={dz_median:.2f}m")
    log_info(f"[TerrainZ] ✓ dz_with_clearance={dz_with_clearance:.2f}m (safety={safety_clearance}m)")
    log_info(f"[TerrainZ] ═══════════════════════════════════")

    return {
        "ok": True,
        "hits": hits,
        "misses": misses,
        "dz_median": dz_median,
        "dz_with_clearance": dz_with_clearance,
        "sample_count": N,
        "low_threshold_z": z_thresh if dzs else None,
        "safety_clearance": safety_clearance,
    }


# ============================================================================
# A4) Apply Z-Offset
# ============================================================================

def apply_z_offset(obj, dz: float, clamp: Optional[Tuple[float, float]] = None) -> float:
    """
    Apply Z-offset to object location.

    Args:
        obj: Blender Object
        dz: Z-offset to add (meters)
        clamp: Optional (min, max) clamp range for absolute Z (e.g., (-5000, 5000))

    Returns:
        new_z: New Z location after applying offset
    """
    if not obj:
        log_warn("[TerrainZ] Cannot apply Z-offset: object is None")
        return 0.0

    old_z = obj.location.z
    new_z = old_z + dz

    # Optional clamp
    if clamp:
        min_z, max_z = clamp
        if new_z < min_z:
            log_warn(f"[TerrainZ] Clamping new_z={new_z:.2f}m to min={min_z:.2f}m")
            new_z = min_z
        elif new_z > max_z:
            log_warn(f"[TerrainZ] Clamping new_z={new_z:.2f}m to max={max_z:.2f}m")
            new_z = max_z

    obj.location.z = new_z

    log_info(f"[TerrainZ] Applied dz={dz:.2f}m: {old_z:.2f}m → {new_z:.2f}m")

    return new_z
