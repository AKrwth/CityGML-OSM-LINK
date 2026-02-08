import os
import tempfile
from pathlib import Path

# Module loaded - logging configured at first call
try:
    import bpy  # type: ignore
except ModuleNotFoundError as exc:
    raise ImportError("bpy not found; run this add-on inside Blender.") from exc
from typing import Iterable, Tuple, Optional

from .logging_system import log_info, log_warn, log_error


def resolve_gpkg_path(raw_path: str) -> tuple[str, str]:
    """Resolve a GeoPackage path from either a direct .gpkg file or a directory.

    Accepts:
    - a path to a .gpkg file
    - a directory containing one or more *.gpkg files

    Selection rules for directories:
    - prefer *_READONLY.gpkg if present (case-insensitive)
    - else if exactly one *.gpkg exists, pick it
    - else pick the first *.gpkg in sorted order

    Returns: (resolved_file_path, info_message). If not resolvable, resolved is "".
    """
    if not raw_path:
        return "", "gpkg_path missing"

    p = Path(str(raw_path))
    try:
        p = p.expanduser()
    except Exception:
        pass

    if p.exists() and p.is_file() and p.suffix.lower() == ".gpkg":
        return str(p), "gpkg_path is a file"

    if p.exists() and p.is_dir():
        gpkg_files = sorted([x for x in p.glob("*.gpkg") if x.is_file()])
        if not gpkg_files:
            return "", f"gpkg_path is a directory with no *.gpkg: {p}"

        readonly = [x for x in gpkg_files if x.name.lower().endswith("_readonly.gpkg")]
        chosen = readonly[0] if readonly else (gpkg_files[0] if gpkg_files else None)
        if chosen is None:
            return "", f"gpkg_path directory had no usable *.gpkg: {p}"

        if readonly:
            return str(chosen), f"resolved from directory (preferred *_READONLY.gpkg); candidates={len(gpkg_files)}"
        if len(gpkg_files) == 1:
            return str(chosen), "resolved from directory (single *.gpkg)"
        return str(chosen), f"resolved from directory (multiple *.gpkg, picked first); candidates={len(gpkg_files)}"

    if p.suffix.lower() == ".gpkg":
        return "", f"gpkg file not found: {p}"
    return "", f"gpkg_path is neither .gpkg file nor directory: {p}"


def log_gpkg_resolution(raw_path: str, resolved_path: str, info: str, prefix: str = "[GPKG]"):
    try:
        log_info(f"{prefix} raw={raw_path}")
        log_info(f"{prefix} resolved={resolved_path or '—'} ({info})")
    except Exception:
        pass


# ============================================================================
# DATABASE READONLY ACCESS (GPKG/SQLite)
# ============================================================================
# All DB connections MUST use this function to ensure:
# - mode=ro (read-only URI mode)
# - PRAGMA query_only=ON (belt-and-suspenders read-only)
# - PRAGMA busy_timeout (prevent locks)
# - Consistent logging for debugging
# ============================================================================

import sqlite3
import shutil

DB_BUSY_TIMEOUT_MS = 5000


def open_db_readonly(db_path: str, log_open: bool = True) -> sqlite3.Connection:
    """
    Open a SQLite/GPKG database in strict read-only mode.

    Features:
    - URI mode=ro (filesystem read-only)
    - PRAGMA query_only=ON (SQLite query-only mode)
    - PRAGMA busy_timeout (prevent lock errors)
    - Optional logging for debugging

    Args:
        db_path: Path to .gpkg or .sqlite file
        log_open: If True, log the connection details

    Returns:
        sqlite3.Connection in read-only mode

    Raises:
        FileNotFoundError: If db_path doesn't exist
        sqlite3.Error: If connection fails
    """
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"DB not found: {p}")

    # Build read-only URI
    uri = f"file:{p.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)

    # Apply PRAGMAs for safety and performance
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA query_only = ON;")

    # Optional: row_factory for dict-like access
    conn.row_factory = sqlite3.Row

    if log_open:
        log_info(f"[DB] opened readonly: {p.name} | uri=1 mode=ro query_only=ON busy_timeout={DB_BUSY_TIMEOUT_MS}")

    return conn


def ensure_readonly_copy(gpkg_path: str, force_refresh: bool = False) -> str:
    """
    Ensure a _READONLY.gpkg copy exists for safe parallel access.

    If the original GPKG is modified more recently than the copy, or if
    force_refresh is True, create a fresh copy.

    Args:
        gpkg_path: Path to original .gpkg file
        force_refresh: Force re-copy even if _READONLY exists

    Returns:
        Path to the _READONLY.gpkg copy (or original if copy fails)
    """
    p = Path(gpkg_path)
    if not p.exists():
        return gpkg_path

    # If already a _READONLY file, use it directly
    if p.stem.lower().endswith("_readonly"):
        return gpkg_path

    readonly_path = p.parent / f"{p.stem}_READONLY{p.suffix}"

    try:
        # Check if copy is needed
        if readonly_path.exists() and not force_refresh:
            orig_mtime = p.stat().st_mtime
            copy_mtime = readonly_path.stat().st_mtime
            if copy_mtime >= orig_mtime:
                log_info(f"[DB] using existing _READONLY copy: {readonly_path.name}")
                return str(readonly_path)

        # Create fresh copy
        log_info(f"[DB] creating _READONLY copy: {p.name} -> {readonly_path.name}")
        shutil.copy2(str(p), str(readonly_path))
        return str(readonly_path)

    except Exception as ex:
        log_warn(f"[DB] could not create _READONLY copy: {ex}")
        return gpkg_path


class status_lines:
    """Collect status messages with pass/fail markers."""

    def __init__(self):
        self._lines = []

    def add(self, ok: bool, msg: str):
        prefix = "✔" if ok else "✖"
        self._lines.append(f"{prefix} {msg}")

    def render(self) -> str:
        return "\n".join(self._lines) if self._lines else "No status."


def check_dir(path: str, label: str) -> Tuple[bool, str]:
    if not path:
        return False, f"{label}: missing"
    if not os.path.isdir(path):
        return False, f"{label}: not a folder"
    return True, f"{label}: OK"


def check_file(path: str, label: str) -> Tuple[bool, str]:
    if not path:
        return False, f"{label}: missing"
    if not os.path.isfile(path):
        return False, f"{label}: not a file"
    return True, f"{label}: OK"


def count_files_by_ext(folder: str, exts: Iterable[str]) -> int:
    if not os.path.isdir(folder):
        return 0
    exts_l = {e.lower() for e in exts}
    n = 0
    try:
        for name in os.listdir(folder):
            _, ext = os.path.splitext(name)
            if ext.lower() in exts_l:
                n += 1
    except OSError:
        return 0
    return n


def apply_view_clip_end(context, clip_end: float):
    """Best-effort application of clip_end to all 3D view spaces."""
    win = getattr(context, "window", None)
    if win is None or not hasattr(win, "screen"):
        return
    for area in win.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type == "VIEW_3D":
                space.clip_end = float(clip_end)


# ---------------- OUTPUT DIR ----------------

def get_output_dir() -> Path:
    """Resolve the configured output folder; safe during early add-on import."""
    scene = getattr(bpy.context, "scene", None)
    settings = getattr(scene, "m1dc_settings", None) if scene else None
    out_raw = None
    source = "default"
    try:
        out_raw = getattr(settings, "output_dir", "") if settings else ""
        if out_raw:
            source = "scene_property"
    except Exception:
        out_raw = ""

    out = Path(out_raw) if out_raw else None
    if not out:
        out = Path(tempfile.gettempdir()) / "M1DC_Output"
        source = "tempdir"

    out.mkdir(parents=True, exist_ok=True)
    exists = out.exists()
    print(f"[OUTPUT_DIR] resolved={out} source={source} exists={exists}")
    return out


# Backward-compat alias for older imports expecting ensure_output_dir
def ensure_output_dir() -> Path:
    return get_output_dir()


# ---------------- TERRAIN CACHE DIR ----------------

def get_terrain_cache_dir(output_dir: str) -> str:
    """Derive terrain cache directory from output_dir.

    Returns:
        <output_dir>/_Merged/terrain_cache
    """
    return os.path.join(str(output_dir), "_Merged", "terrain_cache")


# ---------------- GEOSPATIAL CONTRACT ----------------
# Blender uses single-precision transforms; storing true EPSG:25832 eastings/northings
# (millions of meters) causes jitter and selection issues. Keep CRS truth in metadata,
# but work in a stable local frame anchored by the shared Empty M1DC_WORLD_ORIGIN:
#   x = E - E_min
#   y = N - N_min
# No rotation, no Y flip, no recentering once origin exists. All sources (CityGML,
# BaseMap GeoTIFF, OSM/GPKG) must apply the same world_to_local subtraction. This
# comment is intentionally preserved for future maintainers.

# ---------------- TILE ANCHOR CONTRACT ----------------
# Single source of truth: "CORNER" means E/N in filenames encode the tile's
# lower-left corner.  All tile importers (CityGML, BaseMap, RGB) must import
# this constant rather than re-defining it locally.
TILE_ANCHOR = "CORNER"  # "CORNER" | "CENTER"

# ---------------- WORLD ORIGIN HELPERS ----------------

WORLD_ORIGIN_NAME = "M1DC_WORLD_ORIGIN"
DEFAULT_CRS = "EPSG:25832"

SCENE_KEY_CRS = "M1DC_CRS"
SCENE_KEY_MIN_E = "M1DC_WORLD_MIN_E"
SCENE_KEY_MIN_N = "M1DC_WORLD_MIN_N"
SCENE_KEY_MAX_E = "M1DC_WORLD_MAX_E"
SCENE_KEY_MAX_N = "M1DC_WORLD_MAX_N"
SCENE_KEY_LOCKED = "M1DC_WORLD_ORIGIN_LOCKED"
SCENE_KEY_SOURCE = "M1DC_WORLD_ORIGIN_SOURCE"


def _scene():
    return getattr(bpy.context, "scene", None)


def get_scene_crs() -> str:
    scene = _scene()
    if scene and SCENE_KEY_CRS in scene:
        return str(scene[SCENE_KEY_CRS])
    return DEFAULT_CRS


def set_scene_crs(crs: Optional[str]) -> bool:
    scene = _scene()
    if scene is None:
        return False
    if scene.get(SCENE_KEY_LOCKED):
        return False
    scene[SCENE_KEY_CRS] = crs or DEFAULT_CRS
    return True


def ensure_world_origin(min_e: float = None, min_n: float = None, max_e: float = None, max_n: float = None, source: str = "", crs: Optional[str] = None):
    """Create the WORLD_ORIGIN object and optionally lock scene bounds once.

    When bounds are provided and the origin is not locked yet, they are stored on both
    the scene and the WORLD_ORIGIN object and then locked (write-once). Subsequent calls
    with bounds will be ignored, but the existing origin is still returned.
    """

    scene = _scene()
    obj = bpy.data.objects.get(WORLD_ORIGIN_NAME)
    if obj is None:
        obj = bpy.data.objects.new(WORLD_ORIGIN_NAME, None)
        obj.empty_display_type = "PLAIN_AXES"
        if scene:
            scene.collection.objects.link(obj)

    crs_val = crs or get_scene_crs()
    if "crs" not in obj:
        obj["crs"] = crs_val

    locked = bool(scene.get(SCENE_KEY_LOCKED)) if scene else False
    have_bounds = min_e is not None and min_n is not None and max_e is not None and max_n is not None

    if have_bounds and not locked:
        if scene:
            scene[SCENE_KEY_CRS] = crs_val
            scene[SCENE_KEY_MIN_E] = float(min_e)
            scene[SCENE_KEY_MIN_N] = float(min_n)
            scene[SCENE_KEY_MAX_E] = float(max_e)
            scene[SCENE_KEY_MAX_N] = float(max_n)
            scene[SCENE_KEY_LOCKED] = True
            if source:
                scene[SCENE_KEY_SOURCE] = source
        obj["crs"] = crs_val
        obj["world_min_easting"] = float(min_e)
        obj["world_min_northing"] = float(min_n)
        obj["world_max_easting"] = float(max_e)
        obj["world_max_northing"] = float(max_n)
        obj["world_origin_set_by"] = source or obj.get("world_origin_set_by", "")
        obj["world_origin_locked"] = True
        log_info(
            f"[{'BaseMap' if not source else source}] WORLD_ORIGIN locked: crs={crs_val} min=({float(min_e):.3f},{float(min_n):.3f}) max=({float(max_e):.3f},{float(max_n):.3f})"
        )
    elif have_bounds and locked:
        log_info(f"[{source or 'origin'}] WORLD_ORIGIN locked; reuse existing")

    return obj


def link_exclusively_to_collection(obj, coll):
    """Ensure obj is linked only to coll (no root collection duplicates)."""
    scene_root = bpy.context.scene.collection
    if obj.name not in coll.objects:
        coll.objects.link(obj)
    if obj.name in scene_root.objects:
        scene_root.objects.unlink(obj)
    for c in list(getattr(obj, "users_collection", []) or []):
        if c != coll:
            try:
                c.objects.unlink(obj)
            except Exception:
                pass


def is_world_origin_locked_by_basemap(scene_or_origin_obj=None) -> bool:
    origin = None
    if scene_or_origin_obj is not None and hasattr(scene_or_origin_obj, "get"):
        origin = scene_or_origin_obj if isinstance(scene_or_origin_obj, bpy.types.Object) else bpy.data.objects.get(WORLD_ORIGIN_NAME)
    else:
        origin = bpy.data.objects.get(WORLD_ORIGIN_NAME)
    if origin is None:
        return False
    return origin.get("world_origin_set_by") == "BaseMap"


def set_world_origin_from_minmax(min_e: float, min_n: float, max_e: float, max_n: float, crs: str = "EPSG:25832", source: str = ""):
    # Backward compatibility shim: uses new ensure_world_origin lock semantics
    return ensure_world_origin(min_e, min_n, max_e, max_n, source=source or "unknown", crs=crs)


def get_world_origin_minmax():
    scene = _scene()
    if scene and scene.get(SCENE_KEY_LOCKED):
        return (
            scene.get(SCENE_KEY_MIN_E),
            scene.get(SCENE_KEY_MIN_N),
            scene.get(SCENE_KEY_MAX_E),
            scene.get(SCENE_KEY_MAX_N),
        )

    world = bpy.data.objects.get(WORLD_ORIGIN_NAME)
    if world is None:
        return None, None, None, None
    return (
        world.get("world_min_easting"),
        world.get("world_min_northing"),
        world.get("world_max_easting"),
        world.get("world_max_northing"),
    )


def world_to_local(x: float, y: float):
    min_e, min_n, _, _ = get_world_origin_minmax()
    if min_e is None or min_n is None:
        return x, y
    return x - float(min_e), y - float(min_n)


def local_to_world(x: float, y: float):
    min_e, min_n, _, _ = get_world_origin_minmax()
    if min_e is None or min_n is None:
        return x, y
    return x + float(min_e), y + float(min_n)


def local_to_crs_xy(x_local: float, y_local: float, origin: Optional[dict] = None):
    """Convert local Blender XY back to CRS using stored WORLD_ORIGIN min values.

    origin: Optional object/dict with world_min_easting/world_min_northing. Falls back to scene values.
    """
    if origin and hasattr(origin, "get"):
        min_e = origin.get("world_min_easting")
        min_n = origin.get("world_min_northing")
        if min_e is not None and min_n is not None:
            return float(min_e) + x_local, float(min_n) + y_local

    min_e, min_n, _, _ = get_world_origin_minmax()
    if min_e is None or min_n is None:
        log_error("[Link] WORLD_ORIGIN missing: cannot convert local to CRS")
        return x_local, y_local
    return float(min_e) + x_local, float(min_n) + y_local


def bbox_iou_xy(a, b):
    """Compute IoU for 2D bboxes (minx,miny,maxx,maxy). Returns 0 if no overlap."""
    if not a or not b:
        return 0.0
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    inter = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    if area_a <= 0 or area_b <= 0:
        return 0.0
    return inter / (area_a + area_b - inter)


def bbox_centroid_xy(bbox):
    if not bbox:
        return None
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)
