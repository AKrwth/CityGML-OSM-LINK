"""
CityGML importer integration for M1_DC Pipeline.

Usage as standalone Blender script:
  - Run in Blender Text Editor: bpy.ops.exec_text_block(text=text_datablock)
  - Or as add-on module: import pipeline.citygml_import

Works with any installed CityGML importer (tries multiple common operators).
Parses tile coordinates from filenames and auto-grids imported geometry.
"""

import logging
import os
import sys
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from mathutils import Vector
from ...utils.logging_system import log_info, log_warn, log_error, is_verbose_debug, LoopProgressTracker
from ...utils.common import (
    ensure_world_origin,
    set_world_origin_from_minmax,
    get_world_origin_minmax,
    world_to_local,
    is_world_origin_locked_by_basemap,
    link_exclusively_to_collection,
    WORLD_ORIGIN_NAME,
)
from .citygml_materials import ensure_materials_for_collection
from ..diagnostics.geometry_tripwires import run_geometry_tripwires

try:
    import bpy  # type: ignore
except ModuleNotFoundError as exc:
    raise ImportError("bpy not found; run this add-on inside Blender.") from exc

try:
    from bpy.types import Operator  # type: ignore
    from bpy_extras.io_utils import ImportHelper  # type: ignore
except ImportError:
    # Fallback for minimal bpy context (e.g., headless or limited APIs)
    Operator = object
    ImportHelper = object

log = logging.getLogger(__name__)

# CityGML file extensions
CITYGML_EXTS = {".gml", ".xml", ".citygml"}

# Default tile size to fall back on when filenames omit km
DEFAULT_TILE_KM = 1

# Single source of truth for tile anchor contract (see pipeline/common.py)
from ...utils.common import TILE_ANCHOR
Y_FLIP = False

# Pattern A: ..._32288_5624_2.gml  (E_raw=32288, N_raw=5624, km=2) or dtk10_32288_5624_2_*.tif
PAT_A = re.compile(r"(?:^|[_-])(\d{5})_(\d{4,7})(?:_(\d+))?", re.IGNORECASE)

# Pattern B: LoD2_32_298_5630_1_NW.gml -> e_hi=32, e_lo=298, n=5630, km=1 (e_raw=32298)
PAT_B = re.compile(r"(?:lod\d+_)?(\d{2})_(\d{3})_(\d{4,7})_(\d+)", re.IGNORECASE)


# ----------------------------
# Helpers
# ----------------------------

def iter_citygml_paths(folder: str) -> List[Path]:
    p = Path(folder)
    if not p.is_dir():
        return []
    return [f for f in sorted(p.iterdir()) if f.is_file() and f.suffix.lower() in CITYGML_EXTS]


def kb(n_bytes: int) -> int:
    return round(n_bytes / 1024)


def parse_citygml_tile_coords(filename: str) -> Optional[Tuple[int, int, Optional[int]]]:
    """Extract E, N, and optional km tile size from CityGML filename (supports multiple patterns)."""

    # Pattern B: LoD2_32_290_5626_1_NW.gml
    m = PAT_B.search(filename)
    if m:
        utm_zone = int(m.group(1))
        e_km = int(m.group(2))
        n_km = int(m.group(3))
        tile_km = int(m.group(4))
        # Debug log for strict acceptance test
        if filename.startswith("LoD2_32_290_5626_1_NW"):
            print(f"[DEBUG] Parsed: utm_zone={utm_zone}, e_km={e_km}, n_km={n_km}, tile_km={tile_km} | e_m={e_km*1000}, n_m={n_km*1000}")
        return (e_km, n_km, tile_km)

    # Pattern A: ..._32288_5624_2.gml or ..._32288_5624.gml
    m = PAT_A.search(filename)
    if m:
        e_raw = int(m.group(1))
        n_raw = int(m.group(2))
        km_str = m.group(3)
        km_val = int(km_str) if km_str else None
        # Some datasets encode the UTM zone as a prefix in the easting token,
        # e.g. "32290" meaning zone 32 + e_km 290. Mixing this with the
        # explicit-zone pattern ("LoD2_32_290_...") would blow up bounds
        # (e.g. 32,299,000m). Normalize here so downstream code can treat
        # E/N consistently as kilometer tokens.
        #
        # Heuristic:
        #   - if e_raw is 5 digits and its leading part looks like a zone (30–39),
        #     split as zone_prefix=e_raw//1000 and e_km=e_raw%1000.
        if e_raw >= 10000:
            zone_prefix = e_raw // 1000
            e_km = e_raw % 1000
            if 30 <= zone_prefix <= 39 and 0 <= e_km <= 999:
                return (e_km, n_raw, km_val)
        return (e_raw, n_raw, km_val)

    return None


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


def most_common_value(values):
    """Return the most frequent value (mode)."""
    if not values:
        return None
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _apply_clip_big():
    try:
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D" and area.spaces:
                area.spaces[0].clip_end = 1_000_000.0
    except Exception:
        pass


def _localname(tag: str) -> str:
    """Return namespace-stripped XML tag localname."""
    try:
        if "}" in str(tag):
            return str(tag).split("}", 1)[1]
        return str(tag)
    except Exception:
        return str(tag)


def _parse_corner_text(text: Optional[str]) -> Optional[Tuple[float, ...]]:
    if not text:
        return None
    try:
        vals = [float(t) for t in str(text).strip().split()]
        if len(vals) >= 2:
            return tuple(vals)
    except Exception:
        return None
    return None


def read_citygml_tile_metadata(path: Path) -> Dict[str, Any]:
    """Read lightweight per-tile metadata (name + lowerCorner) from the same source document."""
    metadata_name = path.stem
    lower_corner = None
    source_doc = path.name
    try:
        root_seen = False
        for event, elem in ET.iterparse(str(path), events=("start", "end")):
            lname = _localname(elem.tag)

            if event == "start" and not root_seen:
                root_seen = True
                for k, v in elem.attrib.items():
                    if _localname(k) == "id" and v:
                        metadata_name = str(v).strip()
                        break

            if event != "end":
                continue

            if lower_corner is None and lname == "lowerCorner":
                lower_corner = _parse_corner_text(elem.text)
            elif lname == "name":
                txt = (elem.text or "").strip()
                if txt:
                    metadata_name = txt

            if lower_corner is not None and metadata_name:
                break

            elem.clear()
    except Exception as ex:
        log_warn(f"[CityGML] metadata parse fallback for {path.name}: {ex}")

    return {
        "metadata_name": metadata_name,
        "lower_corner": lower_corner,
        "source_doc": source_doc,
    }


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


def _frame_imported_objects(collection):
    """Log import distances; do not recenter when WORLD_ORIGIN governs coordinates."""
    scene = bpy.context.scene

    meshes = [o for o in collection.objects if o.type == "MESH"]
    if not meshes:
        log.warning("[M1_DC_V6] No mesh objects found after import; skipping frame")
        return

    try:
        distances = sorted(
            ((o.location.length, o.name, o.location[:]) for o in meshes),
            reverse=True,
        )
        for d, name, loc in distances[:5]:
            log.info(
                "[M1_DC_V6] Mesh %.1f m from local origin: %s at (%.1f, %.1f, %.1f)",
                d,
                name,
                loc[0],
                loc[1],
                loc[2],
            )
    except Exception:
        pass

    try:
        if meshes:
            scene.view_layers[0].objects.active = meshes[0]
            meshes[0].select_set(True)
            for area in scene.screen.areas if scene.screen else []:
                if area.type == "VIEW_3D":
                    for region in area.regions:
                        if region.type == "WINDOW":
                            override = {"area": area, "region": region}
                            bpy.ops.view3d.view_selected(override)
                            break
            log.info("[M1_DC_V6] Framed %d meshes in viewport", len(meshes))
    except Exception as ex:
        log.debug("[M1_DC_V6] Could not frame viewport: %s", ex)


# ----------------------------
# Building aggregation
# ----------------------------


def _ensure_gml_building_idx(obj: bpy.types.Object) -> None:
    """Ensure FACE/INT attribute gml_building_idx exists.

    Many CityGML importers create building indices under different names.
    We normalize to gml_building_idx so downstream matching/materialization
    can use a stable key.
    """
    try:
        if obj is None or obj.type != "MESH" or not obj.data:
            return
        mesh = obj.data
        if not hasattr(mesh, "attributes"):
            return
        face_count = len(getattr(mesh, "polygons", []) or [])
        if face_count <= 0:
            return

        dst = mesh.attributes.get("gml_building_idx")
        if dst and dst.domain == "FACE" and dst.data_type == "INT" and len(dst.data) == face_count:
            return

        src = mesh.attributes.get("gml__building_idx") or mesh.attributes.get("building_idx")
        if not src or src.domain != "FACE" or src.data_type != "INT" or len(src.data) != face_count:
            return

        if dst is not None:
            try:
                mesh.attributes.remove(dst)
            except Exception:
                pass

        dst = mesh.attributes.new("gml_building_idx", "INT", "FACE")
        buf = [0] * face_count
        src.data.foreach_get("value", buf)
        dst.data.foreach_set("value", buf)

        # Optional: also restore/overwrite building_idx to match if it exists but is invalid.
        b = mesh.attributes.get("building_idx")
        if b and (b.domain != "FACE" or b.data_type != "INT" or len(b.data) != face_count):
            try:
                mesh.attributes.remove(b)
            except Exception:
                pass
            b = None
        if b is None:
            try:
                b = mesh.attributes.new("building_idx", "INT", "FACE")
            except Exception:
                b = None
        if b is not None and b.domain == "FACE" and b.data_type == "INT" and len(b.data) == face_count:
            b.data.foreach_set("value", buf)

        try:
            mesh.update()
        except Exception:
            pass
    except Exception:
        return


def iter_citygml_buildings(obj: bpy.types.Object) -> Dict[Tuple[str, int], Dict[str, object]]:
    """Aggregate buildings from a CityGML tile mesh via face attribute 'gml_building_idx'.

    Returns mapping {(source_tile, building_idx): {faces, centroid_xy, bbox_xy}}.
    # (source_tile, building_idx) stays stable inside a tile even when meshes are not split.
    """
    if obj.type != "MESH" or not obj.data:
        return {}
    mesh = obj.data
    attr = None
    if hasattr(mesh, "attributes"):
        attr = mesh.attributes.get("gml_building_idx") or mesh.attributes.get("gml__building_idx")
        if attr is None:
            attr = mesh.attributes.get("building_idx")
        if attr and attr.domain != "FACE":
            attr = None
    if attr is None:
        return {}

    # Build per-face building index list
    face_indices = []
    try:
        for i, poly in enumerate(mesh.polygons):
            try:
                bidx = attr.data[i].value
            except Exception:
                bidx = None
            if bidx is None:
                continue
            face_indices.append((i, int(bidx), poly))
    except Exception:
        return {}

    if not face_indices:
        return {}

    result: Dict[Tuple[str, int], Dict[str, object]] = {}
    src = obj.get("source_tile", obj.name)

    def world_vert(idx):
        v = mesh.vertices[idx]
        return obj.matrix_world @ v.co

    for face_idx, bidx, poly in face_indices:
        key = (src, bidx)
        entry = result.get(key)
        if entry is None:
            entry = {
                "faces": [],
                "centroid_sum": [0.0, 0.0],
                "centroid_count": 0,
                "bbox": [float("inf"), float("inf"), float("-inf"), float("-inf")],
            }
            result[key] = entry

        # Face centroid in world XY
        verts_world = [world_vert(vidx) for vidx in poly.vertices]
        cx = sum(v.x for v in verts_world) / len(verts_world)
        cy = sum(v.y for v in verts_world) / len(verts_world)
        entry["centroid_sum"][0] += cx
        entry["centroid_sum"][1] += cy
        entry["centroid_count"] += 1

        # BBox update
        for v in verts_world:
            entry["bbox"][0] = min(entry["bbox"][0], v.x)
            entry["bbox"][1] = min(entry["bbox"][1], v.y)
            entry["bbox"][2] = max(entry["bbox"][2], v.x)
            entry["bbox"][3] = max(entry["bbox"][3], v.y)

        entry["faces"].append(face_idx)

    # Finalize centroid/bbox
    for key, entry in list(result.items()):
        cnt = max(1, entry.pop("centroid_count", 1))
        cx = entry.pop("centroid_sum", [0.0, 0.0])[0] / cnt
        cy = entry.pop("centroid_sum", [0.0, 0.0])[1] / cnt
        bbox = entry["bbox"]
        if bbox[0] == float("inf"):
            bbox = None
        entry["centroid_xy"] = (cx, cy)
        entry["bbox_xy"] = bbox

    return result


# ----------------------------
# Public API
# ----------------------------

def citygml_sanity(folder: str) -> str:
    if not folder or not os.path.isdir(folder):
        return "CityGML: missing or not a folder"

    files = iter_citygml_paths(folder)
    if not files:
        return "CityGML: no .gml/.xml/.citygml files found"

    sizes = [f.stat().st_size for f in files]
    return (
        f"CityGML: OK ({len(files)} files; total={kb(sum(sizes))} KB; "
        f"smallest={kb(min(sizes))} KB; largest={kb(max(sizes))} KB)"
    )


def import_citygml_folder(
    folder: str,
    origin: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
    recalc_clip: bool = True,
    collection=None,
    sort_by_tiles: bool = True,
    tile_size_m: float = 1000.0,
    clamp_to_ground: bool = True,
) -> Tuple[bool, str]:
    """
    Import CityGML files using installed Blender operator.
    
    Uses before/after object detection (reliable, works with any importer).
    Optionally sorts by tile coordinates from filename.
    
    Args:
        folder: Path to folder with .gml/.xml/.citygml files
        origin: (unused, kept for API compat)
        scale: (unused, kept for API compat)
        recalc_clip: Adjust viewport clip_end if needed
        collection: Use this collection, or create CITYGML_IMPORT
        sort_by_tiles: Re-position objects based on filename tile coords
        tile_size_m: Assume this tile size (1000 m = 1 km, 2000 m = 2 km, etc.)
        clamp_to_ground: If True, shift imported tiles so bbox min Z rests on 0 after XY alignment
    """

    files = iter_citygml_paths(folder)
    if not files:
        return False, f"CityGML folder empty: {folder}", 0, 0

    # Create or use provided collection
    if collection is None:
        collection = ensure_collection("CITYGML_IMPORT")
    
    # Create origin empty for sorting (if needed)
    origin_empty = None
    if sort_by_tiles:
        world_shared = ensure_world_origin()
        if world_shared:
            origin_empty = world_shared
        else:
            origin_empty = ensure_empty("CITYGML_ORIGIN")
    
    scene = bpy.context.scene
    old_lock = scene.render.use_lock_interface
    scene.render.use_lock_interface = True

    world_min_e0, world_min_n0, world_max_e0, world_max_n0 = get_world_origin_minmax()
    origin_missing = False
    if world_min_e0 is not None and world_min_n0 is not None:
        log_info(
            f"[CityGML] WORLD_ORIGIN reused: min=({world_min_e0},{world_min_n0}) max=({world_max_e0},{world_max_n0})"
        )
    else:
        log_warn("[CityGML] WORLD_ORIGIN not locked. Will import and lock from geometry bbox.")
        origin_missing = True
        sort_by_tiles = False
    
    imported_objects = 0
    tile_coords = {}  # {filename: (e, n, km_or_none)}
    tile_objects: Dict[str, List[bpy.types.Object]] = {}
    tile_contexts: Dict[str, Dict[str, Any]] = {}
    proof_logged_tiles = set()
    legacy_operator_notice_logged = False
    
    try:
        # Preflight: show available citygml import ops in import_scene
        try:
            available_citygml_ops = [op for op in dir(bpy.ops.import_scene) if "citygml" in op.lower()]
            log.info("[M1_DC_V6] import_scene citygml ops: %s", available_citygml_ops)
        except Exception as ex:
            log.debug("[M1_DC_V6] Could not list import_scene ops: %s", ex)

        preferred_ops = [
            (bpy.ops.import_scene, "citygml_lod1_single_mesh"),
            (bpy.ops.import_scene, "citygml_lod2_single_mesh"),
            (bpy.ops.import_scene, "citygml_import"),
            (bpy.ops.citygml, "import_citygml"),
            (bpy.ops.import_scene, "citygml"),
            (bpy.ops.wm, "citygml_open"),
        ]

        # [PHASE 13] Progress tracker for "3 examples + progress + summary"
        progress = LoopProgressTracker("CityGML Import", total_items=len(files), progress_interval=10)

        for idx, f in enumerate(files):
            before = set(bpy.data.objects)

            imported_ok = False
            last_error = None

            for mod, op_name in preferred_ops:
                op_callable = getattr(mod, op_name, None)
                if op_callable is None:
                    continue
                try:
                    if (not legacy_operator_notice_logged) and op_name == "citygml_lod1_single_mesh":
                        log_info("[CityGML] Operator name is legacy; processing LoD2 tiles from input documents when present.")
                        legacy_operator_notice_logged = True
                    op_callable(filepath=str(f))
                    imported_ok = True
                    # [PHASE 13] Suppress repetitive import logs after first 3
                    if progress.should_log_detail(idx):
                        log.info("[M1_DC_V6] Imported %s via %s.%s", f.name, mod.__name__, op_name)
                    break
                except Exception as ex:
                    last_error = ex
                    log.debug("[M1_DC_V6] Operator %s.%s failed for %s: %s", mod.__name__, op_name, f.name, ex)

            if not imported_ok:
                # [PHASE 13] Keep warnings visible (errors should always show)
                log.warning("[M1_DC_V6] No CityGML importer succeeded for %s (last error: %s)", f.name, last_error)
                continue

            after = set(bpy.data.objects)
            new_objs = list(after - before)

            tile_meta = read_citygml_tile_metadata(f)
            tile_contexts[f.name] = {
                "tile_filename": f.name,
                "metadata_name": tile_meta.get("metadata_name", f.stem),
                "lower_corner": tile_meta.get("lower_corner"),
                "source_doc": tile_meta.get("source_doc", f.name),
            }

            # Tag and organize new objects
            for obj in new_objs:
                # Normalize to stable key used by DB/materialize lookups
                try:
                    from pathlib import Path
                    obj["source_tile"] = Path(str(f.name)).stem
                except Exception:
                    obj["source_tile"] = str(f.name)
                obj["m1dc_tile_filename"] = str(f.name)
                obj["m1dc_metadata_name"] = str(tile_contexts[f.name]["metadata_name"])
                obj["m1dc_lower_corner"] = str(tile_contexts[f.name]["lower_corner"])
                obj["m1dc_source_doc"] = str(tile_contexts[f.name]["source_doc"])
                link_exclusively_to_collection(obj, collection)
                obj.rotation_euler = (0.0, 0.0, 0.0)
                obj.scale = (1.0, 1.0, 1.0)

                # Normalize building index attribute name for downstream tools.
                _ensure_gml_building_idx(obj)
                
                # Log validation info (optional: catch common import issues)
                # [PHASE 13] Only log detail for first 3 tiles
                if progress.should_log_detail(idx):
                    try:
                        from .validation import log_tile_import_summary
                        log_tile_import_summary(f.name, obj, tile_size_m)
                    except Exception:
                        pass  # Validation logging is optional; don't block import

            imported_objects += len(new_objs)
            
            # Parse tile coordinates for optional sorting
            coords = parse_citygml_tile_coords(f.name)
            if coords:
                tile_coords[f.name] = coords
                tile_objects[f.name] = new_objs
                e, n, km_val = coords
                # [PHASE 13] Show progress every 10 tiles after first 3
                if progress.should_log_detail(idx):
                    if km_val:
                        log.info("[M1_DC_V6] Tile %s → E=%d, N=%d, km=%d", f.name, e, n, km_val)
                    else:
                        log.info("[M1_DC_V6] Tile %s → E=%d, N=%d", f.name, e, n)
                elif progress.should_log_progress(idx):
                    print(f"[CityGML] progress: tile {idx+1}/{len(files)} | objects={imported_objects}")
        
        # [PHASE 13] Always print summary
        print(f"[CityGML] SUMMARY: tiles={len(files)} | objects={imported_objects}")
        # Optional: sort imported objects by tile grid using source_tile tag
        if sort_by_tiles and tile_coords and origin_empty:
            e_vals = [c[0] for c in tile_coords.values()]
            n_vals = [c[1] for c in tile_coords.values()]
            km_vals = [c[2] for c in tile_coords.values() if c[2] is not None]
            unique_km = sorted(set(km_vals))
            if len(unique_km) > 1:
                log.warning("[M1_DC_V6] Mixed tile sizes detected in CityGML: %s", unique_km)

            # === CRITICAL DIAGNOSTIC: Tile coordinate parsing ===
            log_info(f"[CityGML] ╔═ PARSER DIAGNOSTICS ({len(tile_coords)} tiles) ═╗")
            log_info(f"[CityGML] ║ E-values: min={min(e_vals) if e_vals else '?'}, max={max(e_vals) if e_vals else '?'}, unique={len(set(e_vals))}")
            log_info(f"[CityGML] ║ N-values: min={min(n_vals) if n_vals else '?'}, max={max(n_vals) if n_vals else '?'}, unique={len(set(n_vals))}")
            log_info(f"[CityGML] ║ Km-values: {sorted(set(km_vals)) if km_vals else 'none'}")
            
            # If all N are the same → PARSER BUG (all tiles on one line)
            if len(set(n_vals)) == 1:
                log_warn(f"[CityGML] ⚠️  CRITICAL: All N-values are IDENTICAL ({n_vals[0]})!")
                log_warn(f"[CityGML]    This means the parser is not correctly extracting northing.")
                log_warn(f"[CityGML]    Check: (1) tile filename pattern, (2) PAT_A/PAT_B regex, (3) group extraction")
                log_warn(f"[CityGML]    Sample files: {list(tile_coords.keys())[:3]}")
            
            # If all E are the same → similar issue
            if len(set(e_vals)) == 1:
                log_warn(f"[CityGML] ⚠️  WARNING: All E-values are IDENTICAL ({e_vals[0]})!")
                log_warn(f"[CityGML]    Tiles may stack in a vertical line instead of grid.")
            
            log_info(f"[CityGML] ╚═══════════════════════════════════╝")

            # Prefer BaseMap contract if available
            world = ensure_world_origin()
            tile_anchor = str(TILE_ANCHOR).upper()
            contract_e_mult = contract_n_mult = contract_tile_size = None
            if world:
                contract_e_mult = world.get("grid_e_mult")
                contract_n_mult = world.get("grid_n_mult")
                contract_tile_size = world.get("tile_size_m_ref")
                contract_anchor = world.get("tile_anchor")
                if contract_anchor:
                    tile_anchor = str(contract_anchor).upper()

            use_contract = all(v is not None for v in (contract_e_mult, contract_n_mult, contract_tile_size))
            delta_raw_e = None
            delta_raw_n = None
            if use_contract:
                tile_size_m_ref = float(contract_tile_size)
                e_mult = float(contract_e_mult)
                n_mult = float(contract_n_mult)
                log_info(
                    f"[CityGML] using BaseMap contract: e_mult={e_mult}, n_mult={n_mult}, tile_size_m_ref={tile_size_m_ref}, anchor={tile_anchor}"
                )
            else:
                log_info("[CityGML] no BaseMap contract found; inferring grid from CityGML filenames.")
                tile_size_m_ref = tile_size_m
                km_mode = most_common_value(km_vals) if km_vals else None
                if km_mode:
                    tile_size_m_ref = km_mode * 1000.0
                else:
                    tile_size_m_ref = max(tile_size_m_ref, DEFAULT_TILE_KM * 1000.0)

                e_mult = n_mult = None
                if len(set(e_vals)) > 1 and len(set(n_vals)) > 1:
                    e_step = most_common_positive_step(e_vals)
                    n_step = most_common_positive_step(n_vals)
                    if e_step and n_step:
                        delta_raw_e = e_step
                        delta_raw_n = n_step
                        e_mult = tile_size_m_ref / float(e_step)
                        n_mult = tile_size_m_ref / float(n_step)
                        log.info(
                            "[M1_DC_V6] Grid: ΔE=%d, ΔN=%d → e_mult=%.1f, n_mult=%.1f (tile_size_ref=%dm)",
                            e_step, n_step, e_mult, n_mult, int(tile_size_m_ref),
                        )
                        # PROOF LOG per user spec
                        log_info(f"[TILES] inferred ΔE_raw_mode={e_step} → e_mult={e_mult:.0f}")
                        log_info(f"[TILES] inferred ΔN_raw_mode={n_step} → n_mult={n_mult:.0f}")

            if e_mult is None or n_mult is None:
                log_warn("[CityGML] Could not determine grid multipliers; skipping tile alignment.")
            else:
                world_min_e, world_min_n, world_max_e, world_max_n = get_world_origin_minmax()
                
                # === CRITICAL DIAGNOSTIC: World origin validity ===
                if world_min_e is None or world_min_n is None:
                    log_warn("[CityGML] ⚠️  CRITICAL: world_min_* values are NONE!")
                    log_warn("[CityGML]    This means M1DC_WORLD_ORIGIN was not properly initialized.")
                    log_warn("[CityGML]    Expected source: basemap.json imported via import_basemap_obj_artifact()")
                    log_warn("[CityGML]    Result: Tiles will likely stack on one line (all delta_y ≈ 0)")
                else:
                    log_info(
                        f"[CityGML] ✓ WORLD_ORIGIN valid: min=({world_min_e},{world_min_n}) max=({world_max_e},{world_max_n})"
                    )

                if world:
                    try:
                        world.setdefault("grid_e_mult", float(e_mult))
                        world.setdefault("grid_n_mult", float(n_mult))
                        world.setdefault("tile_size_m_ref", float(tile_size_m_ref))
                        if world.get("tile_size_m") in (None, 0, 0.0, ""):
                            world["tile_size_m"] = float(tile_size_m_ref)
                        world.setdefault("tile_anchor", str(tile_anchor).upper())
                        world.setdefault("y_flip", False)
                    except Exception:
                        pass

                if origin_empty is not world and origin_empty.parent is None:
                    origin_empty.parent = world

                origin_empty.rotation_euler = (0.0, 0.0, 0.0)
                origin_empty.scale = (1.0, 1.0, 1.0)

                axis_logged = False
                DEBUG_TILE_DIAG = True
                diag_desired_x = []
                diag_desired_y = []



                # --- PATCH: Unique per-tile placement and debug log for first 5 tiles ---
                debug_tile_count = 0
                for source, coords in tile_coords.items():
                    objs = tile_objects.get(source)
                    if not objs:
                        # fallback: collection scan
                        objs = [o for o in collection.objects if o.get("source_tile") == source]
                    if not objs:
                        continue

                    e, n, km_val = coords
                    tile_size_local = (km_val * 1000.0) if km_val else tile_size_m_ref

                    tile_easting_m = e * e_mult
                    tile_northing_m = n * n_mult

                    anchor_offset = tile_size_local * 0.5 if str(tile_anchor).upper() == "CORNER" else 0.0
                    if str(tile_anchor).upper() == "CORNER":
                        tile_easting_m += anchor_offset
                        tile_northing_m += anchor_offset

                    # === PHASE 1 FIX: NO CENTERING, NO SCALING - Pure translation only ===
                    # CityGML tiles must tile seamlessly via object.location only.
                    # REMOVED: Vertex centering (was causing drift between tiles)
                    # ENFORCED: scale=(1,1,1), rotation=(0,0,0), only location varies
                    for obj in objs:
                        # Enforce identity transforms (NO SCALING, NO ROTATION)
                        obj.rotation_euler = (0.0, 0.0, 0.0)
                        obj.scale = (1.0, 1.0, 1.0)

                        # Parent to origin empty (which is at world origin)
                        obj.parent = origin_empty
                        try:
                            obj.matrix_parent_inverse.identity()
                        except Exception:
                            pass

                        # REMOVED: Vertex centering logic
                        # Vertices remain at importer-supplied positions (no normalization)
                        # This ensures tiles align exactly via location offset alone

                    debug_tile_count += 1

                    # Optional ground clamp (only touches Z)
                    z_offset_applied = 0.0
                    z_offset_reason = "clamp_to_ground disabled"
                    if clamp_to_ground:
                        min_z = float("inf")
                        for obj in objs:
                            for corner in obj.bound_box:
                                wco = obj.matrix_world @ Vector(corner)
                                min_z = min(min_z, wco.z)
                        if min_z != float("inf"):
                            z_offset_applied = float(-min_z)
                            z_offset_reason = "align tile minZ to local ground (clamp_to_ground)"
                            for obj in objs:
                                obj.location.z -= min_z
                        else:
                            z_offset_reason = "clamp_to_ground requested but minZ unavailable"

                    # === FINAL XY ASSIGNMENT: Enforce canonical local coordinate mapping ===
                    # This is the LAST write to obj.location.x/y - nothing must modify it afterward
                    # Read world origin from scene properties (authoritative source)
                    scene = bpy.context.scene
                    WME = float(scene.get("M1DC_WORLD_MIN_E", 0.0))
                    WMN = float(scene.get("M1DC_WORLD_MIN_N", 0.0))

                    # Apply canonical mapping: local = world - world_min
                    local_x = tile_easting_m - WME
                    local_y = tile_northing_m - WMN

                    # Track for final diagnostic
                    diag_desired_x.append(local_x)
                    diag_desired_y.append(local_y)

                    # Set location for all objects in this tile (FINAL ASSIGNMENT)
                    for obj in objs:
                        obj.location.x = local_x
                        obj.location.y = local_y
                        # obj.location.z unchanged (ground clamp already applied)

                        obj["m1dc_z_offset_applied"] = float(z_offset_applied)
                        obj["m1dc_z_offset_reason"] = str(z_offset_reason)

                        # === PFLICHT-LOG: Verify scale=(1,1,1) and location ===
                        if is_verbose_debug():
                            print(f"[GML] tile={source} obj={obj.name} loc={tuple(obj.location)} scale={tuple(obj.scale)}")

                    tile_ctx = tile_contexts.get(source, {
                        "tile_filename": source,
                        "metadata_name": source,
                        "lower_corner": None,
                        "source_doc": source,
                    })
                    if source not in proof_logged_tiles:
                        log_info(
                            "[CityGML][TileProof] "
                            f"tile_filename={tile_ctx.get('tile_filename')} | "
                            f"metadata.name={tile_ctx.get('metadata_name')} | "
                            f"lower_corner={tile_ctx.get('lower_corner')} | "
                            f"source_doc={tile_ctx.get('source_doc')}"
                        )
                        log_info(
                            "[CityGML][TileProof] "
                            f"tile_filename={tile_ctx.get('tile_filename')} | "
                            f"z_offset_applied={z_offset_applied:.3f} | "
                            f"z_offset_reason={z_offset_reason}"
                        )
                        proof_logged_tiles.add(source)

                    # Diagnostic for first 3 tiles
                    if debug_tile_count - 1 < 3:
                        sample_obj = objs[0] if objs else None
                        if sample_obj:
                            log_info(f"[CityGML][XY-ALIGN-FINAL] Tile: {source}")
                            log_info(f"[CityGML][XY-ALIGN-FINAL]   World origin: WME={WME:.2f}, WMN={WMN:.2f}")
                            log_info(f"[CityGML][XY-ALIGN-FINAL]   Tile world: e={tile_easting_m:.2f}, n={tile_northing_m:.2f}")
                            log_info(f"[CityGML][XY-ALIGN-FINAL]   FINAL location: ({local_x:.2f}, {local_y:.2f})")

                            # Sanity check
                            if abs(local_x) > 20000 or abs(local_y) > 20000:
                                log_warn(f"[CityGML][XY-ALIGN-FINAL]   ⚠️  Location out of range!")
                            else:
                                log_info(f"[CityGML][XY-ALIGN-FINAL]   ✓ Location in valid range")

                    if not axis_logged:
                        log_info(
                            f"[CityGML] axis-check: E->X, N->Y, anchor={tile_anchor}, y_flip={Y_FLIP}"
                        )
                        axis_logged = True

                if DEBUG_TILE_DIAG:
                    try:
                        unique_x = sorted(set(diag_desired_x))
                        unique_y = sorted(set(diag_desired_y))

                        def _min_max_spacing(vals):
                            if len(vals) < 2:
                                return None, None
                            diffs = [b - a for a, b in zip(vals, vals[1:]) if (b - a) != 0]
                            if not diffs:
                                return None, None
                            return min(diffs), max(diffs)

                        min_dx, max_dx = _min_max_spacing(unique_x)
                        min_dy, max_dy = _min_max_spacing(unique_y)
                        log_info(
                            f"[CityGML][Diag] desired_x unique={len(unique_x)} desired_y unique={len(unique_y)} "
                            f"spacing_x(min,max)=({min_dx},{max_dx}) spacing_y(min,max)=({min_dy},{max_dy})"
                        )
                    except Exception:
                        pass

                origin_empty["min_easting"] = float(world_min_e)
                origin_empty["min_northing"] = float(world_min_n)
                origin_empty["tile_size_m"] = float(tile_size_m_ref)

        # Proof fallback for tiles that were imported but not aligned/sorted in this pass.
        for source, tile_ctx in tile_contexts.items():
            if source in proof_logged_tiles:
                continue
            log_info(
                "[CityGML][TileProof] "
                f"tile_filename={tile_ctx.get('tile_filename')} | "
                f"metadata.name={tile_ctx.get('metadata_name')} | "
                f"lower_corner={tile_ctx.get('lower_corner')} | "
                f"source_doc={tile_ctx.get('source_doc')}"
            )
            log_info(
                "[CityGML][TileProof] "
                f"tile_filename={tile_ctx.get('tile_filename')} | "
                "z_offset_applied=0.000 | "
                "z_offset_reason=tile alignment not executed (missing tile coords or sorting disabled)"
            )
            proof_logged_tiles.add(source)
        
        if imported_objects and recalc_clip:
            _apply_clip_big()
        
        # Automatically frame and diagnose imported geometry
        if imported_objects > 0:
            _frame_imported_objects(collection)
        
        # Ensure all CityGML objects have readable default materials
        if imported_objects > 0:
            try:
                mat_stats = ensure_materials_for_collection(collection.name)
                log_info(f"[CityGML] Materials: assigned={mat_stats['assigned']} skipped={mat_stats['skipped']}")
            except Exception as ex:
                log_warn(f"[CityGML] Failed to apply default materials: {ex}")
        
        if origin_missing and imported_objects > 0:
            try:
                min_x = float("inf")
                min_y = float("inf")
                max_x = float("-inf")
                max_y = float("-inf")
                for obj in collection.objects:
                    if obj.type != "MESH":
                        continue
                    for corner in obj.bound_box:
                        v = obj.matrix_world @ Vector(corner)
                        min_x = min(min_x, v.x)
                        min_y = min(min_y, v.y)
                        max_x = max(max_x, v.x)
                        max_y = max(max_y, v.y)

                if max_x < 1_000_000 or max_y < 1_000_000:
                    raise RuntimeError(
                        "WORLD_ORIGIN inference from CityGML geometry produced non-meter values. Refusing to lock."
                    )

                ensure_world_origin(
                    min_e=min_x,
                    min_n=min_y,
                    max_e=max_x,
                    max_n=max_y,
                    source="CityGML_GeometryBBox",
                    crs="EPSG:25832",
                )
                log_info(
                    f"[CityGML] WORLD_ORIGIN locked from geometry bbox: min=({min_x:.0f},{min_y:.0f}) max=({max_x:.0f},{max_y:.0f})"
                )
            except Exception as ex:
                log_error(f"[CityGML] Failed to lock WORLD_ORIGIN from geometry bbox: {ex}")
                raise

        tile_count = len(files)
        msg = f"Imported {imported_objects} objects from {tile_count} CityGML tiles."
        if sort_by_tiles and tile_coords:
            msg += f" (Sorted by {len(tile_coords)} detected tile coords)"

        return imported_objects > 0, msg, tile_count, imported_objects

    except Exception as ex:
        log_error(f"[CityGML] Import failed: {ex}")
        return False, f"CityGML import failed: {ex}", 0, 0

    finally:
        scene.render.use_lock_interface = old_lock



class M1_DC_V6_OT_ImportCityGML(Operator, ImportHelper):
    """Import CityGML (LoD1) tiles into a single collection."""

    bl_idname = "m1_dc_v6.import_citygml"
    bl_label = "Import CityGML LoD1 (M1_DC_V6)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".gml"
    filter_glob: str = "*.gml;*.xml;*.citygml"  # type: ignore

    def execute(self, context):
        folder = self.directory or os.path.dirname(self.filepath)
        folder = folder or ""

        log_info("[CityGML] Legacy operator label active (LoD1 name); importer processes LoD2 tiles from input data when available.")

        if not folder or not os.path.isdir(folder):
            self.report({"ERROR"}, "Select a folder containing CityGML tiles.")
            return {"CANCELLED"}

        ok, msg, _, _ = import_citygml_folder(folder)

        # PHASE 5: Geometry tripwires (post-import checks)
        if ok:
            try:
                run_geometry_tripwires()
            except RuntimeError as tripwire_error:
                self.report({"ERROR"}, f"Geometry check failed: {tripwire_error}")
                return {"CANCELLED"}

        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED" if ok else "CANCELLED"}


# Optional: keep menu hook for File > Import
def menu_func_import(self, context):
    self.layout.operator(M1_DC_V6_OT_ImportCityGML.bl_idname, text="CityGML (LoD1) [M1_DC_V6]")
