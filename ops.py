import os
import re
import json
import sqlite3
import csv
import math
from datetime import datetime
from pathlib import Path
import bmesh

# ============================================================================
# OPERATOR POLL: Enforce phase dependencies (example: DC_OT_LoadTerrain)
# ============================================================================

@classmethod
def poll(cls, context):
    dc_props = context.scene.dc_props
    return dc_props.world_origin_set and not dc_props.terrain_loaded

# ============================================================================
# EPHEMERAL ERROR STATE (for draw context, avoids ID-property mutations)
# ============================================================================
_spreadsheet_sync_error = None  # Cached error message from last sync attempt


def _set_sync_error(msg):
    """Store error message in module-level cache (safe from draw context)."""
    global _spreadsheet_sync_error
    _spreadsheet_sync_error = msg


def _get_sync_error():
    """Retrieve cached error message (safe from draw context)."""
    return _spreadsheet_sync_error


# ============================================================================

try:
    from .utils.common import (
        get_world_origin_minmax,
        set_world_origin_from_minmax,
        ensure_world_origin,
        world_to_local,
        local_to_crs_xy,
        bbox_iou_xy,
        get_output_dir,
        link_exclusively_to_collection,
        get_scene_crs,
        # Database readonly access
        open_db_readonly,
        ensure_readonly_copy,
    )
except Exception as e:  # fallback if common fails to expose get_output_dir during early load
    from .utils.common import (
        get_world_origin_minmax,
        set_world_origin_from_minmax,
        ensure_world_origin,
        world_to_local,
        local_to_crs_xy,
        bbox_iou_xy,
        link_exclusively_to_collection,
        get_scene_crs,
    )
    # Fallback DB functions if import fails
    open_db_readonly = None
    ensure_readonly_copy = None

# Import terrain scaling module for proven fix automation
try:
    from .pipeline.terrain.terrain_scaling import scale_and_place_terrain_pair
except ImportError:
    scale_and_place_terrain_pair = None

# Import terrain world calibration module
try:
    from .pipeline.terrain.terrain_world_calibration import calibrate_terrain_to_world_bounds, bbox_size_xy_world
except ImportError as e:
    calibrate_terrain_to_world_bounds = None
    bbox_size_xy_world = None
    print("[TerrainCal] IMPORT FAILED:", repr(e))

    def get_output_dir():
        from pathlib import Path
        import tempfile
        try:
            from .utils.logging_system import log_error
        except ImportError:
            log_error = print

        out = Path(tempfile.gettempdir()) / "M1DC_Output"
        out.mkdir(parents=True, exist_ok=True)
        log_error(f"fallback get_output_dir used (import failure): {e}")
        return out
import bpy
from bpy.types import Operator
from .pipeline.citygml import import_citygml_folder, ensure_collection, ensure_empty, parse_citygml_tile_coords, iter_citygml_buildings
from .pipeline.terrain import basemap_tiles as bm
from .pipeline.terrain import rgb_basemap_import
from .pipeline.terrain import dgm_terrain_import
from .pipeline.terrain import z_alignment
from .pipeline.terrain import terrain_validation
from .pipeline.osm import choose_table_and_id, load_osm_features, query_geoms_by_point
from .pipeline.linking import ensure_link_dbs
from .utils.common import get_terrain_cache_dir
from .pipeline.diagnostics import (
    run_diagnostic,
    run_debug_report,
    run_full_gpkg_gml_report,
    write_m1dc_report_txt,
    DEFAULT_EXPORT_PATH,
    DEFAULT_EXPORT_PATH_DEBUG,
    DEFAULT_EXPORT_PATH_FULL,
)
from .pipeline.diagnostics.geometry_tripwires import run_geometry_tripwires
from .pipeline.diagnostics.placement_checks import run_placement_tripwires
from .pipeline.diagnostics import face_attr_tools
from .pipeline.diagnostics import legend_encoding
from .utils.logging_system import log_info, log_warn, log_error, get_logger
from .utils.geometry import (
    extract_wkb_from_gpkg,
    read_uint32,
    parse_wkb_polygon,
    parse_wkb_geoms,
    ring_area,
    point_in_ring,
    point_in_polygon,
    point_segment_dist_sq,
    ring_min_dist_sq,
    bbox_world_minmax_xy,
    detect_dem_placement_mode,
    localize_mesh_data_to_world_min,
    hash_color,
    apply_viewport_solid_cavity,
)

import struct
from math import inf, sqrt

from bpy.props import BoolProperty, IntProperty, StringProperty, EnumProperty

CITYGML_EXTS = (".gml", ".xml", ".citygml")
IMG_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp")
DEFAULT_OSM_COLUMNS = [
    "building",
    "name",
    "height",
    "building_levels",
    "roof_shape",
    "roof_height",
    "roof_material",
    "addr_housenumber",
    "addr_street",
    "addr_city",
    "building_use",
    "material",
    "layer",
]

# TASK A — Add near top of file, near other module-level constants/helpers

CRITICAL_ATTR_SPECS = {
    # Float attrs used by Phase 3 writeback — MUST be FLOAT
    "link_conf":   ("FACE", "FLOAT"),
    "link_dist_m": ("FACE", "FLOAT"),
    "link_iou":    ("FACE", "FLOAT"),
    
    # Int attrs used throughout pipeline — MUST be INT
    "osm_id_int":  ("FACE", "INT"),
    "osm_id":      ("FACE", "INT"),
    "building_idx":("FACE", "INT"),
    "gml_building_idx":("FACE", "INT"),
    "gml_polygon_idx": ("FACE", "INT"),
    "link_dist_mm":("FACE", "INT"),
}

FEATURE_TABLE_FALLBACK = "koelnregbez251213osm__multipolygons"
FIXED_FEATURE_COLS = ["name", "type", "building", "amenity", "landuse", "boundary", "admin_level", "aeroway"]
FEATURE_CODE_COLS = list(FIXED_FEATURE_COLS)


def _settings(context):
    return getattr(context.scene, "m1dc_settings", None)


def _count_files(folder, exts):
    if not folder or not os.path.isdir(folder):
        return 0
    count = 0
    for name in os.listdir(folder):
        if name.lower().endswith(exts):
            count += 1
    return count


def _first_table_in_gpkg(gpkg_path):
    try:
        from .utils.common import resolve_gpkg_path
        resolved, _ = resolve_gpkg_path(gpkg_path)
        gpkg_path = resolved or gpkg_path
    except Exception:
        pass
    if not gpkg_path or not os.path.isfile(gpkg_path):
        return ""
    try:
        # Use centralized readonly DB access
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            # Fallback if import failed
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()
        cur.execute("SELECT table_name FROM gpkg_contents ORDER BY table_name;")
        rows = cur.fetchall()
        con.close()
        return rows[0][0] if rows else ""
    except Exception:
        return ""


def _sanitize_identifier(name: str) -> str:
    return (name or "").replace('"', '""')


def _normalize_osm_id(value) -> str:
    """Normalize any OSM ID representation to a consistent string key.

    Handles: int, float (strips .0), string with decimals, whitespace.
    Returns: stripped integer string like "12345", or empty string if invalid.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        # 12345.0 -> "12345"
        if value == int(value):
            return str(int(value))
        return str(value).strip()
    if isinstance(value, int):
        return str(value)
    # string
    s = str(value).strip()
    if not s or s in ("—", "None", "null"):
        return ""
    # Try to parse as float then int to strip decimals like "12345.0"
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
        return s
    except (ValueError, OverflowError):
        return s


# ── Key normalization: single source of truth (pipeline/linking/key_normalization.py) ──
from .pipeline.linking.key_normalization import normalize_source_tile


def norm_source_tile(v) -> str:
    """Thin wrapper delegating to the canonical normalize_source_tile."""
    return normalize_source_tile(v)


def _norm_source_tile(x: str) -> str:
    # Backwards-compat alias (legacy call sites)
    return normalize_source_tile(x)


def sanitize_attr_name(name: str) -> str:
    """Sanitize column names to Blender-safe attribute identifiers."""
    if not name:
        return "attr"
    safe = []
    for ch in str(name):
        safe.append(ch if (ch.isalnum() or ch == "_") else "_")
    ident = "".join(safe)
    while "__" in ident:
        ident = ident.replace("__", "_")
    ident = ident.strip("_") or "attr"
    if ident[0].isdigit():
        ident = f"a_{ident}"
    return ident[:60]


def _list_user_tables(cur):
    rows = cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;").fetchall()
    tables = []
    for r in rows:
        name = str(r[0])
        if name.startswith("gpkg_") or name.startswith("rtree_") or name.startswith("sqlite_"):
            continue
        tables.append(name)
    return tables


# ============================================================================
# MATERIALIZE PHASE 3 HELPERS — Canonical preflight for FACE attribute storage
# ============================================================================

# TASK C — Add helper function for schema verification

def _proof_attr(mesh, name, want_type):
    """
    Verify attribute exists with correct type and length.
    Returns True if ok, False otherwise.
    Prints [PROOF][ATTR] diagnostic.
    """
    a = mesh.attributes.get(name)
    if not a:
        print(f"[PROOF][ATTR] MISSING {name}")
        return False
    ok = (a.domain == "FACE" and a.data_type == want_type and len(a.data) == len(mesh.polygons))
    print(f"[PROOF][ATTR] {name} type={a.data_type} want={want_type} len={len(a.data)} faces={len(mesh.polygons)} ok={ok}")
    return ok

def ensure_object_mode():
    """Ensure Blender is in OBJECT mode (required for reliable FACE attribute allocation)."""
    import bpy
    if bpy.context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass


def bake_mesh_datablock(obj):
    """
    Forces Blender to allocate FACE attribute storage by copying the mesh datablock.
    This ensures len(attr.data) == len(polygons) for all FACE attributes.
    Returns the (possibly new) mesh datablock.
    """
    obj.data = obj.data.copy()
    obj.data.update()
    return obj.data


# ensure_face_attr is defined below (line ~955) with Clobber Guard (FIX2).
# Do NOT add a second definition here — the guarded version protects Phase-3 IDs.


def ensure_face_storage_ready(obj, attr_specs):
    """
    Comprehensive preflight check: ensure OBJECT mode, bake mesh, create/normalize attributes,
    and verify FACE attribute storage is actually allocated.
    
    attr_specs: list of tuples (name, data_type) e.g. [("osm_id","INT"),("link_conf","FLOAT")]
    
    Raises RuntimeError if storage allocation fails after retries.
    Returns the (possibly new) mesh datablock.
    """
    ensure_object_mode()
    me = bake_mesh_datablock(obj)
    nfaces = len(me.polygons)

    # Create / normalize attributes
    for name, dt in attr_specs:
        ensure_face_attr(me, name, dt)

    # Verify storage allocation (first pass)
    bad = []
    for name, _dt in attr_specs:
        a = me.attributes.get(name)
        if a is None or len(a.data) != nfaces:
            bad.append((name, 0 if a is None else len(a.data), nfaces))

    if bad:
        print(f"[DEBUG] FACE storage not ready on first pass: {bad}. Retrying with one more bake...")
        # One more bake attempt
        me = bake_mesh_datablock(obj)
        for name, dt in attr_specs:
            ensure_face_attr(me, name, dt)
        
        # Second verification
        bad2 = []
        for name, _dt in attr_specs:
            a = me.attributes.get(name)
            if a is None or len(a.data) != len(me.polygons):
                bad2.append((name, 0 if a is None else len(a.data), len(me.polygons)))
        
        if bad2:
            raise RuntimeError(f"FACE attribute storage not allocated after bake retry: {bad2}")
        else:
            print(f"[DEBUG] FACE storage ready after retry.")

    return me


def _list_feature_tables(cur):
    rows = cur.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features' ORDER BY table_name;").fetchall()
    tables = []
    for r in rows:
        name = str(r[0])
        if name.startswith("gpkg_") or name.startswith("rtree_") or name.startswith("sqlite_"):
            continue
        tables.append(name)
    return tables


def _detect_feature_table(gpkg_path):
    """Pick a feature table that has an osm_id column.
    Priority: FEATURE_TABLE_FALLBACK if present with osm_id, else first table containing osm_id.
    """
    try:
        from .utils.common import resolve_gpkg_path
        resolved, _ = resolve_gpkg_path(gpkg_path)
        gpkg_path = resolved or gpkg_path
    except Exception:
        pass
    if not gpkg_path or not os.path.isfile(gpkg_path):
        return "", []
    try:
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()

        def table_cols(table_name):
            t_sane = _sanitize_identifier(table_name)
            rows = cur.execute(f'PRAGMA table_info("{t_sane}");').fetchall()
            return [row[1] for row in rows]

        # fallback table first
        candidate_tables = []
        rows = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'rtree_%';").fetchall()
        for r in rows:
            name = str(r[0])
            candidate_tables.append(name)

        chosen = ""
        cols = []
        if FEATURE_TABLE_FALLBACK in candidate_tables:
            cols = table_cols(FEATURE_TABLE_FALLBACK)
            if "osm_id" in cols:
                chosen = FEATURE_TABLE_FALLBACK
        if not chosen:
            for tbl in candidate_tables:
                cols = table_cols(tbl)
                if "osm_id" in cols:
                    chosen = tbl
                    break
        try:
            con.close()
        except Exception:
            pass
        return chosen or "", cols if chosen else []
    except Exception:
        return "", []


def _refresh_tables_and_columns(s, reset_selection=False):
    """Detect tables, choose table/id_col, and populate column options."""
    gpkg_path = getattr(s, "gpkg_path", "")
    try:
        from .utils.common import resolve_gpkg_path, log_gpkg_resolution
        resolved, info = resolve_gpkg_path(gpkg_path)
        log_gpkg_resolution(gpkg_path, resolved, info, prefix="[Spreadsheet][GPKG]")
        if resolved and resolved != gpkg_path:
            s.gpkg_path = resolved
            gpkg_path = resolved
    except Exception:
        pass
    s.inspector_last_error = ""
    if not gpkg_path or not os.path.isfile(gpkg_path):
        s.attr_table = ""
        s.id_col = ""
        s.status_gpkg_table = ""
        s.status_gpkg_id_col = ""
        s.spreadsheet_tables_cache = "[]"
        s.spreadsheet_columns_available.clear()
        return [], []

    prefer_table = None
    prefer_id_col = None
    try:
        prefer_table, prefer_id_col = choose_table_and_id(gpkg_path)
    except Exception:
        prefer_table, prefer_id_col = None, None

    try:
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        tables = _list_user_tables(cur)
        s.spreadsheet_tables_cache = json.dumps(tables)

        columns_cache = {}
        for t in tables:
            t_sane = _sanitize_identifier(t)
            rows = cur.execute(f'PRAGMA table_info("{t_sane}");').fetchall()
            columns_cache[t] = [row[1] for row in rows]

        # Prefer osm_way_id (TEXT) over osm_id to avoid type mismatch in many GPKGs.
        preferred_ids = [prefer_id_col, getattr(s, "id_col", ""), "osm_way_id", "osm_id", "id", "fid"]
        preferred_ids = [p for p in preferred_ids if p]

        chosen_table = None
        if getattr(s, "spreadsheet_table", "") and s.spreadsheet_table in columns_cache:
            chosen_table = s.spreadsheet_table
        elif prefer_table and prefer_table in columns_cache:
            chosen_table = prefer_table
        elif tables:
            chosen_table = tables[0]

        chosen_id = ""
        cols = columns_cache.get(chosen_table or "", [])
        for cand in preferred_ids:
            if cand in cols:
                chosen_id = cand
                break
        if not chosen_id and cols:
            chosen_id = cols[0]
        if not chosen_id:
            s.inspector_last_error = "ID column not detected"

        s.attr_table = chosen_table or ""
        s.id_col = chosen_id or ""
        s.status_gpkg_table = s.attr_table
        s.status_gpkg_id_col = s.id_col
        s.spreadsheet_table = s.attr_table or ""

        # populate column options
        prev_selected = set()
        if not reset_selection:
            prev_selected = {opt.name for opt in s.spreadsheet_columns_available if opt.selected}
        s.spreadsheet_columns_available.clear()
        default_set = set(DEFAULT_OSM_COLUMNS)
        for col in cols:
            item = s.spreadsheet_columns_available.add()
            item.name = col
            item.selected = col in prev_selected or col in default_set

        return tables, cols
    except Exception as ex:
        s.inspector_last_error = f"Detect failed: {ex}"
        return [], []


def spreadsheet_refresh_tables_only(s, reset_selection=False):
    """Refresh table and column caches without UI draw involvement."""
    if s is None:
        return
    s.spreadsheet_silent = True
    try:
        _refresh_tables_and_columns(s, reset_selection=reset_selection)
    finally:
        s.spreadsheet_silent = False


def _current_table_and_id(s):
    table = getattr(s, "spreadsheet_table", "") or getattr(s, "attr_table", "") or getattr(s, "status_gpkg_table", "")
    id_col = getattr(s, "id_col", "") or getattr(s, "status_gpkg_id_col", "")
    return table, id_col


def spreadsheet_invalidate_and_rebuild(context, s, reason="unknown"):
    """Atomically invalidate and rebuild spreadsheet after table/columns change.
    
    Called by table change callback or operator. Ensures:
    - Column options reflect selected table schema
    - Selected columns are sanitized (invalid ones removed)
    - Rows are rebuilt for active mesh
    - UIList state is consistent
    """
    if s is None or getattr(s, "spreadsheet_silent", False):
        return
    
    s.spreadsheet_silent = True
    try:
        # Step 1: Update available columns from new table
        _refresh_tables_and_columns(s, reset_selection=True)
        
        # Step 2: Sanitize column selections (remove non-existent)
        table, id_col = _current_table_and_id(s)
        if table and id_col:
            valid_cols = {opt.name for opt in s.spreadsheet_columns_available}
            for opt in s.spreadsheet_columns_available:
                if opt.selected and opt.name not in valid_cols:
                    opt.selected = False
        
        # Step 3: Rebuild rows if mesh is active
        active_obj = getattr(context, "object", None)
        if active_obj and active_obj.type == "MESH":
            _build_spreadsheet_rows(context, s)
        else:
            s.spreadsheet_rows.clear()
            s.spreadsheet_last_error = ""
    except Exception as ex:
        s.spreadsheet_last_error = f"Invalidate failed: {ex}"
    finally:
        s.spreadsheet_silent = False


def _ensure_table_and_columns(s):
    table, id_col = _current_table_and_id(s)
    if not table or not id_col:
        _refresh_tables_and_columns(s)


def _selected_osm_columns(s):
    return [opt.name for opt in getattr(s, "spreadsheet_columns_available", []) if getattr(opt, "selected", False)]


def refresh_osm_feature_tables(s, reset_selection=False):
    """Refresh available feature tables from the GPKG and choose a default."""
    gpkg_path = getattr(s, "gpkg_path", "")
    s.osm_feature_tables_cache = "[]"
    if not gpkg_path or not os.path.isfile(gpkg_path):
        s.osm_feature_table = ""
        return []
    try:
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()
        tables = _list_feature_tables(cur)
        s.osm_feature_tables_cache = json.dumps(tables)

        preferred = getattr(s, "osm_feature_table", "")
        chosen = preferred if preferred in tables else (tables[0] if tables else "")
        s.osm_feature_table = chosen
        try:
            con.close()
        except Exception:
            pass
        refresh_osm_feature_columns(s, reset_selection=reset_selection)
        return tables
    except Exception as ex:
        s.osm_feature_table = ""
        s.inspector_last_error = f"Feature table refresh failed: {ex}"
        return []


def _preferred_id_columns():
    # CANONICAL: osm_id is the only OSM key used for joins (HARD RULE)
    return ["osm_id", "osm_way_id", "id", "fid", "ogc_fid"]


def _cap_feature_selection(s, max_sel=8):
    selected = [opt for opt in getattr(s, "osm_feature_columns", []) if getattr(opt, "selected", False)]
    if len(selected) <= max_sel:
        return
    for opt in selected[max_sel:]:
        opt.selected = False


def refresh_osm_feature_columns(s, reset_selection=False):
    """Refresh columns for the selected feature table and apply defaults."""
    gpkg_path = getattr(s, "gpkg_path", "")
    table = getattr(s, "osm_feature_table", "")
    if not gpkg_path or not os.path.isfile(gpkg_path) or not table:
        s.osm_id_column = "osm_id"  # CANONICAL: osm_id only (HARD RULE)
        s.osm_feature_columns.clear()
        return []
    try:
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()
        t_sane = _sanitize_identifier(table)
        rows = cur.execute(f'PRAGMA table_info("{t_sane}");').fetchall()
        try:
            con.close()
        except Exception:
            pass

        cols = [row[1] for row in rows]
        prev_selected = {opt.name for opt in s.osm_feature_columns if opt.selected} if not reset_selection else set()

        s.osm_feature_columns.clear()
        default_pick = {"name", "type", "building", "amenity", "admin_level", "boundary", "landuse", "aeroway"}
        for c in cols:
            item = s.osm_feature_columns.add()
            item.name = c
            item.selected = (c in prev_selected) or (c in default_pick)

        _cap_feature_selection(s, max_sel=8)

        preferred_ids = _preferred_id_columns()
        if getattr(s, "osm_id_column", "") not in cols:
            chosen_id = next((c for c in preferred_ids if c in cols), cols[0] if cols else "")
            s.osm_id_column = chosen_id or "osm_id"
        else:
            s.osm_id_column = s.osm_id_column
        return cols
    except Exception as ex:
        s.osm_feature_columns.clear()
        s.osm_id_column = "osm_id"  # CANONICAL: osm_id only (HARD RULE)
        s.inspector_last_error = f"Feature column refresh failed: {ex}"
        return []


def _selected_feature_columns(s, max_sel=8):
    cols = [opt.name for opt in getattr(s, "osm_feature_columns", []) if getattr(opt, "selected", False)]
    if len(cols) > max_sel:
        cols = cols[:max_sel]
        # enforce cap persistently
        selected_seen = 0
        for opt in getattr(s, "osm_feature_columns", []):
            if getattr(opt, "selected", False):
                selected_seen += 1
                if selected_seen > max_sel:
                    opt.selected = False
    return cols


def _resolve_obj_link_info(obj):
    if obj is None:
        return None
    info = {
        "source_tile": obj.get("source_tile"),
        "building_idx": obj.get("building_idx"),
        "osm_id": obj.get("osm_id") or obj.get("osm_way_id"),
    }
    if info["osm_id"] is None:
        try:
            link_map = json.loads(obj.get("osm_link_map_json", "{}"))
            if info.get("building_idx") is not None:
                osm_entry = link_map.get(str(int(info["building_idx"]))) if isinstance(link_map, dict) else None
                if isinstance(osm_entry, dict):
                    info["osm_id"] = osm_entry.get("osm_way_id") or osm_entry.get("osm_id")
        except Exception:
            pass
    return info


def _lookup_osm_from_links_db(s, source_tile, building_idx):
    link_db = getattr(s, "links_db_path", "")
    if not link_db:
        return None
    p = Path(link_db)
    if not p.exists():
        return None
    try:
        if open_db_readonly:
            con = open_db_readonly(str(p), log_open=False)
        else:
            uri = f"file:{p.as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        row = cur.execute(
            "SELECT osm_id FROM gml_osm_links WHERE source_tile=? AND building_idx=? LIMIT 1;",
            (str(source_tile), int(building_idx)),
        ).fetchone()
        con.close()
        if row:
            try:
                return row["osm_id"]  # CANONICAL: osm_id only (HARD RULE)
            except Exception:
                return None
    except Exception:
        return None
    return None


def _get_active_mesh(context):
    obj = getattr(context, "object", None)
    if obj is None or obj.type != "MESH" or obj.data is None:
        return None, None
    return obj, obj.data


def _sync_edit_mesh(obj):
    """Ensure obj.data reflects Edit Mode changes."""
    try:
        if obj and obj.mode == 'EDIT':
            obj.update_from_editmode()
    except Exception:
        pass


def _get_active_face_poly_index(obj):
    """Return active face polygon index in Edit Mode, or None."""
    if obj is None or obj.type != "MESH" or obj.data is None:
        return None
    if obj.mode != 'EDIT':
        return None
    _sync_edit_mesh(obj)
    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    if bm is None or not bm.faces:
        return None
    bm.faces.ensure_lookup_table()
    face = bm.faces.active if bm.faces else None
    if face is None:
        face = next((f for f in bm.faces if f.select), None)
    if face is None:
        return None
    return face.index


def _read_face_attr_auto(obj, attr_name, face_index, default=None):
    """Read FACE attribute robustly.

    - In EDIT mode: read from BMesh layers (int/float) because mesh.attributes data arrays may be length 0.
    - In OBJECT mode: read from mesh.attributes.
    """
    if obj is None or obj.type != "MESH" or obj.data is None or face_index is None:
        return default

    mesh = obj.data

    # EDIT mode → BMesh
    if getattr(obj, "mode", None) == "EDIT":
        _sync_edit_mesh(obj)
        bm = bmesh.from_edit_mesh(mesh)
        if bm is None or not bm.faces:
            return default
        bm.faces.ensure_lookup_table()
        if face_index >= len(bm.faces):
            return default
        f = bm.faces[face_index]

        li = bm.faces.layers.int.get(attr_name)
        if li is not None:
            try:
                return int(f[li])
            except Exception:
                return default

        lf = bm.faces.layers.float.get(attr_name)
        if lf is not None:
            try:
                return float(f[lf])
            except Exception:
                return default

        return default

    # OBJECT mode → mesh.attributes
    attrs = getattr(mesh, "attributes", None)
    if attrs is None:
        return default
    a = attrs.get(attr_name)
    if not a or a.domain != "FACE":
        return default
    if face_index >= len(a.data):
        return default
    return getattr(a.data[face_index], "value", default)


def _inspect_active_face_impl(s, mesh, poly_idx):
    """Inspect face attributes at poly_idx and populate inspector data.

    Shows RAW face attributes even when legend decoding hasn't been run.
    This makes Inspector independent of legend build status.
    """
    if mesh is None or poly_idx is None:
        return None

    face_count = len(mesh.polygons)
    if poly_idx >= face_count:
        return None

    # Clear previous inspector data
    try:
        s.inspector_decoded_attrs.clear()
    except Exception:
        pass

    result = {"summary": "", "attrs": {}}

    # Core link attributes (always check these)
    core_attrs = [
        "osm_way_id", "osm_id_int", "link_conf", "link_dist_m", "link_iou",
        "has_link", "gml_building_idx", "building_idx", "source_tile_id",
    ]
    # OSM feature string attributes
    osm_string_attrs = [
        "building", "amenity", "landuse", "name", "shop", "office",
        "tourism", "leisure", "historic", "man_made", "highway",
        "public_transport", "railway", "natural", "waterway", "aeroway",
    ]
    # Legend code attributes
    legend_code_attrs = [f"osm_{k}_code" for k in [
        "building", "amenity", "landuse", "shop", "office", "tourism",
        "leisure", "historic", "man_made", "highway",
    ]]

    all_candidate_attrs = core_attrs + osm_string_attrs + legend_code_attrs
    found_attrs = {}

    for attr_name in all_candidate_attrs:
        a = mesh.attributes.get(attr_name)
        if a and a.domain == "FACE" and poly_idx < len(a.data):
            val = a.data[poly_idx].value
            # Skip default/empty values for clean display
            if val not in (0, 0.0, "", b""):
                found_attrs[attr_name] = val

    # Also scan ALL face attrs (catch any custom ones)
    for a in mesh.attributes:
        if a.domain == "FACE" and a.name not in found_attrs:
            if poly_idx < len(a.data):
                val = a.data[poly_idx].value
                if val not in (0, 0.0, "", b""):
                    found_attrs[a.name] = val

    # Populate inspector properties
    osm_id = found_attrs.get("osm_id_int", found_attrs.get("osm_way_id", 0))
    bidx = found_attrs.get("gml_building_idx", found_attrs.get("building_idx", 0))
    conf = found_attrs.get("link_conf", 0.0)

    try:
        s.inspector_osm_id = str(osm_id) if osm_id else ""
        s.inspector_building_idx = str(bidx) if bidx else ""
        s.inspector_link_conf = f"{conf:.3f}" if isinstance(conf, float) else str(conf)
    except Exception:
        pass

    # Populate decoded attrs (works with raw data even without legend)
    for attr_name, val in sorted(found_attrs.items()):
        try:
            item = s.inspector_decoded_attrs.add()
            item.name = attr_name
            item.value = str(val)
        except Exception:
            pass

    has_link = bool(found_attrs.get("has_link", 0))
    has_legend = any(k.endswith("_code") for k in found_attrs)
    mode_desc = "linked" if has_link else "unlinked"
    if has_legend:
        mode_desc += "+legend"
    else:
        mode_desc += " (raw, no legend codes)"

    summary = f"face[{poly_idx}] {mode_desc} | {len(found_attrs)} attrs"
    result["summary"] = summary
    result["attrs"] = found_attrs

    log_info(f"[Inspector] {summary}")
    return result


# ---------------------------------------------------------------------------
# Inspector Query Implementation (Fix 3)
# ---------------------------------------------------------------------------

def _parse_inspector_query(query_text):
    """Parse inspector query text into (attr_name, operator, value).

    Supported forms:
    - "amenity=university"   → ("osm_amenity_code", "==", <code>)
    - "amenity"              → ("osm_amenity_code", ">", 0)
    - "building_code>0"      → ("osm_building_code", ">", 0)
    - "osm_building_code=5"  → ("osm_building_code", "==", 5)

    Returns list of (attr_name, op, value) tuples.
    """
    if not query_text or not query_text.strip():
        return []

    parts = [p.strip() for p in query_text.split(",")]
    filters = []

    for part in parts:
        if not part:
            continue

        # Try "attr=value" or "attr>value"
        for op_char in ("=", ">", "<"):
            if op_char in part:
                key, _, val_str = part.partition(op_char)
                key = key.strip()
                val_str = val_str.strip()
                break
        else:
            # Bare key like "amenity" → means any nonzero code
            key = part.strip()
            op_char = ">"
            val_str = "0"

        # Normalize key to code attr name
        code_attr = _normalize_to_code_attr(key)

        # Resolve value: either integer code or text→code via legend
        try:
            int_val = int(val_str)
        except (ValueError, TypeError):
            # Text value → encode via legend
            feature_key = code_attr.replace("osm_", "").replace("_code", "")
            cache_key = f"{feature_key}_code"
            try:
                from .pipeline.diagnostics.legend_encoding import legend_encode
                int_val = legend_encode(cache_key, val_str)
                if int_val == 0:
                    # Try case-insensitive
                    from .pipeline.diagnostics.legend_encoding import _ENCODE_CACHE
                    cache = _ENCODE_CACHE.get(cache_key, {})
                    for k, v in cache.items():
                        if k.lower() == val_str.lower():
                            int_val = v
                            break
            except Exception:
                int_val = 0

        op_str = "==" if op_char == "=" else op_char
        filters.append((code_attr, op_str, int_val))

    return filters


def _normalize_to_code_attr(key):
    """Normalize a key to the full osm_*_code attribute name.

    Examples:
    - "amenity"           → "osm_amenity_code"
    - "building"          → "osm_building_code"
    - "building_code"     → "osm_building_code"
    - "osm_building_code" → "osm_building_code"
    """
    k = key.strip()
    if k.startswith("osm_") and k.endswith("_code"):
        return k
    if k.endswith("_code"):
        return f"osm_{k}"
    if k.startswith("osm_"):
        return f"{k}_code"
    return f"osm_{k}_code"


def _safe_read_face_int(mesh, attr_name, face_idx):
    """Read an INT face attribute value safely, handling Blender 4.5 empty-data edge case.

    In Blender 4.5, me.attributes[attr].data can have size 0 in Edit Mode
    or when reading non-evaluated mesh. Falls back to evaluated depsgraph mesh.

    Returns int value or 0 on failure.
    """
    attr = mesh.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return 0

    # Fast path: data array is populated and index is valid
    if len(attr.data) > 0 and face_idx < len(attr.data):
        try:
            return int(attr.data[face_idx].value)
        except (IndexError, RuntimeError):
            pass

    # Slow path: try evaluated mesh via depsgraph
    try:
        import bpy
        depsgraph = bpy.context.evaluated_depsgraph_get()
        # Find the object that owns this mesh
        for obj in bpy.data.objects:
            if obj.type == "MESH" and obj.data == mesh:
                eval_obj = obj.evaluated_get(depsgraph)
                eval_mesh = eval_obj.data
                eval_attr = eval_mesh.attributes.get(attr_name)
                if eval_attr and len(eval_attr.data) > face_idx:
                    return int(eval_attr.data[face_idx].value)
                break
    except Exception:
        pass

    return 0


def _apply_inspector_query_impl(s):
    """Apply inspector query: SQL mode (real sqlite3) or reject non-SQL.

    SQL PREFIXES route to _run_inspector_sql_query().
    Non-SQL text gets rejected with a helpful message — use DSL field instead.

    Returns number of matched rows.
    """
    import bpy

    query_text = getattr(s, "inspector_query_text", "").strip()
    if not query_text:
        s.inspector_query_active = False
        s.inspector_query_last_summary = "Empty query"
        return 0

    # ── SQL detection ────────────────────────────────────────────────
    SQL_PREFIXES = ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
    upper = query_text.upper().lstrip()
    is_sql = any(upper.startswith(p) for p in SQL_PREFIXES)

    if is_sql:
        s.inspector_sql_mode = True
        return _run_inspector_sql_query(s, query_text)
    else:
        # Non-SQL: do NOT run through DSL parser here. Reject cleanly.
        s.inspector_query_active = False
        s.inspector_sql_mode = False
        s.inspector_query_last_summary = (
            "Not recognized as SQL. Use the DSL Filter box below for DSL expressions."
        )
        log_warn(f"[Inspector][Query] Non-SQL rejected: {query_text[:80]}")
        return 0


# ── SQL Helpers ────────────────────────────────────────────────────────

def _clear_inspector_rows(s):
    """Clear the inspector row buffer."""
    s.inspector_rows.clear()
    s.inspector_row_count = 0


def _inspector_clear_sql_buffer(s):
    """Clear the SQL-specific result buffer (headers, rows, counts)."""
    s.inspector_sql_last_query = ""
    s.inspector_sql_row_count = 0
    s.inspector_sql_col_count = 0
    hdr = getattr(s, "inspector_sql_headers", None)
    if hdr:
        for i in range(8):
            setattr(hdr, f"h{i}", "")
    _clear_inspector_rows(s)


def _inspector_clear_dsl_stats(s):
    """Clear DSL stats fields."""
    s.dsl_faces_matched = 0
    s.dsl_unique_buildings = 0
    s.dsl_last = ""
    s.dsl_tiles_preview = ""
    s.dsl_sample_preview = ""


def _tag_redraw_all_view3d():
    """Force redraw of all 3D viewports."""
    import bpy
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass


def _safe_read_face_value(mesh, attr_name, face_idx):
    """Read a face attribute as string (works for STRING, INT, FLOAT, BOOL).

    Returns "" on failure or missing attribute.
    """
    attr = mesh.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return ""
    if len(attr.data) <= face_idx:
        return ""
    try:
        v = attr.data[face_idx].value
        if v is None:
            return ""
        return str(v)
    except (IndexError, RuntimeError):
        return ""


def _resolve_inspector_db_path(s):
    """Resolve the best database path for Inspector SQL queries.

    Priority:
      1. mkdb_path (semantic snapshot — has features table with building/amenity)
      2. links_db_path (linkdb — has osm_building_link)
      3. gpkg_path (raw GeoPackage)

    Returns (db_path, db_label) or ("", "") if nothing found.
    """
    # 1) MKDB — best for SELECT building, amenity, ...
    mkdb = getattr(s, "mkdb_path", "").strip()
    if mkdb and os.path.isfile(mkdb):
        return mkdb, "MKDB"

    # Auto-detect mkdb from output_dir
    out = getattr(s, "output_dir", "").strip()
    if out:
        mkdb_dir = os.path.join(out, "mkdb")
        if os.path.isdir(mkdb_dir):
            latest = os.path.join(mkdb_dir, "latest_mkdb.sqlite")
            if os.path.isfile(latest):
                return latest, "MKDB(latest)"
            # fallback: largest sqlite in mkdb/
            candidates = sorted(
                [os.path.join(mkdb_dir, f) for f in os.listdir(mkdb_dir) if f.endswith(".sqlite")],
                key=lambda p: os.path.getsize(p), reverse=True,
            )
            if candidates:
                return candidates[0], "MKDB(auto)"

    # 2) LinkDB
    link = getattr(s, "links_db_path", "").strip()
    if link and os.path.isfile(link):
        return link, "LinkDB"

    # 3) GPKG
    gpkg = getattr(s, "gpkg_path", "").strip()
    if gpkg and os.path.isfile(gpkg):
        return gpkg, "GPKG"

    return "", ""


def _run_inspector_sql_query(s, query_sql: str):
    """Execute raw SQL against the Inspector DB and populate GENERIC row buffer.

    Column names are stored in inspector_sql_headers (h0..h7).
    Row values are stored in inspector_rows (col0..col7) as stringified values.
    Any SQL result shape is supported (up to 8 columns).

    Uses read-only connection. Enforces LIMIT 200 safety.
    Returns number of rows buffered.
    """
    import bpy
    import json
    import time

    _inspector_clear_sql_buffer(s)

    db_path, db_label = _resolve_inspector_db_path(s)
    if not db_path:
        s.inspector_query_active = False
        # Show which paths were checked
        tried = []
        for attr in ("mkdb_path", "links_db_path", "gpkg_path"):
            v = getattr(s, attr, "").strip()
            if v:
                tried.append(f"{attr}={v}")
        out = getattr(s, "output_dir", "").strip()
        if out:
            tried.append(f"output_dir={out}")
        hint = "; ".join(tried) if tried else "all paths empty"
        s.inspector_query_last_summary = f"No database found. Checked: {hint}"
        log_warn(f"[Inspector][SQL] No database found. Tried: {hint}")
        return 0

    log_info(f"[Inspector][SQL] target={db_label} db={db_path}")

    # Safety: enforce LIMIT
    sql = query_sql.rstrip().rstrip(";")
    if "LIMIT" not in sql.upper():
        sql = f"{sql} LIMIT 200"

    conn = None
    try:
        from pathlib import Path as _P
        uri = f"file:{_P(db_path).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")

        t0 = time.time()
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(200)
        elapsed = (time.time() - t0) * 1000.0

        # Store headers (up to 8 columns)
        hdr = s.inspector_sql_headers
        for i in range(8):
            name = cols[i] if i < len(cols) else ""
            setattr(hdr, f"h{i}", str(name))

        s.inspector_sql_col_count = min(len(cols), 8)
        s.inspector_sql_row_count = len(rows)
        s.inspector_sql_last_query = query_sql[:512]

        # Store rows (stringify all values into col0..col7)
        for r in rows:
            item = s.inspector_rows.add()
            for i in range(min(len(r), 8)):
                v = r[i]
                setattr(item, f"col{i}", "" if v is None else str(v))

        s.inspector_row_count = len(s.inspector_rows)
        s.inspector_query_active = True
        s.inspector_sql_mode = True

        summary = f"{len(rows)} rows, {len(cols)} cols from {db_label} ({elapsed:.0f}ms)"
        s.inspector_query_last_summary = summary

        # Stats JSON for compatibility with summary box
        stats = {
            "query_column": "SQL",
            "query_code": 0,
            "faces_count": len(rows),
            "unique_osm_ids": 0,
            "osm_id_list": [],
            "db_target": db_label,
            "elapsed_ms": round(elapsed, 1),
            "columns": cols[:8],
        }
        s.inspector_query_last_stats_json = json.dumps(stats)

        log_info(f"[Inspector][SQL] Result: {summary}")
        _tag_redraw_all_view3d()
        return len(rows)

    except sqlite3.Error as ex:
        s.inspector_query_active = False
        s.inspector_query_last_summary = f"SQL Error: {ex}"
        log_error(f"[Inspector][SQL] {ex}")
        return 0
    except Exception as ex:
        s.inspector_query_active = False
        s.inspector_query_last_summary = f"Error: {ex}"
        log_error(f"[Inspector][SQL] {ex}")
        return 0
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ── DSL Filter (operates on Mesh Face Attributes, no DB) ─────────────

def _dsl_parse(dsl_text):
    """Parse DSL into (mode, payload).

    Returns one of:
      ("EMPTY", None)
      ("SHORTCUT", "school")                    — building=school OR amenity=school
      ("EQ", ("building", "school"))            — exact match
      ("IN", ("building", ["school","house"]))  — set membership
      ("GT", ("osm_amenity_code", "5"))         — numeric >
      ("LT", ("osm_amenity_code", "5"))         — numeric <
      ("UNSUPPORTED", raw_text)
    """
    import re
    q = (dsl_text or "").strip()
    if not q:
        return ("EMPTY", None)

    # Shortcut: single token (no operators) → building=tok OR amenity=tok
    if all(ch.isalnum() or ch in "_-" for ch in q) and "=" not in q:
        upper = q.upper()
        if "IN" != upper:  # avoid matching bare "IN"
            return ("SHORTCUT", q)

    # key IN (a, b, c)
    in_match = re.match(r'^(\w+)\s+IN\s*\(([^)]+)\)', q, re.IGNORECASE)
    if in_match:
        key = in_match.group(1).strip()
        inner = in_match.group(2).strip()
        vals = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        return ("IN", (key, vals))

    # key > N
    if ">" in q and "=" not in q:
        key, _, val = q.partition(">")
        return ("GT", (key.strip(), val.strip().strip("'\"")))

    # key < N
    if "<" in q and "=" not in q:
        key, _, val = q.partition("<")
        return ("LT", (key.strip(), val.strip().strip("'\"")))

    # key = value
    if "=" in q:
        key, _, val = q.partition("=")
        return ("EQ", (key.strip(), val.strip().strip("'\"")))

    return ("UNSUPPORTED", q)


def _dsl_match_face(mesh, face_idx, mode, payload):
    """Test whether face matches the parsed DSL expression.

    Tries STRING face attributes first, then falls back to INT code + legend encode.
    """
    if mode == "SHORTCUT":
        token = payload
        for attr_name in ("building", "amenity", "landuse", "type"):
            # String attribute first
            sv = _safe_read_face_value(mesh, attr_name, face_idx)
            if sv and sv.lower() == token.lower():
                return True
            # Code attribute fallback
            code_attr = _normalize_to_code_attr(attr_name)
            code_val = _safe_read_face_int(mesh, code_attr, face_idx)
            if code_val > 0:
                encoded = _legend_encode_safe(code_attr, token)
                if encoded > 0 and code_val == encoded:
                    return True
        return False

    if mode == "EQ":
        key, val = payload
        # Try raw string attribute first
        sv = _safe_read_face_value(mesh, key, face_idx)
        if sv:
            if sv == val or sv.lower() == val.lower():
                return True
            # If both are numeric, compare as int
            try:
                if int(sv) == int(val):
                    return True
            except (ValueError, TypeError):
                pass
        # Try code attribute fallback
        code_attr = _normalize_to_code_attr(key)
        code_val = _safe_read_face_int(mesh, code_attr, face_idx)
        try:
            int_val = int(val)
        except (ValueError, TypeError):
            int_val = _legend_encode_safe(code_attr, val)
        if int_val != 0 and code_val == int_val:
            return True
        return False

    if mode == "IN":
        key, vals = payload
        vals_lower = {v.lower() for v in vals}
        # String attribute
        sv = _safe_read_face_value(mesh, key, face_idx)
        if sv and sv.lower() in vals_lower:
            return True
        # Code fallback
        code_attr = _normalize_to_code_attr(key)
        code_val = _safe_read_face_int(mesh, code_attr, face_idx)
        if code_val > 0:
            int_vals = set()
            for v in vals:
                try:
                    int_vals.add(int(v))
                except (ValueError, TypeError):
                    int_vals.add(_legend_encode_safe(code_attr, v))
            if code_val in int_vals:
                return True
        return False

    if mode == "GT":
        key, val = payload
        # Numeric comparison only
        sv = _safe_read_face_value(mesh, key, face_idx)
        code_attr = _normalize_to_code_attr(key)
        v_int = _safe_read_face_int(mesh, code_attr, face_idx)
        # Also try direct numeric attribute
        if sv:
            try:
                v_int = max(v_int, int(sv))
            except (ValueError, TypeError):
                pass
        try:
            return v_int > int(val)
        except (ValueError, TypeError):
            return False

    if mode == "LT":
        key, val = payload
        sv = _safe_read_face_value(mesh, key, face_idx)
        code_attr = _normalize_to_code_attr(key)
        v_int = _safe_read_face_int(mesh, code_attr, face_idx)
        if sv:
            try:
                v_int = max(v_int, int(sv))
            except (ValueError, TypeError):
                pass
        try:
            return v_int < int(val)
        except (ValueError, TypeError):
            return False

    return False


def _apply_dsl_filter_impl(s):
    """Apply DSL filter on materialized face attributes (STRING + INT).

    Supports:
      key = value            (exact match, string or code)
      key IN (a, b, c)       (set membership)
      key > N  /  key < N    (numeric comparison)
      SHORTCUT token         (building=token OR amenity=token)

    Populates generic row buffer (col0=tile, col1=face, col2=osm_id, col3=value).
    Sets dsl_faces_matched, dsl_unique_buildings, dsl_tiles_preview, dsl_sample_preview.

    Returns number of matched faces.
    """
    import bpy

    dsl_text = getattr(s, "dsl_text", "").strip()

    # Clear previous results
    _inspector_clear_sql_buffer(s)
    _inspector_clear_dsl_stats(s)

    if not dsl_text:
        log_info("[Inspector][DSL] Empty filter")
        return 0

    s.dsl_last = dsl_text

    mode, payload = _dsl_parse(dsl_text)
    if mode in ("EMPTY", "UNSUPPORTED"):
        log_warn(f"[Inspector][DSL] Unsupported: {dsl_text}")
        return 0

    log_info(f"[Inspector][DSL] Parsed: mode={mode} payload={payload}")

    col = bpy.data.collections.get("CITYGML_TILES")
    if not col:
        log_warn("[Inspector][DSL] No CITYGML_TILES collection")
        return 0

    mesh_objs = [o for o in col.objects if o.type == "MESH" and o.data]
    if not mesh_objs:
        log_warn("[Inspector][DSL] No mesh objects in CITYGML_TILES")
        return 0

    # Set headers: tile | face | osm_id | matched_key | value
    hdr = s.inspector_sql_headers
    hdr.h0 = "Tile"
    hdr.h1 = "Face"
    hdr.h2 = "osm_id"
    if mode == "SHORTCUT":
        hdr.h3 = "building"
        hdr.h4 = "amenity"
    elif mode in ("EQ", "IN", "GT", "LT"):
        key = payload[0] if isinstance(payload, tuple) else ""
        hdr.h3 = key
        hdr.h4 = ""
    s.inspector_sql_col_count = 5 if mode == "SHORTCUT" else 4

    total_matched = 0
    row_limit = 200
    row_count = 0
    tiles_count = {}
    uniq_buildings = set()
    samples = []

    for obj in mesh_objs:
        mesh = obj.data
        fc = len(mesh.polygons)
        if fc == 0:
            continue

        tile_name = obj.name

        for face_idx in range(fc):
            if not _dsl_match_face(mesh, face_idx, mode, payload):
                continue

            total_matched += 1
            tiles_count[tile_name] = tiles_count.get(tile_name, 0) + 1

            # Unique buildings by (tile, building_idx) or osm_id
            bidx = _safe_read_face_value(mesh, "building_idx", face_idx)
            osm_id = _safe_read_face_value(mesh, "osm_id_int", face_idx)
            if bidx:
                uniq_buildings.add((tile_name, bidx))
            elif osm_id:
                uniq_buildings.add(osm_id)

            if row_count < row_limit:
                item = s.inspector_rows.add()
                item.col0 = tile_name[:24]
                item.col1 = str(face_idx)
                item.col2 = osm_id or ""

                if mode == "SHORTCUT":
                    item.col3 = _safe_read_face_value(mesh, "building", face_idx)
                    item.col4 = _safe_read_face_value(mesh, "amenity", face_idx)
                elif mode in ("EQ", "IN", "GT", "LT"):
                    key = payload[0] if isinstance(payload, tuple) else ""
                    item.col3 = _safe_read_face_value(mesh, key, face_idx)
                row_count += 1

            # Sample rows (first 5)
            if len(samples) < 5:
                sv = _safe_read_face_value(mesh, payload[0] if isinstance(payload, tuple) else "building", face_idx)
                samples.append(f"{tile_name}|f={face_idx}|osm={osm_id}|{sv}")

    s.inspector_row_count = row_count
    s.inspector_sql_row_count = row_count
    s.inspector_sql_mode = False
    s.inspector_query_active = True

    # DSL stats
    s.dsl_faces_matched = total_matched
    s.dsl_unique_buildings = len(uniq_buildings)

    top_tiles = sorted(tiles_count.items(), key=lambda kv: kv[1], reverse=True)[:10]
    s.dsl_tiles_preview = ", ".join(f"{t}:{c}" for t, c in top_tiles)
    s.dsl_sample_preview = " || ".join(samples)

    summary = f"DSL: {total_matched} faces, {len(uniq_buildings)} buildings ({row_count} buffered)"
    s.inspector_query_last_summary = summary

    log_info(f"[Inspector][DSL] {summary}")
    _tag_redraw_all_view3d()
    return total_matched


def _legend_encode_safe(code_attr, text_val):
    """Attempt legend_encode, returning 0 on failure."""
    feature_key = code_attr.replace("osm_", "").replace("_code", "")
    cache_key = f"{feature_key}_code"
    try:
        from .pipeline.diagnostics.legend_encoding import legend_encode, _ENCODE_CACHE
        result = legend_encode(cache_key, text_val)
        if result == 0:
            # case-insensitive fallback
            cache = _ENCODE_CACHE.get(cache_key, {})
            for k, v in cache.items():
                if k.lower() == text_val.lower():
                    return v
        return result
    except Exception:
        return 0


def _clear_inspector_query_impl(s):
    """Clear inspector query state and reset all UI fields."""
    s.inspector_query_active = False
    s.inspector_query_last_summary = ""
    s.inspector_query_last_stats_json = ""
    _inspector_clear_sql_buffer(s)
    _inspector_clear_dsl_stats(s)
    # Reset legend decode output
    s.legend_decode_result = ""
    s.legend_decode_status = ""
    log_info("[Inspector][Query] Cleared")
    _tag_redraw_all_view3d()
    return 0


def _zoom_to_inspector_selection_impl(context, s):
    """Zoom viewport to faces matching the current query."""
    import bpy
    # If no query active, just zoom to selected
    if not getattr(s, "inspector_query_active", False):
        bpy.ops.view3d.view_selected()
        return

    # The query results are in stats_json — just zoom to selected for now
    try:
        bpy.ops.view3d.view_selected()
    except Exception:
        pass


def _export_inspector_report_impl(s):
    """Export inspector query results to CSV."""
    import json
    output_dir = getattr(s, "output_dir", "").strip()
    if not output_dir:
        raise RuntimeError("output_dir not set")

    stats_json = getattr(s, "inspector_query_last_stats_json", "")
    if not stats_json:
        raise RuntimeError("No query results to export")

    stats = json.loads(stats_json)
    report_path = os.path.join(output_dir, "inspector_query_report.csv")

    import csv
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["query_column", "query_code", "faces_count", "unique_osm_ids"])
        writer.writerow([
            stats.get("query_column", ""),
            stats.get("query_code", ""),
            stats.get("faces_count", 0),
            stats.get("unique_osm_ids", 0),
        ])
        writer.writerow([])
        writer.writerow(["osm_id"])
        for oid in stats.get("osm_id_list", []):
            writer.writerow([oid])

    log_info(f"[Inspector][Export] Report written: {report_path}")
    return report_path


def _read_face_int_attr(mesh, attr_name, poly_index, default=None):
    if mesh is None or attr_name is None or poly_index is None:
        return default
    attrs = getattr(mesh, "attributes", None)
    if attrs is None:
        return default
    attr = attrs.get(attr_name)
    if not attr or attr.domain != "FACE" or poly_index >= len(attr.data):
        return default
    try:
        return int(attr.data[poly_index].value)
    except Exception:
        return default


def _read_face_int_attr_checked(mesh, attr_name, poly_index):
    """Read a FACE int attribute with validation; returns (value, error_message)."""
    if mesh is None or attr_name is None or poly_index is None:
        return None, "mesh/attr/index missing"
    attrs = getattr(mesh, "attributes", None)
    if attrs is None:
        return None, "mesh has no attributes"
    attr = attrs.get(attr_name)
    if attr is None:
        return None, f"{attr_name} attribute missing"
    if attr.domain != "FACE":
        return None, f"{attr_name} domain is {attr.domain} (expected FACE)"
    if len(attr.data) != len(mesh.polygons):
        return None, f"{attr_name} length mismatch attr={len(attr.data)} polys={len(mesh.polygons)}"
    if poly_index >= len(attr.data):
        return None, f"poly_index {poly_index} out of range {len(attr.data)}"
    try:
        return int(attr.data[poly_index].value), None
    except Exception as ex:
        return None, f"{attr_name} read failed: {ex}"


def _ensure_face_int_attr_repair(obj, mesh, name, log_prefix=""):
    """Ensure FACE/INT attribute exists with correct length; repair/copy from evaluated mesh if needed."""
    if mesh is None or not hasattr(mesh, "attributes"):
        return None, "mesh has no attributes"
    face_count = len(mesh.polygons)

    def _ok(attr):
        return attr and attr.domain == "FACE" and attr.data_type == "INT" and len(attr.data) == face_count

    attr = mesh.attributes.get(name)
    reason = None
    if not _ok(attr):
        if attr:
            reason = f"{name} invalid (domain={attr.domain}, type={attr.data_type}, len={len(attr.data)}, polys={face_count})"
            try:
                mesh.attributes.remove(attr)
            except Exception:
                pass
        else:
            reason = f"{name} missing"

        try:
            attr = mesh.attributes.new(name, "INT", "FACE")
        except Exception as ex:
            return None, f"{name} recreate failed: {ex}"

        try:
            mesh.update()
        except Exception:
            pass

        # Some broken meshes can still produce len(attr.data)==0 after recreation.
        # Attempt a deterministic repair by copying the mesh datablock once.
        if not _ok(attr):
            try:
                if obj and obj.data == mesh:
                    mesh_copy = mesh.copy()
                    obj.data = mesh_copy
                    mesh = mesh_copy
                    attr = mesh.attributes.new(name, "INT", "FACE")
                    try:
                        mesh.update()
                    except Exception:
                        pass
            except Exception:
                pass

        if not _ok(attr):
            return None, f"{name} recreate produced invalid attr (domain={attr.domain}, type={attr.data_type}, len={len(attr.data)}, polys={face_count})"

        # try to copy from evaluated mesh if available
        try:
            deps = bpy.context.evaluated_depsgraph_get()
            eval_obj = obj.evaluated_get(deps) if obj else None
            eval_mesh = eval_obj.to_mesh() if eval_obj else None
            try:
                if eval_mesh and hasattr(eval_mesh, "attributes"):
                    eval_attr = eval_mesh.attributes.get(name)
                    if eval_attr and eval_attr.domain == "FACE" and eval_attr.data_type == "INT" and len(eval_attr.data) == face_count:
                        buf = [0] * face_count
                        eval_attr.data.foreach_get("value", buf)
                        attr.data.foreach_set("value", buf)
                        log_info(f"{log_prefix}Restored {name} from evaluated mesh")
            finally:
                if eval_obj and eval_mesh:
                    eval_obj.to_mesh_clear()
        except Exception as ex:
            log_info(f"{log_prefix}Eval copy for {name} not available: {ex}")

    return attr, reason


_CLOBBER_PROTECTED = frozenset({
    "osm_way_id", "osm_id_int", "osm_id", "has_link",
    "link_conf", "link_dist_m", "link_iou",
})


def _has_nondefault_values(attr, max_scan=200):
    """Quick scan: does attr contain any non-default value?"""
    try:
        n = min(len(attr.data), max_scan)
        dt = attr.data_type
        for i in range(n):
            v = attr.data[i].value
            if dt == "INT" and v != 0:
                return True
            if dt == "FLOAT" and v != 0.0:
                return True
            if dt == "STRING" and v and v != b"" and v != "":
                return True
            if dt == "BOOLEAN" and v:
                return True
    except Exception:
        pass
    return False


def ensure_face_attr(mesh, name, data_type):
    """Ensure a mesh attribute exists with domain FACE, given data_type, and correct length.
    CLOBBER GUARD: protected attributes with non-default data are never deleted/recreated.
    UPGRADE PATH: INT→FLOAT for link_conf/link_dist_m/link_iou (preserves existing values).
    """
    if mesh is None or not hasattr(mesh, "attributes"):
        return None
    face_count = len(mesh.polygons)
    attr = mesh.attributes.get(name)
    if attr is not None:
        if attr.domain != "FACE" or attr.data_type != data_type or len(attr.data) != face_count:
            # UPGRADE PATH: INT→FLOAT for link attributes (before clobber guard)
            if (name in {"link_conf", "link_dist_m", "link_iou"}
                    and attr.domain == "FACE"
                    and attr.data_type == "INT"
                    and data_type == "FLOAT"
                    and len(attr.data) == face_count):
                # Preserve existing values and upgrade type
                try:
                    preserved_values = []
                    permille_scaled = 0
                    for i in range(len(attr.data)):
                        val_int = attr.data[i].value
                        # Handle permille encoding: link_conf stored as 879 (0.879*1000)
                        if name == "link_conf" and val_int > 1:
                            val_float = float(val_int) / 1000.0
                            permille_scaled += 1
                        else:
                            val_float = float(val_int)
                        preserved_values.append(val_float)
                    has_nondefault = any(v != 0.0 for v in preserved_values)
                    mesh.attributes.remove(attr)
                    attr = mesh.attributes.new(name, "FLOAT", "FACE")
                    for i, val in enumerate(preserved_values):
                        attr.data[i].value = val
                    print(f"[ClobberGuard] UPGRADE attr={name} INT→FLOAT preserved_nondefault={has_nondefault} permille_scaled={permille_scaled} faces={face_count}")
                    return attr
                except Exception as e:
                    print(f"[ClobberGuard][ERROR] UPGRADE failed for {name}: {e}")
                    # Fall through to normal handling
            
            # CLOBBER GUARD: keep protected attrs with real data
            if (name in _CLOBBER_PROTECTED
                    and attr.domain == "FACE"
                    and len(attr.data) == face_count
                    and _has_nondefault_values(attr)):
                print(f"[ClobberGuard] KEEP attr name={name} existing_type={attr.data_type} requested={data_type} non_default=True")
                return attr
            try:
                mesh.attributes.remove(attr)
            except Exception:
                pass
            attr = None
    if attr is None:
        try:
            attr = mesh.attributes.new(name, data_type, "FACE")
        except Exception:
            return None
    if attr.domain != "FACE" or attr.data_type != data_type or len(attr.data) != face_count:
        # Final check: if protected and has data, keep it
        if (name in _CLOBBER_PROTECTED
                and attr.domain == "FACE"
                and len(attr.data) == face_count
                and _has_nondefault_values(attr)):
            print(f"[ClobberGuard] KEEP attr name={name} existing_type={attr.data_type} requested={data_type} non_default=True (post-create)")
            return attr
        try:
            mesh.attributes.remove(attr)
        except Exception:
            pass
        return None
    # ALWAYS re-resolve via mesh.attributes.get() before returning.
    # The handle we hold may have been invalidated by a prior .remove()/.new()
    # inside this very function.  Re-fetching guarantees a live pointer.
    return mesh.attributes.get(name)


def _get_evaluated_mesh(context, obj):
    """Return a temporary evaluated mesh for obj (caller must free via obj_eval.to_mesh_clear())."""
    if obj is None:
        return None, None
    try:
        depsgraph = context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        try:
            me_eval = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        except TypeError:
            me_eval = obj_eval.to_mesh()
        return obj_eval, me_eval
    except Exception:
        return None, None


def _gather_building_indices(mesh):
    face_count = len(getattr(mesh, "polygons", []) or [])
    attr = getattr(mesh, "attributes", None)
    if not attr:
        return []

    def _pick_link_attr():
        for name in ("link_bidx", "building_idx"):
            a = attr.get(name)
            if a and a.domain == "FACE" and a.data_type == "INT" and len(a.data) == face_count:
                return a
        return None

    b_attr = _get_face_link_attr(mesh, face_count=face_count)
    if b_attr is None:
        return []


def _get_face_link_attr(mesh, face_count=None, prefer_link_bidx=True):
    """Return a FACE/INT attribute usable as link key, preferring link_bidx."""
    if mesh is None or not hasattr(mesh, "attributes"):
        return None
    if face_count is None:
        try:
            face_count = len(mesh.polygons)
        except Exception:
            face_count = 0
    for name in ("link_bidx", "building_idx") if prefer_link_bidx else ("building_idx", "link_bidx"):
        a = mesh.attributes.get(name)
        if a and a.domain == "FACE" and a.data_type == "INT" and len(a.data) == face_count:
            return a
    return None

    vals = set()
    try:
        for item in b_attr.data:
            vals.add(int(item.value))
    except Exception:
        return list(vals)
    return sorted(vals)


def _is_link_db_valid(link_db_path):
    """Check if link DB file exists and is accessible. Robust against path normalization issues."""
    if not link_db_path:
        return False
    try:
        p = Path(link_db_path).resolve()  # Normalize path (expand ~, resolve symlinks, etc.)
        return p.exists() and p.is_file()
    except Exception:
        return False


def _get_osm_key_col(s):
    """Detect which OSM ID column was used in linking (osm_id or osm_way_id).
    Returns the column name string, or None if not detectable.
    """
    link_db = getattr(s, "links_db_path", "")
    if not link_db or not _is_link_db_valid(link_db):
        return None
    try:
        p = Path(link_db)
        if open_db_readonly:
            con = open_db_readonly(str(p), log_open=False)
        else:
            uri = f"file:{p.as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()
        cols = {r[1] for r in cur.execute("PRAGMA table_info('gml_osm_links');").fetchall()}
        con.close()
        
        if "osm_id" in cols:
            return "osm_id"
        elif "osm_way_id" in cols:
            return "osm_way_id"
    except Exception:
        pass
    return None


def _load_link_lookup(s):
    mapping = {}
    # Prefer link DB
    link_db = getattr(s, "links_db_path", "")

    # Auto-detect from output_dir/links/ if links_db_path is empty
    if not link_db or not _is_link_db_valid(link_db):
        out_dir = getattr(s, "output_dir", "").strip()
        gpkg_stem = Path(getattr(s, "gpkg_path", "")).stem if getattr(s, "gpkg_path", "") else ""
        if out_dir and gpkg_stem:
            for candidate in [
                Path(out_dir) / "links" / f"{gpkg_stem}_links.sqlite",
                Path(out_dir) / f"{gpkg_stem}_links.sqlite",
            ]:
                if candidate.is_file():
                    link_db = str(candidate.resolve())
                    s.links_db_path = link_db
                    log_info(f"[LINKMAP] Auto-detected link DB: {link_db}")
                    break

    if link_db and _is_link_db_valid(link_db):
        p = Path(link_db)
        if p.exists():
            try:
                if open_db_readonly:
                    con = open_db_readonly(str(p), log_open=False)
                else:
                    uri = f"file:{p.as_posix()}?mode=ro"
                    con = sqlite3.connect(uri, uri=True)
                con.row_factory = sqlite3.Row
                cur = con.cursor()
                # Be tolerant to schema differences (some runs include dist_m/iou).
                cols = {r[1] for r in cur.execute("PRAGMA table_info('gml_osm_links');").fetchall()}
                
                # === CANONICAL OSM-KEY: SQL Alias (osm_way_id AS osm_id if needed) ===
                # Discover which ID column to use
                id_col_used = None
                if "osm_id" in cols:
                    id_col_used = "osm_id"
                    id_select = "osm_id"
                elif "osm_way_id" in cols:
                    id_col_used = "osm_way_id"
                    id_select = "osm_way_id AS osm_id"  # ALIAS: normalize to osm_id
                else:
                    # No ID column found
                    con.close()
                    log_warn("[OSM_KEY] linkdb has no osm_id or osm_way_id column")
                    return mapping
                
                log_info(f"[OSM_KEY] linkdb id column used: {id_col_used}")
                
                select_cols = ["source_tile", "building_idx", id_select]
                if "dist_m" in cols:
                    select_cols.append("dist_m")
                if "confidence" in cols:
                    select_cols.append("confidence")
                if "iou" in cols:
                    select_cols.append("iou")
                rows = cur.execute(f"SELECT {', '.join(select_cols)} FROM gml_osm_links;").fetchall()
                for r in rows:
                    try:
                        key = (norm_source_tile(r["source_tile"]), int(r["building_idx"]))
                        osm_raw = _norm_id(r["osm_id"])  # Normalised via _norm_id (always string)
                        mapping[key] = {
                            "osm_id": osm_raw if osm_raw else "—",
                            "link_conf": float(r["confidence"] or 0.0) if ("confidence" in r.keys()) else 0.0,
                            "link_dist_m": float(r["dist_m"] or 0.0) if ("dist_m" in r.keys()) else 0.0,
                            "link_iou": float(r["iou"] or 0.0) if ("iou" in r.keys()) else 0.0,
                        }
                    except Exception:
                        continue
                # ── SCHRITT 2: Link-Map Proof ──
                log_info(f"[LINKMAP] entries={len(mapping)}")
                sample_keys = list(mapping.keys())[:5]
                log_info("[LINKMAP] sample:")
                for sk in sample_keys:
                    row_val = mapping[sk]
                    log_info(f"  {sk!r} -> osm_id={row_val.get('osm_id', '?')}")
                con.close()
                return mapping
            except Exception:
                mapping = {}

    # Fallback: latest link mapping CSV
    out_dir = Path(getattr(s, "output_dir", "") or get_output_dir())
    if out_dir.exists():
        csv_files = sorted(out_dir.glob("M1DC_LinkMapping_*.csv"), reverse=True)
        for csv_path in csv_files:
            try:
                with csv_path.open("r", encoding="utf-8") as f:
                    headers = None
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if headers is None:
                            headers = [h.strip() for h in line.split(",")]
                            continue
                        parts = [p.strip() for p in line.split(",")]
                        row = {h: parts[i] if i < len(parts) else "" for i, h in enumerate(headers)}
                        key = (norm_source_tile(row.get("source_tile", "")), int(float(row.get("building_idx", 0) or 0)))
                        dist_m = float(row.get("dist_m", row.get("link_dist_m", 0)) or 0.0)
                        iou = float(row.get("iou", row.get("link_iou", 0)) or 0.0)
                        mapping[key] = {
                            "osm_id": row.get("osm_id", "") or "—",
                            "link_conf": float(row.get("confidence", 0) or 0.0),
                            "link_dist_m": dist_m,
                            "link_iou": iou,
                        }
                if mapping:
                    return mapping
            except Exception:
                continue
    return mapping


def _norm_id(v) -> str:
    """Central ID normalisation for ALL lookup keys.
    Every key used in any dict/DB lookup MUST pass through this.
    Returns canonical TEXT key: "" for invalid/empty, digit-string otherwise.
    Handles int, float, str, bytes, bool, None.
    """
    if v is None or v is False:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    if isinstance(v, int):
        return str(v) if v != 0 else ""
    if isinstance(v, float):
        iv = int(v)
        return str(iv) if iv != 0 else ""
    if isinstance(v, str):
        s = v.strip()
        if s.endswith(".0"):
            s = s[:-2]
        return s if s.isdigit() and s != "0" else ""
    try:
        s = str(v).strip()
        return s if s.isdigit() and s != "0" else ""
    except Exception:
        return ""


def _sqlite_list_tables(cur):
    """List all tables in SQLite database."""
    return [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    ).fetchall()]


def _resolve_feature_db_path() -> str:
    """Deterministic Feature-DB selection for Phase 4 / MKDB.
    Priority:
      1. scene.m1dc_settings.links_db_path  (if file exists)
      2. output_dir scan: *_links.sqlite first, then *_linkdb.sqlite
      3. among multiples: largest file wins
    Returns absolute path string, or "" if nothing found.
    """
    import bpy

    candidates = []

    # Priority 1: scene setting
    try:
        s = bpy.context.scene.m1dc_settings
        for attr_name in ("links_db_path", "link_db_path"):
            p = getattr(s, attr_name, "").strip() if hasattr(s, attr_name) else ""
            if p and os.path.isfile(p):
                st = os.stat(p)
                candidates.append((p, st.st_size, st.st_mtime, "scene_setting"))
                print(f"[PROOF][FEATURE_DB] candidate={p} exists=True size={st.st_size} mtime={st.st_mtime} source={attr_name}")
    except Exception:
        pass

    # Priority 2: output_dir scan (canonical location: output_dir/links/ per artifact contract)
    try:
        out = get_output_dir()
        if out and out.exists():
            # Scan CANONICAL location first: output_dir/links/
            links_subdir = out / "links"
            scan_dirs = [links_subdir, out]  # canonical first, then root (legacy)
            for scan_dir in scan_dirs:
                if not scan_dir.is_dir():
                    continue
                for pattern in ("*_links.sqlite", "*_linkdb.sqlite"):
                    for fp in sorted(scan_dir.glob(pattern)):
                        if fp.is_file():
                            st = fp.stat()
                            source_tag = f"links_subdir:{pattern}" if scan_dir == links_subdir else f"glob:{pattern}"
                            candidates.append((str(fp), st.st_size, st.st_mtime, source_tag))
                            print(f"[PROOF][FEATURE_DB] candidate={fp} exists=True size={st.st_size} mtime={st.st_mtime} source={source_tag}")
    except Exception:
        pass

    if not candidates:
        print("[PROOF][FEATURE_DB] chosen=NONE reason=no_candidates")
        return ""

    # Prefer scene_setting if available; otherwise largest file
    scene_cands = [c for c in candidates if c[3] == "scene_setting"]
    if scene_cands:
        chosen = max(scene_cands, key=lambda c: c[1])
        print(f"[PROOF][FEATURE_DB] chosen={chosen[0]} reason=scene_setting size={chosen[1]}")
        return chosen[0]

    chosen = max(candidates, key=lambda c: c[1])
    print(f"[PROOF][FEATURE_DB] chosen={chosen[0]} reason=largest_file size={chosen[1]}")
    return chosen[0]


# Mismatch log guard for _safe_read_face_id_attr (1x per mesh name)
_SAFE_READ_MISMATCH_LOGGED = set()


def _safe_read_face_id_attr(mesh, face_idx) -> str:
    """Read canonical OSM ID from a face, bytes-safe, with length guard.
    Priority: osm_way_id > osm_id_int > osm_id.
    Returns canonical TEXT key ("" or digit-string) via _norm_id.
    """
    mesh_name = getattr(mesh, "name", "?")
    face_count = len(mesh.polygons) if mesh else 0
    if face_count == 0:
        return ""

    for attr_name in ("osm_way_id", "osm_id_int", "osm_id"):
        a = mesh.attributes.get(attr_name)
        if a is None or a.domain != "FACE":
            continue
        if len(a.data) != face_count:
            if mesh_name not in _SAFE_READ_MISMATCH_LOGGED:
                _SAFE_READ_MISMATCH_LOGGED.add(mesh_name)
                print(f"[SAFE_READ] SKIP attr={attr_name} mesh={mesh_name} attr_len={len(a.data)} face_count={face_count}")
            continue
        if face_idx < 0 or face_idx >= len(a.data):
            return ""
        raw = a.data[face_idx].value
        k = _norm_id(raw)
        if k:
            return k
    return ""


def _dump_face_attr_schema(mesh, names=("osm_way_id", "osm_id_int", "osm_id", "has_link", "link_conf",
                                        "gml_building_idx", "gml__building_idx", "building_idx", "link_bidx")):
    """Forensics dump: print face-attr schema for a single mesh.
    Only called when MKDB harvests 0 keys (hard proof).
    """
    fc = len(mesh.polygons)
    out = []
    for n in names:
        a = mesh.attributes.get(n)
        if not a:
            out.append((n, "MISSING"))
            continue
        l = len(a.data)
        v0 = a.data[0].value if l else None
        out.append((n, a.domain, a.data_type, l, fc,
                     type(v0).__name__, repr(v0)[:40] if v0 is not None else None))
    print(f"[MKDB][SCHEMA] mesh={mesh.name} {out}")


def _collect_unique_osm_keys_from_meshes(mesh_objs, link_map):
    """Collect unique non-empty osm_way_ids from meshes via link_map lookup.
    EXACTLY the same logic as Materialize uses for unique_osm_way_ids:
      1. iterate mesh objects (same list from _collect_citygml_meshes)
      2. resolve source_tile + gml_building_idx per face
      3. lookup (source_tile, bidx) in link_map
      4. normalize osm_id, keep non-empty / non-"—" / non-"0"
    Returns (keys_set, proof_dict).
    """
    from collections import Counter
    keys = set()
    mesh_count_scanned = 0
    faces_scanned_total = 0
    id_attr_names_seen = Counter()

    for mesh_obj in mesh_objs:
        mesh = mesh_obj.data
        if not mesh or not hasattr(mesh, "attributes"):
            continue

        source_tile = _get_source_tile(mesh_obj)
        face_count = len(mesh.polygons)
        if face_count == 0:
            continue

        # ---- bidx attr: gml_building_idx > gml__building_idx > link_bidx/building_idx
        #      (identical fallback chain as Materialize)
        idx_attr = None
        idx_name = None
        for candidate in ("gml_building_idx", "gml__building_idx"):
            a = mesh.attributes.get(candidate)
            if a and a.domain == "FACE" and a.data_type == "INT" and len(a.data) == face_count:
                idx_attr = a
                idx_name = candidate
                break
        if idx_attr is None:
            idx_attr = _get_face_link_attr(mesh, face_count=face_count)
            if idx_attr:
                idx_name = getattr(idx_attr, "name", "fallback")

        if idx_attr is None or idx_attr.domain != "FACE":
            continue

        id_attr_names_seen[idx_name] += 1
        mesh_count_scanned += 1

        for poly_idx in range(face_count):
            faces_scanned_total += 1
            try:
                bidx = int(idx_attr.data[poly_idx].value)
            except Exception:
                continue
            row = link_map.get((source_tile, bidx))
            if row:
                osm_way_id = _normalize_osm_id(row.get("osm_id"))
                if osm_way_id and osm_way_id not in ("\u2014", "0"):
                    keys.add(osm_way_id)

    proof = {
        "mesh_count_scanned": mesh_count_scanned,
        "faces_scanned_total": faces_scanned_total,
        "id_attr_names_seen": dict(id_attr_names_seen),
        "sample_keys": list(keys)[:10],
    }
    return keys, proof


def _file_sig(path: str):
    """Compute file signature for build hash."""
    import hashlib
    p = Path(path)
    try:
        st = p.stat()
        return f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except FileNotFoundError:
        return f"{p}|MISSING"


def _build_hash(linkdb_path: str, gpkg_path: str | None, schema_version: str):
    """Compute deterministic build hash from source files."""
    import hashlib
    h = hashlib.sha256()
    h.update(_file_sig(linkdb_path).encode("utf-8"))
    if gpkg_path:
        h.update(_file_sig(gpkg_path).encode("utf-8"))
    h.update(f"schema:{schema_version}".encode("utf-8"))
    return h.hexdigest()


def _copy_latest(src_path: str, latest_path: str, logger=print):
    """Copy mkdb to latest_mkdb.sqlite pointer."""
    try:
        import shutil
        shutil.copy2(src_path, latest_path)
        logger(f"[MKDB] updated latest -> {latest_path}")
    except Exception as e:
        logger(f"[MKDB][WARN] could not write latest: {e}")


# REMOVED: _bbox_center_world and _terrain_recenter_xy_to_citygml
# These were dead code (BBox-Center heuristic replaced by Min-Corner Align operator).
# Equivalent stable logic lives in pipeline/terrain/terrain_validation.py:
#   - bbox_world(), extent_xy_minmax() for bbox queries
#   - compute_xy_shift_min_corner() for deterministic alignment
#   - apply_terrain_xy_offset() for applying the shift


# mkdb schema version
MKDB_SCHEMA_VERSION = "mkdb_v1"

# Feature columns to extract from linkdb
FEATURE_COLS = [
    "building", "amenity", "landuse", "type", "name",
    "shop", "office", "leisure", "historic", "tourism", "man_made", "natural", "military",
    "craft", "aeroway", "barrier", "boundary", "admin_level"
]


def build_mkdb_from_linkdb(
    *,
    linkdb_path: str,
    mkdb_dir: str,
    dataset_slug: str,
    build_hash: str,
    ids_str: list[str],
    logger=print,
):
    """
    Build mkdb from linkdb.osm_building_link table.
    
    Args:
        linkdb_path: Path to linkdb.sqlite
        mkdb_dir: Output directory for mkdb
        dataset_slug: Dataset identifier for naming
        build_hash: Hash for deterministic rebuild
        ids_str: List of osm_way_id strings to extract
        logger: Logging function
        
    Returns:
        str: Path to created mkdb file
    """
    import time
    
    mkdb_dir_p = Path(mkdb_dir)
    mkdb_dir_p.mkdir(parents=True, exist_ok=True)

    short = build_hash[:10]
    mkdb_path = str(mkdb_dir_p / f"{dataset_slug}__{short}__mkdb.sqlite")
    latest_path = str(mkdb_dir_p / "latest_mkdb.sqlite")

    # Reuse if exists (deterministic rebuild)
    if Path(mkdb_path).exists():
        logger(f"[MKDB] Reusing existing mkdb: {mkdb_path}")
        _copy_latest(mkdb_path, latest_path, logger)
        return mkdb_path

    logger(f"[MKDB] Building mkdb: {mkdb_path}")
    t0 = time.time()

    # Open linkdb source
    con_src = sqlite3.connect(linkdb_path)
    con_src.row_factory = sqlite3.Row
    cur_src = con_src.cursor()

    # Sanity: check required table
    tables = _sqlite_list_tables(cur_src)
    if "osm_building_link" not in tables:
        con_src.close()
        raise RuntimeError(f"[MKDB] linkdb missing table osm_building_link. tables={tables}")

    # Create mkdb
    con = sqlite3.connect(mkdb_path)
    cur = con.cursor()

    # Optimize SQLite for bulk writes
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")

    # Create meta table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mkdb_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    # Create features table
    col_defs = ",\n".join([f"{c} TEXT" for c in FEATURE_COLS])
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS features (
        osm_way_id TEXT PRIMARY KEY,
        {col_defs}
    );
    """)

    # Write metadata
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta = {
        "schema_version": MKDB_SCHEMA_VERSION,
        "created_at": now,
        "source_linkdb_path": str(Path(linkdb_path).resolve()),
        "build_hash": build_hash,
        "osm_id_kind": "osm_way_id_text",
        "ids_count": str(len(ids_str)),
        "dataset_slug": dataset_slug,
        "feature_source": "linkdb.osm_building_link"
    }
    cur.executemany(
        "INSERT OR REPLACE INTO mkdb_meta(key,value) VALUES (?,?)",
        list(meta.items())
    )

    # Extract features from linkdb in chunks (SQLite IN limit ~1000)
    def chunks(lst, n=900):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    total_insert = 0
    select_cols = ", ".join(["osm_way_id"] + FEATURE_COLS)

    for part in chunks(ids_str):
        ph = ",".join(["?"] * len(part))
        q = f"""
        SELECT {select_cols}
        FROM osm_building_link
        WHERE osm_way_id IN ({ph})
        """
        rows = cur_src.execute(q, part).fetchall()
        if not rows:
            continue

        ins_cols = ", ".join(["osm_way_id"] + FEATURE_COLS)
        ins_ph = ", ".join(["?"] * (1 + len(FEATURE_COLS)))
        ins_q = f"INSERT OR REPLACE INTO features({ins_cols}) VALUES ({ins_ph})"

        payload = []
        for r in rows:
            osm_way_id = str(r["osm_way_id"]).strip()
            if not osm_way_id:
                continue
            payload.append([osm_way_id] + [r[c] for c in FEATURE_COLS])

        if payload:
            cur.executemany(ins_q, payload)
            total_insert += len(payload)

    con.commit()
    con.close()
    con_src.close()

    # Copy to latest pointer
    _copy_latest(mkdb_path, latest_path, logger)

    dt = time.time() - t0
    logger(f"[MKDB][ACCEPT] built features={total_insert} ids_in={len(ids_str)} in {dt:.2f}s -> {mkdb_path}")

    return mkdb_path


def load_feature_map_from_linkdb(*, linkdb_path: str, ids_str: list[str]):
    """
    Load feature map from linkdb table (auto-detect: osm_building_link or osm_building_link_local).
    
    Args:
        linkdb_path: Path to linkdb.sqlite
        ids_str: List of osm_way_id strings to load
        
    Returns:
        dict: feature_map keyed by osm_way_id string
    """
    con = sqlite3.connect(linkdb_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    tables = _sqlite_list_tables(cur)
    
    # Detect actual table name (try in order)
    target_table = None
    for candidate in ["osm_building_link", "osm_building_link_local"]:
        if candidate in tables:
            target_table = candidate
            break
    
    if not target_table:
        con.close()
        raise RuntimeError(f"[LINKDB] No valid feature table found in {linkdb_path}. tables={tables}")
    
    print(f"[LINKDB] Using table: {target_table}")

    # ── TASK A: Detect available ID columns (osm_way_id PREFERRED, fallback osm_id) ──
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info('{target_table}');").fetchall()}
    has_osm_id = "osm_id" in cols
    has_osm_way_id = "osm_way_id" in cols
    
    if not has_osm_id and not has_osm_way_id:
        con.close()
        raise RuntimeError(f"[LINKDB] Table {target_table} has neither osm_id nor osm_way_id column")
    
    # Determine which column(s) to query (PREFER osm_way_id)
    id_col_primary = "osm_way_id" if has_osm_way_id else "osm_id"
    log_info(f"[FEATUREMAP] id_col_primary={id_col_primary} (has_osm_way_id={has_osm_way_id}, has_osm_id={has_osm_id})")

    # Chunk IN query
    def chunks(lst, n=900):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    feature_map = {}
    for part in chunks(ids_str):
        ph = ",".join(["?"] * len(part))
        # Try primary column first
        q = f"SELECT * FROM {target_table} WHERE {id_col_primary} IN ({ph})"
        for r in cur.execute(q, part).fetchall():
            row_dict = dict(r)
            # ── TASK A: Dual-Key Strategy (PREFER osm_way_id) ──
            # Store under BOTH osm_way_id and osm_id if both exist
            k_way = _norm_id(row_dict.get("osm_way_id")) if has_osm_way_id else None
            k_id = _norm_id(row_dict.get("osm_id")) if has_osm_id else None
            
            if k_way:
                feature_map[k_way] = row_dict
            if k_id and k_id not in feature_map:
                feature_map[k_id] = row_dict

    # ── SCHRITT 4: Feature-Map Proof ──
    total_rows = 0
    try:
        total_rows = cur.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
    except Exception:
        pass
    log_info(f"[FEATUREMAP] rows={total_rows} unique_keys={len(feature_map)}")
    sample_fkeys = list(feature_map.keys())[:5]
    log_info(f"[FEATUREMAP] sample_keys={sample_fkeys}")

    con.close()
    return feature_map


_GPKG_SCHEMA_DUMPED = False

_GPKG_FEATURE_COLS = frozenset({
    "building", "amenity", "landuse", "type", "aeroway", "boundary",
    "admin_level", "name", "shop", "office", "tourism", "leisure",
    "historic", "man_made",
})


def _debug_dump_gpkg_schema(gpkg_path):
    """One-shot schema dump of a GPKG – printed only on first GPKG fallback."""
    global _GPKG_SCHEMA_DUMPED
    if _GPKG_SCHEMA_DUMPED:
        return
    _GPKG_SCHEMA_DUMPED = True

    import sqlite3
    try:
        con = sqlite3.connect(gpkg_path)
        cur = con.cursor()
        tables = _sqlite_list_tables(cur)
        print(f"[GPKG_SCHEMA] path={gpkg_path}")
        print(f"[GPKG_SCHEMA] tables_total={len(tables)}")

        candidate_names = []
        for tbl in tables:
            col_names = {r[1] for r in cur.execute(
                f"PRAGMA table_info('{tbl}');").fetchall()}
            id_cols = sorted({"osm_id", "osm_way_id"} & col_names)
            feat_cols = sorted(_GPKG_FEATURE_COLS & col_names)
            is_candidate = bool(id_cols) and len(feat_cols) >= 2
            if id_cols or feat_cols:
                tag = "YES" if is_candidate else "NO"
                print(f"[GPKG_SCHEMA] table={tbl} candidate={tag} "
                      f"id_cols={id_cols} feat_cols={feat_cols}")
            if is_candidate:
                candidate_names.append(tbl)

        print(f"[GPKG_SCHEMA] candidates={candidate_names}")
        con.close()
    except Exception as ex:
        print(f"[GPKG_SCHEMA] ERROR dumping schema: {ex}")


def load_feature_map_from_gpkg(gpkg_path: str, ids_str: list[str], prefer_id_col: str = None):
    """
    Fallback loader: read OSM feature attributes directly from a GeoPackage
    (Feature Source of Truth) when links.sqlite contains only matching tables.

    Args:
        gpkg_path: Path to the READONLY .gpkg file.
        ids_str:   List of normalised OSM-ID strings (via _norm_id).
        prefer_id_col: Preferred ID column name (e.g. "osm_way_id" or "osm_id").
                      If present in table, use it even if osm_id exists.

    Returns:
        dict keyed by _norm_id(osm_id) -> row-dict with feature columns.
    """
    import sqlite3

    con = sqlite3.connect(gpkg_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    tables = _sqlite_list_tables(cur)

    # ── Score each table: must have an ID col + >=2 feature cols ──
    candidates = []  # (table_name, id_col, matched_feature_cols)
    table_info = []  # for error reporting
    for tbl in tables:
        col_info = cur.execute(f"PRAGMA table_info('{tbl}');").fetchall()
        col_names = {r[1] for r in col_info}

        # Determine ID column (prefer prefer_id_col if specified, else osm_id)
        if prefer_id_col and prefer_id_col in col_names:
            id_col = prefer_id_col
            log_info(f"[PHASE4][GPKG] table={tbl} id_col={id_col} (from prefer_id_col)")
        elif "osm_id" in col_names:
            id_col = "osm_id"
            if prefer_id_col:
                log_warn(f"[PHASE4][GPKG] table={tbl} prefer_id_col={prefer_id_col} not in columns, falling back to osm_id")
        elif "osm_way_id" in col_names:
            id_col = "osm_way_id"
        else:
            id_col = None

        matched = _GPKG_FEATURE_COLS & col_names
        if id_col and len(matched) >= 2:
            candidates.append((tbl, id_col, matched))
        # Compact info for error message (only tables with at least an id or feature col)
        if id_col or matched:
            table_info.append(f"{tbl}(id={id_col}, feat={sorted(matched)})")

    if not candidates:
        con.close()
        raise RuntimeError(
            f"[PHASE4][GPKG] No candidate tables with required columns in "
            f"{gpkg_path}. scanned={table_info or tables}"
        )

    # Sort by number of matched feature columns descending (best first)
    candidates.sort(key=lambda c: len(c[2]), reverse=True)

    # ── Fetch rows from best candidate(s), merge "first non-empty wins" ──
    feature_map: dict[str, dict] = {}

    for tbl, id_col, matched_cols in candidates:
        select_cols = [id_col] + sorted(matched_cols)
        cols_sql = ", ".join(f'"{c}"' for c in select_cols)

        def _chunks(lst, n=500):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]

        rows_fetched = 0
        # Debug: Log the query strategy on first table
        if tbl == candidates[0][0]:
            print(f"[PHASE4][GPKG] Querying table={tbl}")
            print(f"[PHASE4][GPKG] id_col={id_col} (prefer_id_col={prefer_id_col})")
            print(f"[PHASE4][GPKG] rows_requested={len(ids_str)}")
        
        for part in _chunks(ids_str):
            ph = ",".join(["?"] * len(part))
            q = f'SELECT {cols_sql} FROM "{tbl}" WHERE "{id_col}" IN ({ph})'
            for r in cur.execute(q, part).fetchall():
                row_dict = dict(r)
                key = _norm_id(row_dict.get(id_col))
                if not key:
                    continue
                rows_fetched += 1
                if key in feature_map:
                    existing = feature_map[key]
                    for col in matched_cols:
                        if not existing.get(col) and row_dict.get(col):
                            existing[col] = row_dict[col]
                else:
                    feature_map[key] = {
                        col: row_dict.get(col, "") for col in matched_cols
                    }

        sample_keys = list(feature_map.keys())[:3]
        print(
            f"[PHASE4] feature_source=GPKG gpkg={gpkg_path} table={tbl} "
            f"id_col={id_col} rows={rows_fetched} unique_keys={len(feature_map)}"
        )
        print(f"[PHASE4] sample_keys={sample_keys}")

        # If best table already delivered rows, no need to scan more tables
        if feature_map:
            break

    con.close()
    
    # Final summary log
    log_info(
        f"[PHASE4][GPKG] FINAL: {len(feature_map)} features loaded "
        f"(prefer_id_col={prefer_id_col})"
    )
    print(f"[PHASE4][GPKG] rows_returned={len(feature_map)} unique_keys={len(set(feature_map.keys()))}")
    
    return feature_map


def load_feature_map_from_mkdb(*, mkdb_path: str, ids_str: list[str]):
    """
    Load feature map from mkdb.features table.
    
    Args:
        mkdb_path: Path to mkdb.sqlite
        ids_str: List of osm_way_id strings to load
        
    Returns:
        dict: feature_map keyed by osm_way_id string
    """
    con = sqlite3.connect(mkdb_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    tables = _sqlite_list_tables(cur)
    if "features" not in tables:
        con.close()
        raise RuntimeError(f"[MKDB] missing 'features' in {mkdb_path}. tables={tables}")

    # Chunk IN query
    def chunks(lst, n=900):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    feature_map = {}
    for part in chunks(ids_str):
        ph = ",".join(["?"] * len(part))
        q = f"SELECT * FROM features WHERE osm_way_id IN ({ph})"
        for r in cur.execute(q, part).fetchall():
            k = str(r["osm_way_id"]).strip()
            if k:
                feature_map[k] = dict(r)

    con.close()
    return feature_map


def _debug_sample_feature_rows(feature_map, sample_keys, logger=print):
    """Print sample feature rows for debugging."""
    shown = 0
    for k in sample_keys:
        row = feature_map.get(k)
        if row:
            logger(f"[MKDB] sample row {k}: building={row.get('building')} amenity={row.get('amenity')} landuse={row.get('landuse')} type={row.get('type')}")
            shown += 1
            if shown >= 5:
                break


def _table_exists(cur, table_name: str) -> bool:
    """
    Check if a table exists in the SQLite database.
    
    Args:
        cur: SQLite cursor
        table_name: Name of table to check
        
    Returns:
        bool: True if table exists
    """
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
        (table_name,)
    ).fetchone()
    return row is not None


def _face_key_from_osm_id_int(face_val) -> str:
    """
    Convert face osm_id_int attribute value to string key for feature lookup.
    
    Delegates to _norm_id for consistent normalisation across all lookups.
    Returns empty string for 0, None, or invalid values.
    
    Args:
        face_val: Value from osm_id_int attribute (int, float, or None)
        
    Returns:
        str: Normalized key as string, or empty string if invalid
    """
    if face_val in (None, 0, "0", ""):
        return ""
    normed = _norm_id(face_val)
    if normed is None or normed == "" or normed == "0":
        return ""
    return normed


# ── P4 HELPERS: Blender 4.5 STRING attributes require bytes ──
def _key_to_str(v):
    """Convert any face attribute value to a clean string key for dict lookup."""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", errors="replace")
    return str(v)


def _to_attr_bytes(v):
    """Convert any value to bytes for Blender STRING attribute write.
    Blender 4.5: StringAttributeValue.value MUST be bytes, not str.
    """
    if v is None:
        return b""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        if v == "None":
            return b""
        return v.encode("utf-8", errors="replace")
    return str(v).encode("utf-8", errors="replace")


def _materialize_osm_features(mesh, osm_id_attr, gpkg_path):
    """
    Write OSM semantic attributes (name, building, amenity, address) to FACE attributes.

    Args:
        mesh: Blender mesh object
        osm_id_attr: FACE INT attribute containing osm_id values
        gpkg_path: Path to GeoPackage file

    Returns:
        Number of faces with non-empty OSM name written
    """
    from pathlib import Path
    import sqlite3

    mesh_name = getattr(mesh, "name", "<unknown>")
    print(f"\n[PHASE4_ENTER] _materialize_osm_features CALLED for mesh={mesh_name}")

    # ========================================
    # LOCAL HELPER: Safe attribute getter with length validation
    # ========================================
    def _get_face_attr_safe(mesh, name):
        """Get FACE attribute only if data length matches polygon count."""
        a = mesh.attributes.get(name)
        if not a or a.domain != 'FACE':
            return None
        face_count = len(mesh.polygons)
        if len(a.data) != face_count:
            # Try sync once
            try:
                mesh.update()
            except Exception:
                pass
            a = mesh.attributes.get(name)
            if not a or a.domain != 'FACE' or len(a.data) != face_count:
                return None
        return a

    # NOTE: local _norm_id REMOVED (FIX2). Using module-level _norm_id (bytes-safe, returns str).

    # ========================================
    # Detect OSM_KEY column from linking (for GPKG fallback)
    # ========================================
    osm_key_col = None
    try:
        import bpy
        s = bpy.context.scene.m1dc_settings
        osm_key_col = _get_osm_key_col(s)
        if osm_key_col:
            log_info(f"[PHASE4] osm_key_col={osm_key_col} (from linking)")
            print(f"[PHASE4] osm_key_col={osm_key_col} (from linking)")
        else:
            log_warn("[PHASE4] osm_key_col detection returned None")
            print("[PHASE4] WARNING: osm_key_col detection returned None")
    except Exception as ex:
        log_error(f"[PHASE4] osm_key_col detection failed: {ex}")
        print(f"[PHASE4] ERROR: osm_key_col detection failed: {ex}")
        import traceback
        traceback.print_exc()

    # ========================================
    # HARD GUARD: Check face count (skip empty meshes)
    # ========================================
    face_count = len(mesh.polygons)
    if face_count == 0:
        print(f"[Phase4][Skip] mesh={mesh_name} has 0 faces")
        return 0

    # ========================================
    # Resolve canonical osm_id attributes: osm_way_id (INT) PREFERRED, fallback to osm_id_int
    # Use SAFE getter to ensure data length matches face count
    # ========================================
    osm_way_id_attr = _get_face_attr_safe(mesh, "osm_way_id")
    osm_id_attr_int = _get_face_attr_safe(mesh, "osm_id_int")
    osm_id_attr_str = _get_face_attr_safe(mesh, "osm_id")

    # Determine primary ID attribute (osm_way_id preferred)
    id_attr = osm_way_id_attr or osm_id_attr_int
    primary_id_name = 'osm_way_id' if osm_way_id_attr else ('osm_id_int' if osm_id_attr_int else None)
    if id_attr is None:
        print(f"[Phase4][Skip] mesh={mesh_name} No valid FACE id attr (osm_way_id/osm_id_int) or attr length mismatch (faces={face_count})")
        log_warn(f"[Phase4][Skip] {mesh_name}: No valid ID attribute or length mismatch")
        return 0

    # ── P4 PRIMARY-ID GUARD: Log exactly which attribute is used ──
    print(f"[Phase4][Guard] mesh={mesh_name} faces={face_count} primary_id_attr={primary_id_name}")
    if osm_key_col and primary_id_name != osm_key_col:
        log_warn(f"[Phase4][GUARD] osm_key_col={osm_key_col} but primary_id_attr={primary_id_name} — may cause key mismatch!")
        print(f"[Phase4][GUARD] WARNING: osm_key_col={osm_key_col} != primary_id_attr={primary_id_name}")
    # Sample first non-zero value from primary ID for type proof
    _sample_id_val = None
    for _si in range(min(20, face_count)):
        _sv = id_attr.data[_si].value
        if _sv and _sv != 0:
            _sample_id_val = _sv
            break
    print(f"[Phase4][Guard] primary_id sample value={_sample_id_val!r} type={type(_sample_id_val).__name__}")

    # Legacy attribute resolution (for backwards compat)
    if osm_id_attr_str and (osm_id_attr_str.domain != "FACE" or osm_id_attr_str.data_type != "STRING"):
        osm_id_attr_str = None

    # ========================================
    # FORENSIC: Select probe face (first with osm_id != 0)
    # Use robust _norm_id to read ID values
    # ========================================
    probe_face_idx = None
    probe_osm_id = None
    for poly in mesh.polygons:
        idx = poly.index
        osm_id = _safe_read_face_id_attr(mesh, idx)
        if osm_id:
            probe_face_idx = idx
            probe_osm_id = osm_id
            break

    if probe_face_idx is not None:
        print(f"\n[M1DC P4 PROBE] mesh={mesh_name} probe_face={probe_face_idx} osm_id={probe_osm_id}")
    else:
        print(f"\n[M1DC P4 PROBE] mesh={mesh_name} NO FACE WITH osm_id > 0 FOUND!")

    # Step 1: Collect unique osm_id values from faces via _safe_read_face_id_attr
    print("[OSM Features] Collecting unique osm_id values from faces...")
    unique_ids = set()
    faces_with_link = 0
    for poly in mesh.polygons:
        k = _safe_read_face_id_attr(mesh, poly.index)
        if k:
            unique_ids.add(k)
            faces_with_link += 1

    sample_ids = list(unique_ids)[:10]
    print(f"[P4][ID_SCAN] mesh={mesh_name} faces={face_count} faces_with_link={faces_with_link} unique_ids={len(unique_ids)}")
    print(f"[P4][ID_SCAN] sample_ids_requested={sample_ids}")

    if not unique_ids:
        log_warn("[Materialize] no ids to materialize (unique_osm_id == 0)")
        return 0

    # Convert to list for processing
    ids_str = list(unique_ids)

    # ========================================
    # LINKDB PATH RESOLUTION via _resolve_feature_db_path (FIX2)
    # ========================================
    linkdb_path = _resolve_feature_db_path()
    print(f"\n[LINKDB] DB PATH RESOLUTION (FIX2):")
    print(f"  linkdb_path = {linkdb_path}")

    if not linkdb_path or not os.path.isfile(linkdb_path):
        log_error(f"[LINKDB] linkdb not found. Run pipeline Phase 2 (linking) first")
        print(f"[LINKDB] ERROR: linkdb does not exist! Cannot materialize without link database.")
        raise RuntimeError("[LINKDB] linkdb.sqlite not found - run linking phase first")
    
    # ========================================
    # LOAD FEATURES: links.sqlite first, then fallback to GPKG
    # ========================================
    feature_source = "LINKS_SQLITE"
    _gpkg = gpkg_path  # may be None; resolved in fallback branch
    print(f"[PHASE4] Trying feature load from links.sqlite: {linkdb_path}")

    try:
        features = load_feature_map_from_linkdb(linkdb_path=linkdb_path, ids_str=ids_str)
    except Exception as ex:
        if "No valid feature table" in str(ex):
            # ── GPKG FALLBACK ──
            print(f"[PHASE4] links.sqlite has no feature tables -> fallback to GPKG (Feature Source of Truth)")
            log_info(f"[PHASE4] links.sqlite has no feature tables -> fallback to GPKG")

            _gpkg = gpkg_path
            if not _gpkg or not os.path.isfile(str(_gpkg)):
                try:
                    import bpy as _bpy
                    _gpkg = getattr(
                        _bpy.context.scene.m1dc_settings, "gpkg_path", ""
                    ).strip()
                except Exception:
                    _gpkg = ""
            if not _gpkg or not os.path.isfile(str(_gpkg)):
                raise RuntimeError(
                    "Phase 4 requires gpkg_path when links.sqlite contains no feature tables."
                )

            # One-shot schema dump (only on first fallback)
            _debug_dump_gpkg_schema(_gpkg)

            features = load_feature_map_from_gpkg(_gpkg, ids_str, prefer_id_col=osm_key_col)
            feature_source = "GPKG"
        else:
            log_error(f"[PHASE4] Failed to load features from links.sqlite: {ex}")
            print(f"[PHASE4] ERROR: {ex}")
            import traceback
            traceback.print_exc()
            raise RuntimeError(f"[PHASE4] Failed to load features: {ex}")

    # ========================================
    # Debug Proof: feature source + rows loaded
    # ========================================
    _feat_db = linkdb_path if feature_source == "LINKS_SQLITE" else _gpkg
    print(f"\n[PHASE4] feature_source={feature_source}")
    print(f"[PHASE4] feature_db={_feat_db}")
    print(f"[PHASE4] feature_rows_loaded={len(features)}")

    sample_feature_keys = list(features.keys())[:5]
    print(f"[PHASE4] sample_feature_keys={sample_feature_keys}")

    _debug_sample_feature_rows(features, sample_feature_keys, print)

    log_info(f"[PHASE4] feature_source={feature_source} rows_requested={len(ids_str)} rows_returned={len(features)}")

    # ========================================
    # HARD FAILURE POLICY: Validate features loaded
    # ========================================
    if len(ids_str) > 0 and len(features) == 0:
        log_error(f"[PHASE4][CRITICAL] 0 features loaded (source={feature_source}) but {len(ids_str)} IDs present in mesh")
        log_error(f"[PHASE4] First 20 IDs requested: {ids_str[:20]}")
        log_error(f"[PHASE4] feature_db: {_feat_db}")

        # Show source tables for diagnosis
        try:
            con_diag = sqlite3.connect(_feat_db)
            cur_diag = con_diag.cursor()
            tables = _sqlite_list_tables(cur_diag)
            log_error(f"[PHASE4] Available tables in {feature_source}: {tables}")
            con_diag.close()
        except Exception as ex:
            log_error(f"[PHASE4] Cannot open {_feat_db} for diagnosis: {ex}")

        raise RuntimeError(f"[PHASE4][CRITICAL] Materialize cannot proceed: 0 features (source={feature_source}) despite {len(ids_str)} mesh IDs")
    
    # ========================================
    # FORENSIC: Features dict validation
    # ========================================
    print(f"\n[M1DC P4 DEBUG] features dict: {len(features)} rows loaded")
    if features:
        sample_keys = list(features.keys())[:3]
        print(f"[M1DC P4 DEBUG] sample_keys: {sample_keys}")
        if probe_osm_id is not None:
            in_dict = probe_osm_id in features
            print(f"[M1DC P4 DEBUG] probe_osm_id='{probe_osm_id}' in features: {in_dict}")

    # ========================================
    # FORENSIC: Log probe face raw values from feature source
    # ========================================
    if probe_osm_id is not None:
        probe_feature = features.get(probe_osm_id)
        if probe_feature:
            print(f"\n[M1DC P4 RAW] mesh={mesh_name} face={probe_face_idx} osm_id={probe_osm_id} source={feature_source}")
            print(f"  row_keys={list(probe_feature.keys())}")
            for key in ["building", "amenity", "landuse", "shop", "tourism", "leisure"]:
                val = probe_feature.get(key, "<NOT_IN_ROW>")
                print(f"  row['{key}']='{val}'")
        else:
            print(f"\n[M1DC P4 RAW] mesh={mesh_name} face={probe_face_idx} osm_id={probe_osm_id}")
            print(f"  FEATURE NOT FOUND in {feature_source}! (no row for this osm_id)")

    # Step 5: Create FACE STRING attributes + has_feature INT (proof attribute)
    # NOTE: STRING FACE attributes are intermediate — Phase 5 encodes them to INT codes.
    # Blender docs warn that STRING attributes on large meshes degrade Spreadsheet performance.
    # The *_code INT attributes (Phase 5) are the canonical columns for Spreadsheet/analysis.
    print("[OSM Features] Creating FACE STRING attributes...")
    
    # Define CODE_KEYS if not already imported
    try:
        from .pipeline.diagnostics.legend_encoding import CODE_KEYS
    except ImportError:
        CODE_KEYS = ["amenity", "building", "landuse", "shop", "office",
                     "tourism", "highway", "leisure", "historic",
                     "man_made", "public_transport", "railway",
                     "natural", "waterway", "aeroway"]

    # === Ensure has_feature INT attribute (proof of lookup success) ===
    has_feature_attr = mesh.attributes.get("has_feature")
    if has_feature_attr is None:
        has_feature_attr = mesh.attributes.new("has_feature", "INT", "FACE")
    elif has_feature_attr.domain != "FACE" or has_feature_attr.data_type != "INT":
        mesh.attributes.remove(has_feature_attr)
        has_feature_attr = mesh.attributes.new("has_feature", "INT", "FACE")
    # FIX2: Length guard
    if has_feature_attr and len(has_feature_attr.data) != face_count:
        print(f"[P4][SKIP] has_feature attr_len={len(has_feature_attr.data)} face_count={face_count}")
        has_feature_attr = None

    # Build attr_mapping: STRING attributes WITHOUT osm_ prefix
    # Column name in feature source -> attribute name on mesh
    attr_mapping = {
        "building": "building",
        "amenity": "amenity",
        "landuse": "landuse",
        "type": "type",
        "aeroway": "aeroway",
        "boundary": "boundary",
        "admin_level": "admin_level",
        "name": "name",
        "shop": "shop",
        "office": "office",
        "tourism": "tourism",
        "leisure": "leisure",
        "historic": "historic",
        "man_made": "man_made",
    }

    print(f"[OSM Features] Will create STRING attrs for: {list(attr_mapping.values())[:10]}...")

    # Create attributes if missing (with length guard - FIX2)
    attr_handles = {}
    for feature_col, attr_name in attr_mapping.items():
        attr = mesh.attributes.get(attr_name)
        if attr is None:
            attr = mesh.attributes.new(attr_name, "STRING", "FACE")
            print(f"[OSM Features] Created attribute: {attr_name}")
        elif attr.domain != "FACE" or attr.data_type != "STRING":
            mesh.attributes.remove(attr)
            attr = mesh.attributes.new(attr_name, "STRING", "FACE")
            print(f"[OSM Features] Recreated attribute: {attr_name}")

        # FIX2: Length guard - skip attr if data length mismatch
        if attr and len(attr.data) != face_count:
            print(f"[P4][SKIP] attr={attr_name} attr_len={len(attr.data)} face_count={face_count} reason=attr_len_mismatch")
            attr = None
        attr_handles[feature_col] = attr

    # ── CRITICAL: Re-resolve ALL attribute handles after bulk creation ──
    # Adding attributes to a mesh invalidates existing bpy_prop_collection
    # references (Blender 4.x API caveat). Re-fetch every handle to get fresh
    # pointers that correctly reflect the final attribute layout.
    has_feature_attr = mesh.attributes.get("has_feature")
    if has_feature_attr and (has_feature_attr.domain != "FACE" or has_feature_attr.data_type != "INT" or len(has_feature_attr.data) != face_count):
        has_feature_attr = None
    for feature_col, attr_name in attr_mapping.items():
        attr = mesh.attributes.get(attr_name)
        if attr and attr.domain == "FACE" and attr.data_type == "STRING" and len(attr.data) == face_count:
            attr_handles[feature_col] = attr
        else:
            attr_handles[feature_col] = None
    _valid_handles = sum(1 for v in attr_handles.values() if v is not None)
    print(f"[P4][ATTR_REFRESH] Re-resolved {_valid_handles}/{len(attr_handles)} attr handles + has_feature={'OK' if has_feature_attr else 'MISSING'}")

    # Step 6: Write features to FACE attributes
    print("[OSM Features] Writing features to FACE attributes...")

    written_count = 0
    probe_write_logged = False

    # Hit/Miss counters and samples
    features_hit = 0
    features_miss = 0
    faces_checked = 0
    strings_written = 0  # Count of non-empty STRING values written
    hit_samples = []
    miss_samples = []
    sample_face_osm_ids = []

    for poly in mesh.polygons:
        idx = poly.index
        faces_checked += 1
        
        # Robust ID read via _safe_read_face_id_attr (FIX2)
        osm_id = _safe_read_face_id_attr(mesh, idx)

        if not osm_id:
            # No link, write empty bytes to all attrs + has_feature=0
            if has_feature_attr:
                has_feature_attr.data[idx].value = 0
            for attr in attr_handles.values():
                if attr and idx < len(attr.data):
                    attr.data[idx].value = b""  # FIX: Blender 4.5 STRING attrs require bytes
            continue

        # Record sample face osm_ids (first 10)
        if len(sample_face_osm_ids) < 10:
            sample_face_osm_ids.append(osm_id)

        # ── P4 FIX: Ensure lookup key is str (features dict keys are always str via _norm_id) ──
        feature = features.get(_key_to_str(osm_id) if not isinstance(osm_id, str) else osm_id)

        if not feature:
            # MISS: Link exists but no feature row found in source
            features_miss += 1
            if len(miss_samples) < 10:
                miss_samples.append(osm_id)
            
            # DIAGNOSTIC: Log first few misses to debug normalization
            if features_miss <= 3:
                print(f"\n[MATERIALIZE_DIAGNOSTIC] MISS: mesh={mesh_name} face={idx} osm_id={osm_id} type={type(osm_id).__name__}")
                print(f"  Looking for key='{osm_id}' in features dict")
                print(f"  Features dict has {len(features)} entries")
                sample_keys_in_dict = list(features.keys())[:5]
                print(f"  Sample keys in dict: {sample_keys_in_dict}")
                # Type proof: compare key types
                if sample_keys_in_dict:
                    print(f"  Dict key type={type(sample_keys_in_dict[0]).__name__} face key type={type(osm_id).__name__}")
            
            if has_feature_attr:
                has_feature_attr.data[idx].value = 0
            for attr in attr_handles.values():
                if attr and idx < len(attr.data):
                    attr.data[idx].value = b""  # FIX: Blender 4.5 STRING attrs require bytes
            continue

        # HIT: Found feature row
        features_hit += 1
        if has_feature_attr:
            has_feature_attr.data[idx].value = 1  # PROOF: has_feature=1 means lookup succeeded
        if len(hit_samples) < 10:
            hit_samples.append((osm_id, feature.get("building", ""), feature.get("amenity", "")))

        # Write feature attributes from row
        for gpkg_col, attr in attr_handles.items():
            if attr is None or idx >= len(attr.data):
                continue
            val = feature.get(gpkg_col, "")
            attr.data[idx].value = _to_attr_bytes(val)  # FIX: must be bytes for Blender 4.5
            if val and str(val).strip():
                strings_written += 1

        # [P4][PROOF] One-shot diagnostic on first hit
        if features_hit == 1:
            print(f"[P4][PROOF] raw_id={osm_id} type={type(osm_id).__name__} "
                  f"hit=True sample_feature_keys={list(features.keys())[:3]}")

        # ========================================
        # FORENSIC: Log probe face STRING writes
        # ========================================
        if idx == probe_face_idx and not probe_write_logged:
            print(f"\n[M1DC P4 WRITE] mesh={mesh_name} face={idx} osm_id={osm_id}")
            # Verify what was written to key STRING attrs
            for key in ["building", "amenity", "landuse"]:
                if key in attr_handles:
                    written_val = attr_handles[key].data[idx].value
                    print(f"  osm_{key} (STRING) = '{written_val}'")
            probe_write_logged = True

        # Count faces with non-empty name
        if feature.get("name", ""):
            written_count += 1

    try:
        mesh.update()
    except Exception:
        pass

    # ========================================
    # CRITICAL: Flush mesh attribute writes
    # ========================================
    try:
        mesh.update()
        import bpy
        bpy.context.view_layer.update()
    except Exception:
        pass

    # ========================================
    # [P4][STATS] Per-mesh summary (surgical proof)
    # ========================================
    print(f"\n[P4][STATS] mesh={mesh_name} hits={features_hit} misses={features_miss} faces={face_count} strings_written={strings_written}")
    print(f"\n[PHASE4] source={feature_source} hits={features_hit} miss={features_miss} strings_written={strings_written}")
    if hit_samples:
        print(f"[PHASE4] hit_samples (first 5): {hit_samples[:5]}")
    else:
        print(f"[PHASE4] WARNING: No hits! All lookups missed despite {faces_with_link} faces with link")

    # ========================================
    # PHASE D GATE 1: Feature lookup validation
    # HARD FAIL if mesh has IDs but 0 feature hits (key mismatch)
    # ========================================
    if faces_with_link > 0 and features_hit == 0:
        log_error(f"[PHASE_D][GATE1_FAIL] {mesh_name}: {faces_with_link} faces_with_link but 0 feature hits (source={feature_source})")
        log_error(f"[PHASE_D] This indicates osm_key_col mismatch or empty feature source")
        log_error(f"[PHASE_D] First 20 face keys: {sample_face_osm_ids}")
        log_error(f"[PHASE_D] First 5 feature keys: {list(features.keys())[:5]}")
        raise RuntimeError(f"[PHASE_D][GATE1_FAIL] 0 feature hits despite {faces_with_link} valid links (key mismatch)")

    # ============================================================
    # [MATERIALIZE_PROOF] Logging (final outcome)
    # ============================================================
    log_info(f"[MATERIALIZE_PROOF] mesh={mesh_name}")
    log_info(f"  faces_total={face_count} faces_checked={faces_checked}")
    log_info(f"  faces_with_link={faces_with_link}")
    log_info(f"  features_hit={features_hit} (lookup succeeded)")
    log_info(f"  features_miss={features_miss} (link exists, {feature_source} miss)")
    log_info(f"  strings_written={strings_written}")
    if hit_samples:
        log_info(f"  hit_samples (first 3): {hit_samples[:3]}")
    if miss_samples:
        log_info(f"  miss_samples (first 3): {miss_samples[:3]}")
    
    print(f"\n[M1DC P4 PROOF] mesh={mesh_name}")
    print(f"  faces_total={face_count}")
    print(f"  faces_with_link={faces_with_link}")
    print(f"  features_hit={features_hit}")
    print(f"  features_miss={features_miss}")
    print(f"  strings_written={strings_written}")

    # Sample face osm_ids (max 10)
    print(f"  sample_face_osm_ids={sample_face_osm_ids}")

    # Sample feature keys (max 10 from features dict)
    sample_feature_keys = list(features.keys())[:10] if features else []
    print(f"  sample_feature_keys={sample_feature_keys}")

    if hit_samples:
        print(f"  hit_samples (osm_id, building, amenity):")
        for sample in hit_samples[:10]:
            print(f"    {sample}")

    if miss_samples:
        print(f"  miss_samples (osm_ids not found in {feature_source}):")
        print(f"    {miss_samples}")

    # Final proof logs (per user spec)
    print(f"\n[Materialize] PROOF source={feature_source} faces={face_count} checked={faces_checked} with_link={faces_with_link} hit={features_hit} miss={features_miss}")
    print(f"  feature_map_rows={len(features)}")
    print(f"  strings_written={strings_written}")

    # Sample face osm_ids from mesh (first 10)
    if sample_face_osm_ids:
        print(f"  sample_face_osm_ids={sample_face_osm_ids}")

    # Sample hit/miss details
    if hit_samples:
        print(f"  hit_samples (osm_id, building, amenity):")
        for sample in hit_samples[:5]:
            print(f"    {sample}")
    
    if miss_samples:
        print(f"  miss_samples (osm_ids NOT in {feature_source}):")
        print(f"    {miss_samples}")

    # Sample decoded features (max 3)
    if features:
        print(f"  sample_decoded (osm_id, building, landuse, name):")
        for k in list(features.keys())[:3]:
            row = features[k]
            decoded = (k, row.get("building", ""), row.get("landuse", ""), row.get("name", ""))
            print(f"    {decoded}")

    # ── SCHRITT 5: Face-Writeback Proof (Phase 4) ──
    log_info(f"[PHASE4] faces_with_link={faces_with_link}")
    log_info(f"[PHASE4] features_hit={features_hit}")
    log_info(f"[PHASE4] features_miss={features_miss}")
    log_info(f"[PHASE4] strings_written={strings_written}")

    # ── TASK C: 3-Face Stichprobe (Sample Proof) ──
    if features_hit > 0:
        log_info("[PHASE4][SAMPLE] 3-Face Proof:")
        sample_count = 0
        for poly in mesh.polygons:
            idx = poly.index
            # Robust ID read via _safe_read_face_id_attr (FIX2)
            osm_id = _safe_read_face_id_attr(mesh, idx)
            if not osm_id:
                continue
            
            feature = features.get(osm_id)
            if not feature:
                continue
            
            # Found a hit, log it
            building_val = attr_handles.get("building").data[idx].value if "building" in attr_handles else "__NULL__"
            amenity_val = attr_handles.get("amenity").data[idx].value if "amenity" in attr_handles else "__NULL__"
            name_val = attr_handles.get("name").data[idx].value if "name" in attr_handles else "__NULL__"
            landuse_val = attr_handles.get("landuse").data[idx].value if "landuse" in attr_handles else "__NULL__"
            
            log_info(f"  face={idx} osm_key={osm_id} building={building_val!r} amenity={amenity_val!r} landuse={landuse_val!r} name={name_val!r}")
            sample_count += 1
            if sample_count >= 3:
                break

    print(f"[OSM Features] Writeback complete: {written_count} faces with name")

    # ========================================
    # ACCEPTANCE GATE: Verify at least one code > 0
    # ========================================
    print("\n[M1DC Acceptance] Scanning for nonzero STRING values (first 5000 faces)...")
    scan_limit = min(5000, face_count)
    nonzero_found = {}
    for i, poly in enumerate(mesh.polygons):
        if i >= scan_limit:
            break
        idx = poly.index
        for gpkg_col, attr_name in attr_mapping.items():
            if gpkg_col not in attr_handles:
                continue
            attr = attr_handles[gpkg_col]
            try:
                val = attr.data[idx].value
                # FIX: bytes-safe check for Blender 4.5 STRING attrs
                cleaned = _bytes_to_clean_str(val) if val else ""
                if cleaned and cleaned != "__NULL__" and cleaned.strip():
                    if gpkg_col not in nonzero_found:
                        nonzero_found[gpkg_col] = 0
                    nonzero_found[gpkg_col] += 1
            except Exception:
                pass
    
    print(f"[M1DC Acceptance] nonzero STRING values (first {scan_limit} faces):")
    for col, count in sorted(nonzero_found.items()):
        print(f"  {col}: {count} faces")
    
    total_nonzero = sum(nonzero_found.values())
    print(f"[M1DC Acceptance] Total nonzero STRING values: {total_nonzero}")
    
    # ========================================
    # PHASE D GATE 2: Writeback validation
    # HARD FAIL if features_hit > 0 but no STRING attrs written
    # ========================================
    if total_nonzero == 0 and faces_with_link > 0:
        error_msg = f"[PHASE_D][GATE2_FAIL] Writeback failed: {features_hit} hits but 0 STRING attributes written"
        print(f"{error_msg}")
        print(f"  features_hit={features_hit}, features_returned={len(features)}")
        print(f"  Diagnostic: Check attribute creation or writeback logic")
        raise RuntimeError(error_msg)
    elif total_nonzero > 0:
        print(f"[M1DC Acceptance] SUCCESS: {total_nonzero} nonzero STRING values found (will be encoded to codes in Phase 5)")
        log_info(f"[MATERIALIZE_SUCCESS] {total_nonzero} STRING attributes populated (hit rate={features_hit}/{faces_with_link})")

    # ========================================
    # MINI VALIDATION: materialize summary
    # ========================================
    print(f"\n[MATERIALIZE][VALIDATE] source={feature_source} faces_with_link={faces_with_link} hits={features_hit} miss={features_miss}")
    print(f"[MATERIALIZE][VALIDATE] feature_db={_feat_db}")

    # ========================================
    # REQUIRED: Per-mesh proof log
    # ========================================
    print(f"[Phase4][Mesh] faces={face_count} faces_with_link={faces_with_link} hit={features_hit} miss={features_miss} strings_written={strings_written}")

    return written_count


def _get_osm_code_attr_name(key):
    """
    Convert feature key to mesh attribute name.

    Input: base key like "building" or "amenity"
    Output: "osm_building_code" etc.

    Always prefix osm_, always suffix _code.
    """
    return f"osm_{key}_code"


def code_attr_to_feature_key(code_attr: str) -> str:
    """
    Normalize code attribute name to feature key.

    Handles:
    - "osm_building_code" -> "building"
    - "building_code" -> "building"
    - "osm_building" -> "building"
    - "building" -> "building"

    Args:
        code_attr: Code attribute name (e.g., "osm_building_code")

    Returns:
        str: Feature key (e.g., "building")
    """
    k = code_attr
    if k.startswith("osm_"):
        k = k[4:]  # Remove "osm_" prefix
    if k.endswith("_code"):
        k = k[:-5]  # Remove "_code" suffix
    return k


# ── P5 HELPER: Blender 4.5 STRING attributes return bytes ──
def _bytes_to_clean_str(v):
    """Decode a Blender STRING attribute value (bytes in Blender 4.5) to a clean Python str.
    Returns '__NULL__' for None/empty/'None', otherwise the decoded string.

    Also handles the 'b\\'...\\'' string representation pattern from DB/cache.
    """
    if v is None:
        return "__NULL__"
    if isinstance(v, (bytes, bytearray)):
        try:
            s = v.decode("utf-8", errors="replace")
        except Exception:
            s = str(v)
    else:
        s = str(v)

    # Handle string representation of bytes: "b'house'" or 'b"house"'
    if isinstance(s, str):
        if s.startswith("b'") and s.endswith("'"):
            s = s[2:-1]
        elif s.startswith('b"') and s.endswith('"'):
            s = s[2:-1]

    if s == "" or s == "None":
        return "__NULL__"
    return s


def _count_nonzero_int_attr(mesh, attr_name):
    """Count faces with nonzero value for an INT face attribute.

    This is a standalone post-check — it reads the attribute independently
    of any writeback variables, making it immune to control-flow bugs.

    Args:
        mesh: Blender mesh data (bpy.types.Mesh)
        attr_name: Name of the INT face attribute (e.g., 'osm_building_code')

    Returns:
        int: Number of faces where attr value != 0, or 0 if attr missing
    """
    a = mesh.attributes.get(attr_name)
    if not a:
        return 0
    if a.domain != 'FACE' or a.data_type != 'INT':
        return 0
    nz = 0
    for i in range(len(mesh.polygons)):
        try:
            if a.data[i].value != 0:
                nz += 1
        except (IndexError, RuntimeError):
            pass
    return nz


def _materialize_legend_codes(mesh, gpkg_path, output_dir):
    """
    Write legend-encoded integer codes to FACE attributes.

    Reads osm_* STRING attributes, encodes them to osm_*_code INT attributes
    using legend CSVs from output_dir/legends/.

    Args:
        mesh: Blender mesh object
        gpkg_path: Path to GeoPackage file (for table name detection)
        output_dir: Base output directory (legends in output_dir/legends/)

    Returns:
        int: Total number of nonzero code values written (deterministic)
    """
    from .pipeline.diagnostics.legend_encoding import (
        CODE_KEYS, get_legend_cache_dir, init_legend_caches,
        legend_encode, legend_export_csv, _ENCODE_CACHE
    )

    mesh_name = getattr(mesh, "name", "<unknown>")
    print(f"\n[M1DC Phase5] Legend code writeback for mesh: {mesh_name}")

    # CRITICAL: Initialize face_count FIRST — used everywhere below.
    # Previously this was set late, causing UnboundLocalError in forensic logging.
    face_count = len(mesh.polygons)
    print(f"[M1DC Phase5] face_count={face_count}")

    # Get legends directory
    legends_dir = get_legend_cache_dir(output_dir)
    if not os.path.isdir(legends_dir):
        print(f"[M1DC Phase5] Legends directory not found: {legends_dir}")
        return 0

    print(f"[M1DC Phase5] Legends directory: {legends_dir}")

    # Find legend CSV files to determine table name
    legend_files = sorted([f for f in os.listdir(legends_dir) if f.endswith("_legend.csv")])
    if not legend_files:
        print("[M1DC Phase5] No legend files found")
        return 0

    print(f"[M1DC Phase5] Found {len(legend_files)} legend files: {legend_files[:5]}...")

    # Extract table name from first legend file
    stem = legend_files[0].replace("_legend.csv", "")
    print(f"[M1DC Phase5] First legend stem: {stem}")
    try:
        table_name, first_col = stem.rsplit("__", 1)
        print(f"[M1DC Phase5] Parsed: table_name='{table_name}', first_col='{first_col}'")
    except ValueError:
        print(f"[M1DC Phase5] Cannot parse table name from: {legend_files[0]}")
        print(f"[M1DC Phase5] Expected format: table__column_legend.csv (double underscore)")
        return 0

    print(f"[M1DC Phase5] Using table: {table_name}")

    # Initialize legend caches
    loaded = init_legend_caches(legends_dir, table_name)
    if loaded == 0:
        print("[M1DC Phase5] No legend caches loaded")
        return 0

    print(f"[M1DC Phase5] Loaded {loaded} legend caches")

    # ========================================
    # FORENSIC: Dump cache keys and sample entries
    # ========================================
    print(f"\n[M1DC FORENSIC] Cache dump after init_legend_caches:")
    print(f"  _ENCODE_CACHE keys: {list(_ENCODE_CACHE.keys())}")
    for cache_key in list(_ENCODE_CACHE.keys())[:3]:
        cache = _ENCODE_CACHE.get(cache_key, {})
        sample_entries = list(cache.items())[:5]
        print(f"  {cache_key}: {len(cache)} entries, samples: {sample_entries}")

    # Find which STRING attributes exist on the mesh (Phase 4 writes without osm_ prefix)
    attrs_to_encode = []
    print(f"[M1DC Phase5] Checking for STRING attributes (CODE_KEYS={CODE_KEYS[:5]}...)")

    for key in CODE_KEYS:
        string_attr_name = key  # Phase 4 writes as "building", not "osm_building"
        code_attr_name = _get_osm_code_attr_name(key)  # osm_{key}_code
        string_attr = mesh.attributes.get(string_attr_name)
        if string_attr and string_attr.domain == "FACE" and string_attr.data_type == "STRING":
            attrs_to_encode.append((key, string_attr_name, code_attr_name))
            print(f"[M1DC Phase5]   Found STRING attr: {string_attr_name} -> will write {code_attr_name}")

    print(f"[M1DC Phase5] Found {len(attrs_to_encode)} STRING attributes to encode")

    if not attrs_to_encode:
        print("[M1DC Phase5] WARNING: No STRING attributes found to encode!")
        # List what attributes exist
        print("[M1DC Phase5] Existing mesh attributes:")
        for attr in mesh.attributes:
            print(f"[M1DC Phase5]   {attr.name}: domain={attr.domain}, type={attr.data_type}")
        return 0

    # Create/ensure INT code attributes exist
    code_attrs = {}
    for key, string_name, code_name in attrs_to_encode:
        code_attr = mesh.attributes.get(code_name)
        attr_existed = code_attr is not None
        if code_attr is None:
            code_attr = mesh.attributes.new(code_name, "INT", "FACE")
            print(f"[M1DC Phase5] Created attribute: {code_name}")
        elif code_attr.domain != "FACE" or code_attr.data_type != "INT":
            mesh.attributes.remove(code_attr)
            code_attr = mesh.attributes.new(code_name, "INT", "FACE")
            print(f"[M1DC Phase5] Recreated attribute: {code_name}")
        code_attrs[key] = code_attr

        # ========================================
        # FORENSIC: Target attribute proof
        # ========================================
        print(f"\n[M1DC P5 TARGET] mesh={mesh_name} key='{key}'")
        print(f"  computed_target_attr='{code_name}'")
        print(f"  target_exists={attr_existed}")
        print(f"  code_attrs['{key}'] -> {code_attr.name if code_attr else 'None'}")

    # ========================================
    # FORENSIC FACE PROBE: Use same criteria as Phase 4 (osm_id_int > 0)
    # ========================================
    print(f"\n[M1DC P5 PROBE] Looking for probe face (first with osm_id_int > 0)...")
    osm_id_int_attr = mesh.attributes.get("osm_id_int")
    building_attr = mesh.attributes.get("building")  # Phase 4 writes as "building" without osm_ prefix
    osm_building_code_attr = code_attrs.get("building")

    probe_face_idx = None
    probe_osm_id = None

    if osm_id_int_attr and osm_id_int_attr.domain == "FACE":
        for poly in mesh.polygons:
            idx = poly.index
            osm_id = osm_id_int_attr.data[idx].value
            if osm_id > 0:
                probe_face_idx = idx
                probe_osm_id = osm_id
                break

    if probe_face_idx is not None:
        print(f"[M1DC P5 PROBE] mesh={mesh_name} probe_face={probe_face_idx} osm_id_int={probe_osm_id}")

        # Read raw STRING value for building (bytes in Blender 4.5)
        raw_building = ""
        if building_attr and building_attr.domain == "FACE" and building_attr.data_type == "STRING":
            _raw_b = building_attr.data[probe_face_idx].value or b""
            raw_building = _bytes_to_clean_str(_raw_b)
            if raw_building == "__NULL__":
                raw_building = ""

        print(f"\n[M1DC P5 ENCODE] mesh={mesh_name} face={probe_face_idx} feature_key=building")
        print(f"  raw='{raw_building}'")

        # Try to encode
        cache_key = "building_code"
        cache = _ENCODE_CACHE.get(cache_key, {})
        probe_code = legend_encode(cache_key, raw_building)

        print(f"  legend_key='{cache_key}' cache_size={len(cache)}")
        print(f"  code={probe_code}")

        if probe_code == 0 and raw_building and raw_building.strip():
            # Raw is non-empty but code is 0 - why?
            print(f"[M1DC P5 ENCODE MISS] raw not in cache!")
            if raw_building in cache:
                print(f"  BUT WAIT: exact match exists: '{raw_building}' -> {cache[raw_building]}")
            else:
                lower_val = raw_building.lower().strip()
                found_similar = [(k, v) for k, v in list(cache.items())[:50] if k.lower() == lower_val]
                if found_similar:
                    print(f"  CASE MISMATCH: found similar: {found_similar}")
                else:
                    sample_keys = list(cache.keys())[:10]
                    print(f"  sample_keys={sample_keys}")

        # BEFORE/AFTER writeback proof
        if osm_building_code_attr:
            before = osm_building_code_attr.data[probe_face_idx].value
            osm_building_code_attr.data[probe_face_idx].value = probe_code
            after = osm_building_code_attr.data[probe_face_idx].value

            print(f"\n[M1DC P5 WRITE] mesh={mesh_name} face={probe_face_idx} target_attr=osm_building_code")
            print(f"  before={before} code={probe_code} after={after}")

            if after == 0 and probe_code > 0:
                print(f"  ERROR: Writeback failed! code={probe_code} but after={after}")
            elif after > 0:
                print(f"  SUCCESS: Writeback confirmed (after > 0)")
        else:
            print(f"[M1DC P5 WRITE] osm_building_code attr not in code_attrs!")
    else:
        print(f"[M1DC P5 PROBE] NO FACE WITH osm_id_int > 0 FOUND!")
        if not osm_id_int_attr:
            print(f"  osm_id_int attribute does not exist on mesh!")

    # Also sample some STRING values to see what Phase 4 wrote
    if building_attr and building_attr.domain == "FACE":
        sample_vals = []
        for poly in mesh.polygons[:20]:
            val = building_attr.data[poly.index].value
            sample_vals.append(repr(val))
        print(f"\n[M1DC P5 SAMPLE] First 20 building STRING values: {sample_vals}")

        # Count non-empty (bytes-safe)
        nonempty_count = 0
        for poly in mesh.polygons:
            raw = building_attr.data[poly.index].value
            cleaned = _bytes_to_clean_str(raw)
            if cleaned != "__NULL__" and cleaned.strip():
                nonempty_count += 1
        print(f"[M1DC P5 SAMPLE] Total non-empty building: {nonempty_count}/{face_count}")
    else:
        print(f"[M1DC FORENSIC] building attribute not found or wrong type!")
        nonempty_count = 0

    # ── P5 ABORT GUARD: If ALL STRING source attrs are empty, Phase 4 didn't write ──
    _p5_any_nonempty = False
    for _p5_key, _p5_sname, _p5_cname in attrs_to_encode:
        _p5_src = mesh.attributes.get(_p5_sname)
        if _p5_src and _p5_src.domain == "FACE" and _p5_src.data_type == "STRING":
            for _p5_poly in mesh.polygons:
                _p5_raw = _p5_src.data[_p5_poly.index].value
                _p5_val = _bytes_to_clean_str(_p5_raw)
                if _p5_val != "__NULL__" and _p5_val.strip():
                    _p5_any_nonempty = True
                    break
        if _p5_any_nonempty:
            break
    if not _p5_any_nonempty and face_count > 0:
        print("[P5][ABORT] No non-empty STRING values found on any source attr (Phase 4 missing or failed).")
        log_warn("[P5][ABORT] No non-empty STRING values found. Phase 4 must write strings first.")
        return 0

    # Write codes to faces with detailed logging
    total_codes_written = 0
    nonzero_codes_written = 0
    # face_count already set at function top

    # Logging limits
    legend_hit_samples = {key: [] for key, _, _ in attrs_to_encode}
    legend_miss_samples = {key: [] for key, _, _ in attrs_to_encode}
    writeback_samples = []
    MAX_SAMPLES = 3  # Reduced from 10 to minimize debug spam

    # Stats per attribute
    attr_nonzero_counts = {key: 0 for key, _, _ in attrs_to_encode}
    attr_unique_codes = {key: set() for key, _, _ in attrs_to_encode}

    # [PHASE 12] Debug flag - only print detailed encoding for first mesh
    DEBUG_ENCODING = (mesh.polygons and len(list(mesh.polygons)) > 0 and mesh.name)
    debug_printed = False

    for poly in mesh.polygons:
        idx = poly.index
        face_any_nonzero = False

        for key, string_name, code_name in attrs_to_encode:
            string_attr = mesh.attributes.get(string_name)
            if string_attr is None:
                continue

            string_value = string_attr.data[idx].value
            # ── P5 FIX: Blender 4.5 STRING attrs return bytes; decode to clean str ──
            string_value = _bytes_to_clean_str(string_value)
            if string_value == "__NULL__":
                string_value = ""  # legend_encode treats empty as code=0
            # [PHASE 12 FIX] Use normalized feature key for legend cache lookup
            # Legend cache is keyed by "{feature_key}_code" where feature_key is normalized
            feature_key = code_attr_to_feature_key(code_name)
            cache_key = f"{feature_key}_code"
            code = legend_encode(cache_key, string_value)

            # [PHASE 12] Debug first encode per mesh
            if DEBUG_ENCODING and not debug_printed and idx < 5 and code > 0:
                debug_printed = True
                print(f"[PH5 ENCODE] face={idx} feature_key='{feature_key}' val='{string_value}' cache_key='{cache_key}' code={code}")

            # Log legend resolution (HIT or MISS) - reduced verbosity
            if code > 0:
                if len(legend_hit_samples[key]) < MAX_SAMPLES:
                    legend_hit_samples[key].append((idx, string_value, code))
            elif string_value and string_value.strip():
                if len(legend_miss_samples[key]) < MAX_SAMPLES:
                    legend_miss_samples[key].append((idx, string_value))

            # Write the code
            code_attrs[key].data[idx].value = code
            total_codes_written += 1

            if code > 0:
                nonzero_codes_written += 1
                face_any_nonzero = True
                attr_nonzero_counts[key] += 1
                attr_unique_codes[key].add(code)

                # Writeback sample log (minimal)
                if len(writeback_samples) < 3:
                    osm_id_attr_str = mesh.attributes.get("osm_id")
                    osm_id_attr_int = mesh.attributes.get("osm_id_int")
                    if osm_id_attr_str and osm_id_attr_str.domain == "FACE":
                        osm_id_val = osm_id_attr_str.data[idx].value
                    elif osm_id_attr_int and osm_id_attr_int.domain == "FACE":
                        osm_id_val = osm_id_attr_int.data[idx].value
                    else:
                        osm_id_val = 0
                    writeback_samples.append((idx, osm_id_val, code_name, code, string_value))

    # ========================================
    # CRITICAL: Flush mesh attribute writes (Phase 5 codes)
    # ========================================
    try:
        mesh.update()
    except Exception:
        pass
    
    # Additional update to ensure Blender UI syncs
    try:
        import bpy
        bpy.context.view_layer.update()
    except Exception:
        pass

    # ========================================
    # Phase 5 Proof Counters
    # ========================================

    # Count faces that have ANY nonzero code across all attrs
    faces_with_any_nonzero = 0
    faces_with_nonempty_building = 0
    osm_building_code_nonzero = 0

    osm_building_code_attr = code_attrs.get("building")
    building_str_attr = mesh.attributes.get("building")  # Phase 4 writes as "building" without osm_ prefix

    for poly in mesh.polygons:
        idx = poly.index
        any_nonzero = False

        # Check all code attrs for this face
        for key in code_attrs:
            try:
                if code_attrs[key].data[idx].value > 0:
                    any_nonzero = True
                    break
            except Exception:
                pass

        if any_nonzero:
            faces_with_any_nonzero += 1

        # Specific building counters (bytes-safe)
        if building_str_attr:
            try:
                val = building_str_attr.data[idx].value
                cleaned = _bytes_to_clean_str(val)
                if cleaned != "__NULL__" and cleaned.strip():
                    faces_with_nonempty_building += 1
            except Exception:
                pass

        if osm_building_code_attr:
            try:
                if osm_building_code_attr.data[idx].value > 0:
                    osm_building_code_nonzero += 1
            except Exception:
                pass

    print(f"\n[M1DC Phase5 Summary] mesh={mesh_name}")
    print(f"  faces_total={face_count}")
    print(f"  total_codes_written={total_codes_written}")
    print(f"  nonzero_codes_written={nonzero_codes_written}")
    print(f"  faces_with_any_nonzero={faces_with_any_nonzero}")
    print(f"  faces_with_nonempty_building={faces_with_nonempty_building}")
    print(f"  osm_building_code_nonzero={osm_building_code_nonzero}/{face_count}")
    
    # [PHASE 9] Proof: Face code materialization validation
    if osm_building_code_nonzero > 0:
        print(f"[PROOF][CODES] attr=building_code nonzero_faces={osm_building_code_nonzero} total_faces={face_count}")
    else:
        if face_count > 0:
            print(f"[PROOF][CODES] WARNING: attr=building_code has 0 nonzero faces but {face_count} total_faces exist")

    # Required proof: nonzero counts for key code attrs (if they exist)
    osm_landuse_code_nonzero = 0
    osm_name_code_nonzero = 0
    osm_landuse_code_attr = mesh.attributes.get("osm_landuse_code")
    osm_name_code_attr = mesh.attributes.get("osm_name_code")
    if osm_landuse_code_attr and osm_landuse_code_attr.domain == "FACE":
        try:
            osm_landuse_code_nonzero = sum(1 for poly in mesh.polygons if osm_landuse_code_attr.data[poly.index].value > 0)
        except Exception:
            osm_landuse_code_nonzero = 0
    if osm_name_code_attr and osm_name_code_attr.domain == "FACE":
        try:
            osm_name_code_nonzero = sum(1 for poly in mesh.polygons if osm_name_code_attr.data[poly.index].value > 0)
        except Exception:
            osm_name_code_nonzero = 0

    print(f"  osm_landuse_code_nonzero={osm_landuse_code_nonzero}/{face_count}")
    print(f"  osm_name_code_nonzero={osm_name_code_nonzero}/{face_count}")
    
    # [PHASE 9] Additional proof: Verify at least one code attribute has nonzero values
    code_attrs_with_nonzero = []
    if osm_building_code_nonzero > 0:
        code_attrs_with_nonzero.append(("building_code", osm_building_code_nonzero))
    if osm_landuse_code_nonzero > 0:
        code_attrs_with_nonzero.append(("landuse_code", osm_landuse_code_nonzero))
    if osm_name_code_nonzero > 0:
        code_attrs_with_nonzero.append(("name_code", osm_name_code_nonzero))
    
    if not code_attrs_with_nonzero:
        print(f"[PROOF][CODES] WARNING: No code attributes have nonzero values on FACE domain")
    else:
        for attr_name, count in code_attrs_with_nonzero:
            print(f"[PROOF][CODES] {attr_name}={count}/{face_count}")

    # Per-attribute stats
    print(f"  attrs_nonzero_summary:")
    for key, string_name, code_name in attrs_to_encode:
        nonzero = attr_nonzero_counts[key]
        unique = len(attr_unique_codes[key])
        print(f"    {code_name}: nonzero_faces={nonzero}, unique_codes={unique}")
        # [PHASE 12] Show sample writeback for verification
        if nonzero > 0 and len(legend_hit_samples[key]) > 0:
            sample = legend_hit_samples[key][0]  # (face_idx, string_value, code)
            print(f"      sample: face={sample[0]} val='{sample[1]}' -> code={sample[2]}")

    # Legend resolution summary
    total_hits = 0
    total_misses = 0
    for key, string_name, code_name in attrs_to_encode:
        hits = len(legend_hit_samples[key])
        misses = len(legend_miss_samples[key])
        total_hits += hits
        total_misses += misses
        if hits > 0 or misses > 0:
            print(f"  legend_resolution[{code_name}]: hits={hits}, misses={misses}")
    
    # [PHASE 9] Proof: Feature lookup validation
    print(f"[PROOF][FEATURE_LOOKUP] hit={total_hits} miss={total_misses}")
    if total_misses > 0 and total_hits == 0:
        print(f"[PROOF][FEATURE_LOOKUP] WARNING: No feature lookups succeeded, all {total_misses} lookups failed")

    # ── SCHRITT 6: Phase 5 Minimal Proof ──
    codes_summary = {}
    for key, string_name, code_name in attrs_to_encode:
        nz = attr_nonzero_counts.get(key, 0)
        codes_summary[code_name] = nz
    log_info("[PHASE5] codes_written:")
    for cname, cnt in codes_summary.items():
        log_info(f"  {cname}: {cnt}")
    
    # ── TASK D: Count nonzero code layers ──
    nonzero_code_layers = sum(1 for cnt in codes_summary.values() if cnt > 0)
    log_info(f"[PHASE5] nonzero_code_layers={nonzero_code_layers}")
    
    if nonzero_code_layers < 2 and face_count > 0:
        log_warn(f"[PHASE5] WARNING: Only {nonzero_code_layers} code layer(s) have nonzero values (expected >= 2)")
    
    all_zero = all(cnt == 0 for cnt in codes_summary.values())
    if all_zero and face_count > 0:
        log_error("[PHASE5] FAIL: all codes = 0 despite faces existing. Check Phase 4 STRING writeback.")

    # [PHASE 4] Export phase reached
    print("[ACCEPT] phase4_reached=True")
    
    # [PHASE 4] Guarantee output folders exist before writing
    from pathlib import Path as PathlibPath
    try:
        output_base = PathlibPath(output_dir)
        legends_dir = output_base / "legends"
        legends_dir.mkdir(parents=True, exist_ok=True)
        print(f"[EXPORT] legends_dir={legends_dir} exists={legends_dir.exists()}")
    except Exception as ex:
        print(f"[EXPORT] ERROR creating legends folder: {ex}")
        raise RuntimeError(f"[EXPORT] Cannot create legends folder: {ex}") from ex

    # Export combined legend CSV
    export_path = os.path.join(output_dir, "legends", "legend_codes_combined.csv")
    try:
        rows_written = legend_export_csv(export_path)
        # Verify export
        export_path_obj = PathlibPath(export_path)
        if export_path_obj.exists():
            size_bytes = export_path_obj.stat().st_size
            print(f"[LEGEND_EXPORT] path={export_path} rows_written={rows_written} size_bytes={size_bytes}")
            if size_bytes == 0:
                raise RuntimeError(f"[LEGEND_EXPORT] CSV file written but is empty (0 bytes)")
        else:
            raise RuntimeError(f"[LEGEND_EXPORT] CSV file not found after export: {export_path}")
    except Exception as ex:
        print(f"[LEGEND_EXPORT] ERROR: {ex}")
        raise RuntimeError(f"[LEGEND_EXPORT] Export failed: {ex}") from ex

    # CRITICAL: Flush mesh attribute writes
    try:
        mesh.update()
        import bpy
        bpy.context.view_layer.update()
    except Exception:
        pass

    # [PHASE 4] Acceptance signals for legend export
    try:
        legends_path = PathlibPath(output_dir) / "legends"
        legend_files = list(legends_path.glob("*.csv")) if legends_path.exists() else []
        print(f"[ACCEPT] legend_files_count={len(legend_files)}")
        for csv_file in sorted(legend_files)[:5]:
            print(f"[ACCEPT] legend_file={csv_file.name}")
    except Exception as ex:
        print(f"[ACCEPT] legend_check_failed={ex}")

    # [PHASE 9] Proof: Legend CSV export validation
    try:
        legends_dir = PathlibPath(output_dir) / "legends"
        csv_files = list(legends_dir.glob("*.csv")) if legends_dir.exists() else []
        if csv_files:
            combined_csv = legends_dir / "legend_codes_combined.csv"
            if combined_csv.exists():
                with open(combined_csv, 'r') as f:
                    row_count = sum(1 for _ in f) - 1  # subtract header
                print(f"[PROOF][LEGENDS] dir={legends_dir} file_count={len(csv_files)} combined_csv_rows={row_count}")
                if row_count == 0:
                    raise RuntimeError(f"[PROOF][LEGENDS] CSV file exists but contains 0 data rows")
            else:
                raise RuntimeError(f"[PROOF][LEGENDS] legends/ exists with {len(csv_files)} files but no combined_csv")
        else:
            raise RuntimeError(f"[PROOF][LEGENDS] legends/ folder is empty, no CSV files created")
    except Exception as ex:
        print(f"[PROOF][LEGENDS] ERROR: {ex}")
        raise RuntimeError(f"[PROOF][LEGENDS] {ex}") from ex

    # ── ACCEPTANCE CHECK: post-count vs return value consistency ──
    # Use nonzero_codes_written as deterministic return value (not total_codes_written
    # which includes zero-value writes and is misleading).
    post_nonzero = _count_nonzero_int_attr(mesh, "osm_building_code")
    if post_nonzero > 0 and nonzero_codes_written == 0:
        log_error(f"[P5][ACCEPT] INCONSISTENCY: post_nonzero_building_code={post_nonzero} "
                  f"but nonzero_codes_written={nonzero_codes_written}. Reporting bug!")
    print(f"[P5][ACCEPT] mesh={mesh_name} nonzero_codes_written={nonzero_codes_written} "
          f"post_nonzero_building_code={post_nonzero} face_count={face_count}")

    return nonzero_codes_written


def _query_feature_columns(gpkg_path, table, id_col, osm_id, columns):
    if not gpkg_path or not table or not id_col or osm_id in (None, "—", ""):
        return {c: "—" for c in columns}
    try:
        # Use centralized readonly DB access
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            con.execute("PRAGMA busy_timeout=5000;")
            con.execute("PRAGMA query_only=ON;")
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cols_sane = [f'"{_sanitize_identifier(c)}"' for c in columns]
        cols_sql = ", ".join(cols_sane) if cols_sane else "*"
        t = _sanitize_identifier(table)
        c = _sanitize_identifier(id_col)
        sql = f'SELECT {cols_sql} FROM "{t}" WHERE "{c}" = ? LIMIT 1;'
        row = cur.execute(sql, (osm_id,)).fetchone()
        con.close()
        result = {}
        for idx, col in enumerate(columns):
            try:
                result[col] = row[idx] if row else None
            except Exception:
                result[col] = None
        for k in result:
            if result[k] in (None, ""):
                result[k] = "—"
        return result
    except Exception:
        return {c: "—" for c in columns}


def _fetch_osm_features_by_id(gpkg_path, table, id_col, columns, osm_ids, chunk_size=900):
    """Batch fetch OSM feature columns for many osm_ids. Returns {id: {col: val}}."""
    if not gpkg_path or not table or not id_col or not columns or not osm_ids:
        return {}
    result = {}
    try:
        # Use centralized readonly DB access
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            con.execute("PRAGMA busy_timeout=5000;")
            con.execute("PRAGMA query_only=ON;")
        cur = con.cursor()
        t_sane = _sanitize_identifier(table)
        id_sane = _sanitize_identifier(id_col)
        col_sane = [f'"{_sanitize_identifier(c)}"' for c in columns]
        select_cols = ", ".join([f'"{id_sane}"'] + col_sane)
        osm_ids_list = list({int(x) for x in osm_ids if x is not None})
        for i in range(0, len(osm_ids_list), chunk_size):
            batch = osm_ids_list[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(batch))
            sql = f'SELECT {select_cols} FROM "{t_sane}" WHERE "{id_sane}" IN ({placeholders});'
            rows = cur.execute(sql, batch).fetchall()
            for row in rows:
                try:
                    osm_id_val = int(row[0]) if row and row[0] is not None else None
                except Exception:
                    osm_id_val = None
                if osm_id_val is None:
                    continue
                feature_row = {}
                for idx, col in enumerate(columns, start=1):
                    val = row[idx] if idx < len(row) else None
                    feature_row[col] = "—" if val in (None, "") else val
                result[osm_id_val] = feature_row
        try:
            con.close()
        except Exception:
            pass
    except Exception as ex:
        log_warn(f"[Materialize] Feature fetch failed: {ex}")
    return result


def _fetch_fixed_features(gpkg_path, table, id_col, columns, osm_ids, chunk_size=900):
    """Fetch fixed feature columns for many ids -> returns mapping and vocab per column.

    Type-tolerant:
    - Works when id_col is TEXT (osm_way_id) or numeric (osm_id)
    - Normalizes all ids to strings and queries via CAST(id_col AS TEXT)
    """
    try:
        from .utils.common import resolve_gpkg_path, log_gpkg_resolution
        resolved, info = resolve_gpkg_path(gpkg_path)
        log_gpkg_resolution(gpkg_path, resolved, info, prefix="[Materialize][GPKG]")
        gpkg_path = resolved or gpkg_path
    except Exception:
        pass
    if not gpkg_path or not table or not id_col or not columns or not osm_ids:
        return {}, {col: {"vocab": {"": 0}, "inv": [""]} for col in columns}

    mapping = {}
    vocab = {col: {"": 0} for col in columns}
    inv_vocab = {col: [""] for col in columns}
    rows_total = 0
    sample_row = None

    try:
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()
        t_sane = _sanitize_identifier(table)
        id_sane = _sanitize_identifier(id_col)
        col_sane = [f'"{_sanitize_identifier(c)}"' for c in columns]
        select_cols = ", ".join([f'"{id_sane}"'] + col_sane)
        osm_ids_list = sorted({_normalize_osm_id(x) for x in osm_ids if _normalize_osm_id(x)})
        log_info(f"[Materialize] Feature fetch: querying {len(osm_ids_list)} ids from {table} on {id_col}")
        for i in range(0, len(osm_ids_list), chunk_size):
            batch = osm_ids_list[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(batch))
            sql = f'SELECT {select_cols} FROM "{t_sane}" WHERE CAST("{id_sane}" AS TEXT) IN ({placeholders});'
            rows = cur.execute(sql, list(batch)).fetchall()
            rows_total += len(rows)
            if sample_row is None and rows:
                sample_row = rows[0]
            for row in rows:
                osm_val = _normalize_osm_id(row[0]) if row else ""
                if not osm_val:
                    continue
                mapping.setdefault(osm_val, {})
                for idx, col in enumerate(columns, start=1):
                    val = row[idx] if idx < len(row) else ""
                    if val in (None, ""):
                        val = ""
                    val = str(val)
                    mapping[osm_val][col] = val
                    voc = vocab[col]
                    if val not in voc:
                        code = len(inv_vocab[col])
                        voc[val] = code
                        inv_vocab[col].append(val)
        try:
            con.close()
        except Exception:
            pass
    except Exception as ex:
        log_warn(f"[Materialize] Fixed feature fetch failed: {ex}")
    if rows_total == 0:
        log_info(
            f"[Materialize] Feature fetch returned 0 rows for {len(osm_ids_list)} ids (check id_col type/text mismatch: {id_col})"
        )
    else:
        log_info(f"[Materialize] Feature fetch rows={rows_total}; sample={sample_row}")
    # package vocab
    vocab_info = {col: {"vocab": vocab[col], "inv": inv_vocab[col]} for col in columns}
    return mapping, vocab_info


def _fetch_name_for_osm_id(gpkg_path, table, osm_id):
    """Fetch single 'name' for an osm_id (CAST to INT) to avoid city-scale string attributes."""
    try:
        from .utils.common import resolve_gpkg_path
        resolved, _ = resolve_gpkg_path(gpkg_path)
        gpkg_path = resolved or gpkg_path
    except Exception:
        pass
    if not gpkg_path or not table or not osm_id:
        return ""
    try:
        if open_db_readonly:
            con = open_db_readonly(gpkg_path, log_open=False)
        else:
            uri = f"file:{Path(gpkg_path).as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()
        t_sane = _sanitize_identifier(table)
        row = cur.execute(
            f'SELECT "name" FROM "{t_sane}" WHERE CAST("osm_id" AS INTEGER)=? LIMIT 1;', (int(osm_id),)
        ).fetchone()
        try:
            con.close()
        except Exception:
            pass
        if row and len(row):
            val = row[0]
            return "" if val in (None, "") else str(val)
    except Exception as ex:
        log_warn(f"[Inspector] Name fetch failed for osm_id={osm_id}: {ex}")
    return ""


def _build_spreadsheet_rows(context, s):
    _ensure_table_and_columns(s)
    obj, mesh = _get_active_mesh(context)
    if obj is None:
        s.spreadsheet_last_error = "Active object is not a mesh"
        return False

    source_tile = _get_source_tile(obj)
    building_indices = sorted(_gather_building_indices(mesh))
    link_map = _load_link_lookup(s)
    columns = [opt.name for opt in s.spreadsheet_columns_available if opt.selected]
    table, id_col = _current_table_and_id(s)

    s.spreadsheet_silent = True
    try:
        s.spreadsheet_rows.clear()
        max_rows = getattr(s, "spreadsheet_max_rows", 5000)
        row_count = 0
        for bidx in building_indices:
            if row_count >= max_rows:
                s.spreadsheet_last_error = f"Showing {max_rows}/{len(building_indices)} buildings — refine selection or filter"
                break
            
            key = (source_tile, int(bidx))
            map_entry = link_map.get(key, {}) if link_map else {}
            osm_id = map_entry.get("osm_id", "—") or "—"
            link_conf = float(map_entry.get("link_conf", 0.0) or 0.0)
            
            # Extract centroids from link map (proof-of-linking)
            citygml_cent = map_entry.get("citygml_centroid", "—") or "—"
            osm_cent = map_entry.get("osm_centroid", "—") or "—"
            
            attrs = _query_feature_columns(s.gpkg_path, table, id_col, osm_id if osm_id != "—" else None, columns)

            item = s.spreadsheet_rows.add()
            item.source_tile = source_tile
            item.building_idx = int(bidx)
            item.citygml_centroid = str(citygml_cent)
            item.link_conf = link_conf
            item.osm_centroid = str(osm_cent)
            item.osm_id = str(osm_id)
            item.attrs_json = json.dumps(attrs)
            item.selected = False
            row_count += 1
        
        s.spreadsheet_row_index = 0 if len(s.spreadsheet_rows) else -1
        s.spreadsheet_cached_obj = obj.name
        if not s.spreadsheet_last_error:
            s.spreadsheet_last_error = ""
        return True
    except Exception as ex:
        s.spreadsheet_last_error = f"Reload failed: {ex}"
        return False
    finally:
        s.spreadsheet_silent = False


def _sync_from_active_face_readonly(context):
    """
    PURE READ-ONLY version: extract building_idx from active face.
    Returns (src_tile, bidx) or (None, None).
    NO Scene mutations. Safe to call from draw().
    """
    src_tile, bidx = _active_face_building_idx(context)
    return src_tile, bidx


def _perform_face_sync(context, s):
    """
    DEFERRED SYNC: Actually update row selections and index.
    ONLY call from operator execute (not from draw).
    Mutates spreadsheet_silent, row.selected, spreadsheet_row_index, spreadsheet_last_error.
    """
    obj, mesh = _get_active_mesh(context)
    if obj is None or mesh is None:
        return False
    
    if len(s.spreadsheet_rows) == 0:
        _set_sync_error("Spreadsheet is empty; use Reload after selecting a CityGML mesh")
        s.spreadsheet_last_error = "Spreadsheet is empty; use Reload after selecting a CityGML mesh"
        return False

    src_tile, bidx = _active_face_building_idx(context)
    if src_tile is None or bidx is None:
        _set_sync_error("Active face has no building_idx attribute; select CityGML mesh faces")
        s.spreadsheet_last_error = "Active face has no building_idx attribute; select CityGML mesh faces"
        return False

    s.spreadsheet_silent = True
    try:
        found_index = -1
        for i, row in enumerate(s.spreadsheet_rows):
            is_target = (row.building_idx == bidx and row.source_tile == src_tile)
            row.selected = is_target
            if is_target:
                found_index = i
        s.spreadsheet_row_index = found_index
        if found_index >= 0:
            s.spreadsheet_last_error = ""
            _set_sync_error(None)
    finally:
        s.spreadsheet_silent = False
    return found_index >= 0


def _active_face_building_idx(context):
    obj, mesh = _get_active_mesh(context)
    if obj is None or obj.mode != 'EDIT':
        return None, None
    _sync_edit_mesh(obj)
    _ensure_face_int_attr_repair(obj, mesh, "building_idx", "[Sync] ")
    poly_idx = _get_active_face_poly_index(obj)
    if poly_idx is None:
        return None, None
    bidx = _read_face_int_attr(mesh, "building_idx", poly_idx, None)
    if bidx is None:
        return None, None
    return _get_source_tile(obj), bidx


def _select_faces_by_building_idx(context, obj, building_idx):
    if obj is None or obj.type != "MESH" or obj.data is None:
        return False
    mesh = obj.data
    attr = getattr(mesh, "attributes", None)
    if not attr:
        return False
    b_attr = attr.get("building_idx")
    if b_attr is None or b_attr.domain != "FACE":
        return False

    # Ensure edit mode
    if obj.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')

    bm = bmesh.from_edit_mesh(mesh)
    try:
        for f in bm.faces:
            idx = f.index
            val = None
            try:
                val = int(b_attr.data[idx].value)
            except Exception:
                val = None
            f.select = bool(val == building_idx)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    finally:
        pass
    return True


def _get_source_tile(obj):
    if obj is None:
        return ""
    if "source_tile" in obj:
        return norm_source_tile(obj.get("source_tile"))
    return norm_source_tile(obj.name)


def _apply_clip_end(context, s):
    if not getattr(s, "auto_clip", False):
        return
    try:
        distance = getattr(s, "clip_end", None)
        if distance is None:
            return
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.clip_end = distance
    except Exception:
        # Never fail the operator because a view might be missing or locked.
        pass


# ---------------- WKB / Geometry helpers (MOVED TO utils/geometry.py) ----------------
# NOTE: All WKB/spatial functions now imported from utils.geometry at top of file


def _update_world_origin_status(s):
    min_e, min_n, max_e, max_n = get_world_origin_minmax()
    s.world_origin_set = min_e is not None and min_n is not None
    if s.world_origin_set:
        s.world_origin_min_easting = float(min_e)
        s.world_origin_min_northing = float(min_n)
        if max_e is not None:
            s.world_origin_max_easting = float(max_e)
        if max_n is not None:
            s.world_origin_max_northing = float(max_n)
        world = ensure_world_origin()
        s.world_origin_set_by = str(world.get("world_origin_set_by", ""))
    else:
        s.world_origin_set_by = ""


def _do_validation(context, s):
    # CityGML (REQUIRED)
    tiles = _count_files(s.citygml_dir, CITYGML_EXTS)
    s.status_citygml_tiles = tiles
    s.status_citygml_loaded = tiles > 0

    # Terrain Source (OPTIONAL: OBJ artifact OR separate DGM and RGB folders)
    # Strategy: OBJ dominates – if terrain_obj_artifact_dir is set, skip DGM/RGB validation
    terrain_status = "missing"
    obj_dir = getattr(s, "terrain_obj_artifact_dir", "").strip()
    dgm_dir = getattr(s, "terrain_dgm_dir", "")
    rgb_dir = getattr(s, "terrain_rgb_dir", "")

    if obj_dir and os.path.isdir(obj_dir):
        # Primary strategy: prebuilt OBJ terrain artifact
        terrain_status = "OBJ artifact ready"
    elif (dgm_dir and os.path.isdir(dgm_dir)) or (rgb_dir and os.path.isdir(rgb_dir)):
        # Fallback strategy: raster-based terrain from DGM/RGB
        terrain_status = "folders ready"

    s.status_basemap_loaded = "ready" in terrain_status

    # GPKG (OPTIONAL: accept file or directory)
    raw_gpkg = getattr(s, "gpkg_path", "")
    resolved_gpkg = ""
    try:
        from .utils.common import resolve_gpkg_path, log_gpkg_resolution
        resolved_gpkg, info = resolve_gpkg_path(raw_gpkg)
        log_gpkg_resolution(raw_gpkg, resolved_gpkg, info, prefix="[Validate][GPKG]")
    except Exception:
        resolved_gpkg = raw_gpkg if (raw_gpkg and os.path.isfile(raw_gpkg)) else ""

    if resolved_gpkg and resolved_gpkg != raw_gpkg:
        try:
            s.gpkg_path = resolved_gpkg
        except Exception:
            pass

    s.status_gpkg_loaded = bool(resolved_gpkg and os.path.isfile(resolved_gpkg))
    if s.status_gpkg_loaded:
        _refresh_tables_and_columns(s)
    else:
        s.status_gpkg_table = ""
        s.status_gpkg_id_col = ""
        s.attr_table = ""
        s.id_col = ""
        s.spreadsheet_tables_cache = "[]"
        s.spreadsheet_columns_available.clear()

    # Status line for UI
    s.status_text = (
        f"CityGML: {tiles} tiles | "
        f"GPKG: {'OK' if s.status_gpkg_loaded else 'missing'} | "
        f"Terrain: {terrain_status}"
    )

    _update_world_origin_status(s)
    _apply_clip_end(context, s)
    return True


def infer_world_origin_from_citygml_tiles(s, citygml_dir: str) -> bool:
    """
    Attempt to infer and lock WORLD_ORIGIN from CityGML tile filenames if not already set.
    
    Strategy:
    1. If WORLD_ORIGIN already locked, do nothing (return True)
    2. If terrain_source_dir has metadata CSVs, parse them (future: not implemented)
    3. Parse CityGML tile filenames to extract E/N grid coordinates
    4. Infer tile spacing and compute world bounds
    5. Lock WORLD_ORIGIN from inferred bounds
    
    Returns: True if WORLD_ORIGIN is now locked, False if unable to infer.
    """
    # Check if already locked
    min_e, min_n, max_e, max_n = get_world_origin_minmax()
    if min_e is not None and min_n is not None:
        log_info("[CityGML] WORLD_ORIGIN already locked, reusing existing bounds.")
        return True
    
    # Try to parse CityGML filenames for tile coordinates
    if not citygml_dir or not os.path.isdir(citygml_dir):
        log_warn("[CityGML] Cannot infer origin: CityGML folder missing or invalid.")
        return False
    
    # Import patterns from citygml_import module
    try:
        from .pipeline.citygml.citygml_import import parse_citygml_tile_coords, iter_citygml_paths
    except ImportError:
        log_warn("[CityGML] Cannot import tile parsing functions; origin inference skipped.")
        return False
    
    # Collect all tile coordinates from filenames
    tile_files = iter_citygml_paths(citygml_dir)
    if not tile_files:
        log_warn(f"[CityGML] No CityGML files found in {citygml_dir}; cannot infer origin.")
        return False
    
    tile_coords = []
    for file_path in tile_files:
        coords = parse_citygml_tile_coords(file_path.name)
        if coords:
            e_raw, n_raw, km_val = coords
            tile_coords.append((e_raw, n_raw, km_val))
    
    if not tile_coords:
        log_warn(f"[CityGML] No recognizable tile coordinate patterns in {citygml_dir}; cannot infer origin.")
        return False
    
    # Extract E and N values separately
    e_values = [e for e, n, km in tile_coords]
    n_values = [n for e, n, km in tile_coords]
    km_values = [km for e, n, km in tile_coords if km is not None]
    
    # Infer tile spacing (most common step between sorted unique values)
    def most_common_positive_step(values):
        vals = sorted(set(values))
        diffs = [b - a for a, b in zip(vals, vals[1:]) if (b - a) > 0]
        if not diffs:
            return None
        counts = {}
        for d in diffs:
            counts[d] = counts.get(d, 0) + 1
        return max(counts.items(), key=lambda kv: kv[1])[0]
    
    delta_raw_e = most_common_positive_step(e_values)
    delta_raw_n = most_common_positive_step(n_values)
    
    if not delta_raw_e or not delta_raw_n:
        log_warn(f"[CityGML] Could not infer tile spacing from {len(tile_coords)} tiles; origin inference failed.")
        return False
    
    # Determine tile size from filenames (3rd numeric token).
    # Examples:
    #   LoD2_32_290_5626_1_NW  -> tile_size_raw=1 (km)  -> tile_size_m=1000
    #   LoD2_32_290000_5626000_2000_NW -> tile_size_raw=2000 (m) -> tile_size_m=2000
    if km_values:
        # Use the most common tile size token across filenames.
        from collections import Counter
        tile_size_raw = Counter(km_values).most_common(1)[0][0]
    else:
        tile_size_raw = 1000

    # Normalize to meters.
    tile_size_m = float(tile_size_raw * 1000) if tile_size_raw < 1000 else float(tile_size_raw)

    # Infer multiplier mapping from filename units to meters.
    mult_e = tile_size_m / float(delta_raw_e)
    mult_n = tile_size_m / float(delta_raw_n)

    log_info(f"[Phase0] tilegrid mult_e={mult_e:.3f} mult_n={mult_n:.3f} (delta_raw_e={delta_raw_e}, delta_raw_n={delta_raw_n})")

    # --- Convert raw tile coordinates to meters (robust against "zone-prefixed" patterns) ---
    # NRW LoD2 tile names often look like: LoD2_32_290_5626_1_NW.gml
    # The raw easting token can appear as either:
    #   - 290     (km)  -> 290000 m
    #   - 32290   (zone 32 + km 290) -> 290000 m
    # We normalize both cases here.
    def _e_raw_to_m(e_raw: int) -> float:
        # If the e_raw looks like "32xxx", strip zone prefix by modulo 1000.
        if 10_000 <= e_raw < 100_000:
            e_km = e_raw % 1000
            return float(e_km * 1000)
        # If it's a small value, interpret as km.
        if e_raw < 10_000:
            return float(e_raw * 1000)
        # Otherwise assume it's already meters.
        return float(e_raw)

    def _n_raw_to_m(n_raw: int) -> float:
        if n_raw < 10_000:
            return float(n_raw * 1000)
        if n_raw < 1_000_000:
            # some datasets store northing in meters already but still < 1e6 is unlikely for EPSG:25832
            return float(n_raw * 1000)
        return float(n_raw)

    e_values_m = [_e_raw_to_m(e) for e in e_values]
    n_values_m = [_n_raw_to_m(n) for n in n_values]
    tile_size_m = float(tile_size_raw * 1000) if tile_size_raw < 1000 else float(tile_size_raw)

    # Compute world bounds from tile grid (expand by one tile edge)
    min_e_inferred = min(e_values_m)
    max_e_inferred = max(e_values_m) + tile_size_m
    min_n_inferred = min(n_values_m)
    max_n_inferred = max(n_values_m) + tile_size_m

    # Plausibility gate for EPSG:25832 meters (Cologne-ish)
    if not (100_000 <= min_e_inferred <= 1_000_000 and 1_000_000 <= min_n_inferred <= 10_000_000):
        raise RuntimeError(
            f"WORLD_ORIGIN inference from CityGML filenames produced implausible EPSG:25832 meters. Got min_e={min_e_inferred:.0f}, min_n={min_n_inferred:.0f}, max_e={max_e_inferred:.0f}, max_n={max_n_inferred:.0f}. Refusing to lock."
        )
    
    # Lock WORLD_ORIGIN using inferred bounds
    try:
        inferred_origin = ensure_world_origin(
            min_e=min_e_inferred,
            min_n=min_n_inferred,
            max_e=max_e_inferred,
            max_n=max_n_inferred,
            source="CityGML_TileGrid",
            crs="EPSG:25832"  # German reference system (Cologne region)
        )
        if inferred_origin:
            log_info(
                f"[CityGML] WORLD_ORIGIN inferred from tile grid: "
                f"min=({min_e_inferred:.0f},{min_n_inferred:.0f}) max=({max_e_inferred:.0f},{max_n_inferred:.0f}) "
                f"from {len(tile_coords)} tiles (e_mult={mult_e}, n_mult={mult_n})"
            )
            try:
                if inferred_origin.get("tile_size_m") in (None, 0, 0.0, ""):
                    inferred_origin["tile_size_m"] = float(tile_size_m)
            except Exception:
                pass
            # Update scene settings to reflect new origin
            s.world_origin_set = True
            s.world_origin_min_easting = min_e_inferred
            s.world_origin_min_northing = min_n_inferred
            s.world_origin_max_easting = max_e_inferred
            s.world_origin_max_northing = max_n_inferred
            s.world_origin_set_by = "CityGML_TileGrid"
            return True
        else:
            log_warn("[CityGML] Failed to lock WORLD_ORIGIN using inferred bounds.")
            return False
    except Exception as ex:
        log_warn(f"[CityGML] Exception during origin locking from tile grid: {ex}")
        return False


def _run_citygml_import(s):
    # === CITYGML FORENSICS LOG ===
    try:
        import mathutils
        world_origin = bpy.data.objects.get('M1DC_WORLD_ORIGIN')
        wo_log = '[CITYGML_FORENSICS]\n'
        if world_origin:
            loc = tuple(round(c, 3) for c in world_origin.location)
            wo_log += f"A) M1DC_WORLD_ORIGIN loc={loc}\n"
        else:
            wo_log += 'A) M1DC_WORLD_ORIGIN not found\n'

        citygml_col = bpy.data.collections.get('CITYGML_TILES')
        citygml_objs = []
        if citygml_col:
            citygml_objs = [o for o in citygml_col.objects if o.type == 'MESH']
        wo_log += f"B) CITYGML_TILES meshes: {len(citygml_objs)}\n"
        for i, o in enumerate(citygml_objs[:5]):
            bbox = [o.matrix_world @ mathutils.Vector(corner) for corner in o.bound_box]
            minx = round(min(v.x for v in bbox), 3)
            miny = round(min(v.y for v in bbox), 3)
            maxx = round(max(v.x for v in bbox), 3)
            maxy = round(max(v.y for v in bbox), 3)
            loc = tuple(round(c, 3) for c in o.location)
            wo_log += f"  tile[{i}] {o.name}: loc={loc} bbox_xy=(({minx}, {miny}), ({maxx}, {maxy}))\n"

        # C) Overlap check for tile[0] and tile[1]
        if len(citygml_objs) >= 2:
            bbox0 = [citygml_objs[0].matrix_world @ mathutils.Vector(corner) for corner in citygml_objs[0].bound_box]
            bbox1 = [citygml_objs[1].matrix_world @ mathutils.Vector(corner) for corner in citygml_objs[1].bound_box]
            min0 = (round(min(v.x for v in bbox0), 3), round(min(v.y for v in bbox0), 3))
            max0 = (round(max(v.x for v in bbox0), 3), round(max(v.y for v in bbox0), 3))
            min1 = (round(min(v.x for v in bbox1), 3), round(min(v.y for v in bbox1), 3))
            max1 = (round(max(v.x for v in bbox1), 3), round(max(v.y for v in bbox1), 3))
            identical = (min0 == min1 and max0 == max1)
            nearly = (abs(min0[0] - min1[0]) < 0.01 and abs(min0[1] - min1[1]) < 0.01 and abs(max0[0] - max1[0]) < 0.01 and abs(max0[1] - max1[1]) < 0.01)
            wo_log += f"C) Overlap check:\n  tile[0] bbox_xy=({min0}, {max0})\n  tile[1] bbox_xy=({min1}, {max1})\n  IDENTICAL={identical}, NEARLY={nearly}\n"

        print(wo_log)
    except Exception as e:
        print(f"[CITYGML_FORENSICS] ERROR: {e}")

    if not s.citygml_dir or not os.path.isdir(s.citygml_dir):
        return False, "CityGML folder missing or invalid."

    target_col = ensure_collection("CITYGML_TILES")
    world_origin = ensure_world_origin()
    tile_size_m = float(world_origin.get("tile_size_m", 1000.0)) if world_origin else 1000.0
    ok, msg, tile_count, obj_count = import_citygml_folder(
        s.citygml_dir,
        collection=target_col,
        sort_by_tiles=True,
        tile_size_m=tile_size_m,
        clamp_to_ground=s.citygml_clamp_to_ground,
    )
    s.status_citygml_tiles = tile_count
    s.step1_citygml_tiles = tile_count
    s.step1_citygml_done = ok
    
    # PATCH 4: Assign default material to CityGML tiles if not already materialized
    if ok:
        try:
            from .pipeline.citygml.citygml_materials import ensure_materials_for_collection
            stats = ensure_materials_for_collection("CITYGML_TILES")
            log_info(f"[CityGML Materials] Assigned to {stats.get('assigned', 0)} objects (skipped {stats.get('skipped', 0)})")
            
            # PATCH 5: Assign deterministic pastel colors per tile (hash-based from name)
            # This makes tile edges and boundaries clearly visible in viewport
            try:
                colored_count = 0
                for obj in target_col.objects:
                    if obj.type == "MESH":
                        # Deterministic color from tile name
                        tile_name = obj.get("source_tile") or obj.name
                        obj.color = hash_color(tile_name)
                        colored_count += 1
                log_info(f"[CityGML Color] Assigned deterministic pastel colors to {colored_count} mesh objects")
            except Exception as ex:
                log_warn(f"[CityGML Color] Failed to set display colors: {ex}")
            
            # PATCH 6: Apply viewport solid shading with cavity (if enabled in settings)
            # Helps visualize tile edges and boundaries
            try:
                use_viewport_cavity = getattr(s, "set_viewport_solid_with_cavity", True)
                if use_viewport_cavity:
                    apply_viewport_solid_cavity(enable=True)
            except Exception as ex:
                log_warn(f"[CityGML Viewport] Failed to apply cavity shading: {ex}")
        except Exception as ex:
            log_warn(f"[CityGML Materials] Failed to assign materials: {ex}")
        
        # === XY ALIGNMENT FIX: CityGML importer is authoritative ===
        # NOTE: The CityGML importer (pipeline/citygml/citygml_import.py:714-724) already applies
        # the canonical world->local mapping: obj.location = (tile_easting_m - WORLD_MIN_E, tile_northing_m - WORLD_MIN_N)
        #
        # Applying it again here causes DOUBLE-SUBTRACTION, producing huge negative coordinates
        # (e.g., -286500 instead of +4000 for a tile at world 290000m with WORLD_MIN_E=290000).
        #
        # DECISION: The importer is authoritative. Do NOT subtract WORLD_MIN here.
        # This block now only validates that imported tiles are in correct local range.
        try:
            min_e, min_n, _, _ = get_world_origin_minmax()
            if min_e is not None and min_n is not None:
                mesh_objs = [o for o in target_col.objects if o.type == "MESH"]
                if mesh_objs:
                    # Validate first 2 tiles are in correct local coordinate range
                    for i, obj in enumerate(mesh_objs[:2]):
                        loc_x = obj.location.x
                        loc_y = obj.location.y
                        log_info(f"[CityGML][XY-Validation] Tile {i+1}/{len(mesh_objs)}: {obj.name}")
                        log_info(f"[CityGML][XY-Validation]   location: ({loc_x:.1f}, {loc_y:.1f})")

                        # Sanity check: local coords should be small (within ~20km of origin)
                        if abs(loc_x) > 20000 or abs(loc_y) > 20000:
                            log_warn(f"[CityGML][XY-Validation]   ⚠️  LOCATION OUT OF RANGE: |x|={abs(loc_x):.0f}, |y|={abs(loc_y):.0f} (expected <20km)")
                            log_warn(f"[CityGML][XY-Validation]   This suggests double-subtraction or importer misconfiguration.")
                        else:
                            log_info(f"[CityGML][XY-Validation]   ✓ Location in valid local range")

                    log_info(f"[CityGML Localization] XY alignment validated for {len(mesh_objs)} tiles (importer authoritative)")
            else:
                log_warn("[CityGML Localization] WORLD_ORIGIN not set; cannot validate XY alignment")
        except Exception as ex:
            log_warn(f"[CityGML Localization] XY validation failed: {ex}")
        
        # === PLACEMENT TRIPWIRES ===
        # Validate tile placement contract (anchor, scale, spacing, local coords)
        try:
            log_info("[Tripwire] validating tile placement")
            mesh_objs = [o for o in target_col.objects if o.type == "MESH"]
            if mesh_objs:
                world_origin = ensure_world_origin()
                tile_size_m = float(world_origin.get("tile_size_m", 1000.0))
                log_info(f"[Tripwire] CityGML tile_size_m={tile_size_m} (from WORLD_ORIGIN or fallback)")
                run_placement_tripwires(
                    lods=mesh_objs,
                    tile_size_m=tile_size_m,
                    world_origin_obj=world_origin
                )
        except AssertionError:
            raise  # Surface placement validation failures immediately
        except Exception as ex:
            log_warn(f"[Tripwire] Unexpected error during placement validation: {ex}")
    
    # === SPATIAL DIAGNOSTICS ===
    try:
        import math
        # 1) WORLD_ORIGIN values
        min_e, min_n, max_e, max_n = get_world_origin_minmax()
        crs = get_scene_crs() if 'get_scene_crs' in globals() else 'unknown'

        # 2) DEM — use SAME lookup as terrain_validation (single truth source)
        dem_obj = None
        try:
            from .pipeline.terrain.terrain_validation import get_terrain_object
            dem_obj = get_terrain_object()
        except Exception:
            dem_obj = bpy.data.objects.get('dem_merged')  # legacy fallback
        if dem_obj:
            dem_loc = tuple(round(v, 3) for v in dem_obj.location)
            dem_bbox = [tuple(round(v, 3) for v in dem_obj.matrix_world @ mathutils.Vector(corner)) for corner in dem_obj.bound_box]
            dem_bbox_min = (min(v[0] for v in dem_bbox), min(v[1] for v in dem_bbox))
            dem_bbox_max = (max(v[0] for v in dem_bbox), max(v[1] for v in dem_bbox))
            dem_bbox_size = (dem_bbox_max[0] - dem_bbox_min[0], dem_bbox_max[1] - dem_bbox_min[1])
            dem_bbox_center = ((dem_bbox_min[0] + dem_bbox_max[0]) / 2, (dem_bbox_min[1] + dem_bbox_max[1]) / 2)
        else:
            dem_loc = dem_bbox = dem_bbox_min = dem_bbox_max = dem_bbox_size = dem_bbox_center = None

        # 3) One sample CityGML tile
        citygml_objs = [o for o in bpy.data.collections.get('CITYGML_TILES', []).objects if o.type == 'MESH'] if bpy.data.collections.get('CITYGML_TILES') else []
        citygml_obj = citygml_objs[0] if citygml_objs else None
        if citygml_obj:
            citygml_loc = tuple(round(v, 3) for v in citygml_obj.location)
            citygml_bbox = [tuple(round(v, 3) for v in citygml_obj.matrix_world @ mathutils.Vector(corner)) for corner in citygml_obj.bound_box]
            citygml_bbox_min = (min(v[0] for v in citygml_bbox), min(v[1] for v in citygml_bbox))
            citygml_bbox_max = (max(v[0] for v in citygml_bbox), max(v[1] for v in citygml_bbox))
            citygml_bbox_size = (citygml_bbox_max[0] - citygml_bbox_min[0], citygml_bbox_max[1] - citygml_bbox_min[1])
            citygml_bbox_center = ((citygml_bbox_min[0] + citygml_bbox_max[0]) / 2, (citygml_bbox_min[1] + citygml_bbox_max[1]) / 2)
        else:
            citygml_loc = citygml_bbox = citygml_bbox_min = citygml_bbox_max = citygml_bbox_size = citygml_bbox_center = None

        # 4) Derived check: intersection and center distance
        intersection_xy = False
        center_distance_xy = None
        if dem_bbox_min and dem_bbox_max and citygml_bbox_min and citygml_bbox_max:
            # Check intersection in X/Y
            ix = not (dem_bbox_max[0] < citygml_bbox_min[0] or dem_bbox_min[0] > citygml_bbox_max[0])
            iy = not (dem_bbox_max[1] < citygml_bbox_min[1] or dem_bbox_min[1] > citygml_bbox_max[1])
            intersection_xy = ix and iy
            # Center distance
            dx = dem_bbox_center[0] - citygml_bbox_center[0]
            dy = dem_bbox_center[1] - citygml_bbox_center[1]
            center_distance_xy = round(math.sqrt(dx*dx + dy*dy), 3)

        # Print diagnostics
        log_info("[SPATIAL_DIAG]\n" +
            f"WORLD_ORIGIN: min_e={min_e}, min_n={min_n}, crs={crs}\n" +
            (f"DEM: loc={dem_loc}, bbox=({dem_bbox_min}, {dem_bbox_max}), size={dem_bbox_size}\n" if dem_obj else "DEM: not found\n") +
            (f"CITYGML: name={citygml_obj.name if citygml_obj else None}, loc={citygml_loc}, bbox=({citygml_bbox_min}, {citygml_bbox_max}), size={citygml_bbox_size}\n" if citygml_obj else "CITYGML: not found\n") +
            f"INTERSECTION_XY: {'YES' if intersection_xy else 'NO'}\n" +
            (f"CENTER_DISTANCE_XY: {center_distance_xy} meters" if center_distance_xy is not None else "")
        )
    except Exception as ex:
        log_warn(f"[SPATIAL_DIAG] Failed to compute diagnostics: {ex}")
    # === END SPATIAL DIAGNOSTICS ===
    return ok, msg


def _load_gpkg_and_link(s, table_hint="osm_multipolygons"):
    try:
        from .utils.common import resolve_gpkg_path, log_gpkg_resolution
        resolved, info = resolve_gpkg_path(getattr(s, "gpkg_path", ""))
        log_gpkg_resolution(getattr(s, "gpkg_path", ""), resolved, info, prefix="[Link][GPKG]")
        if resolved and resolved != getattr(s, "gpkg_path", ""):
            s.gpkg_path = resolved
    except Exception:
        resolved = getattr(s, "gpkg_path", "")

    if not resolved or not os.path.isfile(resolved):
        return False, 0, "", "", 0, [], {}

    table, id_col = choose_table_and_id(resolved)
    if not table or not id_col:
        return False, 0, table or "", id_col or "", 0, [], {}

    osm_features = load_osm_features(resolved, table, id_col, include_geom=True)
    # Determine pk/geom from features (stored per feature); consistent across rows
    pk_col = None
    geom_col = None
    if osm_features:
        pk_col = osm_features[0].get("pk_col") or id_col
        geom_col = osm_features[0].get("geom_col") or "geom"

    # Log chosen columns
    if pk_col or geom_col:
        log_info(f"[Link] using pk_col={pk_col or id_col}, geom_col={geom_col or 'geom'}")

    return True, len(osm_features), table, id_col, osm_features, {"pk_col": pk_col, "geom_col": geom_col}


def _link_gpkg_to_citygml(s):
    """
    Link GPKG features to CityGML buildings using centroid matching.

    Returns:
        tuple: (ok, linked_count, confidences, no_match_reasons, tiles_count, samples)
    """
    try:
        from .pipeline.linking.linking_cache import ensure_link_dbs
        from .pipeline.linking.mesh_discovery import collect_citygml_meshes
        from .utils.logging_system import log_info, log_warn, log_error

        # ── UX: Progress on every gating step ──
        log_info("[Link] ── STEP 3: GPKG LINKING ──")

        gpkg_path = getattr(s, "gpkg_path", "").strip()
        citygml_dir = getattr(s, "citygml_dir", "").strip()
        output_dir = getattr(s, "output_dir", "").strip()

        # Gate checks with visible progress
        log_info(f"[Link] Checking GPKG path → {'Found' if gpkg_path and os.path.isfile(gpkg_path) else 'MISSING'}")
        log_info(f"[Link] Checking CityGML dir → {'Found' if citygml_dir and os.path.isdir(citygml_dir) else ('N/A' if not citygml_dir else 'MISSING')}")
        log_info(f"[Link] Checking output dir → {'OK' if output_dir else 'MISSING (will use default)'}")

        # Check WORLD_ORIGIN status
        from .utils.common import get_world_origin_minmax
        min_e, min_n, max_e, max_n = get_world_origin_minmax()
        wo_ok = min_e is not None and min_n is not None
        log_info(f"[Link] Checking WORLD_ORIGIN → {'OK (min_e=' + f'{min_e:.0f}, min_n={min_n:.0f})' if wo_ok else 'NOT SET ⚠'}")
        if not wo_ok:
            log_warn("[Link] ⚠ WORLD_ORIGIN not set — linking may fail during coordinate projection")

        log_info(f"[Link] Precondition check:")
        log_info(f"[Link]   gpkg_path       = {gpkg_path!r}")
        log_info(f"[Link]   citygml_dir     = {citygml_dir!r}")
        log_info(f"[Link]   os.path.isdir   = {os.path.isdir(citygml_dir) if citygml_dir else 'N/A'}")
        log_info(f"[Link]   output_dir      = {output_dir!r}")

        if not gpkg_path or not os.path.isfile(gpkg_path):
            log_error(f"[Link] No valid GPKG path specified (gpkg_path={gpkg_path!r})")
            return False, 0, [], [], 0, []

        # Validate CityGML dir — accept trailing slash / backslash, normalize
        if citygml_dir:
            citygml_dir = os.path.normpath(citygml_dir)

        # Discover CityGML meshes already imported into scene
        scene_meshes = collect_citygml_meshes(log_prefix="[Link][Discovery]")
        log_info(f"[Link] Scene CityGML meshes: {len(scene_meshes)}")

        if not citygml_dir or not os.path.isdir(citygml_dir):
            if scene_meshes:
                log_warn(
                    f"[Link] citygml_dir is not a valid directory ({citygml_dir!r}), "
                    f"but {len(scene_meshes)} CityGML meshes are in scene — proceeding with scene-based linking."
                )
                # Set citygml_dir to None so ensure_link_dbs uses scene fallback
                citygml_dir = None
            else:
                log_error(
                    f"[Link] No valid CityGML folder specified ({citygml_dir!r}) "
                    f"and no CityGML meshes found in scene."
                )
                return False, 0, [], [], 0, []

        # Run linking pipeline
        log_info(f"[Link] Running centroid matching pipeline...")
        log_info(f"[Link] Linking GPKG → CityGML\n  GPKG: {gpkg_path}\n  GML dir: {citygml_dir}")
        osm_db, gml_db, link_db = ensure_link_dbs(gpkg_path, citygml_dir or "", output_dir)

        # Store link DB path in settings (ensure_link_dbs also sets it, but be explicit)
        link_db_str = str(link_db.resolve())
        s.links_db_path = link_db_str
        log_info(f"[Link] Checking Link DB → {'Found' if link_db.exists() else 'MISSING'} ({link_db_str})")
        log_info(f"[Link][Artifacts] file exists: {link_db.exists()} size={link_db.stat().st_size if link_db.exists() else 0}")

        # Count linked buildings from link database
        import sqlite3
        linked_count = 0
        confidences = []
        tiles_count = 0

        if link_db.exists():
            try:
                conn = sqlite3.connect(str(link_db))
                cur = conn.cursor()

                # Count links with confidence scores
                cur.execute("SELECT COUNT(*), AVG(COALESCE(confidence, 0.0)) FROM gml_osm_links")
                row = cur.fetchone()
                linked_count = row[0] if row else 0
                avg_conf = row[1] if row and row[1] else 0.0

                # Get confidence distribution
                cur.execute("SELECT confidence FROM gml_osm_links WHERE confidence IS NOT NULL")
                confidences = [r[0] for r in cur.fetchall()]

                # Count distinct tiles (gml_osm_links table has source_tile column)
                try:
                    cur.execute("SELECT COUNT(DISTINCT source_tile) FROM gml_osm_links")
                    tiles_row = cur.fetchone()
                    tiles_count = tiles_row[0] if tiles_row else 0
                except Exception:
                    tiles_count = 0

                # ── ACCEPTANCE LOGGING: Distance & IoU stats ──
                dist_stats_msg = ""
                iou_stats_msg = ""
                try:
                    cols = {r[1] for r in cur.execute("PRAGMA table_info('gml_osm_links');").fetchall()}
                    if "dist_m" in cols:
                        cur.execute("SELECT AVG(dist_m), MIN(dist_m), MAX(dist_m) FROM gml_osm_links WHERE dist_m IS NOT NULL AND dist_m > 0")
                        drow = cur.fetchone()
                        if drow and drow[0] is not None:
                            dist_stats_msg = f"[LINKING] avg distance {drow[0]:.1f}m, min {drow[1]:.1f}m, max {drow[2]:.1f}m"
                    if "iou" in cols:
                        cur.execute("SELECT AVG(iou), MIN(iou), MAX(iou) FROM gml_osm_links WHERE iou IS NOT NULL AND iou > 0")
                        irow = cur.fetchone()
                        if irow and irow[0] is not None:
                            iou_stats_msg = f"[LINKING] avg IoU {irow[0]:.3f}, min {irow[1]:.3f}, max {irow[2]:.3f}"
                except Exception:
                    pass

                conn.close()

                # ── UX: Rich linking summary ──
                log_info("=" * 50)
                log_info(f"[LINKING] {linked_count} buildings linked across {tiles_count} tiles")
                log_info(f"[LINKING] avg confidence: {avg_conf:.3f}")
                if dist_stats_msg:
                    log_info(dist_stats_msg)
                if iou_stats_msg:
                    log_info(iou_stats_msg)
                log_info(f"[Link] Writing Face Attributes → {linked_count} linked")
                log_info("=" * 50)
            except Exception as ex:
                log_warn(f"[Link] Could not query link statistics: {ex}")
        else:
            log_error(f"[Link][Artifacts] Link DB does NOT exist at {link_db_str}")
            return False, 0, [], [], 0, []

        # Update settings
        try:
            s.step2_linked_objects = linked_count
        except Exception:
            pass

        return True, linked_count, confidences, [], tiles_count, []

    except Exception as ex:
        from .utils.logging_system import log_error
        log_error(f"[Link] Linking failed: {ex}")
        import traceback
        traceback.print_exc()
        return False, 0, [], [], 0, []


# ============================================================================
# TERRAIN MODE DETECTION — OBJ dominates (hard rule, not heuristic)
# ============================================================================

def detect_terrain_mode(s):
    """Deterministic terrain mode detection.

    HARD RULE: If terrain_obj_artifact_dir is set AND contains a .obj file,
    *always* use OBJ_ARTIFACT mode.  DEM/TIFF is only used when OBJ is
    not available.  This prevents the pipeline from importing a DEM tile
    when an authoritative OBJ artifact exists.

    Args:
        s: M1DCSettings (scene settings)

    Returns:
        (mode, path):
            ("OBJ_ARTIFACT", <path-to-obj>)   — OBJ takes precedence (hard)
            ("DEM", <dem-dir-or-root>)          — raster DEM fallback
            ("NONE", None)                     — nothing configured
    """
    # ── Priority 1: OBJ artifact (hard dominate) ──
    obj_dir = getattr(s, "terrain_obj_artifact_dir", "").strip()
    if obj_dir and os.path.isdir(obj_dir):
        from .pipeline.terrain.m1_basemap import _find_first_obj
        obj_path = _find_first_obj(obj_dir)
        if obj_path:
            log_info(f"[TerrainMode] OBJ_ARTIFACT (hard) | path={obj_path}")
            return "OBJ_ARTIFACT", obj_path
        else:
            log_warn(f"[TerrainMode] terrain_obj_artifact_dir set but no .obj found in {obj_dir}")

    # ── Priority 2: Prepared terrain root (DGM + RGB) ──
    terrain_root = getattr(s, "terrain_root_dir", "").strip()
    if terrain_root and os.path.isdir(terrain_root):
        log_info(f"[TerrainMode] DEM (terrain_root_dir) | path={terrain_root}")
        return "DEM", terrain_root

    # ── Priority 3: Deprecated split DGM/RGB dirs ──
    dgm_dir = getattr(s, "terrain_dgm_dir", "").strip()
    rgb_dir = getattr(s, "terrain_rgb_dir", "").strip()
    if (dgm_dir and os.path.isdir(dgm_dir)) or (rgb_dir and os.path.isdir(rgb_dir)):
        path = dgm_dir if (dgm_dir and os.path.isdir(dgm_dir)) else rgb_dir
        log_info(f"[TerrainMode] DEM (legacy dgm/rgb dirs) | path={path}")
        return "DEM", path

    log_info("[TerrainMode] NONE — no terrain source configured")
    return "NONE", None


# ============================================================================
# TERRAIN CACHE MECHANISM — Avoid rebuilding expensive DEM terrain
# ============================================================================

def _get_terrain_cache_folder(s) -> Path:
    """
    Resolve terrain cache folder path.
    Derived from output_dir: <output_dir>/_Merged/terrain_cache
    """
    output_dir = getattr(s, "output_dir", "").strip()
    terrain_cache_folder = getattr(s, "terrain_cache_folder", "").strip()
    if terrain_cache_folder:
        log_warn("[TerrainCache] terrain_cache_folder is deprecated and ignored; using Output Directory instead")

    if output_dir:
        return Path(get_terrain_cache_dir(output_dir))
    import tempfile
    return Path(get_terrain_cache_dir(Path(tempfile.gettempdir()) / "M1DC_Output"))


def _terrain_cache_paths(cache_folder: Path) -> dict:
    """
    Return paths to terrain cache artifacts.
    
    Returns:
        {
            "meta_json": cache_folder / "terrain_meta.json",
            "glb": cache_folder / "terrain.glb",
            "folder": cache_folder
        }
    """
    return {
        "meta_json": cache_folder / "terrain_meta.json",
        "glb": cache_folder / "terrain.glb",
        "folder": cache_folder
    }


def _write_terrain_meta(cache_folder: Path, world_min_e: float, world_min_n: float, dem_step: int = 0, source_dem: str = "", delta_applied: tuple = None) -> bool:
    """
    Write terrain_meta.json with cache validation info and alignment data.
    
    Args:
        cache_folder: Path to cache folder
        world_min_e, world_min_n: World origin coordinates (METERS)
        dem_step: DEM decimation step used
        source_dem: Path to source DEM file (for reference)
        delta_applied: Tuple (dx, dy, dz) of alignment translation applied
    
    Returns:
        True if successful
    """
    try:
        cache_folder.mkdir(parents=True, exist_ok=True)
        
        meta = {
            "crs": "EPSG:25832",
            "world_min_e": float(world_min_e) if world_min_e else None,
            "world_min_n": float(world_min_n) if world_min_n else None,
            "dem_step": int(dem_step) if dem_step else 0,
            "source_dem": str(source_dem) if source_dem else "",
            "terrain_cached": True,
            "dem_object": "dem_merged",
            "alignment_method": "bbox_center_delta",
            "delta_applied": list(delta_applied) if delta_applied else [0, 0, 0],
            "created_at": datetime.now().isoformat()
        }
        
        meta_json = cache_folder / "terrain_meta.json"
        with open(meta_json, "w") as f:
            json.dump(meta, f, indent=2)
        
        log_info(f"[TerrainCache] Wrote metadata: {meta_json}")
        return True
    except Exception as e:
        log_warn(f"[TerrainCache] Failed to write metadata: {e}")
        return False


def _read_terrain_meta(cache_folder: Path) -> dict:
    """
    Read terrain_meta.json.
    
    Returns:
        Dict with keys: crs, world_min_e, world_min_n, dem_step, source_dem, created_at
        Empty dict if file doesn't exist or read fails
    """
    try:
        meta_json = cache_folder / "terrain_meta.json"
        if not meta_json.is_file():
            return {}
        
        with open(meta_json, "r") as f:
            meta = json.load(f)
        
        return meta
    except Exception as e:
        log_warn(f"[TerrainCache] Failed to read metadata: {e}")
        return {}


def _validate_terrain_cache(cache_folder: Path, current_world_min_e: float, current_world_min_n: float, tolerance: float = 1e-3) -> tuple:
    """
    Validate if cached terrain is still valid.
    
    Returns:
        (is_valid: bool, reason: str)
        - (True, "HIT"): Cache exists and world origin matches
        - (False, "MISS"): Cache doesn't exist
        - (False, "STALE"): Cache exists but world origin mismatch
        - (False, "ERROR"): Cache read error
    """
    paths = _terrain_cache_paths(cache_folder)
    
    # Check if GLB file exists
    if not paths["glb"].is_file():
        return False, "MISS"
    
    # Read metadata
    meta = _read_terrain_meta(cache_folder)
    if not meta:
        return False, "ERROR"
    
    # Validate world origin
    cached_min_e = meta.get("world_min_e")
    cached_min_n = meta.get("world_min_n")
    
    if cached_min_e is None or cached_min_n is None:
        return False, "STALE"
    
    # Compare with tolerance
    delta_e = abs(float(cached_min_e) - float(current_world_min_e))
    delta_n = abs(float(cached_min_n) - float(current_world_min_n))
    
    if delta_e > tolerance or delta_n > tolerance:
        log_info(f"[TerrainCache] World origin mismatch: cached=({cached_min_e:.0f}, {cached_min_n:.0f}), current=({current_world_min_e:.0f}, {current_world_min_n:.0f}), delta=({delta_e:.1f}, {delta_n:.1f})")
        return False, "STALE"
    
    return True, "HIT"


def _load_terrain_cache(cache_folder: Path) -> object:
    """
    Load cached terrain from GLB file via glTF importer.
    
    Strategy:
    - Use bpy.ops.import_scene.gltf() to import terrain.glb
    - Find dem_merged object (or largest mesh object if name mismatch)
    - Return dem_merged object for alignment
    
    Returns:
        dem_merged object, or None if load fails
    """
    import bpy
    
    try:
        paths = _terrain_cache_paths(cache_folder)
        glb_file = paths["glb"]
        
        if not glb_file.is_file():
            log_warn(f"[TerrainCache] GLB file not found: {glb_file}")
            return None
        
        # Track objects before import
        objs_before = set(bpy.data.objects)
        
        # Import GLB via glTF operator
        log_info(f"[TerrainCache] Loading terrain from {glb_file.name}")
        bpy.ops.import_scene.gltf(filepath=str(glb_file))
        
        # Find newly imported objects
        objs_after = set(bpy.data.objects)
        imported_objs = list(objs_after - objs_before)
        
        if not imported_objs:
            log_warn(f"[TerrainCache] No objects imported from GLB")
            return None
        
        # Find dem_merged or largest mesh
        dem_obj = None
        for obj in imported_objs:
            if obj.name == "dem_merged" or obj.name.startswith("dem_merged"):
                dem_obj = obj
                break
        
        if not dem_obj:
            # Fallback: find largest mesh by vertex count
            largest = None
            largest_verts = 0
            for obj in imported_objs:
                if obj.type == "MESH" and len(obj.data.vertices) > largest_verts:
                    largest = obj
                    largest_verts = len(obj.data.vertices)
            dem_obj = largest
        
        if dem_obj:
            log_info(f"[TerrainCache] Loaded terrain object: {dem_obj.name} ({len(dem_obj.data.vertices)} vertices)")
            return dem_obj
        else:
            log_warn(f"[TerrainCache] Could not identify terrain object in import")
            return None
    
    except Exception as e:
        log_error(f"[TerrainCache] Failed to load cached terrain: {e}")
        return None


def _export_terrain_cache(dem_obj: object, cache_folder: Path, world_min_e: float, world_min_n: float, dem_step: int = 0, source_dem: str = "") -> bool:
    """
    Export terrain object to GLB cache file.
    
    Strategy:
    - Select only dem_obj
    - Export to terrain.glb with materials
    - Blender 4.5 compatible (use export_image_format 'AUTO' or 'PNG')
    - Write metadata with alignment info
    
    Args:
        dem_obj: DEM mesh object to export
        cache_folder: Path to cache folder
        world_min_e, world_min_n: World origin (for metadata)
        dem_step: DEM decimation step
        source_dem: Source DEM path
    
    Returns:
        True if successful
    """
    import bpy
    
    try:
        cache_folder.mkdir(parents=True, exist_ok=True)
        paths = _terrain_cache_paths(cache_folder)
        glb_file = paths["glb"]
        
        # Select only dem_obj
        bpy.ops.object.select_all(action='DESELECT')
        dem_obj.select_set(True)
        bpy.context.view_layer.objects.active = dem_obj
        
        log_info(f"[TerrainCache] Exporting terrain to {glb_file.name}")
        
        # Export GLB with materials (Blender 4.5 compatible)
        try:
            bpy.ops.export_scene.gltf(
                filepath=str(glb_file),
                use_selection=True,
                export_format='GLB',
                export_materials=True,
                export_image_format='AUTO',  # Blender 4.5 compatible
            )
        except TypeError:
            # Fallback for different Blender versions
            bpy.ops.export_scene.gltf(
                filepath=str(glb_file),
                use_selection=True,
                export_format='GLB',
                export_materials=True,
            )
        
        log_info(f"[TerrainCache] ✓ Exported terrain to {glb_file}")
        
        # Write metadata with delta (will be filled in by alignment function)
        _write_terrain_meta(cache_folder, world_min_e, world_min_n, dem_step, source_dem, delta_applied=(0, 0, 0))
        
        return True
    
    except Exception as e:
        log_error(f"[TerrainCache] Failed to export terrain: {e}")
        return False


def _align_terrain_to_citygml(dem_obj: object) -> tuple:
    """
    Align terrain (DEM) to CityGML tiles via BBox center delta.
    
    Strategy:
    - Collect all CityGML tiles (name starts with "LoD2_", type MESH)
    - Compute BBox of all CityGML tiles
    - Compute BBox of DEM object
    - delta = gml_center - dem_center
    - Apply translation: dem_obj.location += delta
    
    Args:
        dem_obj: DEM mesh object to align
    
    Returns:
        Tuple (dx, dy, dz) of translation applied
    """
    import bpy
    
    try:
        from mathutils import Vector

        if dem_obj is not None and getattr(dem_obj, "get", None) and dem_obj.get("M1DC_TERRAIN_CALIBRATED"):
            log_info("[TerrainAlign] Terrain calibrated; skipping CityGML alignment")
            return (0, 0, 0)

        citygml_col = bpy.data.collections.get("CITYGML_TILES")
        citygml_tiles = [o for o in citygml_col.objects if o.type == "MESH"] if citygml_col else []

        if not citygml_tiles:
            log_warn("[TerrainAlign] No CityGML tiles found (CITYGML_TILES), skipping alignment")
            return (0, 0, 0)

        def _bbox_world_xy_objs(objs):
            min_x = min_y = float("inf")
            max_x = max_y = float("-inf")
            for obj in objs:
                for corner in obj.bound_box:
                    wc = obj.matrix_world @ Vector(corner)
                    min_x = min(min_x, wc.x)
                    min_y = min(min_y, wc.y)
                    max_x = max(max_x, wc.x)
                    max_y = max(max_y, wc.y)
            return min_x, min_y, max_x, max_y

        def _bbox_world_xy_obj(obj):
            min_x = min_y = float("inf")
            max_x = max_y = float("-inf")
            for corner in obj.bound_box:
                wc = obj.matrix_world @ Vector(corner)
                min_x = min(min_x, wc.x)
                min_y = min(min_y, wc.y)
                max_x = max(max_x, wc.x)
                max_y = max(max_y, wc.y)
            return min_x, min_y, max_x, max_y

        gml_min_x, gml_min_y, gml_max_x, gml_max_y = _bbox_world_xy_objs(citygml_tiles)
        gml_center_x = (gml_min_x + gml_max_x) / 2.0
        gml_center_y = (gml_min_y + gml_max_y) / 2.0
        gml_extent_x = gml_max_x - gml_min_x
        gml_extent_y = gml_max_y - gml_min_y
        gml_count_used = len(citygml_tiles)

        dem_min_x, dem_min_y, dem_max_x, dem_max_y = _bbox_world_xy_obj(dem_obj)
        dem_center_x = (dem_min_x + dem_max_x) / 2.0
        dem_center_y = (dem_min_y + dem_max_y) / 2.0
        dem_extent_x = dem_max_x - dem_min_x
        dem_extent_y = dem_max_y - dem_min_y

        world_origin = bpy.data.objects.get("M1DC_WORLD_ORIGIN")
        tile_size_m = float(world_origin.get("tile_size_m", 1000.0)) if world_origin else 1000.0
        tile_anchor = str(world_origin.get("tile_anchor", "CORNER")).upper() if world_origin else "CORNER"
        min_e, min_n, max_e, max_n = get_world_origin_minmax()

        intersection_xy = not (
            dem_max_x < gml_min_x or dem_min_x > gml_max_x or dem_max_y < gml_min_y or dem_min_y > gml_max_y
        )
        dx_center = gml_center_x - dem_center_x
        dy_center = gml_center_y - dem_center_y
        center_distance_xy = (dx_center * dx_center + dy_center * dy_center) ** 0.5

        log_info("[SPATIAL_HARDREAD] WORLD_ORIGIN: min_e={0}, min_n={1}, max_e={2}, max_n={3}, tile_size_m={4}".format(
            min_e, min_n, max_e, max_n, tile_size_m
        ))
        log_info(
            "[SPATIAL_HARDREAD] DEM: name={0}, loc=({1:.3f}, {2:.3f}, {3:.3f}), scale=({4:.3f}, {5:.3f}, {6:.3f}), "
            "bbox_min=({7:.3f}, {8:.3f}), bbox_max=({9:.3f}, {10:.3f}), center=({11:.3f}, {12:.3f}), extent=({13:.3f}, {14:.3f})".format(
                dem_obj.name if dem_obj else None,
                dem_obj.location.x, dem_obj.location.y, dem_obj.location.z,
                dem_obj.scale.x, dem_obj.scale.y, dem_obj.scale.z,
                dem_min_x, dem_min_y, dem_max_x, dem_max_y,
                dem_center_x, dem_center_y, dem_extent_x, dem_extent_y
            )
        )
        log_info(
            "[SPATIAL_HARDREAD] GML: count_used={0}, bbox_min=({1:.3f}, {2:.3f}), bbox_max=({3:.3f}, {4:.3f}), "
            "center=({5:.3f}, {6:.3f}), extent=({7:.3f}, {8:.3f})".format(
                gml_count_used, gml_min_x, gml_min_y, gml_max_x, gml_max_y, gml_center_x, gml_center_y, gml_extent_x, gml_extent_y
            )
        )
        log_info(f"[SPATIAL_HARDREAD] INTERSECTION_XY: {'YES' if intersection_xy else 'NO'}")
        log_info(f"[SPATIAL_HARDREAD] CENTER_DISTANCE_XY: {center_distance_xy:.3f} m")

        # Guards: NEVER shift if bbox invalid, NaN/Inf, zero-extent, or inconsistent
        # This is a hard tripwire to prevent terrain drift from bad bbox aggregation
        reasons = []
        guards_ok = True
        
        # Check for NaN or Inf (invalid bbox computation)
        import math
        for val, name in [(gml_min_x, "gml_min_x"), (gml_min_y, "gml_min_y"), 
                          (gml_max_x, "gml_max_x"), (gml_max_y, "gml_max_y"),
                          (dem_min_x, "dem_min_x"), (dem_min_y, "dem_min_y"),
                          (dem_max_x, "dem_max_x"), (dem_max_y, "dem_max_y")]:
            if not math.isfinite(val):
                guards_ok = False
                reasons.append(f"{name}={val} (not finite)")
        
        # Check gml_count (must have minimum tiles)
        if gml_count_used < 4:
            guards_ok = False
            reasons.append("gml_count_used < 4")
        
        # Check extents are positive
        if gml_extent_x <= 0 or gml_extent_y <= 0:
            guards_ok = False
            reasons.append("gml_extent invalid (<=0)")
        if dem_extent_x <= 0 or dem_extent_y <= 0:
            guards_ok = False
            reasons.append("dem_extent invalid (<=0)")
        
        # Check for bbox inconsistency (many tiles but tiny extent = aggregation bug)
        if gml_count_used >= 16 and (gml_extent_x < tile_size_m * 2 or gml_extent_y < tile_size_m * 2):
            guards_ok = False
            reasons.append(f"gml_count={gml_count_used} but extent={gml_extent_x:.1f}x{gml_extent_y:.1f}m (expect ~{tile_size_m*2}m+)")
        if gml_count_used >= 2 and (gml_extent_x < tile_size_m * 0.5 or gml_extent_y < tile_size_m * 0.5):
            guards_ok = False
            reasons.append(f"gml_extent too small relative to tile_size_m={tile_size_m}m")
        
        # Check intersection is plausible
        if center_distance_xy > tile_size_m * 5:
            guards_ok = False
            reasons.append(f"center_distance={center_distance_xy:.1f}m >> tile_size_m={tile_size_m}m (bbox mismatch)")

        if not guards_ok:
            log_warn("[TRIPWIRE] NEVER shift if bbox invalid")
            log_info("[SPATIAL_HARDREAD] RECOMMENDATION: FAIL")
            log_info(f"[SPATIAL_HARDREAD] REASON: {'; '.join(reasons) if reasons else 'bbox invalid'}")
            return (0, 0, 0)

        # Strategy selection (one run = one strategy)
        center_tol = max(1.0, max(dem_extent_x, dem_extent_y) * 0.02)
        dem_centered = (
            abs(dem_center_x) < center_tol
            and abs(dem_center_y) < center_tol
            and abs(dem_min_x + dem_max_x) < center_tol
            and abs(dem_min_y + dem_max_y) < center_tol
        )

        if dem_centered and tile_anchor == "CORNER":
            strategy = "ANCHOR_CORRECTION_CENTER_TO_CORNER"
            dx = dem_extent_x / 2.0
            dy = dem_extent_y / 2.0
        else:
            strategy = "CENTER_MATCH"
            dx = dx_center
            dy = dy_center

        log_info(f"[SPATIAL_HARDREAD] RECOMMENDATION: SHIFT")
        log_info(f"[SPATIAL_HARDREAD] REASON: {strategy}")
        log_info(f"[ALIGNMENT] strategy={strategy} dx={dx:.3f} dy={dy:.3f}")

        # Apply translation (XY only)
        dem_obj.location.x += dx
        dem_obj.location.y += dy

        log_info(
            f"[ALIGNMENT] applied dx={dx:.3f} dy={dy:.3f} -> DEM loc=({dem_obj.location.x:.3f}, {dem_obj.location.y:.3f})"
        )

        # Re-check after shift (GML bbox should be unchanged)
        dem_min_x2, dem_min_y2, dem_max_x2, dem_max_y2 = _bbox_world_xy_obj(dem_obj)
        dem_center_x2 = (dem_min_x2 + dem_max_x2) / 2.0
        dem_center_y2 = (dem_min_y2 + dem_max_y2) / 2.0
        dem_extent_x2 = dem_max_x2 - dem_min_x2
        dem_extent_y2 = dem_max_y2 - dem_min_y2
        center_distance_xy2 = ((gml_center_x - dem_center_x2) ** 2 + (gml_center_y - dem_center_y2) ** 2) ** 0.5
        intersection_xy2 = not (
            dem_max_x2 < gml_min_x or dem_min_x2 > gml_max_x or dem_max_y2 < gml_min_y or dem_min_y2 > gml_max_y
        )

        log_info(
            "[SPATIAL_HARDREAD_AFTER] DEM: bbox_min=({0:.3f}, {1:.3f}), bbox_max=({2:.3f}, {3:.3f}), center=({4:.3f}, {5:.3f}), "
            "extent=({6:.3f}, {7:.3f})".format(
                dem_min_x2, dem_min_y2, dem_max_x2, dem_max_y2, dem_center_x2, dem_center_y2, dem_extent_x2, dem_extent_y2
            )
        )
        log_info(
            "[SPATIAL_HARDREAD_AFTER] GML: count_used={0}, bbox_min=({1:.3f}, {2:.3f}), bbox_max=({3:.3f}, {4:.3f}), "
            "center=({5:.3f}, {6:.3f}), extent=({7:.3f}, {8:.3f})".format(
                gml_count_used, gml_min_x, gml_min_y, gml_max_x, gml_max_y, gml_center_x, gml_center_y, gml_extent_x, gml_extent_y
            )
        )
        log_info(f"[SPATIAL_HARDREAD_AFTER] INTERSECTION_XY: {'YES' if intersection_xy2 else 'NO'}")
        log_info(f"[SPATIAL_HARDREAD_AFTER] CENTER_DISTANCE_XY: {center_distance_xy2:.3f} m")

        if center_distance_xy2 > center_distance_xy:
            log_warn("[ALIGNMENT] center distance worsened after shift")

        return (dx, dy, 0)

    except Exception as e:
        log_error(f"[TerrainAlign] Failed to align terrain: {e}")
        return (0, 0, 0)

# ---------------- Bbox/DEM placement helpers (MOVED TO utils/geometry.py) ----------------
# NOTE: bbox_world_minmax_xy, detect_dem_placement_mode, localize_mesh_data_to_world_min
#       now imported from utils.geometry at top of file


def _place_citygml_tiles_from_csv(csv_rows: list, world_min_e: float, world_min_n: float, collection_name: str = "CITYGML_TILES", tile_size_m: float = 2000.0, flip_northing: bool = False) -> dict:
    """
    Use CSV tile extents to position CityGML mesh objects in local coordinates.
    
    Strategy:
    1. Parse CSV rows: {filename, easting, northing, tile_size_m}
    2. For each CityGML tile object, match filename (stem) to CSV entry
    3. Compute local position: local_x = csv_easting - world_min_e, local_y = csv_northing - world_min_n
    4. Apply placement via BBox-center-delta method (centerof tile geometry → desired local position)
    
    Args:
        csv_rows: List of dicts from load_tile_csv()
        world_min_e, world_min_n: World origin
        collection_name: Blender collection containing tiles
        tile_size_m: Default tile size if not in CSV
        flip_northing: If true, negate local_y
    
    Returns:
        {
            "method": "csv_extents" | "fallback",
            "tiles_placed": int,
            "tiles_skipped": int,
            "samples": [{filename, csv_e, csv_n, local_x, local_y}, ...]
        }
    """
    import bpy
    from mathutils import Vector
    
    try:
        col = bpy.data.collections.get(collection_name)
        if not col:
            log_warn(f"[CityGMLPlace] Collection {collection_name} not found")
            return {"method": "fallback", "tiles_placed": 0, "tiles_skipped": 0, "samples": []}
        
        # Build CSV lookup: filename (stem) → {easting, northing, tile_size_m}
        csv_lookup = {}
        for row in csv_rows:
            filename = Path(row['filename']).stem  # Remove extension
            csv_lookup[filename.lower()] = {
                'easting': float(row['easting']),
                'northing': float(row['northing']),
                'tile_size_m': float(row.get('tile_size_m') or tile_size_m),
            }
        
        log_info(f"[CityGMLPlace] CSV lookup loaded: {len(csv_lookup)} tiles")
        
        # Place tiles from CSV
        placed = 0
        skipped = 0
        samples = []
        
        for obj in col.objects:
            if obj.type != "MESH":
                continue
            
            # Extract tile name from object (filename or source_tile attribute)
            tile_name = None
            if "source_tile" in obj:
                tile_name = str(obj["source_tile"])
            else:
                tile_name = Path(obj.name).stem
            
            tile_name_lower = tile_name.lower()
            
            if tile_name_lower not in csv_lookup:
                log_info(f"[CityGMLPlace] ⚠ Tile {obj.name} not in CSV; skipping")
                skipped += 1
                continue
            
            csv_info = csv_lookup[tile_name_lower]
            csv_e = csv_info['easting']
            csv_n = csv_info['northing']
            tile_sz = csv_info['tile_size_m']
            
            # Compute desired local position
            desired_local_x = csv_e - world_min_e
            desired_local_y = csv_n - world_min_n
            
            if flip_northing:
                desired_local_y = -desired_local_y
            
            # Compute current BBox center
            min_v = Vector((float("inf"), float("inf"), float("inf")))
            max_v = Vector((float("-inf"), float("-inf"), float("-inf")))
            for corner in obj.bound_box:
                world_corner = obj.matrix_world @ Vector(corner)
                min_v.x = min(min_v.x, world_corner.x)
                min_v.y = min(min_v.y, world_corner.y)
                min_v.z = min(min_v.z, world_corner.z)
                max_v.x = max(max_v.x, world_corner.x)
                max_v.y = max(max_v.y, world_corner.y)
                max_v.z = max(max_v.z, world_corner.z)
            
            current_cx = (min_v.x + max_v.x) * 0.5
            current_cy = (min_v.y + max_v.y) * 0.5
            
            # Apply delta
            delta_x = desired_local_x - current_cx
            delta_y = desired_local_y - current_cy
            
            obj.location.x += delta_x
            obj.location.y += delta_y
            
            placed += 1
            
            # Log sample
            if len(samples) < 3:
                samples.append({
                    "filename": tile_name,
                    "csv_e": csv_e,
                    "csv_n": csv_n,
                    "local_x": desired_local_x,
                    "local_y": desired_local_y,
                    "delta_applied": (delta_x, delta_y),
                })
            
            log_info(f"[CityGMLPlace] ✓ {obj.name}: CSV=({csv_e:.0f},{csv_n:.0f}) → Local=({desired_local_x:.1f},{desired_local_y:.1f}), delta=({delta_x:.1f},{delta_y:.1f})")
        
        log_info(f"[CityGMLPlace] ✓ Placed {placed}/{placed + skipped} tiles from CSV")
        
        return {
            "method": "csv_extents",
            "tiles_placed": placed,
            "tiles_skipped": skipped,
            "samples": samples,
        }
    
    except Exception as e:
        log_error(f"[CityGMLPlace] Failed to place tiles from CSV: {e}")
        return {"method": "fallback", "tiles_placed": 0, "tiles_skipped": len(csv_rows), "samples": []}


# NOTE: _bbox_world_minmax_xy, _detect_dem_placement_mode, _localize_mesh_data_to_world_min
# moved to utils/geometry.py - imported at top of file


def _place_terrain_local(dem_obj: object, world_min_e: float, world_min_n: float, flip_northing: bool = False) -> tuple:
    """
    Place terrain in local coordinate space (LOCALIZE-ONLY, NO RESCALING).

    NEW STRATEGY (2026-01-26):
    - Detect if DEM bbox is GLOBAL (UTM coords) or already LOCAL
    - If GLOBAL_BBOX/GLOBAL_LIKELY: shift MESH DATA (not just obj.location)
    - If LOCAL: do nothing (already correctly positioned)
    - NEVER touch obj.scale (leave it as-is, typically 1.0)

    This fixes the "shrink" issue where terrain was being incorrectly rescaled.

    Args:
        dem_obj: DEM mesh object to place
        world_min_e: World origin easting (METERS)
        world_min_n: World origin northing (METERS)
        flip_northing: If true, negate local_y coordinate (NOT IMPLEMENTED YET)

    Returns:
        Tuple (method_used, dx_applied, dy_applied) for diagnostics
    """
    if dem_obj is None or dem_obj.type != 'MESH':
        log_warn("[TerrainPlace] dem_obj invalid; skipping placement")
        return ("ERROR", 0, 0)

    try:
        # Check if already calibrated/processed
        if dem_obj.get("M1DC_TERRAIN_LOCALIZED"):
            log_info("[TerrainPlace] Terrain already localized; skipping")
            return ("ALREADY_LOCALIZED", 0, 0)

        # Detect placement mode using bbox heuristic
        mode = detect_dem_placement_mode(dem_obj)
        log_info(f"[TerrainPlace] Detected DEM placement mode: {mode}")

        # Log bbox before localization
        min_x, min_y, max_x, max_y = bbox_world_minmax_xy(dem_obj)
        log_info(f"[TerrainPlace] Bbox BEFORE: X=[{min_x:.1f}, {max_x:.1f}], Y=[{min_y:.1f}, {max_y:.1f}]")
        log_info(f"[TerrainPlace] Object: location={dem_obj.location}, scale={dem_obj.scale}")

        if mode == "GLOBAL_BBOX" or mode == "GLOBAL_LIKELY":
            # GLOBAL: Shift mesh data to local coordinates
            method, dx, dy = localize_mesh_data_to_world_min(dem_obj, world_min_e, world_min_n, flip_northing)

            if method == "mesh_translate":
                # Log bbox after localization
                min_x, min_y, max_x, max_y = bbox_world_minmax_xy(dem_obj)
                log_info(f"[TerrainPlace] Bbox AFTER:  X=[{min_x:.1f}, {max_x:.1f}], Y=[{min_y:.1f}, {max_y:.1f}]")
                log_info(f"[TerrainPlace] ✓ Localized from {mode} to LOCAL: mesh shifted by ({dx:.1f}, {dy:.1f})")

                # Mark as localized
                dem_obj["M1DC_TERRAIN_LOCALIZED"] = True
                return ("localized_mesh_shift", dx, dy)
            else:
                log_error(f"[TerrainPlace] Localization failed: {method}")
                return ("ERROR", 0, 0)

        else:
            # LOCAL: Already in local space, do nothing
            log_info(f"[TerrainPlace] DEM already in LOCAL space (bbox max coord < 1e5)")
            log_info(f"[TerrainPlace] Bbox: X=[{min_x:.1f}, {max_x:.1f}], Y=[{min_y:.1f}, {max_y:.1f}]")

            # Mark as localized (even though no action needed)
            dem_obj["M1DC_TERRAIN_LOCALIZED"] = True
            return ("already_local", 0, 0)

    except Exception as e:
        log_error(f"[TerrainPlace] Failed to place terrain: {e}")
        import traceback
        traceback.print_exc()
        return ("ERROR", 0, 0)


# NOTE: _hash_color and _apply_viewport_solid_cavity moved to utils/geometry.py


def _write_placement_report(output_dir: str, placement_method: str, dem_obj: object, world_min_e: float, world_min_n: float, tile_size_m: float, flip_northing: bool = False, citygml_report: dict = None) -> bool:
    """
    Write placement_report.json with CRS/placement diagnostics.
    
    Args:
        output_dir: Output directory (usually output/_Merged/terrain)
        placement_method: "csv_extents" | "bbox_fallback" | "already_local"
        dem_obj: DEM terrain object
        world_min_e, world_min_n: World origin
        tile_size_m: Tile size in meters
        flip_northing: Whether Y-axis is flipped
        citygml_report: Dict from _place_citygml_tiles_from_csv() (optional)
    
    Returns:
        True if successful
    """
    try:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        report_file = output_path / "placement_report.json"
        
        # Collect DEM samples
        dem_world_loc = [float(dem_obj.location.x), float(dem_obj.location.y), float(dem_obj.location.z)] if dem_obj else [0, 0, 0]
        dem_local_loc = dem_world_loc  # Already localized
        
        # Build report
        report = {
            "crs": "EPSG:25832",
            "origin": {
                "min_e": float(world_min_e),
                "min_n": float(world_min_n),
            },
            "method": placement_method,
            "tile_size_m": float(tile_size_m),
            "flip_northing": bool(flip_northing),
            "samples": {
                "dem_world_loc": dem_world_loc,
                "dem_local_loc": dem_local_loc,
            },
            "citygml_placement": citygml_report or {},
            "timestamp": datetime.now().isoformat(),
        }
        
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2)
        
        log_info(f"[PlacementReport] Written: {report_file}")
        return True
    
    except Exception as e:
        log_error(f"[PlacementReport] Failed to write report: {e}")
        return False





def _import_basemap_pipeline(s) -> bool:
    """
    New terrain pipeline: GDAL CLI merge → BlenderGIS DEM import with RGB texture
    
    Workflow:
    1. MANDATORY FIX #3: Ensure WORLD_ORIGIN is set BEFORE any terrain import
    2. Get DGM and RGB source directories from settings
    3. Call terrain_merge.run_terrain_merge() to create merged GeoTIFFs in output/_Merged/
    4. Import DEM as height mesh using BlenderGIS georaster (not plane)
    5. Import RGB and apply material to DEM mesh
    6. Apply world_to_local translation to terrain objects (same as CityGML)
    
    Returns:
        True if terrain merge successful (BlenderGIS import is optional),
        False if merge itself failed
    """
    # ── OBJ DOMINATES GUARD: Never run DEM pipeline when OBJ artifact exists ──
    _mode, _mode_path = detect_terrain_mode(s)
    if _mode == "OBJ_ARTIFACT":
        log_info(f"[Terrain] OBJ artifact active ({_mode_path}) → DEM pipeline SKIPPED (OBJ dominates)")
        print(f"[Terrain][OBJ_DOMINATES] Skipping _import_basemap_pipeline — OBJ artifact takes precedence")
        return False  # Not an error, just not applicable
    
    dgm_dir = getattr(s, "terrain_dgm_dir", "").strip()
    rgb_dir = getattr(s, "terrain_rgb_dir", "").strip()
    output_dir = getattr(s, "output_dir", "").strip()
    
    # DEBUG: Log entry
    log_info(f"[Terrain] === TERRAIN IMPORT PIPELINE START ===")
    log_info(f"[Terrain]   DGM dir: {dgm_dir}")
    log_info(f"[Terrain]   RGB dir: {rgb_dir}")
    log_info(f"[Terrain]   Output dir: {output_dir}")
    
    if not dgm_dir and not rgb_dir:
        log_info("[Terrain] No DGM or RGB source folders configured; terrain import skipped.")
        return False
    
    if not output_dir:
        log_error("[Terrain] No output directory configured; cannot create merged outputs.")
        return False
    
    # MANDATORY FIX #3: Ensure world origin is set BEFORE starting terrain import
    log_info(f"[Terrain] === ENSURE WORLD ORIGIN ===")
    min_e_m, min_n_m, _, _ = get_world_origin_minmax()
    if min_e_m is None or min_n_m is None:
        # Phase 0A: Prefer merged GeoTIFF bounds if available
        output_dir = getattr(s, "output_dir", "").strip()
        dem_candidate = os.path.join(output_dir, "_Merged", "dem_merged.tif") if output_dir else ""
        if dem_candidate and os.path.isfile(dem_candidate):
            try:
                from .pipeline.terrain.terrain_merge import read_world_bounds_from_geotiff

                bounds = read_world_bounds_from_geotiff(dem_candidate)
                if bounds:
                    min_e_b, min_n_b, max_e_b, max_n_b = bounds
                    if max_e_b < 1_000_000 or max_n_b < 1_000_000:
                        raise RuntimeError("WORLD_ORIGIN inference from GeoTIFF produced non-meter values. Refusing to lock.")
                    ensure_world_origin(min_e=min_e_b, min_n=min_n_b, max_e=max_e_b, max_n=max_n_b, source="TerrainGeoTIFF", crs="EPSG:25832")
                    min_e_m, min_n_m, _, _ = get_world_origin_minmax()
                    log_info(f"[Terrain] ✓ WORLD_ORIGIN from GeoTIFF bounds: min_e={min_e_m:.1f}m, min_n={min_n_m:.1f}m")
            except Exception as ex:
                log_error(f"[Terrain] Failed to set WORLD_ORIGIN from GeoTIFF: {ex}")
                raise

    if min_e_m is None or min_n_m is None:
        log_info("[Terrain] WORLD_ORIGIN not yet locked. Attempting to infer from CityGML tiles...")
        citygml_dir = getattr(s, "citygml_dir", "").strip()
        if citygml_dir and os.path.isdir(citygml_dir):
            try:
                log_info(f"[Terrain]   Parsing CityGML folder: {citygml_dir}")
                success = infer_world_origin_from_citygml_tiles(s, citygml_dir)
                if success:
                    min_e_m, min_n_m, _, _ = get_world_origin_minmax()
                    log_info(f"[Terrain] ✓ WORLD_ORIGIN inferred from CityGML grid: min_e={min_e_m:.1f}m, min_n={min_n_m:.1f}m")
                else:
                    log_warn("[Terrain] Could not infer WORLD_ORIGIN from CityGML tiles (no valid filenames found)")
            except Exception as ex:
                log_error(f"[Terrain] Failed to infer WORLD_ORIGIN: {ex}")
                raise
        else:
            log_warn(f"[Terrain] CityGML folder not available: {citygml_dir}")
        
        if min_e_m is None or min_n_m is None:
            log_error("[Terrain] WORLD_ORIGIN could not be ensured. Cannot localize terrain correctly.")
            log_error("[Terrain] Solution: Run CityGML validation first (auto-infers world origin), then retry terrain.")
            return False
    else:
        log_info(f"[Terrain] ✓ WORLD_ORIGIN already locked: min_e={min_e_m:.1f}m, min_n={min_n_m:.1f}m")
    
    # TERRAIN CACHE CHECK: Try to load cached terrain if enabled
    use_terrain_cache = getattr(s, "use_terrain_cache", True)
    if use_terrain_cache:
        log_info(f"[Terrain] === TERRAIN CACHE CHECK ===")
        cache_folder = _get_terrain_cache_folder(s)
        is_valid, reason = _validate_terrain_cache(cache_folder, min_e_m, min_n_m)
        
        if reason == "HIT":
            log_info(f"[TerrainCache] HIT: loaded cached terrain, skipping DEM build")
            try:
                loaded_objs = _load_terrain_cache(cache_folder)
                if loaded_objs:
                    log_info(f"[Terrain] ✓ Cached terrain loaded: {dem_obj.name}")
                    
                    # Apply alignment (even for cached terrain, to match CityGML)
                    delta = _align_terrain_to_citygml(dem_obj)
                    
                    return True
                else:
                    log_warn(f"[TerrainCache] Load failed; will rebuild terrain")
            except Exception as e:
                log_warn(f"[TerrainCache] Load failed with error: {e}; will rebuild terrain")
        elif reason == "MISS":
            log_info(f"[TerrainCache] MISS: cache not found, building terrain")
        elif reason == "STALE":
            log_info(f"[TerrainCache] STALE: world origin mismatch, rebuilding terrain")
        else:
            log_info(f"[TerrainCache] {reason}: will rebuild terrain")
    else:
        log_info(f"[Terrain] Terrain cache disabled (use_terrain_cache=False)")
        cache_folder = None
    
    # Extract terrain_dem_step override from settings (MANDATORY FIX #1)
    terrain_dem_step = getattr(s, "terrain_dem_step", 0)
    if terrain_dem_step > 0:
        log_warn(f"[Terrain] DEM step override active: terrain_dem_step={terrain_dem_step} (manual mode)")
    
    # Import terrain merge module
    try:
        from .pipeline.terrain.terrain_merge import run_terrain_merge
    except ImportError as e:
        log_error(f"[Terrain] Failed to import terrain_merge module: {e}")
        return False
    
    # Run merge
    try:
        result = run_terrain_merge(
            dgm_dir=dgm_dir or output_dir,  # fallback to output_dir if not set
            rgb_dir=rgb_dir or output_dir,
            output_dir=output_dir,
            force=False,  # use cached if exists
        )
        log_info(f"[Terrain] ✓ Merge completed: {result.get('status', 'unknown')}")
        dem_tif = result.get("dem_merged_tif", "")
        rgb_tif = result.get("rgb_merged_tif", "")
        merged_dir = result.get("merged_dir", "")
        
        # Requirement 3: Import DEM as height mesh via BlenderGIS (not plane-only)
        try:
            # Pass terrain_dem_step UI override to import function
            terrain_objs = _import_terrain_dem_mesh(dem_tif, rgb_tif, merged_dir, min_e_m, min_n_m, terrain_dem_step)
            if terrain_objs:
                log_info(f"[Terrain] ✓ Imported DEM mesh + RGB texture to {len(terrain_objs)} objects")
                
                # ========== LOCALIZE-ONLY (NO RESCALING) ==========
                # NEW APPROACH (2026-01-26): Localize terrain to corner-origin,
                # never rescale (fixes "shrink" issue).
                #
                # Corner-origin policy: World origin = terrain MIN corner (not center)
                # Terrain corner should be at (0,0,0) in local space
                # obj.scale must remain ~1.0 (never reset or apply scale)

                placement_method = "localize_only"
                dem_obj = terrain_objs[0]
                flip_northing = getattr(s, "flip_northing", False)
                min_e_max, min_n_max, max_e_max, max_n_max = get_world_origin_minmax()

                if min_e_max is not None and min_n_max is not None:
                    try:
                        log_info(f"[Terrain] === LOCALIZE-ONLY START ===")
                        log_info(f"[Terrain]   World origin (corner): E={min_e_max:.0f}, N={min_n_max:.0f}")
                        log_info(f"[Terrain]   Policy: Corner-origin, no rescaling")

                        placement_method, dx, dy = _place_terrain_local(dem_obj, min_e_max, min_n_max, flip_northing)

                        if placement_method in ("localized_mesh_shift", "already_local"):
                            log_info(f"[Terrain] ✓ LOCALIZE-ONLY: Terrain positioned at corner-origin")
                            log_info(f"[Terrain]   Method: {placement_method}, shift: ({dx:.1f}, {dy:.1f})")
                        else:
                            log_warn(f"[Terrain] ✗ LOCALIZE-ONLY: Failed with method={placement_method}")

                        log_info(f"[Terrain] === LOCALIZE-ONLY COMPLETE ===")

                    except Exception as e:
                        log_error(f"[Terrain] LOCALIZE-ONLY failed: {e}")
                        import traceback
                        traceback.print_exc()
                        placement_method = "ERROR"
                else:
                    log_warn(f"[Terrain] World origin not available; skipping terrain localization")
                    placement_method = "no_world_origin"
                
                # Step 2: If CSV provided, also place CityGML tiles deterministically
                csv_path = getattr(s, "terrain_tiles_csv", "").strip()
                citygml_placement_report = {}
                if csv_path and os.path.isfile(csv_path):
                    try:
                        from .pipeline.terrain.m1_terrain_csv import load_tile_csv
                        csv_rows = load_tile_csv(csv_path)
                        tile_size = getattr(s, "tile_size_m", 8000.0)
                        citygml_placement_report = _place_citygml_tiles_from_csv(
                            csv_rows, min_e_m, min_n_m, "CITYGML_TILES", tile_size, flip_northing
                        )
                        log_info(f"[Placement] ✓ CityGML tiles placed from CSV: {citygml_placement_report['tiles_placed']} placed, {citygml_placement_report['tiles_skipped']} skipped")
                    except Exception as e:
                        log_warn(f"[Placement] CSV-based placement failed: {e}; will use BBox-Delta fallback")
                        citygml_placement_report = {"method": "fallback"}
                
                # Step 3: Fallback to BBox-Delta if no CSV or CSV placement failed
                if citygml_placement_report.get("method") != "csv_extents":
                    if dem_obj.get("M1DC_TERRAIN_CALIBRATED"):
                        log_info("[Placement] Terrain calibrated; skipping BBox fallback alignment")
                        placement_method = "terrain_calibrated"
                    else:
                        log_info("[Placement] Using BBox-center-delta fallback alignment")
                        delta = _align_terrain_to_citygml(dem_obj)
                        placement_method = "bbox_fallback"
                else:
                    placement_method = "csv_extents"
                
                # Step 4: Write placement diagnostics
                tile_size = getattr(s, "tile_size_m", 8000.0)
                _write_placement_report(merged_dir, placement_method, dem_obj, min_e_m, min_n_m, tile_size, flip_northing, citygml_placement_report)
                
                # TERRAIN CACHE EXPORT: After successful placement, export to GLB
                if use_terrain_cache and cache_folder:
                    try:
                        _export_terrain_cache(dem_obj, cache_folder, min_e_m, min_n_m, terrain_dem_step, dem_tif)
                    except Exception as e:
                        log_warn(f"[TerrainCache] Export failed: {e}")
            else:
                log_warn("[Terrain] DEM import returned no objects")
        except Exception as e:
            log_warn(f"[Terrain] DEM/RGB import failed: {e}")

        # Terrain sanity check (replaced sabotage detector with warning)
        try:
            if bbox_size_xy_world is not None:
                sc = bpy.context.scene
                dem = bpy.data.objects.get("dem_merged")
                if dem:
                    # Check if terrain appears to be shrunk
                    w = bbox_size_xy_world(dem)[0]
                    if "M1DC_WORLD_MAX_E" in sc and "M1DC_WORLD_MIN_E" in sc:
                        target_w = sc["M1DC_WORLD_MAX_E"] - sc["M1DC_WORLD_MIN_E"]
                        if w < 0.5 * target_w:
                            log_warn(f"[Terrain] WARNING: Terrain bbox width ({w:.0f}m) is much smaller than world bounds ({target_w:.0f}m)")
                            log_warn(f"[Terrain] This may indicate incorrect scaling. Check terrain.scale = {dem.scale}")
                            self.report({"WARNING"}, f"Terrain may be incorrectly scaled (width={w:.0f}m vs expected={target_w:.0f}m)")
                        else:
                            log_info(f"[Terrain] ✓ Sanity check: Terrain bbox width={w:.0f}m, world bounds={target_w:.0f}m (OK)")
        except Exception as e:
            log_warn(f"[Terrain] Sanity check failed: {e}")
        
        return True
    
    except Exception as e:
        log_error(f"[Terrain] Merge failed: {e}")
        return False


def _import_terrain_dem_mesh(dem_tif: str, rgb_tif: str, merged_dir: str, world_min_e_m, world_min_n_m, ui_dem_step: int = 0) -> list:
    """
    Import terrain as DEM height mesh (Requirement 3) and apply RGB texture (Requirement 4).
    
    Args:
        dem_tif: Path to DEM GeoTIFF
        rgb_tif: Path to RGB GeoTIFF
        merged_dir: Output directory for merged files
        world_min_e_m: World origin min easting (meters)
        world_min_n_m: World origin min northing (meters)
        ui_dem_step: UI override for DEM step (0=auto, 1-32=manual). Overrides heuristic if non-zero.
    
    Steps:
    1. Import DEM as true height mesh using BlenderGIS georaster (DEM_RAW or DEM mode)
    2. Import RGB plane and extract material
    3. Apply RGB material to DEM mesh
    4. Hide/delete RGB plane
    5. Apply world_to_local translation (same as CityGML: subtract world origin)
    6. Add diagnostics (world_min, bbox centers, deltas)
    
    Returns:
        List of terrain objects (DEM mesh), empty if import failed
    """
    import bpy
    
    if not dem_tif or not os.path.isfile(dem_tif):
        raise RuntimeError(f"DEM file not found: {dem_tif}")
    if not rgb_tif or not os.path.isfile(rgb_tif):
        raise RuntimeError(f"RGB file not found: {rgb_tif}")
    
    # Check if BlenderGIS available
    if "importgis" not in dir(bpy.ops):
        raise RuntimeError("BlenderGIS (importgis) not available in bpy.ops")
    
    terrain_objs = []
    rgb_mat = None
    rgb_objs = []

    def _georaster_safe(**kwargs):
        """Call BlenderGIS georaster with backward-compatible args."""
        try:
            return bpy.ops.importgis.georaster(**kwargs)
        except TypeError:
            # Some BlenderGIS versions don't support newer keyword args.
            if "subdivision" in kwargs:
                kwargs2 = dict(kwargs)
                kwargs2.pop("subdivision", None)
                log_info("[Terrain] BlenderGIS: 'subdivision' arg unsupported; retrying without it")
                return bpy.ops.importgis.georaster(**kwargs2)
            raise

    def _pick_step(tif_path: str) -> int:
        """Aggressive heuristic to avoid DEM import hangs on large rasters.
        
        Rule: More aggressive decimation for larger rasters to keep import time reasonable.
        - pixels > 700MP => step=32 (decimates to ~28k vertices total)
        - pixels > 300MP => step=16 (decimates to ~117k vertices total)  
        - pixels > 100MP => step=8 (decimates to ~156k vertices total)
        - pixels > 20MP => step=2 (decimates to ~5M vertices total)
        - else => step=1 (full resolution)
        """
        # Prefer raster dimensions over file size (GeoTIFF can be highly compressed).
        try:
            from PIL import Image

            try:
                Image.MAX_IMAGE_PIXELS = None
            except Exception:
                pass

            with Image.open(tif_path) as im:
                width, height = im.size

            pixels = int(width) * int(height)
            mp = pixels / 1_000_000
            
            # More aggressive thresholds to prevent hangs
            if pixels > 700_000_000:  # > 700 MP
                step = 32
            elif pixels > 300_000_000:  # > 300 MP
                step = 16
            elif pixels > 100_000_000:  # > 100 MP
                step = 8
            elif pixels > 20_000_000:  # > 20 MP
                step = 2
            else:
                step = 1

            log_info(f"[Terrain] DEM raster: {width}x{height} = {mp:.1f} MP -> auto step={step}")
            return step
        except Exception as e:
            log_warn(f"[Terrain] Failed to read raster dimensions; falling back to file size: {e}")

        # Fallback to file size heuristic.
        try:
            size_mb = os.path.getsize(tif_path) / (1024 * 1024)
        except Exception:
            return 1

        if size_mb >= 512:
            return 32
        if size_mb >= 256:
            return 16
        if size_mb >= 128:
            return 8
        if size_mb >= 64:
            return 4
        if size_mb >= 32:
            return 2
        return 1

    rgb_step = 1
    dem_step = _pick_step(dem_tif)
    
    # MANDATORY FIX #1: Apply UI override if set (non-zero)
    if ui_dem_step > 0:
        log_warn(f"[Terrain] UI override active: terrain_dem_step={ui_dem_step} (was auto-heuristic: {dem_step})")
        dem_step = ui_dem_step
    elif dem_step != 1:
        log_info(f"[Terrain] DEM auto-decimated to step={dem_step} to keep import responsive")

    # ── DEM STRIP TRIPWIRE: Fail-fast if DEM extent is absurdly non-square ──
    # A valid DEM should have roughly comparable X/Y extent.
    # A span ratio > 10 means it's a strip (corrupted, wrong axis mapping, or
    # pixel→meter conversion error) and will produce "Kaugummi-Terrain".
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(dem_tif) as _img:
            _dem_w, _dem_h = _img.size
        if _dem_w > 0 and _dem_h > 0:
            _span_ratio = max(_dem_w, _dem_h) / max(1, min(_dem_w, _dem_h))
            log_info(f"[Terrain][STRIP_CHECK] DEM pixels: {_dem_w}x{_dem_h} ratio={_span_ratio:.1f}")
            if _span_ratio > 10:
                raise RuntimeError(
                    f"[Terrain][STRIP_TRIPWIRE] DEM extent looks like a strip "
                    f"({_dem_w}x{_dem_h}, ratio={_span_ratio:.1f}). "
                    f"This produces invalid terrain. Aborting DEM import."
                )
    except ImportError:
        log_warn("[Terrain][STRIP_CHECK] PIL not available, skipping strip check")
    except RuntimeError:
        raise  # re-raise our own tripwire
    except Exception as _ex:
        log_warn(f"[Terrain][STRIP_CHECK] Could not verify DEM dimensions: {_ex}")
    
    # Step 0.5: Store all objects BEFORE imports so we can identify newly imported ones
    existing_objs = set(bpy.data.objects)
    
    # Step 1: Import RGB plane first to extract material
    try:
        log_info(f"[Terrain] BlenderGIS: importing RGB plane from {os.path.basename(rgb_tif)}...")
        _georaster_safe(
            filepath=rgb_tif,
            importMode='PLANE',
            demOnMesh=False,
            buildFaces=True,
            step=rgb_step,
            subdivision='none'
        )
        # Find the newly imported RGB object and extract its material
        newly_imported = [o for o in bpy.data.objects if o not in existing_objs and o.type == "MESH"]
        rgb_objs = newly_imported
        
        if rgb_objs:
            rgb_obj = rgb_objs[0]
            if rgb_obj.data.materials:
                rgb_mat = rgb_obj.data.materials[0]
                log_info(f"[Terrain] Extracted RGB material: {rgb_mat.name}")
            log_info(f"[Terrain] RGB plane imported: {rgb_obj.name}")
            existing_objs.add(rgb_obj)
    except Exception as e:
        log_warn(f"[Terrain] RGB plane import failed: {e}")
    
    # Step 2: Import DEM as height mesh (Requirement 3)
    try:
        import time
        
        # Get raster dimensions for diagnostic
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(dem_tif) as im:
                w, h = im.size
                pixels_mp = (int(w) * int(h)) / 1_000_000
        except Exception:
            w, h, pixels_mp = 0, 0, 0
        
        # MANDATORY FIX #2: Proof logging before DEM import
        log_info(f"[Terrain] === DEM IMPORT START ===")
        # TASK E: Performance instrumentation - proof before DEM import
        log_info(f"[Terrain] DEM raster: {w}x{h} = {pixels_mp:.1f} MP -> step={dem_step}")
        log_info(f"[Terrain] Import params: importMode=DEM_RAW (fallback DEM), demOnMesh=True, buildFaces=True, subdivision=none")
        log_info(f"[Terrain] BlenderGIS operator calling (may block for several seconds on large rasters)...")
        
        t0 = time.time()
        
        # Try DEM_RAW first (true height mesh), fallback to DEM if unavailable
        try:
            _georaster_safe(
                filepath=dem_tif,
                importMode='DEM_RAW',
                demOnMesh=True,
                buildFaces=True,
                step=dem_step,
                subdivision='none'
            )
            mode_used = 'DEM_RAW'
        except TypeError:
            # Fallback to DEM if DEM_RAW not supported
            log_info(f"[Terrain] DEM_RAW mode not available; falling back to DEM mode")
            _georaster_safe(
                filepath=dem_tif,
                importMode='DEM',
                demOnMesh=True,
                buildFaces=True,
                step=dem_step,
                subdivision='none'
            )
            mode_used = 'DEM'
        
        elapsed = time.time() - t0
        # TASK E: Performance instrumentation - proof after DEM import
        log_info(f"[Terrain] === DEM IMPORT COMPLETE ===")
        log_info(f"[Terrain] DEM operator returned in {elapsed:.2f}s using mode={mode_used}")
        if elapsed > 60:
            log_warn(f"[Terrain] ⚠ import took {elapsed:.1f}s (slow); try increasing terrain_dem_step in UI")
        else:
            log_info(f"[Terrain] ✓ import time acceptable ({elapsed:.1f}s)")
        
        # Find imported DEM mesh objects
        dem_objs = [o for o in bpy.data.objects if o not in existing_objs and o.type == "MESH"]
        
        if dem_objs:
            for dem_obj in dem_objs:
                terrain_objs.append(dem_obj)
                
                # Get Z-range for diagnostics (Requirement 5)
                if dem_obj.data.vertices:
                    z_vals = [v.co.z for v in dem_obj.data.vertices]
                    z_min, z_max = min(z_vals), max(z_vals)
                    bbox_center = [
                        (dem_obj.bound_box[0][0] + dem_obj.bound_box[6][0]) * 0.5,
                        (dem_obj.bound_box[0][1] + dem_obj.bound_box[6][1]) * 0.5,
                        (dem_obj.bound_box[0][2] + dem_obj.bound_box[6][2]) * 0.5,
                    ]
                    log_info(f"[Terrain] DEM mesh: {dem_obj.name}, Z=[{z_min:.2f}, {z_max:.2f}]m, bbox_center={bbox_center}")
                
                # FIX: Generate planar XY UVs on DEM mesh (CRITICAL for texture visibility)
                try:
                    me = dem_obj.data
                    uv_layer = me.uv_layers.get("UVMap")
                    if not uv_layer:
                        uv_layer = me.uv_layers.new(name="UVMap")
                        log_info(f"[Terrain] Created new UVMap on {dem_obj.name}")
                    
                    # Compute bounds in object space
                    xs = [v.co.x for v in me.vertices]
                    ys = [v.co.y for v in me.vertices]
                    minx = min(xs) if xs else 0
                    maxx = max(xs) if xs else 1
                    miny = min(ys) if ys else 0
                    maxy = max(ys) if ys else 1
                    dx = (maxx - minx) or 1.0
                    dy = (maxy - miny) or 1.0
                    
                    # Assign UVs per loop (face-corner, not per vertex)
                    for poly in me.polygons:
                        for li in poly.loop_indices:
                            vi = me.loops[li].vertex_index
                            v = me.vertices[vi].co
                            u = (v.x - minx) / dx
                            v_uv = (v.y - miny) / dy
                            uv_layer.data[li].uv = (u, v_uv)
                    
                    me.update()
                    log_info(f"[Terrain] Generated planar UVs on {dem_obj.name} ({len(uv_layer.data)} loop UVs)")
                except Exception as e:
                    log_warn(f"[Terrain] Failed to generate UVs on {dem_obj.name}: {e}")
                
                # Requirement 4: Apply RGB material to DEM mesh
                if rgb_mat:
                    try:
                        dem_obj.data.materials.clear()
                        dem_obj.data.materials.append(rgb_mat)
                        log_info(f"[Terrain] Applied RGB material to {dem_obj.name}")
                    except Exception as e:
                        log_warn(f"[Terrain] Failed to assign RGB material to {dem_obj.name}: {e}")
                
                # TASK D: Terrain alignment guard - apply world_to_local only if DEM was global
                if world_min_e_m is not None and world_min_n_m is not None:
                    dem_mag = (dem_obj.location.x ** 2 + dem_obj.location.y ** 2) ** 0.5
                    if dem_mag > 1e6:  # Huge = probably global coords
                        dem_obj.location.x -= world_min_e_m
                        dem_obj.location.y -= world_min_n_m
                        log_info(f"[Terrain] DEM was GLOBAL, shifted by world_min: dx={-world_min_e_m:.1f}, dy={-world_min_n_m:.1f}")
                    else:  # Already local
                        log_info(f"[Terrain] DEM already LOCAL (no shift needed); magnitude={dem_mag:.0f}m")
            
            # FIX: Align RGB to DEM (same frame, same dimensions)
            if rgb_objs and terrain_objs:
                try:
                    rgb_obj = rgb_objs[0]
                    dem_obj = terrain_objs[0]
                    
                    # Copy location, rotation, scale from DEM to RGB
                    rgb_obj.location = dem_obj.location.copy()
                    rgb_obj.rotation_euler = dem_obj.rotation_euler.copy()
                    rgb_obj.scale = dem_obj.scale.copy()
                    
                    # Match dimensions via scale factor (NOT obj.dimensions which is
                    # a derived value and unreliable when rotation/scale are non-trivial)
                    dem_dims = dem_obj.dimensions
                    rgb_dims = rgb_obj.dimensions
                    if rgb_dims.x > 1e-6 and rgb_dims.y > 1e-6 and rgb_dims.z > 1e-6:
                        rgb_obj.scale.x *= dem_dims.x / rgb_dims.x if rgb_dims.x > 1e-6 else 1.0
                        rgb_obj.scale.y *= dem_dims.y / rgb_dims.y if rgb_dims.y > 1e-6 else 1.0
                        rgb_obj.scale.z *= dem_dims.z / rgb_dims.z if rgb_dims.z > 1e-6 else 1.0
                    
                    log_info(f"[Terrain] Aligned RGB to DEM: loc={tuple(dem_obj.location)}, dim={tuple(dem_obj.dimensions)}")
                except Exception as e:
                    log_warn(f"[Terrain] Failed to align RGB to DEM: {e}")
            
            # ===== NEW: WORLD BOUNDS CALIBRATION (Deterministic scaling & positioning) =====
            # This surgical patch ensures terrain bbox matches WORLD_BOUNDS exactly.
            # Critical invariant: CityGML objects are NEVER used in this calculation.
            if terrain_objs:
                if calibrate_terrain_to_world_bounds is None or bbox_size_xy_world is None:
                    raise RuntimeError("[TerrainCal] calibrate_terrain_to_world_bounds import failed")

                dem = bpy.data.objects.get("dem_merged")
                rgb = bpy.data.objects.get("rgb_merged")

                if dem is None:
                    raise RuntimeError("[TerrainCal] dem_merged not found for calibration")

                print("[TerrainCal] PRE-CALL dem scale:", tuple(dem.scale), "bbox:", bbox_size_xy_world(dem))
                calibrate_terrain_to_world_bounds(bpy.context.scene, dem, rgb, tol_rel=0.02)
                print("[TerrainCal] POST-CALL dem scale:", tuple(dem.scale), "bbox:", bbox_size_xy_world(dem))
            
            log_info(f"[Terrain] ✓ Imported {len(dem_objs)} DEM mesh object(s)")
        else:
            log_warn("[Terrain] No DEM mesh objects found after import")
    
    except Exception as e:
        log_error(f"[Terrain] DEM mesh import failed: {e}")
        raise
    
    # Requirement 5: Print diagnostics (world_min, bbox centers, deltas)
    try:
        if world_min_e_m is not None and world_min_n_m is not None:
            log_info(f"[Terrain] === DIAGNOSTICS ===")
            log_info(f"[Terrain] World origin: min_e={world_min_e_m:.1f}m, min_n={world_min_n_m:.1f}m")
            for obj in terrain_objs:
                if obj.data.vertices:
                    bbox_center_local = [
                        sum(v.co.x for v in obj.data.vertices) / len(obj.data.vertices),
                        sum(v.co.y for v in obj.data.vertices) / len(obj.data.vertices),
                        sum(v.co.z for v in obj.data.vertices) / len(obj.data.vertices),
                    ]
                    bbox_center_global = [
                        obj.location.x + bbox_center_local[0],
                        obj.location.y + bbox_center_local[1],
                        obj.location.z + bbox_center_local[2],
                    ]
                    log_info(f"[Terrain]   {obj.name} local bbox_center: {[f'{c:.1f}' for c in bbox_center_local]}")
                    log_info(f"[Terrain]   {obj.name} global bbox_center: {[f'{c:.1f}' for c in bbox_center_global]}")
    except Exception as e:
        log_warn(f"[Terrain] Diagnostics failed: {e}")
    
    # Requirement 4: Hide RGB plane (keep material for DEM)
    if rgb_objs:
        for rgb_obj in rgb_objs:
            rgb_obj.hide_set(True)
            log_info(f"[Terrain] Hidden RGB plane: {rgb_obj.name}")
    
    # TERRAIN CACHE SAVE: If caching enabled and terrain was built, save it
    if terrain_objs and world_min_e_m is not None and world_min_n_m is not None:
        try:
            # Collect terrain objects into TERRAIN collection
            import bpy
            scene = bpy.context.scene
            terrain_coll = bpy.data.collections.get("TERRAIN")
            if not terrain_coll:
                terrain_coll = bpy.data.collections.new("TERRAIN")
                scene.collection.children.link(terrain_coll)
            
            # Add all terrain objects to collection (avoid duplicates)
            for obj in terrain_objs:
                if obj.name not in terrain_coll.objects:
                    terrain_coll.objects.link(obj)
                # Remove from default collection if present
                for coll in obj.users_collection:
                    if coll.name == scene.collection.name:
                        scene.collection.objects.unlink(obj)
            
            log_info(f"[TerrainCache] Grouped {len(terrain_objs)} objects into TERRAIN collection")
        except Exception as e:
            log_warn(f"[TerrainCache] Failed to organize terrain collection: {e}")
    
    return terrain_objs


# ============================================================================
# OPERATORS MOVED TO pipeline/operations/
# ============================================================================
# All 57 operator classes (M1DC_OT_*) have been extracted to:
# - pipeline/operations/terrain_ops.py (7 operators)
# - pipeline/operations/workflow_ops.py (3 operators)
# - pipeline/operations/citygml_ops.py (2 operators)
# - pipeline/operations/linking_ops.py (1 operator)
# - pipeline/operations/materialize_ops.py (4 operators)
# - pipeline/operations/spreadsheet_ops.py (5 operators)
# - pipeline/operations/inspector_ops.py (6 operators)
# - pipeline/operations/face_attr_ops.py (7 operators)
# - pipeline/operations/debug_ops.py (9 operators)
# - pipeline/operations/sql_ops.py (3 operators)
# - pipeline/operations/legend_ops.py (1 operator)
# - pipeline/operations/wizard_ops.py (3 operators)
# - pipeline/operations/export_log_ops.py (6 operators)
#
# ops.py now contains only:
# - Imports
# - Helper functions (used by operators in pipeline/operations/)
# - Shared utilities
# ============================================================================