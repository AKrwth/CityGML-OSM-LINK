"""
pipeline/linking: Linking GML + OSM domain (bridges both worlds).

Public API:
- ensure_link_dbs(gpkg_path, gml_dir, out_dir) -> (db_gml, db_osm, db_links)
    Ensure link databases exist
"""

from .linking_cache import ensure_link_dbs

__all__ = [
    "ensure_link_dbs",
]
