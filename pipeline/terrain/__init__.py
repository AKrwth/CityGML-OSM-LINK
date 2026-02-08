"""
pipeline/terrain: Base terrain (DEM → Heightmap displacement mesh, OBJ artifacts) domain.

Public API:
- import_basemap_terrain(context, settings) -> bool
    Import DEM, create heightmap PNG, build mesh with displace modifier
- basemap_tiles module for OBJ artifact loading
- validate_prepared_terrain_dataset(terrain_dir) -> dict
    Validate prepared terrain dataset (DGM_Tiles/, RGB_Tiles/derived/)
"""

from .m1_basemap import import_basemap_terrain
from . import basemap_tiles
from .terrain_validation import validate_prepared_terrain_dataset

__all__ = [
    "import_basemap_terrain",
    "basemap_tiles",
    "validate_prepared_terrain_dataset",
]
