"""
pipeline/linking: Linking GML + OSM domain (bridges both worlds).

Public API:
- normalize_source_tile(x) -> stable tile key (SINGLE SOURCE OF TRUTH)
- ensure_link_dbs(gpkg_path, gml_dir, out_dir) -> (db_gml, db_osm, db_links)
    Ensure link databases exist
"""

from .key_normalization import normalize_source_tile
from .linking_cache import ensure_link_dbs

__all__ = [
    "normalize_source_tile",
    "ensure_link_dbs",
]
