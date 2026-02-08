import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Sequence

from ...utils.logging_system import log_info, log_warn
def _detect_pk_and_geom(cur, table: str, fallback_id_col: str):
    pk_col = None
    geom_col = None

    try:
        cur.execute(f"PRAGMA table_info('{_safe_ident(table)}')")
        for row in cur.fetchall():
            # pragma columns: cid, name, type, notnull, dflt_value, pk
            name = row[1]
            is_pk = row[5] == 1
            if is_pk and pk_col is None:
                pk_col = name
    except Exception:
        pk_col = None

    try:
        cur.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name=? LIMIT 1", (table,))
        r = cur.fetchone()
        if r:
            geom_col = r[0]
    except Exception:
        geom_col = None

    if not geom_col:
        # common defaults
        for cand in ("geom", "geometry", "wkb_geometry"):
            try:
                cur.execute(f"PRAGMA table_info('{_safe_ident(table)}')")
                cols = [r[1] for r in cur.fetchall()]
                if cand in cols:
                    geom_col = cand
                    break
            except Exception:
                break

    if pk_col is None:
        pk_col = fallback_id_col

    return pk_col, geom_col or "geom"

PREFERRED_TABLES = ["multipolygons", "osm_multipolygons"]
PREFERRED_ID_COLS = ["osm_id"]


def _connect_ro(gpkg_path: str) -> sqlite3.Connection:
    from ...utils.common import resolve_gpkg_path, log_gpkg_resolution

    resolved, info = resolve_gpkg_path(gpkg_path)
    log_gpkg_resolution(gpkg_path, resolved, info, prefix="[GPKGReader]")
    if not resolved:
        raise FileNotFoundError(info)

    # Use centralized readonly DB access
    try:
        from ...utils.common import open_db_readonly
        con = open_db_readonly(resolved, log_open=True)
    except ImportError:
        # Fallback if API import fails
        uri = f"file:{Path(resolved).as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.execute("PRAGMA busy_timeout=5000;")
        con.execute("PRAGMA query_only=ON;")
    return con


def _safe_ident(name: str) -> str:
    return name.replace("'", "''")


def _list_tables(cur) -> List[str]:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def _list_columns(cur, table: str) -> List[str]:
    t = _safe_ident(table)
    cur.execute(f"PRAGMA table_info('{t}')")
    return [r[1] for r in cur.fetchall()]


def choose_table_and_id(gpkg_path: str) -> Tuple[Optional[str], Optional[str]]:
    with _connect_ro(gpkg_path) as con:
        cur = con.cursor()
        tables = _list_tables(cur)
        best_table = None
        best_id = None
        for t in tables:
            name_l = t.lower()
            if not any(pref in name_l for pref in PREFERRED_TABLES):
                continue
            cols = _list_columns(cur, t)
            id_col = None
            for cand in PREFERRED_ID_COLS:
                if cand in cols:
                    id_col = cand
                    break
            if id_col:
                best_table = t
                best_id = id_col
                break
        return best_table, best_id


def load_osm_features(gpkg_path: str, table: str, id_col: str, include_geom: bool = False) -> List[Dict[str, object]]:
    """Load OSM-like features with bbox (rtree keyed by PK) and optional geometry WKB."""
    features: List[Dict[str, object]] = []
    t = _safe_ident(table)
    idc = _safe_ident(id_col)
    rtree_table = f"rtree_{t}_geom"
    with _connect_ro(gpkg_path) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        pk_col, geom_col = _detect_pk_and_geom(cur, t, id_col)
        has_rtree = False
        try:
            cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (rtree_table,))
            has_rtree = cur.fetchone() is not None
        except Exception:
            has_rtree = False
            log_warn("[GPKG] no rtree table found")

        bbox_map = {}
        if has_rtree:
            try:
                cur.execute(f"SELECT id, minx, maxx, miny, maxy FROM {rtree_table}")
                for row in cur.fetchall():
                    fid = row[0]
                    bbox_map[fid] = (row[1], row[3], row[2], row[4])
            except Exception:
                bbox_map = {}
        log_info(f"[GPKG] pk_col={pk_col} geom_col={geom_col} rtree_table={rtree_table} rtree={'yes' if has_rtree else 'no'}")

        try:
            cur.execute(f"SELECT {pk_col} AS fid, {idc} AS osm_id, {geom_col} AS geom, * FROM '{t}' WHERE {idc} IS NOT NULL")
            rows = cur.fetchall()
        except Exception:
            return []

        for r in rows:
            key = r["osm_id"] if "osm_id" in r.keys() else r[id_col]
            fid = r["fid"] if "fid" in r.keys() else None
            if key is None or fid is None:
                continue
            # Keep light attribute subset
            attrs = {}
            for k in r.keys():
                if k == id_col or k.lower() == "geom":
                    continue
                v = r[k]
                if isinstance(v, (int, float, str)):
                    attrs[k] = v
            bbox = bbox_map.get(fid)
            centroid = None
            if bbox:
                cx = (bbox[0] + bbox[2]) * 0.5
                cy = (bbox[1] + bbox[3]) * 0.5
                centroid = (cx, cy)
            geom_wkb = r["geom"] if include_geom and "geom" in r.keys() else None
            features.append({
                "fid": fid,
                "osm_key": key,
                "bbox_xy": bbox,
                "centroid_xy": centroid,
                "attrs": attrs,
                "geom_wkb": geom_wkb,
                "pk_col": pk_col,
                "geom_col": geom_col,
            })
    return features


def query_geoms_by_point(
    gpkg_path: str,
    table: str,
    pk_col: str,
    geom_col: str,
    id_col: str,
    x_crs: float,
    y_crs: float,
    max_candidates: int = 200,
    fallback_limit: int = 50000,
) -> List[Dict[str, object]]:
    """Return candidate features whose rtree bbox contains the point (CRS coords).

    If rtree missing, fallback loads all features up to fallback_limit rows.
    """
    results: List[Dict[str, object]] = []
    if not gpkg_path or not table or not id_col:
        return results

    t = _safe_ident(table)
    pkc = _safe_ident(pk_col)
    idc = _safe_ident(id_col)
    geomc = _safe_ident(geom_col)
    rtree_table = f"rtree_{t}_{geomc}"

    try:
        with _connect_ro(gpkg_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            # Check rtree existence
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (rtree_table,))
            has_rtree = cur.fetchone() is not None

            ids = []
            if has_rtree:
                cur.execute(
                    f"SELECT id FROM {rtree_table} WHERE minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ? LIMIT ?",
                    (x_crs, x_crs, y_crs, y_crs, max_candidates),
                )
                ids = [r[0] for r in cur.fetchall()]
            else:
                log_warn(f"[RTree] missing {rtree_table}; fallback scan (<= {fallback_limit} rows)")
                cur.execute(f"SELECT COUNT(*) FROM '{t}'")
                total = cur.fetchone()[0]
                if total > fallback_limit:
                    log_warn(f"[RTree] fallback aborted: {total} rows exceeds {fallback_limit}")
                    return []
                cur.execute(f"SELECT {pkc} FROM '{t}'")
                ids = [r[0] for r in cur.fetchall()]

            log_info(
                f"[RTree] pk_col={pk_col} geom_col={geom_col} rtree_table={rtree_table} candidates={len(ids)}"
            )

            if not ids:
                return results

            placeholders = ",".join(["?"] * len(ids))
            cur.execute(
                f"SELECT {pkc} AS fid, {idc} AS osm_id, {geomc} AS geom FROM '{t}' WHERE {pkc} IN ({placeholders})",
                ids,
            )
            rows = cur.fetchall()
            for r in rows:
                try:
                    key = r["osm_id"]
                    fid = r["fid"]
                except Exception:
                    continue
                geom_wkb = r["geom"] if "geom" in r.keys() else None
                results.append({"osm_key": key, "fid": fid, "geom_wkb": geom_wkb})
    except Exception:
        return []

    return results
