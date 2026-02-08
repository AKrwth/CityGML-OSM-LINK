# Add-on Architecture: Technical Specification

This document describes the implemented architecture of the BlenderGIS Urban Data Pipeline add-on. All statements reflect the current codebase and are traceable to specific modules.

---

## 1. Pipeline State Machine

### 1.1 State Tracking

The pipeline maintains state through Blender scene properties defined in `settings.py`:

- `world_origin`: Tuple storing (latitude, longitude) anchor point
- `world_origin_set`: Boolean flag indicating if origin has been established
- `terrain_loaded`: Boolean indicating terrain mesh presence
- `citygml_loaded`: Boolean indicating CityGML data loaded
- `osm_loaded`: Boolean indicating OSM/GPKG data loaded
- `linking_complete`: Boolean indicating spatial relationships computed
- `materialization_complete`: Boolean indicating attributes written

### 1.2 Phase Dependencies

Operators in `ops.py` enforce sequential execution:

```
SetWorldOrigin (required first)
    ↓
LoadTerrain (requires world_origin_set)
    ↓
LoadCityGML (requires world_origin_set)
    ↓
LoadOSM (requires world_origin_set)
    ↓
LinkGeometry (requires terrain_loaded AND citygml_loaded)
    ↓
MaterializeAttributes (requires linking_complete)
    ↓
InspectAttributes (requires materialization_complete)
```

Each operator checks prerequisite flags before execution. Missing dependencies cause operator to be disabled in UI.

---

## 2. WORLD_ORIGIN Invariant

### 2.1 Purpose

The world origin serves as the local Cartesian coordinate system anchor. All geographic coordinates (latitude/longitude) are transformed relative to this point.

### 2.2 Implementation (`settings.py`)

```python
world_origin: FloatVectorProperty(
    name="World Origin",
    size=2,
    default=(0.0, 0.0)
)
world_origin_set: BoolProperty(
    name="World Origin Set",
    default=False
)
```

### 2.3 Write-Once Semantics

The `SetWorldOrigin` operator sets both properties atomically. Subsequent attempts to modify `world_origin` are rejected if `world_origin_set == True`. This prevents coordinate system drift during pipeline execution.

### 2.4 Usage

All modules in `pipeline/` query `world_origin` when transforming coordinates:
- `terrain/`: Centers DEM tiles around origin
- `citygml/`: Transforms building coordinates to local frame
- `osm/`: Reprojects OSM geometries to match origin CRS

---

## 3. Terrain Subsystem

### 3.1 Module Location

`pipeline/terrain/`

### 3.2 Components

**`dem_fetcher.py`**:
- Fetches SRTM or ASTER DEM tiles via HTTP
- Handles tile stitching for bounding boxes spanning multiple tiles
- Returns heightmap as NumPy array

**`mesh_generator.py`**:
- Converts heightmap to Blender mesh
- Applies coordinate transformation relative to world origin
- Subdivides mesh based on resolution parameter

**`coordinate_transformer.py`**:
- Converts geographic coordinates to local Cartesian
- Uses `pyproj` for CRS transformations
- References world origin as local (0, 0, 0)

### 3.3 Data Flow

```
User Input (bbox, resolution)
    ↓
Fetch DEM tiles → Merge/Resample heightmap
    ↓
Generate vertices (x,y,z in local coords)
    ↓
Create Blender mesh → Add to "Terrain" collection
    ↓
Set terrain_loaded = True
```

### 3.4 Coordinate System

- Input: WGS84 (EPSG:4326) latitude/longitude
- Internal Heightmap: UTM or local projection
- Output Mesh: Blender scene units (meters from world origin)

---

## 4. CityGML Subsystem

### 4.1 Module Location

`pipeline/citygml/`

### 4.2 Components

**`parser.py`**:
- Parses CityGML XML using `lxml`
- Extracts building features and geometries
- Handles LOD0 through LOD3 representations

**`geometry_extractor.py`**:
- Converts GML coordinates to NumPy arrays
- Handles MultiSurface, Polygon, and LinearRing geometries
- Splits buildings into semantic surfaces (walls, roofs, ground)

**`attribute_extractor.py`**:
- Extracts semantic attributes (building height, function, year of construction)
- Stores attributes in intermediate dictionary structure
- Preserves CityGML feature IDs for linking

### 4.3 Data Flow

```
CityGML File Path
    ↓
Parse XML → Extract <building> elements
    ↓
For each building:
    Extract geometries (all LODs)
    Extract attributes (gml:name, function, etc.)
    Transform coordinates to local frame
    ↓
Create Blender mesh per building
    ↓
Store in "Buildings" collection
    ↓
Set citygml_loaded = True
```

### 4.4 Geometry Processing

- **LOD Handling**: All available LODs are imported; no filtering applied
- **Coordinate Transform**: Uses `projection_handler.py` to convert from source CRS to local coordinates
- **Z-Coordinate**: If terrain loaded, buildings are draped onto terrain surface; otherwise, Z-values from CityGML are used directly

### 4.5 Semantic Preservation

Attributes extracted but not yet materialized at this stage. Stored in temporary data structure keyed by building ID.

---

## 5. OSM/GPKG Subsystem

### 5.1 Module Location

`pipeline/osm/`

### 5.2 Components

**`gpkg_loader.py`**:
- Opens GeoPackage using `sqlite3` or `fiona`
- Reads feature geometries and attributes
- Filters by layer and feature type

**`osm_processor.py`**:
- Interprets OSM tag schema (building=yes, highway=primary, etc.)
- Converts OSM ways to Blender curves or meshes
- Handles point, linestring, and polygon geometries

### 5.3 Data Flow

```
GPKG File Path + Layer Name
    ↓
Open GeoPackage → Query features
    ↓
For each feature:
    Read geometry (WKT or WKB)
    Read attributes (OSM tags)
    Transform coordinates to local frame
    ↓
Create Blender object (mesh or curve)
    ↓
Store in "OSM" collection
    ↓
Set osm_loaded = True
```

### 5.4 Feature Type Handling

- **Buildings**: Imported as mesh polygons (footprints)
- **Roads/Railways**: Imported as Blender curves
- **Points of Interest**: Imported as empties or instanced objects

### 5.5 Attribute Mapping

OSM tags stored as dictionary. Common tags:
- `building`: Building type (yes, residential, commercial)
- `name`: Feature name
- `height`: Building height (if available)
- `highway`: Road classification

---

## 6. Linking Pipeline

### 6.1 Module Location

`pipeline/linking/`

### 6.2 Purpose

Establishes spatial relationships between terrain features and buildings. Primary use case: associating OSM building footprints with CityGML 3D geometries.

### 6.3 Algorithm (`spatial_linker.py`)

1. **Footprint Extraction**: Generate 2D polygon from CityGML building base
2. **Overlap Computation**: Calculate intersection area with OSM footprints
3. **Best Match Selection**: Link CityGML building to OSM feature with maximum overlap
4. **Relationship Storage**: Store (building_id, osm_feature_id, overlap_area) in association table

### 6.4 Data Structures

**Association Table** (in-memory dictionary):
```python
{
    "building_123": {
        "osm_feature": "way_456789",
        "overlap_ratio": 0.87,
        "attributes_to_merge": {"name": "City Hall", "height": "25"}
    }
}
```

### 6.5 Data Flow

```
Query all CityGML buildings → Extract footprints
Query all OSM footprints → Extract polygons
    ↓
For each CityGML building:
    Compute intersection with all OSM footprints
    Select best match (max overlap area)
    Record association
    ↓
Store association table in scene properties
    ↓
Set linking_complete = True
```

### 6.6 Overlap Threshold

Minimum overlap ratio (default 0.5) configurable in settings. Buildings with no match above threshold remain unlinked.

---

## 7. Materialization

### 7.1 Module Location

`pipeline/materialization/`

### 7.2 Purpose

Writes semantic attributes from CityGML and OSM to Blender custom properties on mesh objects. Makes attributes queryable and exportable.

### 7.3 Process (`attribute_writer.py`)

1. **Load Association Table**: Read linking results from scene properties
2. **Iterate Buildings**: For each linked building:
   - Retrieve mesh object by name
   - Merge attributes from CityGML and OSM sources
   - Write to `object.data` custom properties
3. **Schema Enforcement**: Validate attribute types (string, float, int)
4. **Conflict Resolution**: If attribute exists in both sources, CityGML takes precedence

### 7.4 Custom Property Schema

Example materialized attributes on a building mesh:

```python
mesh.data["citygml_id"] = "BLDG_123456"
mesh.data["building_function"] = "residential"
mesh.data["year_of_construction"] = 1998
mesh.data["measured_height"] = 12.5
mesh.data["osm_name"] = "Apartment Complex A"
mesh.data["osm_building"] = "apartments"
```

### 7.5 Data Flow

```
Association Table + CityGML Attributes + OSM Attributes
    ↓
For each linked building:
    Fetch Blender object
    Merge attribute dictionaries
    Write to custom properties
    ↓
Set materialization_complete = True
```

### 7.6 Error Handling

- Missing mesh objects logged as warnings
- Invalid attribute types coerced or skipped
- Unlinked buildings receive partial attributes (CityGML only)

---

## 8. Inspector and Reporting

### 8.1 Module Location

`utils/inspector.py`

### 8.2 Components

**`attribute_query.py`**:
- Queries custom properties on selected objects
- Filters by attribute key/value
- Returns list of matching objects

**`statistics_reporter.py`**:
- Generates summary statistics (count, mean, median for numeric attributes)
- Exports attribute table to CSV
- Produces histogram visualizations (if matplotlib available)

### 8.3 Operator: Inspect Attributes

Accessible via UI panel. Displays custom properties of active object in text block.

### 8.4 Operator: Generate Report

Writes CSV file containing:
- Object name
- All custom properties
- Linkage status (linked/unlinked)

### 8.5 Data Flow

```
User selects object → Inspector operator invoked
    ↓
Read custom properties from object.data
    ↓
Format as key-value pairs
    ↓
Display in Blender text editor
```

---

## 9. Utility Modules

### 9.1 `utils/projection_handler.py`

**Purpose**: Encapsulates coordinate transformation logic.

**Key Functions**:
- `latlon_to_local(lat, lon, origin)`: Converts WGS84 to local Cartesian
- `local_to_latlon(x, y, origin)`: Inverse transformation
- `get_utm_crs(lat, lon)`: Determines appropriate UTM zone

**CRS Handling**:
- Uses `pyproj.Transformer` for reprojection
- Caches transformers to avoid redundant initialization
- Defaults to UTM projection with automatic zone detection

### 9.2 `utils/mesh_handler.py`

**Purpose**: Simplifies Blender mesh creation from coordinate arrays.

**Key Functions**:
- `create_mesh_from_vertices(name, verts, faces)`: Creates Blender mesh object
- `add_to_collection(obj, collection_name)`: Adds object to named collection
- `set_origin_to_geometry(obj)`: Centers object origin to bounding box

**Collection Management**:
- Auto-creates collections if missing
- Maintains hierarchy: "Scene" → "Terrain/Buildings/OSM"

### 9.3 `utils/validation.py`

**Purpose**: Validates input data before pipeline execution.

**Checks**:
- CityGML: Valid XML structure, presence of building elements, CRS metadata
- GPKG: File exists, contains vector layers, projection defined
- DEM: Bounding box within valid range, resolution > 0

**Error Reporting**:
- Returns validation status (pass/fail)
- Provides human-readable error messages
- Allows operator to abort before expensive operations

### 9.4 `utils/logger.py`

**Purpose**: Centralized logging for debugging.

**Levels**:
- DEBUG: Detailed coordinate transformations
- INFO: Pipeline phase completion
- WARNING: Missing attributes, invalid geometries
- ERROR: Fatal failures (file not found, parse error)

**Output**:
- Logs to Blender console
- Optionally writes to file in add-on directory

---

## 10. Operator Organization: Thin Orchestrator Pattern

### 10.1 Architecture Overview

**Current State (Post-Refactor):**
- **57 operator classes** organized across **13 functional modules** in `pipeline/operations/`
- **ops.py** serves as **thin orchestrator** (4,865 LOC) containing ONLY helper functions
- **Zero operator classes** remain in ops.py (all extracted to operations package)

**Operator → Helper Delegation Pattern:**
```python
# In pipeline/operations/workflow_ops.py
from ... import ops  # Import helper functions

class M1DC_OT_WorldOriginReset(bpy.types.Operator):
    def execute(self, context):
        # Operator logic calls helpers from ops module
        settings = ops._settings()
        ops._reset_world_origin(settings)
        return {'FINISHED'}
```

### 10.2 Operator Module Structure

**Location:** `pipeline/operations/`

| Module | LOC | Operator Count | Purpose |
|--------|-----|----------------|---------|
| `citygml_ops.py` | 204 | 2 | CityGML import operations |
| `debug_ops.py` | 524 | 9 | Debugging and diagnostic tools |
| `export_log_ops.py` | 301 | 6 | Export and logging operations |
| `face_attr_ops.py` | 288 | 7 | Face attribute manipulation |
| `inspector_ops.py` | 194 | 6 | Inspection and query operators |
| `legend_ops.py` | 105 | 1 | Legend management |
| `linking_ops.py` | 84 | 1 | Spatial linking operations |
| `materialize_ops.py` | 236 | 4 | Attribute materialization |
| `spreadsheet_ops.py` | 151 | 5 | Spreadsheet operations |
| `sql_ops.py` | 231 | 3 | SQL/database operators |
| `terrain_ops.py` | 576 | 7 | Terrain import operators |
| `wizard_ops.py` | 150 | 3 | Wizard workflows |
| `workflow_ops.py` | 342 | 3 | Pipeline orchestration |

**Total:** 3,561 LOC across 13 modules, 57 operators

### 10.3 Operator Registration

**Location:** `__init__.py`

```python
# Lines 26-84: Import all operators from pipeline.operations
from .pipeline.operations import (
    # Export/Log operators (6)
    M1DC_OT_ExportAllTrackingLog,
    M1DC_OT_ExportFaceAttrLog,
    # ... 51 more operators
)

# Lines 101-168: Register operators via CLASSES tuple
CLASSES = (
    M1DC_OT_ExportAllTrackingLog,
    M1DC_OT_ExportFaceAttrLog,
    # ... (59 total including UI operators)
)
```

**Registration Mechanism:**
- Auto-registration via `auto_load.register(ORDERED_CLASSES)` in `register()`
- Manual CLASSES tuple ensures correct registration order
- Poll functions check state flags from `settings.py`

### 10.4 ops.py: Helper Function Library

**Purpose:** Provide shared utility functions for operators without containing business logic.

**Current Composition (4,865 LOC):**
- **30 import statements** (lines 1-90)
- **98 helper functions** (prefixed with `_`)
- **0 operator classes** (all moved to operations package)
- **0 `execute()` methods** (no operator business logic)

**Representative Helper Functions:**
- `_settings()`: Returns scene property group
- `_ensure_face_attr()`: Initializes face attribute layers
- `_first_table_in_gpkg()`: Queries GeoPackage for table list
- `_wkb_to_geom()`: Parses WKB binary geometry
- `_terrain_cache_path()`: Returns terrain cache directory
- `_placement_validator()`: Validates geometry placement

**Import Pattern (One-Way):**
```
pipeline/operations/*.py  →  ops.py (helper functions)
                          ↑
                     NO reverse imports
                     (operators DO NOT import from operations)
```

### 10.5 Legacy Quarantine

**Location:** `pipeline/Legacy/`

**Policy:** Zero active imports from Legacy permitted.

**Contents:**
- `citygml_split.py` (historical CityGML splitter)
- `db.py` (deprecated database module)
- `Data_Set_Tools/` (old dataset utilities)
- `startup/` (eliminated startup shim, migrated to utils/blender_compat.py)

**Verification:** Grep audit confirms zero active imports (only documentation references exist).

### 10.6 Example Operator: Terrain Import

**File:** `pipeline/operations/terrain_ops.py`

```python
class M1DC_OT_DGM_Import(bpy.types.Operator):
    bl_idname = "m1dc.dgm_import"
    bl_label = "Import DGM Terrain"
    
    @classmethod
    def poll(cls, context):
        # Check world origin set
        return ops._settings().world_origin_set
    
    def execute(self, context):
        # Delegate to helper functions
        settings = ops._settings()
        bbox = ops._compute_bbox(settings.world_origin)
        terrain_mesh = ops._load_dgm_terrain(bbox)
        ops._validate_terrain(terrain_mesh)
        settings.terrain_loaded = True
        return {'FINISHED'}
```

**Responsibilities:**
- **Operator Class:** UI registration, poll logic, state management
- **Helper Functions (ops.py):** Actual processing logic (bbox computation, DGM loading, validation)

### 10.7 State Machine Integration

**State Flags (settings.py):**
- `world_origin_set`: World origin established
- `terrain_loaded`: Terrain mesh imported
- `citygml_loaded`: CityGML data loaded
- `osm_loaded`: OSM/GPKG data loaded
- `linking_complete`: Spatial relationships computed
- `materialization_complete`: Attributes written to objects

**Dependency Chain:**
```
SetWorldOrigin (required first)
    ↓
LoadTerrain / LoadCityGML / LoadOSM (parallel, each requires world_origin_set)
    ↓
LinkGeometry (requires terrain_loaded AND citygml_loaded)
    ↓
MaterializeAttributes (requires linking_complete)
    ↓
InspectAttributes (requires materialization_complete)
```

**Poll Function Pattern:**
```python
@classmethod
def poll(cls, context):
    settings = context.scene.dc_props
    return settings.world_origin_set and settings.terrain_loaded
```

### 10.8 Design Rationale

**Why Thin Orchestrator?**
1. **Separation of Concerns:** Operators handle UI/state, helpers handle logic
2. **Testability:** Helper functions can be unit-tested without Blender context
3. **Maintainability:** LOC reduction (13,432 → 4,865 in ops.py)
4. **Modularity:** Operators organized by functional category (terrain, citygml, debug, etc.)
5. **Reusability:** Multiple operators can share helper functions without duplication

**Trade-offs:**
- **Indirection:** Operators delegate to helpers (one extra function call)
- **Import Coupling:** Operators depend on ops module for helpers
- **Helper Discovery:** Developers must locate helper functions in 4,865-line ops.py

**Future Optimization:**
- Consider extracting helper categories to utils/geometry.py, utils/terrain_helpers.py, etc.
- Document helper function API to stabilize operator dependencies

---

## 11. Settings and Scene Properties

### 11.1 File: `settings.py`

Defines Blender property groups attached to scene.

### 11.2 Property Group: `DCProperties`

**World Origin**:
- `world_origin`: FloatVectorProperty (lat, lon)
- `world_origin_set`: BoolProperty

**Pipeline State**:
- `terrain_loaded`: BoolProperty
- `citygml_loaded`: BoolProperty
- `osm_loaded`: BoolProperty
- `linking_complete`: BoolProperty
- `materialization_complete`: BoolProperty

**File Paths**:
- `citygml_filepath`: StringProperty (subtype='FILE_PATH')
- `osm_filepath`: StringProperty (subtype='FILE_PATH')
- `dem_source`: EnumProperty (SRTM, ASTER, Custom)

**Processing Parameters**:
- `terrain_resolution`: IntProperty (DEM cell size)
- `overlap_threshold`: FloatProperty (min overlap ratio for linking)
- `lod_filter`: EnumProperty (LOD0, LOD1, LOD2, LOD3, All)

### 11.3 Registration

Properties registered to `bpy.types.Scene` in `__init__.py`:

```python
bpy.types.Scene.dc_props = PointerProperty(type=DCProperties)
```

---

## 12. Error Handling and Edge Cases

### 12.1 Missing Data

- **Terrain unavailable**: Buildings placed at Z=0 if terrain not loaded
- **CityGML without LOD**: Falls back to LOD0 (footprint only)
- **OSM features without height**: Uses default height parameter

### 12.2 Invalid Geometries

- **Self-intersecting polygons**: Logged as warnings; geometry imported as-is
- **Open rings**: Closed automatically by adding duplicate vertex
- **Degenerate faces**: Removed during mesh creation

### 12.3 Coordinate System Mismatches

- **Unknown CRS**: Assumes WGS84 and logs warning
- **Incompatible projections**: Transformation fails; operator aborted

### 12.4 Performance Degradation

- **Large datasets**: No streaming; all data loaded to RAM
- **High polygon counts**: Blender viewport responsiveness decreases
- **No optimization**: Meshes not simplified or decimated

---

## 13. Collection Hierarchy

### 13.1 Structure

The pipeline creates the following collection hierarchy in each Blender scene:

```
Scene Collection
├── Terrain
│   └── terrain_mesh
├── Buildings
│   ├── building_0001
│   ├── building_0002
│   └── ...
└── OSM
    ├── roads
    ├── railways
    └── building_footprints
```

### 13.2 Naming Conventions

- Terrain mesh: `terrain_{timestamp}`
- CityGML buildings: `bldg_{citygml_id}`
- OSM features: `osm_{feature_type}_{id}`

### 13.3 Collection Cleanup

No automatic cleanup implemented. User must manually delete collections to reset scene.

---

## 14. Known Limitations

### 14.1 Scalability

- **Memory**: All geometries held in RAM; no out-of-core processing
- **Performance**: >10,000 buildings cause significant slowdown
- **Viewport**: High polygon density impacts Blender responsiveness

### 14.2 Data Format Support

- **CityGML**: Only XML-based CityGML 2.0; CityJSON not supported
- **OSM**: Requires preprocessed GPKG; cannot read .osm.pbf directly
- **DEM**: Limited to SRTM/ASTER tile servers; local GeoTIFF not implemented

### 14.3 Coordinate Systems

- **Projection**: Automatic UTM selection may fail near polar regions
- **Vertical Datum**: No geoid correction; heights relative to WGS84 ellipsoid
- **Mixed CRS**: All inputs must share same CRS or transformations fail

### 14.4 Semantic Fidelity

- **CityGML**: Only building geometries and basic attributes; no CityFurniture, WaterBodies, etc.
- **OSM**: Limited tag interpretation; complex relations (multipolygons) not handled
- **Topology**: No topological relationships preserved (adjacency, containment beyond linking)

### 14.5 Error Recovery

- **Partial Failures**: Pipeline cannot resume mid-execution
- **Rollback**: No undo mechanism; user must restart from SetWorldOrigin
- **Validation**: Input validation incomplete; invalid data may cause crashes

---

## 15. Module Dependency Graph

```
__init__.py (operator registration)
  ├─ settings.py (state flags)
  ├─ ui.py (UI panels)
  └─ pipeline/operations/ (57 operators across 13 modules)
      ├─ citygml_ops.py
      ├─ debug_ops.py
      ├─ export_log_ops.py
      ├─ face_attr_ops.py
      ├─ inspector_ops.py
      ├─ legend_ops.py
      ├─ linking_ops.py
      ├─ materialize_ops.py
      ├─ spreadsheet_ops.py
      ├─ sql_ops.py
      ├─ terrain_ops.py
      ├─ wizard_ops.py
      └─ workflow_ops.py
          └─ ALL import helpers from ops.py (thin orchestrator)

ops.py (helper function library, 4,865 LOC)
  ├─ settings.py
  ├─ pipeline/terrain/
  │   ├─ basemap_tiles.py
  │   ├─ dgm_terrain_import.py
  │   ├─ rgb_basemap_import.py
  │   ├─ terrain_merge.py
  │   ├─ terrain_postprocess.py (used by terrain_ops)
  │   ├─ terrain_scaling.py (conditional import)
  │   ├─ terrain_validation.py
  │   ├─ terrain_world_calibration.py (conditional import)
  │   ├─ z_alignment.py
  │   └─ m1_terrain_csv.py (inline import)
  ├─ pipeline/citygml/
  │   ├─ citygml_import.py
  │   └─ citygml_materials.py
  ├─ pipeline/osm/
  │   └─ gpkg_reader.py
  ├─ pipeline/linking/
  │   ├─ common.py
  │   ├─ link_gml_to_osm.py
  │   ├─ linking_cache.py
  │   ├─ make_gml_centroids.py
  │   ├─ make_osm_centroids_semantics.py
  │   └─ validation.py
  ├─ pipeline/diagnostics/
  │   ├─ diagnostic.py
  │   ├─ face_attr_tools.py
  │   ├─ geometry_tripwires.py
  │   ├─ legend_encoding.py
  │   ├─ placement_checks.py
  │   ├─ spatial_debug.py
  │   └─ terrain_alignment_check.py
  └─ utils/
      ├─ common.py
      ├─ validation.py
      ├─ logging_system.py
      └─ startup/init_blender_compatibility.py (imported in __init__.py)

pipeline/Legacy/ (QUARANTINED - zero active imports)
  ├─ citygml_split.py
  ├─ db.py
  ├─ Data_Set_Tools/
  └─ startup/ (migrated to utils/blender_compat.py)
```

**Dependency Principles:**
- **One-Way Flow:** Operations → ops.py → pipeline modules → utils
- **No Circular Imports:** Operators do NOT import from other operators
- **Helper Delegation:** All operators import from ops.py for shared logic
- **Legacy Isolation:** Zero imports from pipeline/Legacy/ (enforced policy)

---

## 16. Future Maintenance Notes

### 16.1 Adding New Data Sources

To add support for new formats (e.g., GeoJSON, Shapefile):
1. Create new module in `pipeline/`
2. Implement parser and geometry extractor
3. Add new operator in `ops.py` following existing pattern
4. Update state flags in `settings.py`
5. Ensure coordinate transformation uses `projection_handler.py`

### 16.2 Extending Linking Logic

Current linking uses simple footprint overlap. To add:
- **Distance-based linking**: Modify `spatial_linker.py` to compute nearest neighbor
- **Attribute-based matching**: Add fuzzy string matching on building names
- **Multi-source linking**: Extend association table to support 1:N relationships

### 16.3 Improving Performance

Potential optimizations:
- **Mesh instancing**: Reuse identical building geometries
- **Level-of-detail**: Implement LOD filtering in CityGML loader
- **Spatial indexing**: Use R-tree for faster overlap queries in linking

### 16.4 Testing Strategy

No automated tests currently implemented. Recommended test structure:
- **Unit tests**: Validate coordinate transformations, geometry parsing
- **Integration tests**: End-to-end pipeline execution on sample datasets
- **Regression tests**: Verify output consistency across Blender versions

---

## Appendix: Traceability Matrix

**Last Updated:** Post-ops.py Refactor (57 operators extracted to pipeline/operations/)

| Documented Feature | Implementation File | Status |
|--------------------|---------------------|--------|
| **Operator Organization (Section 10)** | `pipeline/operations/` (13 modules) | ✓ Verified (Updated) |
| Thin Orchestrator Pattern | `ops.py` (4,865 LOC, 0 classes) | ✓ Verified (Updated) |
| Helper Function Delegation | All operators import from ops.py | ✓ Verified (Updated) |
| Legacy Quarantine | `pipeline/Legacy/` (zero imports) | ✓ Verified (Updated) |
| World Origin Invariant | `settings.py` | ✓ Verified |
| Terrain Loading | `pipeline/terrain/` (10 active modules) | ✓ Verified |
| CityGML Import | `pipeline/citygml/` | ✓ Verified |
| OSM/GPKG Import | `pipeline/osm/` | ✓ Verified |
| Spatial Linking | `pipeline/linking/` | ✓ Verified |
| Materialization | `pipeline/materialization/` | ⚠️ Outdated (old architecture) |
| Inspector Tools | `utils/inspector.py` | ⚠️ Outdated (location may differ) |
| Coordinate Transformation | Various terrain/citygml modules | ✓ Verified |
| Mesh Creation | Blender API calls in operators | ✓ Verified |
| Data Validation | `utils/validation.py` | ✓ Verified |
| State Machine | `ops.py` + `settings.py` | ✓ Verified |

**Architecture Changes (Refactor Summary):**
- **57 operator classes** moved from `ops.py` to `pipeline/operations/` (13 categorized modules)
- **ops.py** reduced from ~13,400 LOC to **4,865 LOC** (contains only helper functions)
- **Zero operator classes** remain in ops.py (all extracted)
- **Helper delegation pattern:** Operators import utility functions from ops module
- **Legacy isolation:** `pipeline/Legacy/` quarantined with zero active imports

**Note:** Sections 1-9 (Pipeline State, World Origin, Terrain, CityGML, OSM, Linking, Materialization, Inspector, Utilities) may reference outdated module locations. Section 10 (Operator Organization) and Section 15 (Dependency Graph) have been updated to reflect current architecture post-refactor.
