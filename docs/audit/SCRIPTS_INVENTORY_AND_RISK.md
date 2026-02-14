# M1_DC_V6 Add-on: Scripts Inventory and Modularization Risk Assessment

**Generated:** 2026-02-08  
**Purpose:** Complete inventory of all Python scripts with risk ratings for future modularization  
**Status:** READ-ONLY ANALYSIS (No code changes)

---

## A) Orchestrator Architecture Rule

**Core Principle:** `ops.py` is the **orchestrator** and operator entry point.

### Rules of the Land (DO NOT VIOLATE):
1. **ops.py stays the orchestrator** — All Blender operators (`bpy.types.Operator`) must remain in `ops.py`
2. **ops.py calls, others do work** — Pure functions and helpers can be extracted into new modules
3. **Operators are never moved** — Operators define the add-on's public API and must stay in `ops.py`
4. **Future modularization** — Only pure logic will be extracted (e.g., attribute schema, DB queries, OSM key handling)

### Why This Matters:
- `ops.py` is 13,355 lines — the largest file by far (10x the next largest)
- Contains ~75+ Blender operators that define the add-on's functionality
- Future refactoring will extract only **pure helper functions** while keeping operators in place
- Key extraction targets identified: `ops/attrs.py`, `ops/osm_keys.py`, `ops/db_paths.py`, `ops/materialize_phase3.py`, `ops/materialize_phase4_mkdb.py`

---

## B) Complete Scripts Inventory

**Total Python files:** 50  
**Total lines of code:** ~24,000 (estimated across all modules)

| Relative Path | Lines | Main Responsibility | Risk | Risk Reason |
|---------------|-------|---------------------|------|-------------|
| `ops.py` | 13355 | **Orchestrator**: All Blender operators, face attribute handling, DB queries, linking logic, spreadsheet, inspector, materialization | **HIGH** | • Contains 75+ operators (cannot move)<br>• Central pipeline orchestration<br>• Heavy side effects (mesh writes, DB access)<br>• 200+ import dependencies<br>• Known bug hotspots from Phase 3-12 |
| `pipeline/terrain/terrain_validation.py` | 1068 | Terrain validation, auto-correction for scale/Z-offset issues, geo-referencing checks | **MED** | • Shared validation logic used by multiple operators<br>• Touches mesh scale (global state)<br>• Complex decision trees for Aachen dataset quirks |
| `pipeline/diagnostics/legend_encoding.py` | 919 | Automatic categorical encoding (string → int codes), legend CSV generation, GPKG column detection | **MED** | • Shared by materialization phases<br>• In-memory caches (_ENCODE_CACHE, _DECODE_CACHE)<br>• READONLY GPKG connections (safe) |
| `pipeline/terrain/m1_basemap.py` | 845 | Basemap OBJ+JSON import, M1DC_WORLD_ORIGIN setup, axis/origin correction | **MED** | • Initializes world origin (critical state)<br>• Complex geometry transforms<br>• Used by terrain import operators |
| `pipeline/citygml/citygml_import.py` | 880 | CityGML folder import, tile coordinate parsing, auto-gridding, world origin inference | **MED** | • Sets world origin from tiles (critical)<br>• Heavy bpy context usage<br>• Multiple CityGML importer fallbacks |
| `settings.py` | 647 | Blender PropertyGroup definitions, UI property callbacks, cache invalidation triggers | **HIGH** | • Defines add-on state (bpy.types.PropertyGroup)<br>• Callbacks trigger ops.py functions (_on_gpkg_path_changed)<br>• Circular import risk with ops.py |
| `ui.py` | 526 | UI panels, spreadsheet/inspector display, column selection operators | **MED** | • Contains 2 operators (M1DC_OT_SelectTable, M1DC_OT_SelectOSMFeatureTable)<br>• Heavy ops.py dependency<br>• Mixed UI and logic |
| `pipeline/diagnostics/diagnostic.py` | 499 | Full diagnostic report generation, mesh/DB/state validation, CSV export | **LOW** | • Pure reporting function<br>• Minimal side effects<br>• Safe extraction candidate |
| `utils/common.py` | 480 | Shared utilities: world origin, CRS transforms, DB readonly access, GPKG path resolution | **MED** | • Core utilities used everywhere<br>• DB connection helpers (critical for safety)<br>• Circular import risk (imported by ops.py and pipelines) |
| `pipeline/diagnostics/placement_checks.py` | 356 | XY placement validation, terrain/CityGML alignment checks | **LOW** | • Pure validation helpers<br>• Minimal state |
| `pipeline/linking/linking_cache.py` | 313 | Link database caching, file signature validation, hash-based cache invalidation | **MED** | • Shared cache logic for linking operators<br>• File I/O and DB queries |
| `pipeline/terrain/basemap_tiles.py` | 311 | GeoTIFF tile discovery, bbox computation, WCS URL generation | **LOW** | • Pure tile discovery logic<br>• No Blender state |
| `pipeline/terrain/terrain_scaling.py` | 312 | Proven terrain scale fix automation, anisotropic correction | **MED** | • Shared by validation and operators<br>• Mesh transform side effects |
| `pipeline/terrain/terrain_world_calibration.py` | 338 | Terrain world bounds calibration, bbox size computation | **MED** | • Shared geometry helpers<br>• Used by import operators |
| `pipeline/terrain/z_alignment.py` | 327 | CityGML Z-offset correction, terrain median Z detection | **MED** | • Shared Z alignment logic<br>• Mesh modification side effects |
| `pipeline/terrain/terrain_postprocess.py` | 326 | Terrain post-import cleanup, Z-offset application | **LOW** | • Standalone processing steps<br>• Clear function boundaries |
| `pipeline/terrain/rgb_basemap_import.py` | 307 | RGB basemap GeoTIFF import via GDAL, material setup | **LOW** | • Standalone import logic<br>• Minimal dependencies |
| `pipeline/terrain/dgm_terrain_import.py` | 296 | DGM terrain import via GDAL, mesh generation | **LOW** | • Standalone import logic<br>• Clear entry points |
| `pipeline/terrain/terrain_merge.py` | 294 | Tile merging logic, mesh combination | **MED** | • Shared by multiple terrain operators<br>• Heavy mesh operations |
| `pipeline/linking/link_gml_to_osm.py` | 348 | CityGML-OSM linking algorithm, grid-based nearest neighbor search | **LOW** | • Pure linking algorithm<br>• No Blender dependency<br>• Standalone script |
| `pipeline/Legacy/Data_Set_Tools/prepare_terrain_rgb_wcs.py` | 278 | Legacy: WCS terrain/RGB download script | **LOW** | • Legacy tool (not used in pipeline)<br>• Safe to ignore |
| `pipeline/Legacy/Data_Set_Tools/prepare_rgb_tiles_only.py` | 269 | Legacy: RGB tiles download script | **LOW** | • Legacy tool (not used in pipeline)<br>• Safe to ignore |
| `pipeline/terrain/m1_terrain_csv.py` | 266 | Terrain CSV export from Blender mesh | **LOW** | • Standalone export utility<br>• Clear boundaries |
| `pipeline/Legacy/Data_Set_Tools/organize_and_downscale_dop.py` | 255 | Legacy: DOP tile organization script | **LOW** | • Legacy tool (not used in pipeline)<br>• Safe to ignore |
| `pipeline/diagnostics/terrain_alignment_check.py` | 255 | Terrain alignment validation report | **LOW** | • Pure validation function<br>• Minimal state |
| `pipeline/osm/gpkg_reader.py` | 217 | GPKG table/column enumeration, feature table detection | **MED** | • Shared GPKG schema utilities<br>• Used by linking and spreadsheet |
| `pipeline/linking/validation.py` | 213 | Link validation, duplicate detection, confidence thresholds | **LOW** | • Pure validation helpers<br>• No side effects |
| `pipeline/diagnostics/face_attr_tools.py` | 203 | Face attribute debugging tools, value distribution analysis | **LOW** | • Pure diagnostic helpers<br>• Safe extraction candidate |
| `__init__.py` | 184 | Add-on entry point, class registration, imports from ops.py | **HIGH** | • Imports all operators from ops.py<br>• Registration order critical<br>• Must import terrain_merge before ops.py (fragile) |
| `pipeline/Legacy/citygml_split.py` | 169 | Legacy: CityGML file splitting tool | **LOW** | • Legacy tool (not used in pipeline)<br>• Safe to ignore |
| `pipeline/diagnostics/geometry_tripwires.py` | 165 | Geometry sanity checks (degenerate faces, zero normals) | **LOW** | • Pure geometry validation<br>• Safe extraction candidate |
| `pipeline/linking/make_osm_centroids_semantics.py` | 164 | OSM centroid DB creation script (standalone) | **LOW** | • Standalone preprocessing script<br>• No Blender dependency |
| `pipeline/linking/make_gml_centroids.py` | 155 | CityGML centroid DB creation script (standalone) | **LOW** | • Standalone preprocessing script<br>• No Blender dependency |
| `utils/logging_system.py` | 144 | Centralized logging, progress tracking, LoopProgressTracker | **LOW** | • Pure logging utilities<br>• No side effects<br>• Stable since Phase 13 |
| `pipeline/terrain/terrain_discovery.py` | 135 | Terrain file discovery, cache folder management | **LOW** | • Pure file discovery logic<br>• No state changes |
| `pipeline/citygml/citygml_materials.py` | 127 | CityGML material setup for collections | **LOW** | • Standalone material helper<br>• Minimal dependencies |
| `pipeline/Legacy/db.py` | 65 | Legacy: Old DB utilities | **LOW** | • Legacy (replaced by utils/common.py)<br>• Safe to ignore |
| `utils/startup/init_blender_compatibility.py` | 51 | pkg_resources shim for Blender Python | **LOW** | • Startup shim (safe no-op)<br>• No changes needed |
| `auto_load.py` | 39 | Robust class registration helper | **LOW** | • Pure registration utility<br>• Stable boilerplate |
| `pipeline/citygml/__init__.py` | 27 | CityGML module exports | **LOW** | • Package __init__ (exports only) |
| `pipeline/osm/__init__.py` | 20 | OSM module exports | **LOW** | • Package __init__ (exports only) |
| `pipeline/terrain/__init__.py` | 17 | Terrain module exports | **LOW** | • Package __init__ (exports only) |
| `pipeline/diagnostics/__init__.py` | 49 | Diagnostics module exports, convenience imports | **LOW** | • Package __init__ (exports only) |
| `pipeline/diagnostics/spatial_debug.py` | 12 | Spatial debug placeholder | **LOW** | • Empty/minimal file |
| `pipeline/linking/__init__.py` | 10 | Linking module exports | **LOW** | • Package __init__ (exports only) |
| `pipeline/__init__.py` | 9 | Pipeline root exports | **LOW** | • Package __init__ (exports only) |
| `pipeline/linking/common.py` | 8 | Linking shared constants/helpers | **LOW** | • Minimal helpers |
| `utils/validation.py` | 7 | Validation utilities (minimal) | **LOW** | • Nearly empty |
| `utils/__init__.py` | 1 | Utils package marker | **LOW** | • Empty __init__ |
| `utils/startup/__init__.py` | 1 | Startup package marker | **LOW** | • Empty __init__ |

---

## C) Top Risk Files (Top 5)

### 1. **`ops.py`** (13,355 lines) — **CRITICAL HIGH RISK**

**Why it's risky:**
- **Operators cannot be moved** — Contains 75+ Blender operators that define the add-on's public API
- **Massive orchestration logic** — Handles pipelines, linking, materialization, spreadsheet, inspector, validation
- **Heavy side effects** — Direct mesh attribute writes, DB queries, scene state mutations
- **Circular import hazards** — Imported by `__init__.py`, `ui.py`, `settings.py` (callbacks)
- **Known bug hotspots** — Phases 3-12 documented fixes for attribute clobber, DB locking, materialization crashes

**Future modularization targets (Pure functions only):**
```
ops.py (13,355 lines)
├─ ops/attrs.py (~500 lines)          # ensure_face_attr, _ensure_face_attrs, clobber guard
├─ ops/osm_keys.py (~300 lines)       # _get_osm_key_col, sanitize_attr_name, _normalize_osm_id
├─ ops/db_paths.py (~200 lines)       # _resolve_feature_db_path, _table_exists, _list_user_tables
├─ ops/materialize_phase3.py (~800 lines)  # _materialize_osm_features (GPKG feature loading)
├─ ops/materialize_phase4_mkdb.py (~600 lines)  # load_feature_map_from_mkdb, _materialize_legend_codes
└─ ops.py (remaining ~10,955 lines)   # ALL OPERATORS + orchestration
```

**Evidence:**
```python
# Lines 983-1024: ensure_face_attr with clobber guard (FIX2)
def ensure_face_attr(mesh, name, data_type):
    """Ensure face attribute exists; skip if type mismatch (FIX2: clobber guard)."""

# Lines 2016-2602: _materialize_osm_features (Phase 3 massive GPKG query)
def _materialize_osm_features(mesh, osm_id_attr, gpkg_path):
    """Query GPKG for OSM features and write to face attributes."""

# Lines 7634-8079: M1DC_OT_MaterializeLinks (Phase 4 operator entry point)
class M1DC_OT_MaterializeLinks(Operator):
    """Materialize OSM features from GPKG into face attributes."""
```

---

### 2. **`settings.py`** (647 lines) — **HIGH RISK**

**Why it's risky:**
- **Defines add-on state** — All Blender PropertyGroups (bpy.types.PropertyGroup) live here
- **Callback hell** — Property update callbacks trigger ops.py functions (_on_gpkg_path_changed → ops.spreadsheet_invalidate_and_rebuild)
- **Circular import risk** — Imports ops.py in callbacks, ops.py uses settings properties
- **UI mutation side effects** — Changing a property can trigger expensive cache rebuilds

**Evidence:**
```python
# Lines 13-17: Circular import in callback
def _on_spreadsheet_table_changed(self, context):
    try:
        from . import ops
        ops.spreadsheet_invalidate_and_rebuild(context, self, reason="table_changed")
```

**Modularization challenge:**
- Cannot move PropertyGroups (Blender API requirement)
- Callbacks must stay with properties (Blender convention)
- Future fix: Extract pure callback logic into `ops/callbacks.py`, keep thunks in settings.py

---

### 3. **`__init__.py`** (184 lines) — **HIGH RISK**

**Why it's risky:**
- **Registration order is critical** — Incorrect order causes Blender crashes
- **Fragile import order** — Must import `terrain_merge` before `ops.py` (workaround for import cycle)
- **Imports all operators** — 50+ operator classes from ops.py
- **Single point of failure** — If registration fails, entire add-on breaks

**Evidence:**
```python
# Lines 23-24: Critical import order workaround
from .pipeline.terrain import terrain_merge  # noqa: F401
from .ops import (
    M1DC_OT_ImportBasemapTerrain,
    ...
```

---

### 4. **`ui.py`** (526 lines) — **MEDIUM-HIGH RISK**

**Why it's risky:**
- **Contains operators** — 2 operators (M1DC_OT_SelectTable, M1DC_OT_SelectOSMFeatureTable) should be in ops.py
- **Mixed UI and logic** — Panel draw code mixed with state queries (inspector, spreadsheet)
- **Heavy ops.py dependency** — Calls ~20 ops.py functions directly
- **Draw context restrictions** — Cannot mutate ID properties in draw() (error-prone)

**Future fix:**
- Move operators to ops.py (consistency)
- Extract UI state queries into `ops/ui_state.py`

---

### 5. **`utils/common.py`** (480 lines) — **MEDIUM RISK**

**Why it's risky:**
- **Central dependency** — Imported by ops.py, all pipelines, settings.py
- **Circular import potential** — Currently safe, but fragile
- **Mixes concerns** — World origin (stateful), CRS transforms (pure), DB access (I/O), GPKG resolution (filesystem)

**Strengths:**
- Well-documented
- Stable since Phase 10 (utils consolidation)
- READONLY DB access helpers prevent corruption

---

## D) Low-Risk Extraction Candidates (Safe to Move Later)

These files contain pure functions or standalone utilities with minimal dependencies:

### **Diagnostics (Pure reporting, no side effects):**
- `pipeline/diagnostics/diagnostic.py` (499 lines) — Full diagnostic report generation
- `pipeline/diagnostics/face_attr_tools.py` (203 lines) — Face attribute debugging
- `pipeline/diagnostics/geometry_tripwires.py` (165 lines) — Geometry sanity checks
- `pipeline/diagnostics/terrain_alignment_check.py` (255 lines) — Terrain alignment validation

### **Linking (Standalone scripts, no Blender dependency):**
- `pipeline/linking/link_gml_to_osm.py` (348 lines) — Pure linking algorithm (grid-based nearest neighbor)
- `pipeline/linking/make_osm_centroids_semantics.py` (164 lines) — OSM centroid DB creation (standalone)
- `pipeline/linking/make_gml_centroids.py` (155 lines) — CityGML centroid DB creation (standalone)
- `pipeline/linking/validation.py` (213 lines) — Link validation helpers

### **Terrain (Standalone import logic):**
- `pipeline/terrain/rgb_basemap_import.py` (307 lines) — RGB GeoTIFF import
- `pipeline/terrain/dgm_terrain_import.py` (296 lines) — DGM terrain import
- `pipeline/terrain/m1_terrain_csv.py` (266 lines) — Terrain CSV export

### **Utilities:**
- `utils/logging_system.py` (144 lines) — Logging and progress tracking (stable since Phase 13)
- `auto_load.py` (39 lines) — Class registration helper (stable boilerplate)

---

## E) Potential Circular Import Hazards

### **Existing Import Cycles (Currently Managed):**

#### 1. **`settings.py` ↔ `ops.py`**
- **Direction:** settings.py imports ops.py in callbacks → ops.py reads settings properties
- **Current mitigation:** Import inside callback function scope (deferred import)
- **Risk:** High — Adding more callbacks without care could break initialization
- **Evidence:**
  ```python
  # settings.py line 13
  def _on_spreadsheet_table_changed(self, context):
      from . import ops  # Deferred import
      ops.spreadsheet_invalidate_and_rebuild(...)
  ```

#### 2. **`__init__.py` → `ops.py` → `utils/common.py` → (potential back to ops.py)**
- **Direction:** ops.py imports common.py → common.py imports logging_system → (safe for now)
- **Risk:** Medium — If common.py ever imports ops.py helpers, cycle would form
- **Current status:** Safe (common.py only exports pure functions)

#### 3. **`ui.py` → `ops.py` → (implicit back via operators)**
- **Direction:** ui.py calls ops.py functions → ops.py operators called by ui.py panels
- **Risk:** Low — Separated by Blender event loop (operators invoked via bpy.ops, not direct calls)

### **Future Circular Import Risks (If Modularization Done Wrong):**

⚠️ **DO NOT:**
- Move operators out of ops.py (breaks registration)
- Have `ops/attrs.py` import from `ops.py` (cycle)
- Have `utils/common.py` import from ops.py (cycle)
- Split operators across multiple files (violates orchestrator rule)

✅ **SAFE PATTERN:**
```
ops.py (orchestrator)
  ├─ imports utils/common.py (pure helpers)
  ├─ imports ops/attrs.py (pure face attribute logic)
  ├─ imports ops/db_paths.py (pure DB queries)
  └─ imports pipeline/* (pure pipeline logic)

ops/attrs.py (pure functions)
  └─ imports ONLY bpy, typing, logging
      (never imports ops.py)
```

---

## F) Recommended Modularization Order (Future Phase 1+)

**Guiding Principle:** Extract pure functions first, highest value/lowest risk.

### **Phase 1: Diagnostics & Validation (Weeks 1-2)**
**Why first:** Zero Blender state, pure reporting, immediate value for debugging.

1. **Extract standalone diagnostics:**
   - `ops/diagnostics_export.py` ← from ops.py lines 11000-12000 (diagnostic operators)
   - Move logic to `pipeline/diagnostics/` (keep operators in ops.py)

2. **Consolidate face attribute tools:**
   - Document all face attribute patterns in `docs/audit/FACE_ATTRS_REFERENCE.md`
   - No code changes yet (just mapping)

**Risk:** LOW  
**Value:** HIGH (improves debugging, no operator changes)

---

### **Phase 2: Face Attribute Schema (Weeks 3-4)**
**Why second:** Central to linking/materialization, well-bounded problem.

1. **Extract `ops/attrs.py`:**
   ```python
   # ops/attrs.py (~500 lines)
   def ensure_face_attr(mesh, name, data_type): ...
   def _ensure_face_attrs(mesh, include_centroids=False): ...
   def _ensure_face_attrs_for_materialization(mesh): ...
   ```

2. **Update ops.py:**
   ```python
   from .ops.attrs import ensure_face_attr, _ensure_face_attrs
   # (replace ~500 lines with imports)
   ```

3. **Validation:**
   - Run all linking operators (M1DC_OT_LinkCityGMLtoOSM)
   - Run all materialization operators (M1DC_OT_MaterializeLinks)
   - Verify face attributes unchanged (checksum comparison)

**Risk:** MEDIUM (touches critical linking code)  
**Value:** HIGH (simplifies ops.py by 500 lines, improves testability)

---

### **Phase 3: OSM Key Handling (Weeks 5-6)**
**Why third:** Isolated string utilities, no state, heavily reused.

1. **Extract `ops/osm_keys.py`:**
   ```python
   # ops/osm_keys.py (~300 lines)
   def sanitize_attr_name(name: str) -> str: ...
   def _normalize_osm_id(value) -> str: ...
   def _get_osm_key_col(s): ...
   def _get_osm_code_attr_name(key): ...
   ```

2. **Update ops.py:**
   ```python
   from .ops.osm_keys import sanitize_attr_name, _normalize_osm_id
   # (replace ~300 lines)
   ```

**Risk:** LOW (pure string functions)  
**Value:** MEDIUM (cleans up ops.py, reusable utilities)

---

### **Phase 4: Database Path Resolution (Weeks 7-8)**
**Why fourth:** Isolated I/O logic, clear boundaries.

1. **Extract `ops/db_paths.py`:**
   ```python
   # ops/db_paths.py (~200 lines)
   def _resolve_feature_db_path() -> str: ...
   def _table_exists(cur, table_name: str) -> bool: ...
   def _list_user_tables(cur): ...
   def _detect_feature_table(gpkg_path): ...
   ```

2. **Merge with `utils/common.py` DB helpers:**
   - Consolidate all DB path logic in one place
   - Update imports across pipeline

**Risk:** LOW (pure queries, no writes)  
**Value:** MEDIUM (simplifies ops.py by 200 lines)

---

### **Phase 5: Materialization Logic (Weeks 9-12) — **HIGH RISK****
**Why last:** Massive, complex, has known Phase 3-4 bugs.

1. **Extract `ops/materialize_phase3.py`:**
   ```python
   # _materialize_osm_features (lines 2016-2602, ~600 lines)
   # GPKG feature loading, face attribute writes
   ```

2. **Extract `ops/materialize_phase4_mkdb.py`:**
   ```python
   # load_feature_map_from_mkdb (lines 1927-1964)
   # _materialize_legend_codes (lines 2639-3136, ~500 lines)
   ```

3. **Validation (CRITICAL):**
   - TEST on full Köln dataset (50k+ buildings)
   - Verify materialization results byte-for-byte identical
   - Check for memory leaks (Phases 3-4 had memory issues)

**Risk:** HIGH (touches known bug zones, complex state)  
**Value:** VERY HIGH (ops.py reduced by ~1,200 lines, improves maintainability)

---

### **Phase ∞: Operators Stay in ops.py**
**Never move:**
- Any class inheriting from `bpy.types.Operator`
- Top-level operator registration lists
- Operator `execute()` methods (orchestration logic stays)

**Move only:**
- Pure helper functions called by operators
- Data transformation logic (no bpy context)
- I/O operations (DB queries, file reads)

---

## G) Hot Zones (Files > 800 Lines or Known Bug Areas)

### **Files > 800 Lines:**
1. `ops.py` (13,355 lines) — See Section C.1
2. `pipeline/terrain/terrain_validation.py` (1,068 lines) — Aachen terrain scale bug (Phase 6)
3. `pipeline/diagnostics/legend_encoding.py` (919 lines) — Legend caching system (stable)
4. `pipeline/citygml/citygml_import.py` (880 lines) — World origin inference (critical)
5. `pipeline/terrain/m1_basemap.py` (845 lines) — Basemap axis correction (Phase 8 fix)

### **Files Containing Known Bug Fixes:**
- `ops.py` — Phase 3 (attr clobber), Phase 4 (mkdb), Phase 11 (legend writeback), Phase 12 (legend fix)
- `pipeline/terrain/terrain_validation.py` — Phase 6 (Aachen scale bug)
- `pipeline/terrain/terrain_scaling.py` — Phase 6 (proven fix automation)
- `utils/common.py` — Phase 10 (utils consolidation), READONLY DB fixes

### **Files Touching Attribute Schema:**
**All ensure_face_attr calls (must be tracked during modularization):**
- `ops.py` (lines 983, 352, 366, 6377-6386, 7027-7029) — Primary location
- No other files directly call ensure_face_attr (all go through ops.py)

### **Files Deciding DB Paths:**
- `ops.py` — _resolve_feature_db_path (line 1258)
- `utils/common.py` — resolve_gpkg_path (line 16)
- `settings.py` — _on_gpkg_path_changed (callback)

### **Files Reading/Writing FACE Attributes:**
**Writers (modifies mesh.attributes):**
- `ops.py` — _materialize_osm_features (line 2016), ensure_face_attr (line 983), M1DC_OT_MaterializeLinks (line 7634)

**Readers (queries mesh.attributes):**
- `ops.py` — _read_face_attr_auto (line 798), _read_face_int_attr (line 848), spreadsheet/inspector operators
- `pipeline/diagnostics/face_attr_tools.py` — _debug_face_attrs (read-only analysis)

---

## H) Summary Statistics

| Metric | Value |
|--------|-------|
| **Total Python files** | 50 |
| **Total lines of code** | ~24,000 |
| **Largest file** | ops.py (13,355 lines, 56% of codebase) |
| **Files > 500 lines** | 8 |
| **Files > 1000 lines** | 2 (ops.py, terrain_validation.py) |
| **Operator count** | 77 (75 in ops.py, 2 in ui.py) |
| **HIGH risk files** | 3 (ops.py, settings.py, __init__.py) |
| **MEDIUM risk files** | 12 |
| **LOW risk files** | 35 |
| **Legacy files (unused)** | 5 (pipeline/Legacy/*) |
| **Empty/minimal files** | 10 (package __init__.py files) |

---

## I) Key Takeaways for Future Modularization

### ✅ **DO:**
1. **Extract pure functions** — String utils, DB queries, validation helpers
2. **Keep operators in ops.py** — Never move bpy.types.Operator subclasses
3. **Test incrementally** — Extract 200-500 lines at a time, validate before next step
4. **Document imports** — Update docs/IMPORT_MAP.md after each extraction
5. **Preserve evidence** — Keep git history, reference phase docs

### ❌ **DON'T:**
1. **Move operators** — Violates orchestrator rule, breaks registration
2. **Split ops.py into ops1.py, ops2.py** — Defeats the purpose, maintains 13k-line problem
3. **Circular imports** — ops/* modules must never import ops.py
4. **Rename existing functions** — Breaks git history, complicates rollback
5. **Batch refactor** — Phase 3-4 bugs prove incremental approach is essential

### 🎯 **Success Criteria:**
- ops.py reduced to ~10,000 lines (from 13,355)
- All operators remain in ops.py (75+ classes)
- Zero functional regressions (byte-for-byte identical results)
- Import graph remains acyclic (no circular imports)
- Phase docs updated with extraction log

---

## J) Next Steps (Future Phase 1 Prep)

**Before starting any modularization:**

1. **Create import dependency graph:**
   ```bash
   # Generate visual import map
   python scripts/generate_import_graph.py > docs/audit/IMPORT_GRAPH.md
   ```

2. **Baseline test suite:**
   - Run full pipeline on Köln dataset
   - Capture checksums of all face attributes
   - Document expected operator outputs

3. **Create extraction template:**
   ```python
   # ops/template.py
   """
   Extracted from ops.py on YYYY-MM-DD
   Original lines: X-Y
   Phase: N
   Risk: LOW/MED/HIGH
   """
   # ... pure functions only ...
   ```

4. **Update phase docs:**
   - Create `docs/phases_YYYYMMDD/PHASE_14_MODULARIZATION_PREP.md`
   - Reference this audit document

---

**END OF AUDIT REPORT**  
**Status:** COMPLETE (no code changes made)  
**Ready for:** Phase 1 planning (diagnostics extraction)
