"""
pipeline/citygml: CityGML tile import domain.

Public API:
- import_citygml_folder(folder, settings) -> tuple
    Import CityGML tiles from folder
- ensure_collection(name: str) -> bpy.types.Collection
- ensure_empty(name: str) -> bpy.types.Object  
- parse_citygml_tile_coords(filename: str) -> tuple or None
- iter_citygml_buildings(...) -> iterator
- iter_citygml_paths(folder: str) -> list[Path]
"""

from .citygml_import import (
    import_citygml_folder,
    ensure_collection,
    ensure_empty,
    parse_citygml_tile_coords,
    iter_citygml_buildings,
    iter_citygml_paths,
)

__all__ = [
    "import_citygml_folder",
    "ensure_collection",
    "ensure_empty",
    "parse_citygml_tile_coords",
    "iter_citygml_buildings",
    "iter_citygml_paths",
]
