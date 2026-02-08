"""
RGB Basemap (DTK10) Import for M1_DC_V6 Pipeline

PHASE 2: Import RGB tiles as reference basemap (XY-only, no height)

Requirements:
- Collection: M1DC_RGB_BASEMAP
- Scale: (1,1,1) enforced - plane geometry has real meter size
- No scaling, no centering - pure translation
- Reads dtk10_*.tif from terrain_rgb_dir
- Parses tile coordinates from filename
- Creates planes with image textures
- Correctly mosaiked in EPSG:25832 meters (localized via WORLD_MIN)
"""

import re
import os
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
    world_to_local,
    link_exclusively_to_collection,
)

# Constants
COLLECTION_NAME = "M1DC_RGB_BASEMAP"
ORIGIN_EMPTY_NAME = "M1DC_RGB_ORIGIN"
IMG_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}
from ...utils.common import TILE_ANCHOR  # single source of truth (see pipeline/common.py)
Y_FLIP = False

# Filename pattern: dtk10_32288_5624_2_nw_col.tif -> (E_raw=32288, N_raw=5624, km=2)
FILENAME_PATTERN = re.compile(
    r"dtk\d+_(\d+)_(\d+)_(\d+)_.*\.(tif|tiff|png|jpg|jpeg|webp)$",
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


def parse_tile_name(filename: str):
    """
    Parse DTK10 tile filename to extract E, N, km.

    Example: dtk10_32288_5624_2_nw_col.tif -> (32288, 5624, 2)

    Returns:
        (e_raw, n_raw, km) or None if no match
    """
    m = FILENAME_PATTERN.search(filename)
    if not m:
        return None
    e_raw = int(m.group(1))
    n_raw = int(m.group(2))
    km = int(m.group(3))
    return e_raw, n_raw, km


def most_common_positive_step(values):
    """Return the most common positive step between sorted unique values."""
    vals = sorted(set(values))
    diffs = [b - a for a, b in zip(vals, vals[1:]) if (b - a) > 0]
    if not diffs:
        return None
    counts = {}
    for d in diffs:
        counts[d] = counts.get(d, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def make_plane_with_uv(name: str, width_m: float, height_m: float):
    """
    Create plane mesh with real meter dimensions.

    CRITICAL: Plane geometry has width_m x height_m size, scale stays (1,1,1).
    UVs are standard (0,0) to (1,1) mapping.

    Args:
        name: Object name
        width_m: Plane width in meters
        height_m: Plane height in meters

    Returns:
        Blender object with mesh data
    """
    mesh = bpy.data.meshes.new(name + "_mesh")
    half_w = width_m * 0.5
    half_h = height_m * 0.5

    # Standard plane order (CCW): bottom-left, bottom-right, top-right, top-left
    verts = [
        (-half_w, -half_h, 0.0),  # 0 bottom-left
        (half_w, -half_h, 0.0),   # 1 bottom-right
        (half_w, half_h, 0.0),    # 2 top-right
        (-half_w, half_h, 0.0),   # 3 top-left
    ]
    faces = [(0, 1, 2, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # UVs
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uvs = [
        (0.0, 0.0),  # bottom-left
        (1.0, 0.0),  # bottom-right
        (1.0, 1.0),  # top-right
        (0.0, 1.0),  # top-left
    ]
    for i in range(4):
        uv_layer.data[i].uv = uvs[i]

    obj = bpy.data.objects.new(name, mesh)
    obj.scale = (1.0, 1.0, 1.0)  # ENFORCE: No scaling
    return obj


def make_image_material(img: bpy.types.Image, name: str):
    """Create emission material with image texture."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (500, 0)

    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Linear"
    tex.location = (0, 0)

    # Use emission for basemap visibility
    em = nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    em.location = (250, 0)
    links.new(tex.outputs["Color"], em.inputs["Color"])
    links.new(em.outputs["Emission"], out.inputs["Surface"])

    return mat


def import_rgb_basemap_tiles(folder: str):
    """
    Import RGB tiles as basemap planes.

    PHASE 2 Implementation: RGB-only, no height, correct XY mosaic.

    Args:
        folder: Path to folder containing dtk10_*.tif files

    Returns:
        (success: bool, imported_count: int)
    """
    if not folder or not os.path.isdir(folder):
        log_warn(f"[RGB Basemap] Folder not found: {folder}")
        return False, 0

    # Find all tile images
    files = [f for f in sorted(os.listdir(folder)) if os.path.splitext(f)[1].lower() in IMG_EXTS]
    if not files:
        log_warn(f"[RGB Basemap] No image files found in {folder}")
        return False, 0

    # Parse filenames
    raw_tiles = []
    unmatched = 0
    for name in files:
        parsed = parse_tile_name(name)
        if parsed:
            e_raw, n_raw, km = parsed
            raw_tiles.append((name, e_raw, n_raw, km))
        else:
            unmatched += 1

    if not raw_tiles:
        log_warn(f"[RGB Basemap] No tiles matched filename pattern (expected dtk10_XXXXX_YYYY_Z_*.tif)")
        return False, 0

    # === PFLICHT-LOG: After Parse ===
    log_info(f"[RGB Basemap] ╔═══════════════════════════════════╗")
    log_info(f"[RGB Basemap] ║ PARSE COMPLETE                    ║")
    log_info(f"[RGB Basemap] ╚═══════════════════════════════════╝")
    log_info(f"[RGB Basemap] Folder: {folder}")
    log_info(f"[RGB Basemap] Total files: {len(files)}")
    log_info(f"[RGB Basemap] Matched tiles: {len(raw_tiles)}")
    if unmatched:
        log_info(f"[RGB Basemap] Unmatched files: {unmatched}")

    # Determine tile size (mode of km values)
    km_values = [km for _, _, _, km in raw_tiles]
    km_counts = {}
    for km in km_values:
        km_counts[km] = km_counts.get(km, 0) + 1
    km_mode = max(km_counts.items(), key=lambda kv: kv[1])[0] if km_counts else 1
    tile_size_m = km_mode * 1000.0

    # Compute grid steps (ΔE_raw, ΔN_raw)
    e_vals = [e for _, e, _, _ in raw_tiles]
    n_vals = [n for _, _, n, _ in raw_tiles]
    delta_e_raw = most_common_positive_step(e_vals)
    delta_n_raw = most_common_positive_step(n_vals)

    if not delta_e_raw or not delta_n_raw:
        log_error("[RGB Basemap] Cannot infer grid steps (not enough distinct tiles)")
        return False, 0

    # Compute multipliers
    mult_e = tile_size_m / float(delta_e_raw)
    mult_n = tile_size_m / float(delta_n_raw)

    # === PFLICHT-LOG: Grid parameters ===
    log_info(f"[RGB Basemap] ╔═══════════════════════════════════╗")
    log_info(f"[RGB Basemap] ║ GRID PARAMETERS                   ║")
    log_info(f"[RGB Basemap] ╚═══════════════════════════════════╝")
    log_info(f"[RGB Basemap] ΔE_raw: {delta_e_raw}")
    log_info(f"[RGB Basemap] ΔN_raw: {delta_n_raw}")
    log_info(f"[RGB Basemap] mult_e: {mult_e:.6f}")
    log_info(f"[RGB Basemap] mult_n: {mult_n:.6f}")
    log_info(f"[RGB Basemap] tile_size_m: {tile_size_m:.1f}")

    # Compute world coordinates
    placements = []
    for name, e_raw, n_raw, km in raw_tiles:
        tile_size_local = km * 1000.0
        e_m = e_raw * mult_e
        n_m = n_raw * mult_n
        placements.append((name, e_raw, n_raw, e_m, n_m, tile_size_local))

    # Compute bounds
    min_e = min(p[3] for p in placements)
    min_n = min(p[4] for p in placements)
    max_e = max(p[3] + p[5] for p in placements)
    max_n = max(p[4] + p[5] for p in placements)

    # === PFLICHT-LOG: World bounds ===
    log_info(f"[RGB Basemap] ╔═══════════════════════════════════╗")
    log_info(f"[RGB Basemap] ║ WORLD BOUNDS (meters)             ║")
    log_info(f"[RGB Basemap] ╚═══════════════════════════════════╝")
    log_info(f"[RGB Basemap] minE: {min_e:.1f}")
    log_info(f"[RGB Basemap] minN: {min_n:.1f}")
    log_info(f"[RGB Basemap] maxE: {max_e:.1f}")
    log_info(f"[RGB Basemap] maxN: {max_n:.1f}")
    log_info(f"[RGB Basemap] Extent: {max_e - min_e:.1f} x {max_n - min_n:.1f} meters")

    # Create collection and origin
    col = ensure_collection(COLLECTION_NAME)
    origin = ensure_empty(ORIGIN_EMPTY_NAME)
    world = ensure_world_origin()

    # Enforce neutral transforms on parents
    origin.rotation_euler = (0.0, 0.0, 0.0)
    origin.scale = (1.0, 1.0, 1.0)
    if world:
        world.rotation_euler = (0.0, 0.0, 0.0)
        world.scale = (1.0, 1.0, 1.0)

    # Parent origin to world
    if origin.parent is None and world:
        origin.parent = world

    # Import tiles
    imported = 0
    log_info(f"[RGB Basemap] ╔═══════════════════════════════════╗")
    log_info(f"[RGB Basemap] ║ TILE IMPORT                       ║")
    log_info(f"[RGB Basemap] ╚═══════════════════════════════════╝")
    log_info(f"[RGB Basemap] Axis: E→X, N→Y, anchor={TILE_ANCHOR}, y_flip={Y_FLIP}")

    for idx, (name, e_raw, n_raw, e_m, n_m, tile_size_local) in enumerate(placements):
        obj_name = os.path.splitext(name)[0]

        # Skip if exists
        if bpy.data.objects.get(obj_name):
            continue

        # Load image
        try:
            img = bpy.data.images.load(os.path.join(folder, name), check_existing=True)
            img.colorspace_settings.name = "sRGB"
        except Exception as ex:
            log_warn(f"[RGB Basemap] Failed to load image {name}: {ex}")
            continue

        # Create plane with real meter size
        obj = make_plane_with_uv(obj_name, tile_size_local, tile_size_local)

        # Compute center (if CORNER, add offset)
        if TILE_ANCHOR.upper() == "CORNER":
            cx = e_m + (tile_size_local * 0.5)
            cy = n_m + (tile_size_local * 0.5)
        else:
            cx = e_m
            cy = n_m

        # Apply y-flip if needed (should be False for correct georef)
        cy_final = cy
        if Y_FLIP:
            cy_final = min_n - cy

        # Convert world -> local
        loc_x, loc_y = world_to_local(cx, cy_final)

        # Set location (ONLY translation, no scaling)
        obj.location.x = loc_x
        obj.location.y = loc_y
        obj.location.z = 0.0

        # Enforce neutral transforms
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        obj.parent = origin
        try:
            obj.matrix_parent_inverse.identity()
        except Exception:
            pass

        # Apply material
        mat = make_image_material(img, obj_name + "_MAT")
        obj.data.materials.clear()
        obj.data.materials.append(mat)

        # Link to collection
        link_exclusively_to_collection(obj, col)

        imported += 1

        # === PFLICHT-LOG: First 3 tiles ===
        if idx < 3:
            log_info(f"[RGB Basemap] ╔═══════════════════════════════════╗")
            log_info(f"[RGB Basemap] ║ Tile {idx + 1}: {obj_name[:30]}")
            log_info(f"[RGB Basemap] ╚═══════════════════════════════════╝")
            log_info(f"[RGB Basemap]   (E_raw, N_raw): ({e_raw}, {n_raw})")
            log_info(f"[RGB Basemap]   (E_m, N_m): ({e_m:.2f}, {n_m:.2f})")
            log_info(f"[RGB Basemap]   (x, y): ({loc_x:.2f}, {loc_y:.2f})")
            log_info(f"[RGB Basemap]   scale: {tuple(obj.scale)}")
            if tuple(obj.scale) != (1.0, 1.0, 1.0):
                log_warn(f"[RGB Basemap]   ⚠️  Scale is NOT (1,1,1)!")

    # Final summary
    log_info(f"[RGB Basemap] ╔═══════════════════════════════════╗")
    log_info(f"[RGB Basemap] ║ IMPORT COMPLETE                   ║")
    log_info(f"[RGB Basemap] ╚═══════════════════════════════════╝")
    log_info(f"[RGB Basemap] Imported: {imported} planes")
    log_info(f"[RGB Basemap] Collection: {COLLECTION_NAME}")
    log_info(f"[RGB Basemap] All scales: (1,1,1) ✓")

    return imported > 0, imported
