"""
pipeline/osm: OSM/GeoPackage data domain.

Public API:
- choose_table_and_id(gpkg_path) -> (str, str)
    Detect main table and ID column in GeoPackage
- load_osm_features(gpkg_path, table, id_col, include_geom) -> list[dict]
    Load OSM features from GeoPackage
- query_geoms_by_point(...) -> list
    Query geometries by point bbox
"""

from .gpkg_reader import (
    choose_table_and_id,
    load_osm_features,
    query_geoms_by_point,
)

__all__ = [
    "choose_table_and_id",
    "load_osm_features",
    "query_geoms_by_point",
]
