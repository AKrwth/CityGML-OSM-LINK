# Documentation (Deep Dive)

<p align="center">
  <img src="Images/CityGML-OSM-LINK.png" width="700">
</p>

This is the technical documentation entry point for the CityGML-OSM-LINK Blender add-on.
Use the repository root README for quick onboarding and this document for deeper internals.

## Research positioning

This repository is structured as research infrastructure for reproducible urban semantic-geometric integration experiments. It is designed to make transformation, linking, and attribute materialization steps explicit and inspectable, rather than to act as a full GIS or production content pipeline.

## Documentation Map

- [ARCHITECTURE.md](ARCHITECTURE.md)
  - Pipeline and add-on architecture, module responsibilities, and implementation constraints.
- [DIAGRAMS.md](DIAGRAMS.md)
  - Diagram index and references to visual workflow material.
- [DOCS_INDEX.md](DOCS_INDEX.md)
  - Documentation governance and update policy (kept as a maintenance aid).

## Additional Technical Material

- [audit/](audit/)
  - Validation checklists, repair summaries, and risk analyses.
- [_archive/](_archive/)
  - Historical phase documents and archived references.

## Reading Order

1. Start at the root [../README.md](../README.md).
2. Continue with [ARCHITECTURE.md](ARCHITECTURE.md).

## GDAL Installation Note

GDAL is a compiled library with system-level dependencies.

Depending on your platform, installation may differ:

- On Conda environments, GDAL is recommended via `conda-forge`.
- On system Python (Linux), GDAL may rely on system packages.
- On Windows, precompiled wheels or Conda are recommended.
- Blender uses its own embedded Python; installing GDAL into Blender may require manual wheel installation or using Blender’s bundled Python interpreter.

If GDAL import errors occur (`from osgeo import gdal`), verify that:

- The GDAL version matches Python 3.11.
- The installation source (pip vs conda vs system) is consistent.

## Scientific and standards anchors

- OGC CityGML standard family (semantic 3D city model source format)
  - https://www.ogc.org/standards/citygml/
- OGC GeoPackage Encoding Standard (OSM-derived tabular/vector container in this project)
  - https://www.ogc.org/standards/geopackage/
- Blender Manual (general platform behavior and numerical constraints context)
  - https://docs.blender.org/manual/en/latest/
- GDAL/OGR documentation (compiled geospatial I/O stack used by this repository)
  - https://gdal.org/
