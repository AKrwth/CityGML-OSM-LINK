# Development Environment

This document clarifies runtime and development environment constraints for the CityGML–OSM-LINK add-on.

---

## Blender Python Version

Blender 4.5.x embeds **Python 3.11.x** internally.

All external environments (virtualenv or Conda) used for preprocessing or development should match Python 3.11 to avoid binary incompatibilities.

The add-on itself runs inside Blender’s embedded Python interpreter.

---

## GDAL and Raster Dependencies

The project depends on:

- GDAL (via `from osgeo import gdal`)
- rasterio

Both libraries rely on compiled native components.

Because of this, installation differs across systems.

### Important

GDAL is not a pure Python library. It wraps compiled C/C++ binaries.
Version mismatches between:

- Python
- GDAL
- Operating system
- Installation source (pip vs conda vs system packages)

may result in import errors.

---

## Recommended Installation Approaches

### Conda (Recommended for Development)

Use `conda-forge`:

```bash
conda create -n citygml-osm-link python=3.11 gdal rasterio pillow requests setuptools -c conda-forge
conda activate citygml-osm-link
```