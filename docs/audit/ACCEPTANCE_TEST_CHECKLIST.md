# Acceptance Test Checklist

Date: 2026-02-08
Scope: Verify all surgical fixes are working correctly.

## Test Environment

- Blender 4.5 LTS
- Fresh .blend file (no stale INT attributes)
- CityGML tiles loaded
- GPKG linked

---

## TEST 1: Startup Import

**Expected Logs:**

```log
[STARTUP] ensure_pkg_resources OK
```

**Pass Criteria:**

- ✅ Add-on loads without ImportError
- ✅ Startup log appears in Blender console
- ✅ No "ModuleNotFoundError" for init_blender_compatibility

---

## TEST 2: Phase 3 INT→FLOAT Upgrade

**Scenario:** Re-run Materialize on a .blend file with old INT attributes.

**Expected Logs:**

```log
[ClobberGuard] UPGRADE attr=link_conf INT→FLOAT preserved_nondefault=True permille_scaled=<N> faces=<N>
[ClobberGuard] UPGRADE attr=link_dist_m INT→FLOAT preserved_nondefault=<bool> permille_scaled=0 faces=<N>
[ClobberGuard] UPGRADE attr=link_iou INT→FLOAT preserved_nondefault=<bool> permille_scaled=0 faces=<N>
```

**Pass Criteria:**

- ✅ No TypeError: IntAttributeValue.value expected an int type, not float
- ✅ Upgrade logs show correct permille_scaled count for link_conf
- ✅ Values preserved (check with Inspector or spreadsheet)

---

## TEST 3: Phase 3 Preflight Guards

**Expected Logs:**

```log
[PROOF][ATTR_REQUEST] link_conf=FLOAT link_dist_m=FLOAT link_iou=FLOAT
[PROOF][ATTR_SCHEMA] mesh=<name> link_conf=('FACE', 'FLOAT') link_dist_m=('FACE', 'FLOAT') link_iou=('FACE', 'FLOAT')
[PROOF][FLOAT_WRITE] face0 conf=<val> dist=<val> iou=<val> types=(float, float, float)
```

**Pass Criteria:**

- ✅ All link attributes show FLOAT in schema proof
- ✅ Readback proof shows Python float types
- ✅ No "Phase 3 preflight failed" error

---

## TEST 4: MKDB Multi-Tile Scope

**Scenario:** Run Materialize with 56 CityGML tiles loaded.

**Expected Logs:**

```log
[MKDB][SCOPE] meshes_to_scan=56 names_sample=['GML_Kachel_1', 'GML_Kachel_2', 'GML_Kachel_3']
[MKDB][ROUTING] link_map entries from DB: <N>
[MKDB][PROOF] harvested_ids=<N> sample=[<id1>, <id2>, <id3>, <id4>, <id5>]
```

**Pass Criteria:**

- ✅ meshes_to_scan count matches tile count (not 1)
- ✅ harvested_ids > 0
- ✅ No `[MKDB][WARN] No keys harvested` (unless legitimately no links)

---

## TEST 5: End-to-End Phase 3+4+5

**Scenario:** Fresh run: Link → Materialize (with "Include OSM Attributes" ON)

**Expected Console Flow:**

1. Phase 1: Matching proof - PASS
2. Phase 2: Writeback proof - PASS
3. Mesh↔Link proof - hit > 0, miss < total
4. Phase 3: REAL MATERIALIZE
   - Preflight PASS (all FLOAT)
   - Write loop completes
   - Spot-check proof shows values
   - Schema verification PASS
   - Readback proof shows float types
5. Phase 4.5: MKDB
   - Scope shows N tiles
   - Harvested IDs > 0
   - mkdb file created
6. Phase 4: OSM FEATURE WRITEBACK
   - osm_features_written > 0
7. Phase 5: LEGEND CODE WRITEBACK
   - legend_codes_written > 0
8. Phase 5.5: MATERIALIZE PROOF COUNTERS
   - faces_with_osm_id > 0
   - faces_written_any_nonzero > 0
   - unique_nonzero_osm_building_code > 0

**Pass Criteria:**

- ✅ No Python TypeError or RuntimeError
- ✅ All phases complete without CANCELLED
- ✅ Inspector shows decoded attributes (building=residential, etc.)
- ✅ Spreadsheet shows *_code attributes with integer codes > 0

---

## Regression Tests

- ✅ Single-object scenes still work (MKDB fallback to active object)
- ✅ Re-running Materialize multiple times produces identical results (deterministic)
- ✅ Clobber guard protects existing link data (no silent overwrite)

---

## If Tests Fail

1. **Startup ImportError**: Check that `utils/startup/init_blender_compatibility.py` exists.
2. **INT TypeError persists**: Check if .blend file has stale attributes (delete objects, re-import CityGML).
3. **MKDB harvested_ids=0**: Check if [MESH↔LINK] proof showed hit > 0 (if 0, linking failed).
4. **Phase 4/5 write 0**: Check logs for missing mkdb file or wrong table selection.
