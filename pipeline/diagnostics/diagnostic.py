"""
M1DC diagnostic export helper.
Creates human-readable reports in the configured output directory.
"""

import datetime
import sqlite3
import traceback
from pathlib import Path
from ...utils.logging_system import log_info, log_warn, log_error
from ...utils.common import get_output_dir, get_world_origin_minmax, WORLD_ORIGIN_NAME

try:
    import bpy  # type: ignore
except ModuleNotFoundError as exc:  # pragma: no cover - requires Blender
    raise ImportError("bpy not found; run this add-on inside Blender.") from exc

DEFAULT_EXPORT_PATH = None  # resolved at runtime via get_output_dir()
DEFAULT_EXPORT_PATH_DEBUG = None
DEFAULT_EXPORT_PATH_FULL = None
DEFAULT_EXPORT_PATH_LINK = None  # resolved at runtime


def _safe_value(v):
    if isinstance(v, (int, float, str, bool, type(None))):
        return v
    if isinstance(v, (list, tuple)):
        return [_safe_value(x) for x in v]
    return str(v)


def _safe_obj_keys(o):
    ks = []
    for k in o.keys():
        if k in {"_RNA_UI"}:
            continue
        v = o.get(k)
        t = type(v).__name__
        if t in {"int", "float", "str", "bool"}:
            ks.append((k, v))
        else:
            ks.append((k, f"<{t}>"))
    return ks


def _safe_items(idblock):
    out = {}
    for k in idblock.keys():
        v = idblock.get(k)
        if k in {"_RNA_UI"}:
            continue
        if hasattr(v, "bl_rna") or str(type(v)).endswith("IDPropertyGroup'>"):
            out[k] = f"<{type(v).__name__}>"
        else:
            try:
                out[k] = v
            except Exception:
                out[k] = f"<unprintable {type(v).__name__}>"
    return out


def _report_write(path: Path, lines):
    path.write_text("\n".join(lines), encoding="utf-8")


def _gpkg_open_ro(path: str):
    from ...utils.common import resolve_gpkg_path, log_gpkg_resolution

    resolved, info = resolve_gpkg_path(path)
    log_gpkg_resolution(path, resolved, info, prefix="[Diagnostic][GPKG]")
    if not resolved:
        raise FileNotFoundError(info)

    # Use centralized readonly DB access
    try:
        from ...utils.common import open_db_readonly
        con = open_db_readonly(resolved, log_open=True)
    except ImportError:
        # Fallback if API import fails
        from pathlib import Path
        uri = f"file:{Path(resolved).as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.execute("PRAGMA busy_timeout=5000;")
        con.execute("PRAGMA query_only=ON;")
    return con


def run_diagnostic(gpkg_path: str = "", out_path: Path | None = None) -> Path:
    """Collects scene, origin, and GPKG metadata and writes a text report.

    Args:
        gpkg_path: Optional GeoPackage path to inspect.
        out_path: Optional override for export path. Defaults to output_dir/M1_DC_Export.txt.
    Returns:
        Path of the written report.
    """
    target = out_path or (get_output_dir() / "M1_DC_Export.txt")
    lines: list[str] = []

    def log(msg: str = ""):
        log_info(msg)
        lines.append(str(msg))

    log("=== M1DC GPKG ↔ CityGML DIAGNOSTIC REPORT ===")
    log(datetime.datetime.now().isoformat())
    log("")

    # 1) Scene overview
    log("=== SCENE OVERVIEW ===")
    log(f"Total objects: {len(bpy.data.objects)}")
    log(f"Mesh objects: {len([o for o in bpy.data.objects if o.type == 'MESH'])}")
    log(f"Collections: {[c.name for c in bpy.data.collections]}")
    out_dir = get_output_dir()
    log(f"Output dir: {out_dir}")
    log("")

    # 2) Suspect keys
    log("=== SUSPECT CUSTOM PROPERTIES ===")
    suspect_keys = set()
    for o in bpy.data.objects:
        for k in o.keys():
            kl = k.lower()
            if "osm" in kl or "gpkg" in kl or "source" in kl:
                suspect_keys.add(k)
    log(f"Detected keys: {sorted(suspect_keys)}")
    log("")

    # 3) CityGML objects
    log("=== CITYGML OBJECTS ===")
    citygml = [
        o
        for o in bpy.data.objects
        if o.type == "MESH" and ("lod" in o.name.lower() or "gml" in o.name.lower())
    ]
    log(f"CityGML mesh count: {len(citygml)}")
    if citygml:
        o = citygml[0]
        log(f"Sample CityGML object: {o.name}")
        log("Properties:")
        for k in o.keys():
            log(f"  {k}: {_safe_value(o.get(k))}")
    log("")

    # 4) GPKG / OSM-linked objects
    log("=== GPKG / OSM OBJECTS ===")
    osm_objs = [o for o in bpy.data.objects if "osm_id" in o.keys()]
    log(f"Objects with 'osm_id': {len(osm_objs)}")
    if osm_objs:
        o = osm_objs[0]
        log(f"Sample OSM object: {o.name}")
        for k in o.keys():
            log(f"  {k}: {_safe_value(o.get(k))}")
    log("")

    # 5) WORLD ORIGIN / ANCHORS
    log("=== WORLD ORIGINS ===")
    origins = [o for o in bpy.data.objects if "ORIGIN" in o.name.upper()]
    log(f"Origin objects found: {len(origins)}")
    min_e, min_n, max_e, max_n = get_world_origin_minmax()
    for o in origins:
        log(f"- {o.name}")
        log(f"  location: {tuple(o.location)}")
        for k in o.keys():
            log(f"  {k}: {_safe_value(o.get(k))}")
    if min_e is not None and min_n is not None:
        log(f"WORLD_ORIGIN bounds: min=({min_e},{min_n}) max=({max_e},{max_n}) locked_by={bpy.data.objects.get(WORLD_ORIGIN_NAME).get('world_origin_set_by', '') if bpy.data.objects.get(WORLD_ORIGIN_NAME) else ''}")
    log("")

    # 5b) Collection sanity (duplicate links)
    log("=== COLLECTION LINKING ===")
    dup_link_objs = [o.name for o in bpy.data.objects if len(getattr(o, "users_collection", []) or []) > 1]
    log(f"Objects linked to >1 collection: {len(dup_link_objs)}")
    if dup_link_objs:
        log(f"Samples: {dup_link_objs[:10]}")
    log("")

    # 5c) Tile stats (local bbox)
    log("=== CITYGML TILE STATS ===")
    tile_col = bpy.data.collections.get("CITYGML_TILES")
    tiles = list(tile_col.objects) if tile_col else []
    bboxes = []
    for o in tiles:
        if o.type != "MESH":
            continue
        try:
            coords = [o.matrix_world @ v.co for v in o.data.vertices]
            xs = [c.x for c in coords]
            ys = [c.y for c in coords]
            bboxes.append((min(xs), min(ys), max(xs), max(ys)))
        except Exception:
            continue
    if bboxes:
        minx = min(bb[0] for bb in bboxes)
        miny = min(bb[1] for bb in bboxes)
        maxx = max(bb[2] for bb in bboxes)
        maxy = max(bb[3] for bb in bboxes)
        log(f"Tiles: {len(bboxes)} bbox_local=({minx:.3f},{miny:.3f})-({maxx:.3f},{maxy:.3f})")
    else:
        log("Tiles: none with mesh bbox")
    log("")

    # 6) Scene m1dc_settings (pointer props)
    log("=== SCENE SETTINGS (m1dc_settings) ===")
    settings = getattr(bpy.context.scene, "m1dc_settings", None)
    if settings:
        try:
            log(f"dir(m1dc_settings): {dir(settings)}")
        except Exception:
            log("dir(m1dc_settings): <error>")
        for attr in dir(settings):
            if attr.startswith("_"):
                continue
            try:
                v = getattr(settings, attr)
                if v not in (None, "", [], False, 0):
                    log(f"{attr}: {_safe_value(v)}")
            except Exception:
                continue

        # Explicit paths to verify UI persistence
        for attr_name in ("gpkg_path", "citygml_dir", "terrain_source_dir", "output_dir"):
            try:
                val = getattr(settings, attr_name)
                log(f"{attr_name}: {_safe_value(val)}")
            except Exception:
                log(f"{attr_name}: <error>")
    else:
        log("No m1dc_settings found")
    log("")

    # 7) GPKG SQLite check
    log("=== GPKG SQLITE CHECK ===")
    log(f"GPKG_PATH: {gpkg_path or 'not provided'}")
    if gpkg_path:
        try:
            try:
                from ...utils.common import open_db_readonly
                con = open_db_readonly(gpkg_path, log_open=False)
            except ImportError:
                # Fallback
                uri = f"file:{gpkg_path}?mode=ro"
                con = sqlite3.connect(uri, uri=True)
                con.execute("PRAGMA busy_timeout=5000;")
                con.execute("PRAGMA query_only=ON;")
            cur = con.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            ).fetchall()]
            log(f"Tables ({len(tables)}): {tables[:30]}{' ...' if len(tables) > 30 else ''}")
            candidate_tables = [
                "osm_multipolygons",
                "multipolygons",
                "planet_osm_polygon",
                "buildings",
                "building",
            ]
            for t in candidate_tables:
                if t in tables:
                    log(f"Candidate table found: {t}")
                    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t});").fetchall()]
                    log(f"Columns in {t}: {cols}")
                    break
            else:
                log("No common building table found")
            con.close()
        except Exception:
            log("!! SQLITE ERROR !!")
            log(traceback.format_exc())
    else:
        log("GPKG not provided; skipped SQLite inspection")
    log("")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    log("=== REPORT SAVED ===")
    log(str(target))
    return target


def run_debug_report(out_path: Path | None = None) -> Path:
    """Lean debug report avoiding large IDProperty dumps."""
    target = out_path or (get_output_dir() / "M1DC_DIAG_REPORT.txt")
    lines: list[str] = []

    def log(msg: str = ""):
        log_info(msg)
        lines.append(str(msg))

    now = datetime.datetime.now().isoformat(timespec="seconds")
    log("=== M1DC GPKG ↔ CityGML DIAGNOSTIC REPORT (LEAN) ===")
    log(now)
    log("")

    s = getattr(bpy.context.scene, "m1dc_settings", None)

    # Scene overview
    log("=== SCENE OVERVIEW ===")
    log(f"Total objects: {len(bpy.data.objects)}")
    log(f"Mesh objects: {len([o for o in bpy.data.objects if o.type=='MESH'])}")
    log(f"Collections: {[c.name for c in bpy.data.collections]}")
    log("")

    # Settings
    log("=== M1DC SETTINGS ===")
    if s:
        log(f"citygml_dir: {getattr(s, 'citygml_dir', None)}")
        log(f"terrain_source_dir: {getattr(s, 'terrain_source_dir', None)}")
        log(f"gpkg_path:   {getattr(s, 'gpkg_path', None)}")
    else:
        log("m1dc_settings not found")
    log("")

    # Origins
    log("=== WORLD ORIGINS ===")
    origins = [o for o in bpy.data.objects if "ORIGIN" in o.name.upper()]
    for o in origins:
        log(f"- {o.name} loc={tuple(o.location)} keys={dict(_safe_obj_keys(o))}")
    log("")

    # CityGML objects
    log("=== CITYGML OBJECTS ===")
    citygml = [o for o in bpy.data.objects if o.type == "MESH" and o.name.lower().startswith("lod2")]
    log(f"CityGML mesh count: {len(citygml)}")
    if citygml:
        o = citygml[0]
        log(f"Sample: {o.name}")
        log(f"Keys: {dict(_safe_obj_keys(o))}")
    log("")

    # OSM/GPKG attached objects
    log("=== OBJECTS WITH osm_id ===")
    osm_objs = [o for o in bpy.data.objects if "osm_id" in o.keys()]
    log(f"Count: {len(osm_objs)}")
    if osm_objs:
        o = osm_objs[0]
        log(f"Sample: {o.name} osm_id={o.get('osm_id')}")
    log("")

    # SQLite check
    log("=== GPKG SQLITE CHECK ===")
    gpkg = getattr(s, "gpkg_path", "") if s else ""
    log(f"GPKG PATH: {gpkg}")
    log(f"EXISTS: {Path(gpkg).exists()}")
    if gpkg and Path(gpkg).exists():
        try:
            from ...utils.common import open_db_readonly
            con = open_db_readonly(gpkg, log_open=False)
        except ImportError:
            # Fallback
            uri = "file:" + str(Path(gpkg).as_posix()) + "?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            con.execute("PRAGMA busy_timeout=5000;")
            con.execute("PRAGMA query_only=ON;")
        try:
            cur = con.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            log(f"TABLE COUNT: {len(tables)}")
            log("FIRST 30 TABLES: " + ", ".join(tables[:30]))
            hits = []
            for t in tables:
                cols = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')").fetchall()]
                if "osm_id" in cols:
                    hits.append(t)
            log("TABLES WITH osm_id: " + (", ".join(hits) if hits else "<none>"))
            con.close()
        except Exception as ex:  # pragma: no cover - Blender runtime
            log("!! SQLITE ERROR !!")
            log(repr(ex))
    else:
        log("!! PATH DOES NOT EXIST !!")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    log_info("Wrote report: " + str(target))
    return target


def run_full_gpkg_gml_report(out_path: Path | None = None) -> Path:
    """Full diagnostic similar to provided script; writes to output_dir."""
    target = out_path or (get_output_dir() / "M1DC_GPKG_GML_DIAGNOSTIC.txt")
    lines: list[str] = []

    lines.append("=== M1DC GPKG ↔ CityGML DIAGNOSTIC REPORT ===")
    lines.append(datetime.datetime.now().isoformat())
    lines.append("")

    # Scene overview
    lines.append("=== SCENE OVERVIEW ===")
    lines.append(f"Total objects: {len(bpy.data.objects)}")
    lines.append(f"Mesh objects: {len([o for o in bpy.data.objects if o.type == 'MESH'])}")
    lines.append(f"Collections: {[c.name for c in bpy.data.collections]}")
    lines.append("")

    # Settings
    lines.append("=== SETTINGS (scene.m1dc_settings) ===")
    s = getattr(bpy.context.scene, "m1dc_settings", None)
    if not s:
        lines.append("!! scene.m1dc_settings NOT FOUND !!")
    else:
        lines.append(f"gpkg_path: {getattr(s, 'gpkg_path', None)}")
        lines.append(f"citygml_dir: {getattr(s, 'citygml_dir', None)}")
        lines.append(f"terrain_source_dir: {getattr(s, 'terrain_source_dir', None)}")
        lines.append(f"output_dir: {getattr(s, 'output_dir', None)}")
        lines.append(f"verbose: {getattr(s, 'verbose', None)}")
        lines.append(f"auto_clip: {getattr(s, 'auto_clip', None)}")
        lines.append(f"clip_end: {getattr(s, 'clip_end', None)}")
    lines.append("")

    # Custom prop scan
    lines.append("=== SUSPECT CUSTOM PROPERTIES ===")
    suspect = set()
    for o in bpy.data.objects:
        for k in o.keys():
            lk = k.lower()
            if "osm" in lk or "gpkg" in lk or "source" in lk:
                suspect.add(k)
    lines.append(f"Detected keys: {sorted(suspect) if suspect else '[]'}")
    lines.append("")

    # CityGML objects
    lines.append("=== CITYGML OBJECTS ===")
    citygml_objects = [
        o
        for o in bpy.data.objects
        if o.type == "MESH" and (
            "lod" in o.name.lower() or "gml" in o.name.lower() or "citygml" in o.name.lower()
        )
    ]
    lines.append(f"CityGML mesh count: {len(citygml_objects)}")
    if citygml_objects:
        g = citygml_objects[0]
        lines.append(f"Sample: {g.name}")
        props = _safe_items(g)
        for k, v in props.items():
            lines.append(f"  {k}: {v}")
    lines.append("")

    # GPKG/osm_id objects
    lines.append("=== GPKG / OSM OBJECTS ===")
    osm_objs = [o for o in bpy.data.objects if "osm_id" in o.keys()]
    lines.append(f"Objects with osm_id: {len(osm_objs)}")
    if osm_objs:
        for o in osm_objs[:5]:
            lines.append(f"  {o.name}: osm_id={o.get('osm_id')}")
    lines.append("")

    # Origin objects
    lines.append("=== WORLD ORIGINS ===")
    origins = [o for o in bpy.data.objects if "ORIGIN" in o.name.upper()]
    lines.append(f"Origin objects found: {len(origins)}")
    for o in origins:
        lines.append(f"- {o.name}  loc={tuple(o.location)}")
        props = _safe_items(o)
        for k, v in props.items():
            lines.append(f"    {k}: {v}")
    lines.append("")

    # GPKG sqlite checks
    lines.append("=== GPKG SQLITE CHECK ===")
    gpkg_path = getattr(s, "gpkg_path", "") if s else ""
    if gpkg_path and Path(gpkg_path).exists():
        try:
            con = _gpkg_open_ro(gpkg_path)
            cur = con.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
            lines.append(f"Table count: {len(tables)}")
            lines.append("First tables: " + ", ".join(tables[:15]))

            try:
                rows = cur.execute(
                    "SELECT table_name, srs_id, min_x, min_y, max_x, max_y FROM gpkg_contents"
                ).fetchall()
                lines.append("gpkg_contents:")
                for r in rows:
                    lines.append("  " + str(r))
            except Exception:
                lines.append("gpkg_contents: (query failed)")

            hits = []
            for t in tables:
                try:
                    cols = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')").fetchall()]
                    if "osm_id" in cols:
                        hits.append(t)
                except Exception:
                    pass
            lines.append("Tables containing osm_id: " + (", ".join(hits) if hits else "(none)"))

            con.close()
            lines.append("OK: sqlite opened and queried.")
        except Exception:
            lines.append("!! SQLITE ERROR !!")
            lines.append(traceback.format_exc())
    else:
        lines.append("No gpkg_path set or file missing.")
    lines.append("")

    target.parent.mkdir(parents=True, exist_ok=True)
    _report_write(target, lines)
    log_info("Wrote: " + str(target))
    return target


def write_m1dc_report_txt(
    table_used: str = "",
    id_col_used: str = "",
    citygml_tiles: int = 0,
    citygml_buildings: int = 0,
    matched: int = 0,
    confidences: list[float] | None = None,
    sample_mappings: list[dict] | None = None,
    no_match_reasons: dict[str, int] | None = None,
    output_dir: str | None = None,
) -> Path:
    """Write a concise linking report avoiding non-serializable data."""

    # Resolve target path
    if output_dir:
        target_dir = Path(output_dir)
    else:
        target_dir = get_output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"M1DC_Report_{stamp}.txt"

    lines: list[str] = []

    def log(msg: str = ""):
        lines.append(str(msg))

    log("=== M1DC LINK REPORT ===")
    log(datetime.datetime.now().isoformat())
    log("")

    log("=== OVERVIEW ===")
    log(f"Table used: {table_used or '-'}")
    log(f"ID column: {id_col_used or '-'}")
    log(f"CityGML tiles: {citygml_tiles}")
    log(f"CityGML buildings: {citygml_buildings}")
    log(f"Matches: {matched}")
    if confidences:
        sorted_c = sorted(confidences)
        mn = min(sorted_c)
        mx = max(sorted_c)
        med = sorted_c[len(sorted_c)//2]
        log(f"Confidence min/median/max: {mn:.3f}/{med:.3f}/{mx:.3f}")
    else:
        log("Confidence: n/a")
    log("")

    if sample_mappings:
        log("=== SAMPLE MAPPINGS (up to 10) ===")
        for m in sample_mappings[:10]:
            log(f"tile={m.get('source_tile')} building_idx={m.get('building_idx')} -> osm_key={m.get('osm_key')} conf={m.get('confidence')}")
        log("")

    if no_match_reasons:
        log("=== TOP NO-MATCH REASONS ===")
        for reason, count in sorted(no_match_reasons.items(), key=lambda kv: kv[1], reverse=True):
            log(f"{reason}: {count}")
        log("")

    target.write_text("\n".join(lines), encoding="utf-8")
    return target
