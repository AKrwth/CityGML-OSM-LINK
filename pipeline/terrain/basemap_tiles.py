import re
from pathlib import Path
import bpy
from ...utils.logging_system import log_info, log_warn, log_error
from ...utils.common import (
    ensure_world_origin,
    set_world_origin_from_minmax,
    world_to_local,
    link_exclusively_to_collection,
    WORLD_ORIGIN_NAME,
)

# ---------------- CONFIG ----------------
# Default tiles folder; can be overridden by passing tiles_dir to main()
TILES_DIR = r"C:\Users\Akhai\Desktop\DC_M1_OSM-CityGML\Kacheln\koeln_regbez"
COLLECTION_NAME = "KOELN_TILES"
ORIGIN_EMPTY_NAME = "KOELN_ORIGIN"

LIMIT = 0  # 0 = all
USE_LOCAL_ORIGIN = True  # re-zero for Blender numeric stability

USE_EMISSION = True
EMISSION_STRENGTH = 1.0

# Single source of truth for tile anchor contract (see pipeline/common.py)
from ...utils.common import TILE_ANCHOR
# Basemap tiles must NEVER flip geometry. If imagery appears upside down, fix via UVs/material mapping instead.
Y_FLIP = False

IMG_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}


def basemap_sanity(folder: str) -> str:
    """Lightweight folder check for UI validation."""
    p = Path(folder)
    if not p.is_dir():
        return "BaseMap: not a folder"

    files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXTS]
    if not files:
        return "BaseMap: no tile images (.tif/.png/.jpg) found"

    matched = [parse_tile_name(f) for f in files]
    matched = [m for m in matched if m]
    km_values = sorted({m[2] for m in matched}) if matched else []

    parts = [f"BaseMap: {len(files)} images"]
    if matched:
        parts.append(f"{len(matched)} match pattern")
        if km_values:
            parts.append(f"tile km: {km_values}")
    else:
        parts.append("no filename matches; placement may be skipped")

    return "; ".join(parts)

# dtk10_32288_5624_2_nw_col.tif  -> E, N, km
FILENAME_PATTERN = re.compile(
    r"dtk\d+_(\d+)_(\d+)_(\d+)_.*\.(tif|tiff|png|jpg|jpeg|webp)$",
    re.IGNORECASE
)

# EPSG:25832 plausibility ranges
E_RANGE = (100_000, 900_000)
N_RANGE = (5_000_000, 6_200_000)

# ---------------- HELPERS ----------------
def ensure_collection(name: str):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col

def ensure_empty(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "PLAIN_AXES"
        bpy.context.scene.collection.objects.link(obj)
    return obj

def parse_tile_name(p: Path):
    m = FILENAME_PATTERN.search(p.name)
    if not m:
        return None
    e_raw = int(m.group(1))
    n_raw = int(m.group(2))
    km = int(m.group(3))
    return e_raw, n_raw, km

def choose_scale_to_range(value: int, lo: int, hi: int):
    """
    Choose a multiplier from common powers of 10 so that value*mult falls into [lo, hi].
    Returns (scaled_value, multiplier). If nothing fits, returns (value, 1).
    """
    for mult in (1, 10, 100, 1000, 10000):
        v = value * mult
        if lo <= v <= hi:
            return v, mult
    return value, 1

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

    uv_layer = mesh.uv_layers.new(name="UVMap")
    # Match UVs to the vertex order above (no implicit rotation/mirror)
    uvs = [
        (0.0, 0.0),  # bottom-left
        (1.0, 0.0),  # bottom-right
        (1.0, 1.0),  # top-right
        (0.0, 1.0),  # top-left
    ]
    for i in range(4):
        uv_layer.data[i].uv = uvs[i]

    obj = bpy.data.objects.new(name, mesh)
    obj.scale = (1.0, 1.0, 1.0)
    return obj

def make_image_material(img: bpy.types.Image, name: str):
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

    if USE_EMISSION:
        em = nodes.new("ShaderNodeEmission")
        em.inputs["Strength"].default_value = EMISSION_STRENGTH
        em.location = (250, 0)
        links.new(tex.outputs["Color"], em.inputs["Color"])
        links.new(em.outputs["Emission"], out.inputs["Surface"])
    else:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (250, 0)
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    return mat

# ---------------- MAIN ----------------
def resolve_tiles_path(tiles_dir_override: str = None) -> Path:
    """Return a valid tiles folder path, using override or default."""
    candidate = tiles_dir_override or TILES_DIR
    tiles_path = Path(candidate)
    if not tiles_path.exists():
        raise FileNotFoundError(f"Tiles folder not found: {tiles_path}")
    return tiles_path


def main(tiles_dir: str = None):
    tiles_path = resolve_tiles_path(tiles_dir)

    col = ensure_collection(COLLECTION_NAME)
    origin = ensure_empty(ORIGIN_EMPTY_NAME)
    world_origin = ensure_world_origin()

    # HARD LOCK: parents must NEVER rotate or mirror
    origin.rotation_euler = (0.0, 0.0, 0.0)
    origin.scale = (1.0, 1.0, 1.0)
    world_origin.rotation_euler = (0.0, 0.0, 0.0)
    world_origin.scale = (1.0, 1.0, 1.0)

    files = sorted([p for p in tiles_path.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS])
    total_files = len(files)
    if LIMIT and LIMIT > 0:
        files = files[:LIMIT]

    # Parse filenames first
    raw_tiles = []  # (Path, e_raw, n_raw, km)
    unmatched = 0
    for f in files:
        parsed = parse_tile_name(f)
        if not parsed:
            unmatched += 1
            continue
        e_raw, n_raw, km = parsed
        raw_tiles.append((f, e_raw, n_raw, km))

    if not raw_tiles:
        log_warn("No tiles matched filename pattern.")
        log_warn("    Expected like: dtk10_32288_5624_2_nw_col.tif")
        return

    # Choose one consistent multiplier for all tiles (avoid per-tile differences)
    def median_int(vals):
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        mid = n // 2
        if n % 2:
            return vals_sorted[mid]
        return (vals_sorted[mid - 1] + vals_sorted[mid]) // 2

    # --- derive multipliers from grid spacing (robust against filename units) ---
    unique_km = sorted(set(km for _, _, _, km in raw_tiles))
    if len(unique_km) > 1:
        log_warn(f"Mixed tile sizes found in filenames: {unique_km}. Using first for grid step inference.")
    tile_size_m_ref = unique_km[0] * 1000.0

    e_step_raw = most_common_positive_step([e for _, e, _, _ in raw_tiles])
    n_step_raw = most_common_positive_step([n for _, _, n, _ in raw_tiles])

    if not e_step_raw or not n_step_raw:
        raise RuntimeError("Could not infer grid steps from filenames (not enough distinct tiles).")

    e_mult = tile_size_m_ref / float(e_step_raw)
    n_mult = tile_size_m_ref / float(n_step_raw)

    log_info(f"Inferred grid: ΔE_raw={e_step_raw}, ΔN_raw={n_step_raw}")
    log_info(f"Inferred multipliers: e_mult={e_mult}, n_mult={n_mult}")

    placements = []  # (Path, e_m, n_m, tile_size_m)
    for f, e_raw, n_raw, km in raw_tiles:
        tile_size_m = km * 1000.0
        e_m = e_raw * e_mult
        n_m = n_raw * n_mult
        if abs(tile_size_m - tile_size_m_ref) > 1e-6:
            log_info(f"Tile {f.name} has size {tile_size_m}m (ref {tile_size_m_ref}m) -> using its own size for plane, ref for spacing")
        placements.append((f, e_m, n_m, tile_size_m))

    # use min corner as origin for stable local coordinates
    min_e = min(p[1] for p in placements)
    min_n = min(p[2] for p in placements)
    max_e = max(p[1] + p[3] for p in placements)
    max_n = max(p[2] + p[3] for p in placements)

    log_info("==============================")
    log_info(" DTK Tiles → Planes (filename → EPSG:25832-ish meters)")
    log_info("==============================")
    log_info("Folder: " + str(tiles_path))
    log_info(f"Tiles (matched): {len(placements)} of {total_files} files")
    if unmatched:
        log_info(f"Skipped {unmatched} files that did not match pattern.")
    log_info(f"tile_size_m_ref = {tile_size_m_ref}")
    log_info(f"e_mult,n_mult = {e_mult},{n_mult}")
    log_info(f"Min corner (E,N): {min_e}, {min_n}")
    # Set shared WORLD_ORIGIN from basemap min/max (world meters) if not already set
    world = set_world_origin_from_minmax(min_e, min_n, max_e, max_n, source="BaseMap")
    if world:
        world["world_origin_set_by"] = "BaseMap"

    # Warn if an existing placement contract on the shared origin differs
    try:
        existing_e_mult = world.get("grid_e_mult")
        existing_n_mult = world.get("grid_n_mult")
        existing_tile_size = world.get("tile_size_m_ref")
        existing_anchor = world.get("tile_anchor")
        existing_y_flip = world.get("y_flip")
        tol = 1e-6
        if existing_e_mult is not None and abs(existing_e_mult - e_mult) > tol:
            log_warn(f"[BaseMap] WORLD_ORIGIN grid_e_mult mismatch: origin={existing_e_mult} current={e_mult}")
        if existing_n_mult is not None and abs(existing_n_mult - n_mult) > tol:
            log_warn(f"[BaseMap] WORLD_ORIGIN grid_n_mult mismatch: origin={existing_n_mult} current={n_mult}")
        if existing_tile_size is not None and abs(existing_tile_size - tile_size_m_ref) > tol:
            log_warn(f"[BaseMap] WORLD_ORIGIN tile_size_m_ref mismatch: origin={existing_tile_size} current={tile_size_m_ref}")
        if existing_anchor is not None and str(existing_anchor).upper() != str(TILE_ANCHOR).upper():
            log_warn(f"[BaseMap] WORLD_ORIGIN tile_anchor mismatch: origin={existing_anchor} current={TILE_ANCHOR}")
        if existing_y_flip is not None and bool(existing_y_flip) != bool(Y_FLIP):
            log_warn(f"[BaseMap] WORLD_ORIGIN y_flip mismatch: origin={existing_y_flip} current={Y_FLIP}")
    except Exception:
        pass

    if origin.parent is None:
        origin.parent = world
    log_info("Tip: Material Preview to see textures.")

    created = 0
    skipped_existing = 0
    for f, e_m, n_m, tile_size_m in placements:
        obj_name = f.stem
        if bpy.data.objects.get(obj_name):
            skipped_existing += 1
            continue

        img = bpy.data.images.load(str(f), check_existing=True)
        img.colorspace_settings.name = "sRGB"

        # Plane should be full tile_size_m (unit quad scaled to size)
        obj = make_plane_with_uv(obj_name, tile_size_m, tile_size_m)

        # Convert corner → center if filename encodes corner
        if TILE_ANCHOR.upper() == "CORNER":
            cx = e_m + (tile_size_m * 0.5)
            cy = n_m + (tile_size_m * 0.5)
        else:
            cx = e_m
            cy = n_m

        # Basemap placement: no geometry flipping; local coordinates only
        loc_x, loc_y = world_to_local(cx, cy)
        obj.location.x = loc_x
        obj.location.y = loc_y

        obj.location.z = 0.0
        # Agent-proof: enforce neutral transforms to avoid accidental mirroring
        obj.matrix_parent_inverse.identity()
        obj.rotation_mode = 'XYZ'
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        obj.parent = origin

        mat = make_image_material(img, obj_name + "_MAT")
        obj.data.materials.clear()
        obj.data.materials.append(mat)

        link_exclusively_to_collection(obj, col)

        created += 1

        if created == 1:
            log_info(
                f"[BaseMap] {obj.name}: tile_size_m={tile_size_m}, "
                f"dims=({obj.dimensions.x:.3f},{obj.dimensions.y:.3f}), "
                f"scale=({obj.scale.x:.3f},{obj.scale.y:.3f})"
            )

    origin["min_easting"] = float(min_e)
    origin["min_northing"] = float(min_n)
    # Persist placement contract on the shared WORLD_ORIGIN for downstream checks
    if world:
        try:
            world["grid_e_mult"] = float(e_mult)
            world["grid_n_mult"] = float(n_mult)
            world["tile_size_m_ref"] = float(tile_size_m_ref)
            world["tile_anchor"] = str(TILE_ANCHOR).upper()
            world["y_flip"] = bool(Y_FLIP)
        except Exception:
            pass

    log_info(f"Done. Created {created} planes in '{COLLECTION_NAME}'.")
    if skipped_existing > 0:
        log_info(f"Skipped {skipped_existing} already-existing objects (by name).")
    log_info("This is still 'filename-based georef'. For true georef, use .tfw or GeoTIFF tag reading.")

if __name__ == "__main__":
    main()
