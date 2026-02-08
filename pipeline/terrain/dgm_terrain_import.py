"""
DGM Terrain (DEM) Import for M1_DC_V6 Pipeline

PHASE 3: Import DGM terrain artifact with deterministic XY placement

Requirements:
- Import dem_merged.obj artifact (pre-merged terrain)
- Deterministic XY placement (no trial-and-error)
- Scale=(1,1,1) enforced
- Z is preserved from OBJ (not corrected)
- Collection: M1DC_TERRAIN_DGM

Placement sources (priority order):
1. JSON (basemap.json or dgm_basemap.json) - contains extents/CRS
2. CSV fallback (DGM1_nw.csv) - parse tile coordinates
"""

import os
import json
import re
from pathlib import Path

try:
    import bpy
    from mathutils import Vector
except ImportError:
    pass

from ...utils.logging_system import log_info, log_warn, log_error
from ...utils.common import (
    ensure_world_origin,
    get_world_origin_minmax,
    link_exclusively_to_collection,
)

# Constants
COLLECTION_NAME = "M1DC_TERRAIN_DGM"
ORIGIN_EMPTY_NAME = "M1DC_TERRAIN_ORIGIN"

# CSV filename pattern: DGM1_nw.csv or similar
CSV_PATTERN = re.compile(r"DGM\d+.*\.csv$", re.IGNORECASE)

# DGM tile filename pattern (if parsing individual tiles)
# Example: dgm1_32288_5624_1_nw.xyz -> E=32288, N=5624, km=1
TILE_PATTERN = re.compile(
    r"dgm\d+_(\d+)_(\d+)_(\d+)",
    re.IGNORECASE
)


def ensure_collection(name: str):
    """Get or create collection, link to scene if needed."""
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def ensure_empty(name: str):
    """Get or create empty object, link to scene if needed."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "PLAIN_AXES"
        bpy.context.scene.collection.objects.link(obj)
    return obj


def read_basemap_json(json_path: str):
    """
    Read basemap.json or dgm_basemap.json.

    Expected format:
    {
        "crs": "EPSG:25832",
        "min_e": 286000.0,
        "min_n": 5624000.0,
        "max_e": 294000.0,
        "max_n": 5630000.0,
        "tile_size_m": 1000.0
    }

    Returns:
        dict with keys: crs, min_e, min_n, max_e, max_n, tile_size_m
        or None if parse fails
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        required = ["crs", "min_e", "min_n", "max_e", "max_n"]
        if not all(k in data for k in required):
            log_warn(f"[DGM] JSON missing required keys: {json_path}")
            return None

        # Validate CRS
        crs = str(data.get("crs", ""))
        if "25832" not in crs and "EPSG:25832" not in crs.upper():
            log_warn(f"[DGM] JSON CRS mismatch: expected EPSG:25832, got {crs}")

        return {
            "crs": crs,
            "min_e": float(data["min_e"]),
            "min_n": float(data["min_n"]),
            "max_e": float(data["max_e"]),
            "max_n": float(data["max_n"]),
            "tile_size_m": float(data.get("tile_size_m", 1000.0)),
        }
    except Exception as ex:
        log_error(f"[DGM] Failed to parse JSON {json_path}: {ex}")
        return None


def parse_csv_extents(csv_path: str, tile_size_m: float = 1000.0):
    """
    Parse DGM CSV to extract tile extents.

    Fallback method when JSON is not available.
    Reads tile filenames/keys from CSV, infers grid parameters.

    Args:
        csv_path: Path to DGM1_nw.csv or similar
        tile_size_m: Tile size in meters (default 1000m = 1km)

    Returns:
        dict with keys: min_e, min_n, max_e, max_n, tile_size_m
        or None if parse fails
    """
    try:
        import csv as csv_module

        tiles = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv_module.reader(f)
            for row in reader:
                if not row:
                    continue
                # Assume first column contains filename or tile key
                filename = row[0].strip()
                match = TILE_PATTERN.search(filename)
                if match:
                    e_raw = int(match.group(1))
                    n_raw = int(match.group(2))
                    km = int(match.group(3))
                    tiles.append((e_raw, n_raw, km))

        if not tiles:
            log_warn(f"[DGM] No tile coordinates parsed from CSV: {csv_path}")
            return None

        # Infer grid steps
        e_vals = [t[0] for t in tiles]
        n_vals = [t[1] for t in tiles]

        from .basemap_tiles import most_common_positive_step

        delta_e_raw = most_common_positive_step(e_vals)
        delta_n_raw = most_common_positive_step(n_vals)

        if not delta_e_raw or not delta_n_raw:
            log_error("[DGM] Cannot infer grid steps from CSV (not enough distinct tiles)")
            return None

        # Compute multipliers
        mult_e = tile_size_m / float(delta_e_raw)
        mult_n = tile_size_m / float(delta_n_raw)

        # Compute extents
        min_e = min(e_vals) * mult_e
        min_n = min(n_vals) * mult_n
        max_e = max(e_vals) * mult_e + tile_size_m
        max_n = max(n_vals) * mult_n + tile_size_m

        log_info(f"[DGM] CSV fallback: ΔE_raw={delta_e_raw}, ΔN_raw={delta_n_raw}")
        log_info(f"[DGM] CSV fallback: mult_e={mult_e:.6f}, mult_n={mult_n:.6f}")

        return {
            "min_e": min_e,
            "min_n": min_n,
            "max_e": max_e,
            "max_n": max_n,
            "tile_size_m": tile_size_m,
        }
    except Exception as ex:
        log_error(f"[DGM] Failed to parse CSV {csv_path}: {ex}")
        return None


def import_dgm_terrain(artifact_dir: str, tile_size_m: float = 1000.0):
    """
    Import DGM terrain artifact with deterministic XY placement.

    PHASE 3 Implementation: Import pre-merged dem_merged.obj with correct XY.

    Args:
        artifact_dir: Path to folder containing dem_merged.obj and optional JSON/CSV
        tile_size_m: Tile size in meters (for CSV fallback, default 1000m)

    Returns:
        (success: bool, imported_obj: bpy.types.Object or None)
    """
    if not artifact_dir or not os.path.isdir(artifact_dir):
        log_warn(f"[DGM Terrain] Artifact folder not found: {artifact_dir}")
        return False, None

    # Check for dem_merged.obj
    obj_path = os.path.join(artifact_dir, "dem_merged.obj")
    if not os.path.isfile(obj_path):
        log_error(f"[DGM Terrain] dem_merged.obj not found in {artifact_dir}")
        return False, None

    # === PRIORITY 1: JSON-based placement ===
    extents = None
    json_paths = [
        os.path.join(artifact_dir, "basemap.json"),
        os.path.join(artifact_dir, "dgm_basemap.json"),
    ]

    for json_path in json_paths:
        if os.path.isfile(json_path):
            log_info(f"[DGM Terrain] Found JSON: {json_path}")
            extents = read_basemap_json(json_path)
            if extents:
                log_info(f"[DGM Terrain] Using JSON-based placement")
                break

    # === PRIORITY 2: CSV fallback ===
    if not extents:
        log_info(f"[DGM Terrain] JSON not found, trying CSV fallback")
        csv_files = [f for f in os.listdir(artifact_dir) if CSV_PATTERN.match(f)]
        if csv_files:
            csv_path = os.path.join(artifact_dir, csv_files[0])
            log_info(f"[DGM Terrain] Found CSV: {csv_path}")
            extents = parse_csv_extents(csv_path, tile_size_m)
            if extents:
                log_info(f"[DGM Terrain] Using CSV-based placement")

    if not extents:
        log_error("[DGM Terrain] Cannot determine extents (no JSON or CSV found)")
        return False, None

    # === IMPORT OBJ ===
    log_info(f"[DGM Terrain] ╔═══════════════════════════════════╗")
    log_info(f"[DGM Terrain] ║ IMPORTING DGM ARTIFACT            ║")
    log_info(f"[DGM Terrain] ╚═══════════════════════════════════╝")
    log_info(f"[DGM Terrain] OBJ path: {obj_path}")

    # Remember existing objects
    before = set(bpy.data.objects)

    try:
        # Use Blender 4.x OBJ import
        bpy.ops.wm.obj_import(filepath=obj_path)
    except Exception as ex:
        log_error(f"[DGM Terrain] OBJ import failed: {ex}")
        return False, None

    # Find newly imported objects
    after = set(bpy.data.objects)
    new_objs = list(after - before)

    if not new_objs:
        log_error("[DGM Terrain] No objects imported from OBJ")
        return False, None

    # Get main terrain object (typically first mesh)
    terrain_obj = None
    for obj in new_objs:
        if obj.type == 'MESH':
            terrain_obj = obj
            break

    if not terrain_obj:
        log_error("[DGM Terrain] No mesh object found in imported OBJ")
        return False, None

    log_info(f"[DGM Terrain] Imported object: {terrain_obj.name}")

    # === DETERMINISTIC XY PLACEMENT ===
    log_info(f"[DGM Terrain] ╔═══════════════════════════════════╗")
    log_info(f"[DGM Terrain] ║ DETERMINISTIC XY PLACEMENT        ║")
    log_info(f"[DGM Terrain] ╚═══════════════════════════════════╝")

    min_e = extents["min_e"]
    min_n = extents["min_n"]
    max_e = extents["max_e"]
    max_n = extents["max_n"]

    log_info(f"[DGM Terrain] Extents (meters):")
    log_info(f"[DGM Terrain]   minE: {min_e:.1f}, maxE: {max_e:.1f}")
    log_info(f"[DGM Terrain]   minN: {min_n:.1f}, maxN: {max_n:.1f}")
    log_info(f"[DGM Terrain]   Extent: {max_e - min_e:.1f} x {max_n - min_n:.1f} meters")

    # Get world origin (from scene properties)
    scene = bpy.context.scene
    world_min_e = float(scene.get("M1DC_WORLD_MIN_E", 0.0))
    world_min_n = float(scene.get("M1DC_WORLD_MIN_N", 0.0))

    log_info(f"[DGM Terrain] World origin: ({world_min_e:.1f}, {world_min_n:.1f})")

    # Compute local position
    # DGM min corner should be at world_to_local(min_e, min_n)
    loc_x = min_e - world_min_e
    loc_y = min_n - world_min_n
    loc_z = 0.0  # Z preserved from OBJ vertices (not adjusted here)

    # Apply placement
    terrain_obj.location.x = loc_x
    terrain_obj.location.y = loc_y
    terrain_obj.location.z = loc_z

    # Enforce neutral transforms
    terrain_obj.rotation_euler = (0.0, 0.0, 0.0)
    terrain_obj.scale = (1.0, 1.0, 1.0)

    log_info(f"[DGM Terrain] Applied location: ({loc_x:.2f}, {loc_y:.2f}, {loc_z:.2f})")
    log_info(f"[DGM Terrain] Scale: {tuple(terrain_obj.scale)}")
    log_info(f"[DGM Terrain] Rotation: {tuple(terrain_obj.rotation_euler)}")

    if tuple(terrain_obj.scale) != (1.0, 1.0, 1.0):
        log_warn(f"[DGM Terrain] ⚠️  Scale is NOT (1,1,1)!")

    # === ORGANIZE ===
    col = ensure_collection(COLLECTION_NAME)
    origin = ensure_empty(ORIGIN_EMPTY_NAME)
    world = ensure_world_origin()

    # Enforce neutral transforms on parents
    origin.rotation_euler = (0.0, 0.0, 0.0)
    origin.scale = (1.0, 1.0, 1.0)
    if world:
        world.rotation_euler = (0.0, 0.0, 0.0)
        world.scale = (1.0, 1.0, 1.0)

    # Parent to origin
    if origin.parent is None and world:
        origin.parent = world

    # Link terrain to collection
    for obj in new_objs:
        link_exclusively_to_collection(obj, col)

    # Parent terrain to origin (optional, for consistency)
    # terrain_obj.parent = origin
    # try:
    #     terrain_obj.matrix_parent_inverse.identity()
    # except Exception:
    #     pass

    # Store extents as custom properties
    terrain_obj["min_e"] = min_e
    terrain_obj["min_n"] = min_n
    terrain_obj["max_e"] = max_e
    terrain_obj["max_n"] = max_n
    terrain_obj["tile_size_m"] = extents.get("tile_size_m", tile_size_m)

    # === FINAL SUMMARY ===
    log_info(f"[DGM Terrain] ╔═══════════════════════════════════╗")
    log_info(f"[DGM Terrain] ║ IMPORT COMPLETE                   ║")
    log_info(f"[DGM Terrain] ╚═══════════════════════════════════╝")
    log_info(f"[DGM Terrain] Object: {terrain_obj.name}")
    log_info(f"[DGM Terrain] Collection: {COLLECTION_NAME}")
    log_info(f"[DGM Terrain] Location: {tuple(terrain_obj.location)}")
    log_info(f"[DGM Terrain] Scale: {tuple(terrain_obj.scale)} ✓")

    return True, terrain_obj
