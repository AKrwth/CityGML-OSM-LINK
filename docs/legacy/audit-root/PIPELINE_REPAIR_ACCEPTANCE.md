# M1_DC_V6 Pipeline Repair — Acceptance Checklist

## Overview
This checklist verifies that the 5-task surgical repair for pipeline regressions has resolved all identified issues without architectural redesigns.

**Target State:** Terrain detection works for both OBJ and raster modes; linking produces composite keys correctly; materialize writes to FACE attributes.

---

## TASK A: Pipeline Call Order ✅ 
**Status:** VERIFIED — No changes needed (order already correct)

### Verification
- [x] Workflow sequence: Terrain → CityGML → Validation → Linking → Materialize
- [x] Order confirmed in [workflow_ops.py](../pipeline/operations/workflow_ops.py#L225-L370)
- [x] No calls reordered (surgical constraint: minimal changes only)

---

## TASK B: Robust Terrain Detection ✅
**Status:** IMPLEMENTED

### Changes Made
1. **[terrain_validation.py](../pipeline/terrain/terrain_validation.py#L164-L199)**
   - Changed: `get_terrain_object()` → Multi-strategy detection
   - Strategy 1: Object property `m1dc_role="terrain"`
   - Strategy 2: Collection `TERRAIN`
   - Strategy 3: Legacy names `dem_merged` / `rgb_merged`

2. **[z_alignment.py](../pipeline/terrain/z_alignment.py#L35-L95)**
   - Added: Identical `get_terrain_object()` helper (copied for isolation)
   - Updated: Line 406 to call helper instead of hardcoded name

### Verification Checklist
- [ ] Test 1: OBJ terrain import
  - Import OBJ file with `m1dc_role="terrain"` property
  - Validation should find it via Strategy 1 (property-based)
  - Log should show: `[VALIDATION] Terrain found via m1dc_role property: <name>`

- [ ] Test 2: TERRAIN collection fallback
  - Place mesh in `TERRAIN` collection (no property)
  - Validation should find it via Strategy 2
  - Log should show: `[VALIDATION] Terrain found in TERRAIN collection: <name>`

- [ ] Test 3: Legacy "dem_merged" name
  - Import as "dem_merged" (no property/collection)
  - Validation should find it via Strategy 3
  - Log should show: `[VALIDATION] Terrain found via legacy name: dem_merged`

- [ ] Test 4: Validation passes
  - Validation should return `"CLEAN"` or `"FIX_SCALE_Z"` (not `"BLOCKED"`)
  - Pipeline should continue to linking phase

**Expected Logs:**
```
[VALIDATION] Terrain found via <method>: <name>
[VALIDATION] ✓ Validation decision: CLEAN
```

---

## TASK C: Source_tile Normalization ✅
**Status:** IMPLEMENTED

### Changes Made
1. **[linking_cache.py](../pipeline/linking/linking_cache.py#L193-L213)**
   - Added: `norm_source_tile()` import (with fallback implementation)
   - Change: Line 205 applies normalization when writing to gml_building_centroids
   - Before: `rows.append((str(source_tile), ...))`
   - After: `rows.append((str(norm_source_tile(source_tile)), ...))`
   - Effect: All source_tile keys in DB are normalized (no extensions, no directories)

### Verification Checklist
- [ ] Test 1: Composite key matching
  - CityGML mesh: `source_tile = "Tile_1.gml"`
  - Linking database stored: `source_tile = "Tile_1"` (normalized)
  - Face attribute reads: `_get_source_tile()` applies `norm_source_tile()` → `"Tile_1"`
  - Result: Keys match in lookup ✓

- [ ] Test 2: Path-based source_tile
  - CityGML mesh: `source_tile = "data/imports/Tile_1.gml"`
  - Normalized in DB: `"Tile_1"`
  - Expected: Link lookup succeeds

- [ ] Test 3: Linking stats
  - After linking, check: `s.step2_linked_objects > 0`
  - If 0, diagnostic must show key mismatch in logs

**Expected Proof Logs:**
```
[Link] Linked 150 buildings across 5 tiles (avg confidence: 0.87)
```

---

## TASK D: Materialize FACE Writeback ✅
**Status:** IMPLEMENTED

### Changes Made
1. **[materialize_ops.py](../pipeline/operations/materialize_ops.py#L30-L119)**
   - Replaced: Stub operator that tried to delegate to ops.py
   - Added: Complete `execute()` implementation
   - Calls: `_materialize_osm_features()` (Phase 4) and `_materialize_legend_codes()` (Phase 5)
   - Effect: FACE attributes (osm_id, link_conf, building, amenity, codes) now written

### Verification Checklist
- [ ] Test 1: Faces have osm_id (INT, FACE domain)
  - In Blender, inspect any CityGML mesh
  - Attribute `osm_id_int` or `osm_id` should exist with FACE domain
  - Sample values: 123456789 (nonzero for linked faces), 0 (unlinked)

- [ ] Test 2: Faces have building (STRING, FACE domain)
  - Attribute `building` should exist
  - Sample values: "residential", "house", "commercial", "" (empty for unlinked)
  - At least 50%+ of linked faces should have non-empty building values

- [ ] Test 3: Faces have amenity (STRING, FACE domain)
  - Attribute `amenity` should exist
  - Sample values: "restaurant", "clinic", "", etc.

- [ ] Test 4: Faces have code attributes (INT, FACE domain)
  - Attributes like `osm_building_code`, `osm_amenity_code` should exist
  - Nonzero values = legend-encoded data
  - At least 50%+ of linked faces should have nonzero codes

- [ ] Test 5: Pipeline calls materialize
  - Workflow logs should show:
    ```
    [Pipeline] Materializing face attributes...
    [Materialize] Starting materialization pipeline...
    [Materialize] P4: Materializing OSM features...
    [Materialize] P5: Materializing legend codes...
    [Materialize] ✓ Materialization pipeline complete
    ```

**Expected Output States:**
- After linking: `s.step2_linked_objects = 150` (example)
- After materialize: Mesh has STRING/INT face attributes populated
- Sample diagnostic:
  ```
  [Materialize] Phase 4: LoD2_32_290_5626_1_NW wrote 150 features
  [Materialize] Phase 5: LoD2_32_290_5626_1_NW wrote 450 codes
  ```

---

## TASK E: Import Error Fix ✅
**Status:** VERIFIED — Already fixed in previous session

- [x] `_link_gpkg_to_citygml()` function exists in ops.py line 4038
- [x] Function call in workflow_ops.py line 336 works
- [x] Return signature matches: `(ok, linked_count, confidences, [], tiles_count, [])`

---

## Complete Pipeline Run Acceptance Test

### Pre-Requisites
- [ ] Blender 4.5.3+ open with M1DC addon loaded
- [ ] Scene has:
  - [ ] CityGML folder with ≥3 GML tiles
  - [ ] GeoPackage with OSM features (≥100 buildings)
  - [ ] Terrain (as OBJ artifact OR dem_merged.tif)
  - [ ] WORLD_ORIGIN locked to valid EPSG:25832 bounds

### Test Sequence

**Phase 1: Terrain (10 minutes)**
```
[Pipeline] Step1 Terrain: imported (X faces, Y meshes)
```
- [ ] Terrain object found (via any strategy)
- [ ] dem_merged or alternative name shows in scene
- [ ] Properties: `m1dc_role = "terrain"` visible in inspector (if applied)

**Phase 2: CityGML Import (5 minutes)**
```
[CityGML] Imported 3 tiles: 450 buildings, 5400 faces
```
- [ ] CITYGML_TILES collection visible in Outliner
- [ ] Tiles have `source_tile` property set
- [ ] Tiles have `gml_building_idx` face attribute

**Phase 2.5: Validation (5 seconds)**
```
[VALIDATION] Terrain found via m1dc_role property: dem_merged
[VALIDATION] ✓ Validation decision: CLEAN
```
- [ ] Validation passes (decision: CLEAN or FIX_SCALE_Z, NOT BLOCKED)
- [ ] No error: "Terrain object missing"

**Phase 3: Linking (30-60 seconds, varies by GPKG size)**
```
[Link] Linked 435 / 450 buildings across 3 tiles (avg confidence: 0.87)
[Link] Linked count written to s.step2_linked_objects = 435
```
- [ ] Log shows "Linked N buildings"
- [ ] `s.step2_linked_objects > 0`
- [ ] No key mismatch errors in console

**Phase 4-5: Materialization (20 seconds)**
```
[Materialize] Starting materialization pipeline...
[Materialize] P4: Materializing OSM features...
[Materialize] Phase 4: LoD2_32_290_5626_1_NW wrote 435 features
[Materialize] P5: Materializing legend codes...
[Materialize] Phase 5: LoD2_32_290_5626_1_NW wrote 1305 codes
[Materialize] ✓ Materialization pipeline complete
```
- [ ] Materialize invoke succeeds (not "not available")
- [ ] Phase 4 and Phase 5 logs appear
- [ ] No traceback errors

**Phase 6: Verification (5 minutes)**
- [ ] Select any CityGML mesh in Edit Mode
- [ ] Inspect face attributes (Properties > Object Data > Attributes)
  - [ ] `osm_id` exists (INT, FACE domain), has nonzero values
  - [ ] `building` exists (STRING, FACE domain), has values like "residential"
  - [ ] `osm_building_code` exists (INT, FACE domain), nonzero values
- [ ] In Spreadsheet: inspect link data
  - [ ] Columns show osm_id values
  - [ ] Columns show building/amenity strings

---

## Known Limitations & Future Improvements

### Current Architecture
- **Terrain detection**: Falls back through 3 strategies (satisfies OBJ, collection, legacy name)
- **Source_tile normalization**: Applied at DB write time (not retroactive for existing DBs)
- **Materialize**: Delegates to ops.py helpers (can be refactored to pipeline module)

### Documented Issues
- [ ] If existing link DB has un-normalized source_tile keys, may cause mismatches. Solution: Delete links_db and re-run linking.
- [ ] Materialize operator stub in materialize_ops.py now properly delegates (no longer broken)

---

## Test Report Template

After running complete pipeline, fill in:

```
Date: _______________
Dataset: _______________
Blender Version: _______________
M1DC Version: _______________

=== RESULTS ===
Terrain Detection: PASS / FAIL
  - Method found: [ ] Property  [ ] Collection  [ ] Legacy
  - Terrain object: _______________

Validation: PASS / FAIL
  - Decision: [ ] CLEAN  [ ] FIX_SCALE_Z  [ ] BLOCKED  [ ] ERROR
  - Reason (if fail): _______________

Linking: PASS / FAIL
  - Linked buildings: _______________
  - Total CityGML buildings: _______________
  - Success rate: _____% (linked / total)

Materialize Phase 4: PASS / FAIL
  - building attribute exists: [ ] YES  [ ] NO
  - Sample values: _______________

Materialize Phase 5: PASS / FAIL
  - osm_building_code nonzero faces: _______________

Overall: PASS / FAIL
  - Notes: _______________
```

---

## Success Criteria

✅ **PASS** if ALL of:
1. Validation passes (not BLOCKED due to "Terrain object missing")
2. Linking report shows linked_count > 0
3. Materialize logs show Phase 4 and Phase 5 completion
4. Face attributes can be inspected in Blender (osm_id, building, codes visible)

❌ **FAIL** if ANY of:
1. Validation blocks with "Terrain not found"
2. Linking shows 0 linked buildings (key mismatch)
3. Materialize operator not available or crashes
4. Face attributes missing or empty despite successful linking

---

## Files Modified (Surgical Change Summary)

| File | Line | Change | Impact |
|------|------|--------|--------|
| [terrain_validation.py](../pipeline/terrain/terrain_validation.py#L164) | 164 | Replace `get_terrain_object()` → robust detection | Terrain finds OBJ objects |
| [z_alignment.py](../pipeline/terrain/z_alignment.py#L48) | 48-95 | Add `get_terrain_object()` helper + update call | Z-alignment uses robust detection |
| [linking_cache.py](../pipeline/linking/linking_cache.py#L205) | 205 | Normalize source_tile when writing DB | Keys match in linking |
| [materialize_ops.py](../pipeline/operations/materialize_ops.py#L30) | 30-119 | Implement complete `execute()` method | FACE attributes written |

---

## Regression Tests (Run Before Deploying)

```bash
# Test terrain detection with all strategies
pytest tests/test_terrain_detection.py
  - test_strategy_1_property_based
  - test_strategy_2_collection_based
  - test_strategy_3_legacy_names

# Test source_tile normalization
pytest tests/test_source_tile_norm.py
  - test_normalize_with_extension
  - test_normalize_with_path
  - test_normalize_composite_key_match

# Test materialize operator
pytest tests/test_materialize.py
  - test_operator_invoke
  - test_face_attributes_written
  - test_phase4_features
  - test_phase5_codes
```

---

**Document Version:** 1.0  
**Last Updated:** Session [Date]  
**Status:** Ready for Testing
