import os
import importlib
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Tuple

from ...utils.logging_system import log_info, log_warn, log_error
from ...utils.common import get_output_dir

CITYGML_EXTS = (".gml", ".xml", ".citygml")

try:
    import bpy  # type: ignore
    from ..citygml.citygml_import import iter_citygml_buildings
    from ...utils.common import local_to_crs_xy, resolve_gpkg_path, log_gpkg_resolution
    _HAVE_BPY = True
except Exception:
    bpy = None  # type: ignore
    iter_citygml_buildings = None
    local_to_crs_xy = None
    resolve_gpkg_path = None
    log_gpkg_resolution = None
    _HAVE_BPY = False


def _is_scene_fallback_allowed() -> bool:
    if not _HAVE_BPY or bpy is None:
        return False
    scene = getattr(bpy.context, "scene", None)
    settings = getattr(scene, "m1dc_settings", None) if scene else None
    try:
        return bool(getattr(settings, "allow_scene_fallback", False)) if settings else False
    except Exception:
        return False


def _get_world_origin_min_en(world_origin_obj=None, log_fn=log_info) -> tuple[float, float]:
    if not _HAVE_BPY or bpy is None:
        raise RuntimeError("WORLD_ORIGIN lookup requires Blender context")

    origin = world_origin_obj or (
        bpy.data.objects.get("M1DC_WORLD_ORIGIN")
        or bpy.data.objects.get("WORLD_ORIGIN")
        or bpy.data.objects.get("KOELN_ORIGIN")
    )
    if origin is None:
        raise RuntimeError("WORLD_ORIGIN object not found; cannot project OSM centroids to local coords")

    keys_e = ["world_min_easting", "world_min_e", "min_e", "easting_min"]
    keys_n = ["world_min_northing", "world_min_n", "min_n", "northing_min"]

    min_e = None
    min_n = None
    for k in keys_e:
        if k in origin.keys():
            min_e = origin.get(k)
            break
    for k in keys_n:
        if k in origin.keys():
            min_n = origin.get(k)
            break

    if min_e is None or min_n is None:
        raise RuntimeError(
            f"WORLD_ORIGIN min_e/min_n not found on origin object (keys_e={keys_e}, keys_n={keys_n})"
        )

    try:
        min_e_f = float(min_e)
        min_n_f = float(min_n)
    except Exception as ex:
        raise RuntimeError(
            f"WORLD_ORIGIN min_e/min_n missing or invalid (min_e={min_e!r}, min_n={min_n!r})"
        ) from ex

    log_fn(f"[LinkDB] WORLD_ORIGIN min_e={min_e_f:.3f}, min_n={min_n_f:.3f} (local projection)")
    return min_e_f, min_n_f


def ensure_osm_local_table(linkdb_path: Path, min_e: float, min_n: float, semantic_cols: list[str], log_fn=log_info) -> None:
    linkdb_path = Path(linkdb_path)
    if not linkdb_path.exists():
        raise RuntimeError(f"OSM link DB not found: {linkdb_path}")

    con = sqlite3.connect(str(linkdb_path))
    try:
        cur = con.cursor()
        row = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='osm_building_link' LIMIT 1;"
        ).fetchone()
        if not row:
            raise RuntimeError("OSM link DB missing table 'osm_building_link'")

        cols_info = cur.execute("PRAGMA table_info(osm_building_link);").fetchall()
        available = {c[1] for c in cols_info}
        for req in ("osm_way_id", "cx", "cy"):
            if req not in available:
                raise RuntimeError(f"osm_building_link missing required column: {req}")

        sem_cols = [c for c in (semantic_cols or []) if c in available]
        select_sem = ",\n            ".join([f'"{c}"' for c in sem_cols]) if sem_cols else ""
        if select_sem:
            select_sem = ",\n            " + select_sem

        log_fn(f"[LinkDB] Building osm_building_link_local in {linkdb_path}")
        log_fn(f"[LinkDB] Semantic columns preserved: {sem_cols}")

        cur.execute("DROP TABLE IF EXISTS osm_building_link_local;")

        # Include bbox columns (shifted) for IoU computation in linker
        bbox_cols = ["minx", "miny", "maxx", "maxy"]
        has_bbox = all(c in available for c in bbox_cols)
        if has_bbox:
            bbox_select = (
                ",\n            (minx - ?) AS minx,"
                "\n            (miny - ?) AS miny,"
                "\n            (maxx - ?) AS maxx,"
                "\n            (maxy - ?) AS maxy"
            )
            bbox_params = (min_e, min_n, min_e, min_n)
            log_fn(f"[LinkDB] Including shifted bbox columns for IoU")
        else:
            bbox_select = ""
            bbox_params = ()
            log_fn(f"[LinkDB] No bbox columns in osm_building_link; IoU will be 0.0")

        cur.execute(
            f"""
                CREATE TABLE osm_building_link_local AS
                SELECT
                    osm_way_id,
                    (cx - ?) AS cx,
                    (cy - ?) AS cy
                    {bbox_select}
                    {select_sem}
                FROM osm_building_link
                WHERE osm_way_id IS NOT NULL;
                """,
            (min_e, min_n, *bbox_params),
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_osm_local_xy ON osm_building_link_local(cx, cy);")
        n = cur.execute("SELECT COUNT(*) FROM osm_building_link_local;").fetchone()[0]
        log_fn(f"[LinkDB] osm_building_link_local rows: {n:,}")
        con.commit()
    finally:
        con.close()


def _latest_mtime(path: Path, exts: Iterable[str] | None = None) -> Optional[float]:
    if not path.exists():
        return None
    if path.is_file():
        try:
            return path.stat().st_mtime
        except OSError:
            return None
    newest = None
    suffixes = {e.lower() for e in exts} if exts else None
    for root, _, files in os.walk(path):
        for name in files:
            if suffixes and Path(name).suffix.lower() not in suffixes:
                continue
            try:
                mt = Path(root, name).stat().st_mtime
            except OSError:
                continue
            newest = mt if newest is None else max(newest, mt)
    return newest


def _needs_refresh(target: Path, inputs: Iterable[Path | float | None]) -> bool:
    if not target.exists():
        return True
    try:
        target_mtime = target.stat().st_mtime
    except OSError:
        return True
    for src in inputs:
        if src is None:
            continue
        if isinstance(src, (int, float)):
            src_mtime = float(src)
        else:
            if not Path(src).exists():
                continue
            try:
                src_mtime = Path(src).stat().st_mtime
            except OSError:
                continue
        if src_mtime and src_mtime > target_mtime:
            return True
    return False


def _run_make_osm_centroids(gpkg_path: Path, out_db: Path) -> None:
    mod = importlib.import_module(".make_osm_centroids_semantics", package=__package__)
    # Patch module-level constants before running
    if hasattr(mod, "SRC_GPKG"):
        mod.SRC_GPKG = str(gpkg_path)
    if hasattr(mod, "OUT_DB"):
        mod.OUT_DB = str(out_db)
    if not hasattr(mod, "main"):
        raise RuntimeError("make_osm_centroids_semantics.main() not found")
    log_info(f"[LinkDB] Building OSM centroid DB -> {out_db}")
    mod.main()


def _build_gml_from_scene(out_db: Path) -> int:
    if not _HAVE_BPY or iter_citygml_buildings is None or local_to_crs_xy is None:
        raise RuntimeError("Blender context not available for GML centroid fallback")

    # Import canonical key normalization (single source of truth)
    from .key_normalization import normalize_source_tile as norm_source_tile

    objs = bpy.data.collections.get("CITYGML_TILES")
    candidates = list(objs.objects) if objs else [o for o in bpy.data.objects if o.type == "MESH"]
    rows = []
    for obj in candidates:
        if obj.type != "MESH":
            continue
        aggr = iter_citygml_buildings(obj)
        if not aggr:
            continue
        for key, data in aggr.items():
            source_tile_raw, bidx = key
            source_tile = norm_source_tile(source_tile_raw)  # NORMALIZE KEY (C-fix)
            centroid_xy = data.get("centroid_xy")
            bbox_xy = data.get("bbox_xy")
            if not centroid_xy:
                continue
            cx, cy = local_to_crs_xy(centroid_xy[0], centroid_xy[1], origin=obj)
            if bbox_xy:
                minx, miny = local_to_crs_xy(bbox_xy[0], bbox_xy[1], origin=obj)
                maxx, maxy = local_to_crs_xy(bbox_xy[2], bbox_xy[3], origin=obj)
            else:
                minx = maxx = cx
                miny = maxy = cy
            rows.append((str(source_tile), int(bidx), None, float(cx), float(cy), float(minx), float(miny), float(maxx), float(maxy)))

    if not rows:
        raise RuntimeError("No CityGML buildings found for centroid export")

    if out_db.exists():
        out_db.unlink()
    out_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out_db))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE gml_building_centroids(
            source_tile TEXT NOT NULL,
            building_idx INTEGER NOT NULL,
            gml_id TEXT,
            cx REAL NOT NULL,
            cy REAL NOT NULL,
            minx REAL NOT NULL,
            miny REAL NOT NULL,
            maxx REAL NOT NULL,
            maxy REAL NOT NULL,
            PRIMARY KEY (source_tile, building_idx)
        );
        """
    )
    cur.executemany(
        "INSERT INTO gml_building_centroids(source_tile, building_idx, gml_id, cx, cy, minx, miny, maxx, maxy) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    log_info(f"[LinkDB] Wrote {len(rows)} GML centroid rows (fallback from scene) -> {out_db}")
    return len(rows)


def _run_make_gml_centroids(gml_dir: Path, out_db: Path) -> Path:
    mod = importlib.import_module(".make_gml_centroids", package=__package__)
    if hasattr(mod, "GML_DIR"):
        mod.GML_DIR = str(gml_dir)
    target_name = out_db.name
    if hasattr(mod, "OUT_DB_NAME"):
        mod.OUT_DB_NAME = target_name
    if hasattr(mod, "main"):
        log_info(f"[LinkDB] Building GML centroid DB -> {out_db}")
        mod.main()
        candidate = gml_dir / target_name
        if candidate.exists() and candidate != out_db:
            try:
                out_db.parent.mkdir(parents=True, exist_ok=True)
                candidate.replace(out_db)
                log_info(f"[LinkDB] Moved GML centroid DB to {out_db}")
            except Exception:
                log_warn(f"[LinkDB] Could not move {candidate} to {out_db}; using original path")
        if out_db.exists():
            return out_db
        if candidate.exists():
            log_warn(f"[LinkDB] Using centroid DB at {candidate}")
            return candidate
        raise RuntimeError(f"GML centroid DB not created at {out_db}")

    if not _is_scene_fallback_allowed():
        msg = "[LinkDB][ERROR] make_gml_centroids.py has no callable main(); aborting linking. Scene fallback is forbidden."
        log_error(msg)
        raise RuntimeError(msg)

    log_warn("[LinkDB] ALLOW_SCENE_FALLBACK=True -> using fallback centroids from scene (debug)")
    _build_gml_from_scene(out_db)
    return out_db


def _run_link_gml_to_osm(gml_db: Path, osm_db: Path, out_db: Path, min_e: float = 0.0, min_n: float = 0.0) -> None:
    mod = importlib.import_module(".link_gml_to_osm", package=__package__)
    if hasattr(mod, "GML_DB_PATH"):
        mod.GML_DB_PATH = str(gml_db)
    if hasattr(mod, "OSM_DB_PATH"):
        mod.OSM_DB_PATH = str(osm_db)
    if hasattr(mod, "OUT_DB_PATH"):
        mod.OUT_DB_PATH = str(out_db)
    if not hasattr(mod, "main"):
        raise RuntimeError("link_gml_to_osm.main() not found")
    log_info(f"[LinkDB] Linking GML-OSM centroids -> {out_db}")
    
    # Check if main accepts arguments
    import inspect
    sig = inspect.signature(mod.main)
    if "min_e" in sig.parameters:
        mod.main(min_e=min_e, min_n=min_n)
    else:
        log_warn("[LinkDB] link_gml_to_osm.main() does not accept offsets; using default.")
        mod.main()


def ensure_link_dbs(gpkg_path: str, gml_dir: str, out_dir: str | Path | None = None) -> Tuple[Path, Path, Path]:
    if not gpkg_path:
        raise ValueError("gpkg_path is required for linking")
    from ...utils.common import resolve_gpkg_path as resolve_gpkg_path_fn, log_gpkg_resolution as log_gpkg_resolution_fn

    resolved, info = resolve_gpkg_path_fn(gpkg_path)
    log_gpkg_resolution_fn(gpkg_path, resolved, info, prefix="[LinkDB][GPKG]")
    if not resolved:
        raise FileNotFoundError(info)
    gpkg = Path(resolved)

    gml_path = Path(gml_dir) if gml_dir else None
    base_out = Path(out_dir) if out_dir else get_output_dir()
    base_out.mkdir(parents=True, exist_ok=True)

    # ── TASK C: Link artifacts go into output_dir/links/ for deterministic discovery ──
    links_dir = base_out / "links"
    links_dir.mkdir(parents=True, exist_ok=True)

    gpkg_stem = gpkg.stem or "links"
    osm_db = links_dir / f"{gpkg_stem}_linkdb.sqlite"
    gml_db = Path(gml_path) / "db_GML_Kacheln_centroids.sqlite" if gml_path else links_dir / "db_GML_Kacheln_centroids.sqlite"
    link_db = links_dir / f"{gpkg_stem}_links.sqlite"

    log_info(f"[Link][Artifacts] links_dir={links_dir}")
    log_info(f"[Link][Artifacts] link_db target={link_db}")

    gpkg_mtime = _latest_mtime(gpkg)
    gml_mtime = _latest_mtime(gml_path, CITYGML_EXTS) if gml_path else None

    if _needs_refresh(osm_db, [gpkg_mtime]):
        _run_make_osm_centroids(gpkg, osm_db)
    else:
        log_info(f"[LinkDB] OSM centroid DB up-to-date: {osm_db}")

    try:
        origin = (
            bpy.data.objects.get("M1DC_WORLD_ORIGIN")
            or bpy.data.objects.get("WORLD_ORIGIN")
            or bpy.data.objects.get("KOELN_ORIGIN")
        ) if _HAVE_BPY else None
        min_e, min_n = _get_world_origin_min_en(origin, log_info)
        semantics = ["name", "building", "building_levels", "height"]
        ensure_osm_local_table(osm_db, min_e, min_n, semantics, log_info)
    except Exception as ex:
        log_error(f"[LinkDB] Failed to build local OSM table: {ex}")
        raise

    if _needs_refresh(gml_db, [gml_mtime]):
        if not gml_path:
            # No CityGML directory — try scene fallback (builds centroids from imported meshes)
            if _HAVE_BPY:
                log_info("[LinkDB] No CityGML dir; attempting scene-based centroid fallback")
                try:
                    _build_gml_from_scene(gml_db)
                except Exception as ex:
                    raise FileNotFoundError(
                        f"CityGML directory not set and scene fallback failed: {ex}"
                    )
            else:
                raise FileNotFoundError("CityGML directory required to build GML centroid DB (no Blender context)")
        else:
            gml_db = _run_make_gml_centroids(gml_path, gml_db)
    else:
        log_info(f"[LinkDB] GML centroid DB up-to-date: {gml_db}")

    if _needs_refresh(link_db, [gml_db, osm_db]):
        _run_link_gml_to_osm(gml_db, osm_db, link_db, min_e=min_e, min_n=min_n)
    else:
        log_info(f"[LinkDB] Link DB up-to-date: {link_db}")

    # ── TASK C: Verify artifact was created ──
    if not link_db.exists():
        raise RuntimeError(f"[Link][Artifacts] link DB was NOT created at: {link_db}")
    file_size = link_db.stat().st_size
    log_info(f"[Link][Artifacts] link_db verified: {link_db} size={file_size} bytes")

    # ── TASK C: Persist links_db_path on scene settings ──
    if _HAVE_BPY and bpy is not None:
        try:
            settings = getattr(bpy.context.scene, "m1dc_settings", None)
            if settings is not None:
                settings.links_db_path = str(link_db.resolve())
                log_info(f"[Link][Artifacts] links_db_path set to: {settings.links_db_path}")
                log_info(f"[Link][Artifacts] file exists: True size={file_size}")
        except Exception as ex:
            log_warn(f"[Link][Artifacts] Could not set links_db_path on settings: {ex}")

    return osm_db, gml_db, link_db
