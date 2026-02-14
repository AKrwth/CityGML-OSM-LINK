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

The pipeline is fully deterministic. No stochastic operations are used at any stage.

### Orchestration separation

`ops.py` is the orchestration layer. All core logic lives in `pipeline/` subpackages (`terrain/`, `citygml/`, `linking/`, `operations/`, `diagnostics/`, `spreadsheet/`, `osm/`). `settings.py` defines scene properties. `ui.py` defines panel layout.

### Artifact contract

All pipeline artifacts are written into `output_dir/` under two subdirectories:

- `output_dir/links/` — link databases (SQLite).
- `output_dir/legends/` — legend CSVs.

No artifacts are written outside `output_dir`. `links_db_path` must point inside `output_dir/links/`. If `links_db_path` is empty, Materialize auto-detects the link DB from `output_dir/links/`.

### Linking determinism

`pipeline.linking.key_normalization.normalize_source_tile()` is the single source of truth for `source_tile` identity. It is applied uniformly in centroid generation, link DB creation, link DB loading, and materialize lookup.

### Failure semantics

Operators return `{'CANCELLED'}` when preconditions are not met (missing link DB, empty link DB, terrain validation failure). No operator returns `{'FINISHED'}` on a data-processing failure.

### Terrain policy

DGM terrain import enforces scale `(1,1,1)` and uses min-corner alignment. No bounding-box center heuristics are used in the DGM import path.

### ClobberGuard

Attribute writes use schema enforcement: domain must be `FACE`, data type must match (`INT`/`FLOAT`), type upgrades are allowed, and protected attributes with non-default values are not overwritten.

### Reproducibility

`output_dir` can be deleted safely between runs. The pipeline recreates all artifacts deterministically from source inputs. Intermediate databases use mtime-based staleness detection to trigger rebuilds.

For full architectural detail, see [docs/ADDON_ARCHITECTURE.md](docs/ADDON_ARCHITECTURE.md).

## Scientific context

This repository is research infrastructure for urban digital representation under BIM/GIS integration constraints. It works at the boundary between linked semantic urban data and geometric interoperability, with operational reliance on OGC CityGML and OGC GeoPackage inputs.

## Documentation

The root README is the primary entry point. For architecture, phased workflow details, and audits, see:

- [docs/README.md](docs/README.md)
- [docs/ADDON_ARCHITECTURE.md](docs/ADDON_ARCHITECTURE.md) — Deterministic pipeline architecture and contracts.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Pipeline stage overview.
- [docs/DIAGRAMS.md](docs/DIAGRAMS.md)
- [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)
- [docs/DOCS_INDEX.md](docs/DOCS_INDEX.md)

## Python dependencies

Runtime dependencies are listed in [requirements.txt](requirements.txt).

Notes:

- Blender-provided modules (`bpy`, `bmesh`, `mathutils`) are intentionally not listed in `requirements.txt`.
- Python standard-library modules are intentionally not listed in `requirements.txt`.

## Repository Structure

```text
M1_DC_V6/
├── __init__.py          # Add-on entry point (bl_info, registration)
├── ops.py               # Orchestrator (thin wrappers, no operational logic)
├── ui.py                # Panel layout
├── settings.py          # Scene property definitions
├── auto_load.py         # Class auto-loader
├── pipeline/            # All operational logic
│   ├── citygml/         # CityGML import & materials
│   ├── linking/         # GML↔OSM linking, key normalization, caching
│   ├── operations/      # Blender operator implementations
│   ├── diagnostics/     # Legend encoding, spatial debug, geometry checks
│   ├── osm/             # GPKG reader
│   ├── spreadsheet/     # Building table data management
│   └── terrain/         # Terrain import, alignment, validation
├── utils/               # Shared helpers (logging, geometry, validation)
├── docs/                # Documentation & architecture
│   ├── dev/             # Developer-only debug/test scripts (not runtime)
│   └── Images/          # Documentation images
├── requirements.txt     # Python runtime dependencies
└── environment.yml      # Conda environment spec
```

`docs/dev/` contains standalone diagnostic scripts for development and debugging. They are never imported by the add-on. See [docs/dev/README.md](docs/dev/README.md).
