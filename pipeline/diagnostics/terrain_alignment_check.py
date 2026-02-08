#!/usr/bin/env python3
"""
terrain_alignment_check.py - Terrain/CityGML Alignment Diagnostic (Read-Only)

Post-Phase-1 "Terrain Freeze Test": Diagnose XY/Z alignment between prepared
terrain and CityGML tiles WITHOUT any auto-correction.

Policy:
- READ ONLY - No object modifications
- NO auto-fixing / shifting
- Pure diagnostic data collection + reporting
- Robust fallback if objects not found (don't hard fail)

Usage:
    from pipeline.diagnostics.terrain_alignment_check import report_terrain_citygml_alignment
    result = report_terrain_citygml_alignment(bpy.context.scene)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import bpy
from mathutils import Vector


def report_terrain_citygml_alignment(scene) -> Dict:
    """
    Diagnose Terrain ↔ CityGML alignment in scene (read-only).

    Collects:
    - Scene metadata (CRS, world origin)
    - Terrain object bboxes (dem_merged / rgb_merged)
    - CityGML object bboxes (combined)
    - XY alignment metrics (center delta, bbox overlap)

    Args:
        scene: Blender scene

    Returns:
        dict with diagnostic data:
        {
            "crs": str,
            "world_min_e": float or None,
            "world_min_n": float or None,
            "terrain_obj": str or None (object name found),
            "terrain_bbox": dict or None,
            "citygml_mesh_count": int,
            "citygml_bbox": dict or None,
            "delta_xy_m": float or None,
            "overlap_xy": bool or None,
            "warnings": list[str],
        }
    """
    from ...utils.logging_system import log_info, log_warn

    result = {
        "crs": None,
        "world_min_e": None,
        "world_min_n": None,
        "terrain_obj": None,
        "terrain_bbox": None,
        "citygml_mesh_count": 0,
        "citygml_bbox": None,
        "delta_xy_m": None,
        "overlap_xy": None,
        "warnings": [],
    }

    log_info("[TerrainAlignmentCheck] ═══════════════════════════════════")
    log_info("[TerrainAlignmentCheck] TERRAIN ↔ CITYGML ALIGNMENT CHECK")
    log_info("[TerrainAlignmentCheck] ═══════════════════════════════════")

    # ========================================================================
    # A) Scene Metadata
    # ========================================================================
    result["crs"] = scene.get("M1DC_CRS", "UNKNOWN")
    result["world_min_e"] = scene.get("M1DC_WORLD_ORIGIN_MIN_EASTING")
    result["world_min_n"] = scene.get("M1DC_WORLD_ORIGIN_MIN_NORTHING")

    log_info(f"[TerrainAlignmentCheck] CRS: {result['crs']}")
    log_info(f"[TerrainAlignmentCheck] World Origin: E={result['world_min_e']}, N={result['world_min_n']}")

    # Store NaN repair info if available
    result["terrain_nan_repaired"] = None

    # ========================================================================
    # B) Find Terrain Object (robustly, don't hard fail)
    # ========================================================================
    terrain_obj = _find_terrain_object(scene)
    if terrain_obj:
        result["terrain_obj"] = terrain_obj.name
        result["terrain_bbox"] = _compute_object_bbox(terrain_obj)
        result["terrain_location_z"] = terrain_obj.location.z

        # Check if NaN repair was done
        nan_count = terrain_obj.get("M1DC_NAN_REPAIRED_COUNT")
        if nan_count is not None:
            result["terrain_nan_repaired"] = int(nan_count)

        log_info(f"[TerrainAlignmentCheck] Terrain object: {terrain_obj.name}")
        log_info(f"[TerrainAlignmentCheck]   Location Z: {result['terrain_location_z']:.2f}m")
        if result["terrain_nan_repaired"] is not None and result["terrain_nan_repaired"] > 0:
            log_info(f"[TerrainAlignmentCheck]   NaN repaired: {result['terrain_nan_repaired']} vertices")
        log_info(f"[TerrainAlignmentCheck]   BBox: {result['terrain_bbox']}")
    else:
        result["warnings"].append("No terrain object found (dem_merged / rgb_merged)")
        log_warn("[TerrainAlignmentCheck] No terrain object found")

    # ========================================================================
    # C) Find CityGML Objects
    # ========================================================================
    citygml_objs = _find_citygml_objects(scene)
    result["citygml_mesh_count"] = len(citygml_objs)

    if citygml_objs:
        # Limit to first 20 tiles for bbox computation (performance)
        sample_objs = citygml_objs[:20]
        result["citygml_bbox"] = _compute_combined_bbox(sample_objs)
        log_info(f"[TerrainAlignmentCheck] CityGML meshes: {result['citygml_mesh_count']}")
        log_info(f"[TerrainAlignmentCheck]   BBox (from {len(sample_objs)} tiles): {result['citygml_bbox']}")
    else:
        result["warnings"].append("No CityGML objects found")
        log_warn("[TerrainAlignmentCheck] No CityGML objects found")

    # ========================================================================
    # D) Alignment Metrics
    # ========================================================================
    if result["terrain_bbox"] and result["citygml_bbox"]:
        t_center = result["terrain_bbox"]["center"]
        g_center = result["citygml_bbox"]["center"]

        dx = g_center[0] - t_center[0]
        dy = g_center[1] - t_center[1]
        delta_xy = math.sqrt(dx**2 + dy**2)

        # Z delta (center Z difference)
        dz = g_center[2] - t_center[2]

        result["delta_xy_m"] = delta_xy
        result["delta_x_m"] = dx
        result["delta_y_m"] = dy
        result["delta_z_m"] = dz

        # Z bbox ranges
        result["terrain_z_min"] = result["terrain_bbox"]["min"][2]
        result["terrain_z_max"] = result["terrain_bbox"]["max"][2]
        result["citygml_z_min"] = result["citygml_bbox"]["min"][2]
        result["citygml_z_max"] = result["citygml_bbox"]["max"][2]

        # Overlap check (simple 2D bbox intersection)
        result["overlap_xy"] = _bbox_overlaps_xy(result["terrain_bbox"], result["citygml_bbox"])

        log_info(f"[TerrainAlignmentCheck] Center Delta: ΔX={dx:.2f}m, ΔY={dy:.2f}m, ΔZ={dz:.2f}m, ΔXY={delta_xy:.2f}m")
        log_info(f"[TerrainAlignmentCheck] Terrain Z range: [{result['terrain_z_min']:.2f}, {result['terrain_z_max']:.2f}]m")
        log_info(f"[TerrainAlignmentCheck] CityGML Z range: [{result['citygml_z_min']:.2f}, {result['citygml_z_max']:.2f}]m")
        log_info(f"[TerrainAlignmentCheck] XY Overlap: {result['overlap_xy']}")

        # Interpretation hints
        if delta_xy < 10.0:
            log_info("[TerrainAlignmentCheck] ✓ Alignment GOOD (delta < 10m)")
        elif delta_xy < 50.0:
            log_warn("[TerrainAlignmentCheck] ⚠ Alignment FAIR (10m < delta < 50m)")
        else:
            log_warn(f"[TerrainAlignmentCheck] ✗ Alignment POOR (delta = {delta_xy:.2f}m)")
            result["warnings"].append(f"Large XY center offset: {delta_xy:.2f}m")

        if not result["overlap_xy"]:
            log_warn("[TerrainAlignmentCheck] ✗ No XY bbox overlap!")
            result["warnings"].append("Terrain and CityGML bboxes do not overlap in XY")
    else:
        if not result["terrain_bbox"]:
            result["warnings"].append("Cannot compute alignment: no terrain bbox")
        if not result["citygml_bbox"]:
            result["warnings"].append("Cannot compute alignment: no CityGML bbox")

    log_info("[TerrainAlignmentCheck] ═══════════════════════════════════")

    return result


# ============================================================================
# Helper Functions
# ============================================================================

def _find_terrain_object(scene) -> Optional[bpy.types.Object]:
    """Find terrain object (priority: dem_merged > rgb_merged > contains 'dem'/'rgb')."""
    # Try exact names first
    if "dem_merged" in bpy.data.objects:
        obj = bpy.data.objects["dem_merged"]
        if obj.type == 'MESH':
            return obj

    if "rgb_merged" in bpy.data.objects:
        obj = bpy.data.objects["rgb_merged"]
        if obj.type == 'MESH':
            return obj

    # Fallback: search for objects with "dem" or "rgb" in name
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        name_lower = obj.name.lower()
        if "dem" in name_lower or "rgb" in name_lower:
            if "merged" in name_lower or "terrain" in name_lower:
                return obj

    return None


def _find_citygml_objects(scene) -> List[bpy.types.Object]:
    """Find CityGML mesh objects (via collection or custom props)."""
    objs = []

    # Strategy 1: Find collection with "CityGML" or "GML" in name
    for coll in bpy.data.collections:
        coll_name_lower = coll.name.lower()
        if "citygml" in coll_name_lower or "gml" in coll_name_lower:
            for obj in coll.all_objects:
                if obj.type == 'MESH':
                    objs.append(obj)

    # Strategy 2: If no collection found, search by custom props
    if not objs:
        for obj in bpy.data.objects:
            if obj.type != 'MESH':
                continue
            # Check for known CityGML custom props
            if obj.get("source_tile") or obj.get("building_idx") is not None:
                objs.append(obj)

    return objs


def _compute_object_bbox(obj: bpy.types.Object) -> Dict:
    """Compute world-space bounding box for single object."""
    if not obj.data or not hasattr(obj.data, 'vertices'):
        return None

    if len(obj.data.vertices) == 0:
        return None

    # Compute bbox in world space
    matrix = obj.matrix_world
    verts_world = [matrix @ v.co for v in obj.data.vertices]

    min_x = min(v.x for v in verts_world)
    max_x = max(v.x for v in verts_world)
    min_y = min(v.y for v in verts_world)
    max_y = max(v.y for v in verts_world)
    min_z = min(v.z for v in verts_world)
    max_z = max(v.z for v in verts_world)

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    center_z = (min_z + max_z) / 2.0

    return {
        "min": (min_x, min_y, min_z),
        "max": (max_x, max_y, max_z),
        "center": (center_x, center_y, center_z),
        "size": (max_x - min_x, max_y - min_y, max_z - min_z),
    }


def _compute_combined_bbox(objs: List[bpy.types.Object]) -> Dict:
    """Compute combined world-space bounding box for multiple objects."""
    if not objs:
        return None

    all_mins = []
    all_maxs = []

    for obj in objs:
        bbox = _compute_object_bbox(obj)
        if bbox:
            all_mins.append(bbox["min"])
            all_maxs.append(bbox["max"])

    if not all_mins:
        return None

    min_x = min(v[0] for v in all_mins)
    min_y = min(v[1] for v in all_mins)
    min_z = min(v[2] for v in all_mins)

    max_x = max(v[0] for v in all_maxs)
    max_y = max(v[1] for v in all_maxs)
    max_z = max(v[2] for v in all_maxs)

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    center_z = (min_z + max_z) / 2.0

    return {
        "min": (min_x, min_y, min_z),
        "max": (max_x, max_y, max_z),
        "center": (center_x, center_y, center_z),
        "size": (max_x - min_x, max_y - min_y, max_z - min_z),
    }


def _bbox_overlaps_xy(bbox1: Dict, bbox2: Dict) -> bool:
    """Check if two bboxes overlap in XY plane (2D intersection test)."""
    # Extract XY ranges
    min1_x, min1_y = bbox1["min"][0], bbox1["min"][1]
    max1_x, max1_y = bbox1["max"][0], bbox1["max"][1]

    min2_x, min2_y = bbox2["min"][0], bbox2["min"][1]
    max2_x, max2_y = bbox2["max"][0], bbox2["max"][1]

    # Check for no overlap (easier to test)
    if max1_x < min2_x or max2_x < min1_x:
        return False
    if max1_y < min2_y or max2_y < min1_y:
        return False

    return True
