# CityGML-OSM-LINK (Blender Add-on)

![CityGML-OSM Link](docs/Images/CityGML-OSM-LINK.png)

Research-oriented Blender add-on for integrating CityGML, OSM (GeoPackage), and terrain/raster data into one inspectable scene.

## Problem statement

Urban data integration is structurally heterogeneous:

- CityGML provides 3D semantic building geometry.
- OSM/GeoPackage provides mostly 2D volunteered GIS features.
- Terrain/raster sources provide elevation and imagery context.

In practice, these sources are often semantically fragmented and spatially misaligned. This project addresses that integration gap by enforcing a deterministic, staged pipeline inside one shared scene model.

## What this add-on does

- Imports CityGML building geometry and semantic attributes.
- Loads OSM-derived GeoPackage layers for infrastructure and context.
- Integrates terrain/raster sources used for scene grounding.
- Uses a central world-origin workflow (EPSG:25832 / WORLD_ORIGIN) to keep spatial transforms consistent across pipeline phases.

## Why Blender

Blender is used here as an experimental integration environment, not as a GIS replacement. It provides:

- a stable mesh data model,
- custom attribute layers,
- a scriptable Python runtime,
- visualization and inspection in one workspace.

Within this project, Blender functions as a semantic laboratory for geometric-semantic reconciliation.

## Quick Start

1. Install Python runtime dependencies from [requirements.txt](requirements.txt) into Blender's Python environment.
2. Install/enable the add-on in Blender.
3. Set project inputs (CityGML, OSM/GPKG, terrain/raster) in the add-on panel.
4. Run the phased operators in order (terrain → CityGML → OSM/GPKG → linking/materialization).
5. Validate alignment with the EPSG:25832 world-origin workflow before analysis/export steps.

## What this add-on does NOT do

- It is not a general GIS desktop application.
- It is not a BIM authoring tool.
- It does not guarantee topology repair or full semantic preservation for every CityGML feature class.
- It does not target unlimited city-scale streaming/tiling workflows.
- It is not a one-click production render pipeline.

## Determinism and reproducibility

- No stochastic operations are used in the pipeline.
- Linking is rule-based.
- Legend encoding is deterministic.
- Pipeline stages are explicit.
- State can be reconstructed from scene properties and generated artifacts.

## Scientific context

This repository is research infrastructure for urban digital representation under BIM/GIS integration constraints. It works at the boundary between linked semantic urban data and geometric interoperability, with operational reliance on OGC CityGML and OGC GeoPackage inputs.

## Documentation

The root README is the primary entry point. For architecture, phased workflow details, and audits, see:

- [docs/README.md](docs/README.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/DIAGRAMS.md](docs/DIAGRAMS.md)
- [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)
- [docs/DOCS_INDEX.md](docs/DOCS_INDEX.md)

## Python dependencies

Runtime dependencies are listed in [requirements.txt](requirements.txt).

Notes:

- Blender-provided modules (`bpy`, `bmesh`, `mathutils`) are intentionally not listed in `requirements.txt`.
- Python standard-library modules are intentionally not listed in `requirements.txt`.
