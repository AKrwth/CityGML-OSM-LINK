"""
DEPRECATED: This module is legacy and should not be used.
Use pipeline.api.open_db_readonly() instead for all DB access.
"""
import re
import sqlite3
import warnings
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

BUSY_TIMEOUT_MS = 5000
CHUNK_SIZE = 500


def parse_other_tags(s: str) -> dict:
    """Parse OGR-style other_tags: "k"=>"v" into a dict."""
    if not s:
        return {}
    pairs = re.findall(r'"([^"]+)"=>"(.*?)"', s)
    return {k: v for k, v in pairs}


def open_db_readonly(db_path: str) -> sqlite3.Connection:
    """
    DEPRECATED: Use pipeline.api.open_db_readonly() instead.
    This function is kept for backwards compatibility but redirects to the central helper.
    """
    warnings.warn(
        "Legacy db.open_db_readonly() is deprecated. Use pipeline.api.open_db_readonly() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    # Redirect to central readonly helper
    try:
        from ...utils.common import open_db_readonly as _central_open_db_readonly
        return _central_open_db_readonly(db_path, log_open=True)
    except ImportError:
        # Fallback if central import fails
        p = Path(db_path)
        if not p.exists():
            raise FileNotFoundError(f"DB not found: {p}")
        uri = f"file:{p.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
        conn.execute("PRAGMA query_only = ON;")
        conn.row_factory = sqlite3.Row
        return conn


def fetch_rows_by_osm_ids(
    conn: sqlite3.Connection,
    table: str,
    osm_ids: Sequence[int],
    chunk_size: int = CHUNK_SIZE,
) -> Dict[int, sqlite3.Row]:
    if not osm_ids:
        return {}
    table_safe = table.replace("'", "''")
    result: Dict[int, sqlite3.Row] = {}
    cur = conn.cursor()
    for i in range(0, len(osm_ids), chunk_size):
        chunk = osm_ids[i : i + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        cur.execute(f"SELECT * FROM '{table_safe}' WHERE osm_id IN ({placeholders})", chunk)
        for row in cur.fetchall():
            try:
                oid = int(row["osm_id"])
            except Exception:
                continue
            if oid not in result:
                result[oid] = row
    return result
