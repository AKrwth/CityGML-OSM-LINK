# BlenderGIS Urban Data Pipeline
<p align="center">
  <img src="docs/images/CityGML-OSM-LINK.png" width="700">
</p>

![Graphical Abstract](docs/images/CityGML-OSM-LINK.png)

<p align="center">
  <img src="docs/images/CityGML-OSM-LINK.png" width="700">
</p>

![Graphical Abstract](docs/images/CityGML-OSM-LINK.png)

## Overview

This Blender add-on implements a multi-phase pipeline for importing and integrating urban geospatial data. It combines terrain elevation data, building geometries from CityGML, and infrastructure features from OpenStreetMap into a unified 3D scene.

## What This Add-on Does

The pipeline performs the following operations:

1. **Terrain Import**: Fetches and processes Digital Elevation Model (DEM) data to create a terrain mesh
2. **CityGML Import**: Parses CityGML files and extracts building geometries with semantic attributes
3. **OSM/GPKG Import**: Loads GeoPackage data containing OpenStreetMap infrastructure features
4. **Spatial Linking**: Establishes geometric relationships between buildings and terrain features
5. **Materialization**: Writes semantic attributes from source data to Blender custom properties
6. **Inspection**: Provides query tools to examine materialized attributes

## What This Add-on Does NOT Do

- **Does not render or shade** buildings automatically (geometry only)
- **Does not provide export functionality** back to GIS formats
- **Does not perform geometric topology repair** (invalid input geometries may fail)
- **Does not handle extremely large datasets** (no tiling/streaming for city-scale models)
- **Does not preserve all CityGML semantics** (focuses on buildings, not all feature types)

## Pipeline Architecture

The add-on uses a **phased execution model** with the following stages:

### Phase 1: World Origin Establishment
- User defines a geographic anchor point (latitude, longitude)
- This origin becomes a **write-once invariant** for the entire scene
- All subsequent coordinate transformations reference this origin

### Phase 2: Terrain Loading
- Fetches DEM tiles covering the specified bounding box
- Resamples and merges heightmap data
- Generates terrain mesh in Blender local coordinates

### Phase 3: CityGML Loading
- Parses CityGML XML structure
- Extracts building geometries (walls, roofs)
- Transforms geographic coordinates to scene-local coordinates
- Drapes buildings onto terrain surface if elevation data exists

### Phase 4: OSM/GPKG Loading
- Loads vector features from GeoPackage files
- Filters by feature type (buildings, roads, etc.)
- Transforms geometries to match world origin and terrain

### Phase 5: Spatial Linking
- Computes footprint overlaps between buildings and terrain features
- Establishes parent-child relationships based on geometric containment
- Prepares association table for materialization

### Phase 6: Materialization
- Writes semantic attributes to Blender custom properties
- Transfers metadata from CityGML/OSM to mesh objects
- Creates queryable attribute structure on geometry

### Phase 7: Inspection
- Provides operators to query materialized attributes
- Generates statistical reports on loaded data
- Validates attribute completeness

## Key Constraints

- **Sequential Execution**: Later phases depend on earlier phases completing successfully
- **No Partial Rollback**: Pipeline cannot easily resume from intermediate failures
- **Memory-Bound**: All geometries loaded into RAM simultaneously
- **Single World Origin**: Coordinate system cannot be changed after terrain load

## Usage Context

This add-on is designed for:
- Urban planning visualization
- GIS data preview in 3D environment
- Academic research requiring combined terrain/building analysis
- Small to medium-scale city district modeling (not full city-scale)

## Technical Requirements

- Blender 3.x or later
- Python libraries: `lxml`, `pyproj`, `requests`, `numpy`
- Internet connection for DEM tile fetching
- Valid CityGML and GeoPackage input files with correct CRS metadata

## File Structure

```
M1_DC_V6/
├── __init__.py               # Addon entry point, operator registration
├── ops.py                    # Thin orchestrator (helper functions only, 4,865 LOC)
├── settings.py               # Scene properties and state tracking
├── ui.py                     # UI panels and layouts
├── pipeline/
│   ├── operations/           # ⭐ 57 Blender operators across 13 modules
│   │   ├── citygml_ops.py       # CityGML import operators (2 ops)
│   │   ├── debug_ops.py         # Debugging tools (9 ops)
│   │   ├── export_log_ops.py    # Export/logging operators (6 ops)
│   │   ├── face_attr_ops.py     # Face attribute tools (7 ops)
│   │   ├── inspector_ops.py     # Inspection/query operators (6 ops)
│   │   ├── legend_ops.py        # Legend management (1 op)
│   │   ├── linking_ops.py       # Spatial linking (1 op)
│   │   ├── materialize_ops.py   # Materialization (4 ops)
│   │   ├── spreadsheet_ops.py   # Spreadsheet operations (5 ops)
│   │   ├── sql_ops.py           # SQL/database operators (3 ops)
│   │   ├── terrain_ops.py       # Terrain import operators (7 ops)
│   │   ├── wizard_ops.py        # Wizard workflows (3 ops)
│   │   └── workflow_ops.py      # Pipeline orchestration (3 ops)
│   ├── terrain/              # DEM fetching and mesh generation
│   ├── citygml/              # CityGML parsing and geometry extraction
│   ├── osm/                  # GPKG loading and OSM feature processing
│   ├── linking/              # Spatial relationship computation
│   ├── diagnostics/          # Geometry validation and debugging tools
│   └── Legacy/               # ⚠️ Quarantined historical code (DO NOT IMPORT)
├── utils/
│   ├── common.py             # Shared utility functions
│   ├── validation.py         # Data validation framework
│   ├── logging_system.py     # Centralized logging
│   └── startup/              # Blender compatibility initialization
└── docs/
    ├── README.md             # This file
    ├── ADDON_ARCHITECTURE.md # Technical specification
    ├── DOCS_INDEX.md         # Documentation strategy
    └── _archive/             # Deprecated documentation
```

**Key Architectural Principles:**
- **Thin Orchestrator Pattern**: `ops.py` contains NO operator classes (all moved to `pipeline/operations/`)
- **Helper Function Delegation**: Operators call utility functions from `ops.py` for shared logic
- **Legacy Isolation**: `pipeline/Legacy/` is quarantined—no active imports permitted
- **Modular Operators**: 57 operators organized by functional category across 13 modules

## Limitations and Known Issues

- **Large Datasets**: Performance degrades significantly with >10,000 buildings
- **Error Handling**: Invalid geometries may cause silent failures
- **CRS Support**: Limited testing with non-European coordinate systems
- **LOD Handling**: All CityGML LOD levels merged without distinction
- **Attribute Schema**: Custom properties not validated against standards

For detailed technical implementation, see [ADDON_ARCHITECTURE.md](ADDON_ARCHITECTURE.md).
