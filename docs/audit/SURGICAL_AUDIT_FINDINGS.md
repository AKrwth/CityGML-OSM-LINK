# SURGICAL AUDIT & REPAIR — FINDINGS

**Date:** 2025-02-08 (continued session)  
**Addon:** M1_DC_V6 v(6, 11, 11) for Blender (4, 5, 3)  
**Symptom:** "Spreadsheet lost all of its work" — OSM feature columns + codes/legends disappear or never materialize.

---

## 1. ROOT CAUSES IDENTIFIED

### ROOT CAUSE 1 — Missing Phase 3 (Critical)

**File:** `pipeline/operations/materialize_ops.py`  
**Operator:** `M1DC_OT_MaterializeLinks` (`m1dc.materialize_links`)

The Materialize operator jumped directly from mesh collection to Phase 4 (`_materialize_osm_features`) and Phase 5 (`_materialize_legend_codes`), **skipping Phase 3** entirely.

**Phase 3's job:** Read the link SQLite DB (`gml_osm_links` table) and write core link data (`osm_way_id`, `link_conf`, `link_dist_m`, `link_iou`, `has_link`) to FACE attributes on each CityGML mesh.

**Consequence chain:**
1. Phase 3 missing → `osm_way_id` never written to FACE attributes
2. Phase 4 (`_materialize_osm_features`) reads `osm_way_id`/`osm_id_int` per face to look up OSM features in GPKG → finds 0 IDs → writes 0 feature columns
3. Phase 5 (`_materialize_legend_codes`) reads feature columns to encode legend codes → finds 0 features → writes 0 codes
4. Spreadsheet shows empty columns for all OSM/legend data

**Fix:** Added ~65 lines implementing Phase 3 (lines 99–175 in patched file):
- Loads link mapping via `ops._load_link_lookup(s)`
- Iterates all CityGML meshes, reads `source_tile` + `gml_building_idx` per face
- Looks up `(source_tile, building_idx)` in link map
- Writes `osm_way_id` (INT), `link_conf` (FLOAT), `link_dist_m` (FLOAT), `link_iou` (FLOAT), `has_link` (INT) to FACE attributes
- Includes proof log: mesh count + linked face count

---

### ROOT CAUSE 2 — Broken bare `import ops` (29 occurrences across 5 files)

**Pattern:** `import ops` (bare) instead of `from ... import ops` (relative)

Since `ops` is a package-relative module (lives at `M1_DC_V6/ops.py`), bare `import ops` fails when running as a Blender addon (package context). All 29 occurrences were inside `try/except` blocks, so failures were **silent** — `ops` resolved to `None` or a wrong module, causing every `getattr(ops, "function_name", None)` to return `None`. Operators then reported "X logic not available" errors.

**Affected files (29 total):**
| File | Occurrences |
|------|-------------|
| `debug_ops.py` | 11 |
| `inspector_ops.py` | 6 |
| `spreadsheet_ops.py` | 5 |
| `face_attr_ops.py` | 4 |
| `materialize_ops.py` | 3 |

**Fix:** Changed all 29 `import ops` → `from ... import ops`.

**Intentionally untouched:** `workflow_ops.py` line 28 — inside `except ImportError:` fallback for direct-execution mode. This is correct behavior.

---

## 2. AUDIT CHECKLIST RESULTS

| # | Check | Result |
|---|-------|--------|
| A | Duplicate/Shadowed operator classes | **CLEAR** — All operator classes exist ONLY in `pipeline/operations/` modules. `ops.py` contains only helper functions (5520 LOC). No duplicates found. |
| B | Attribute writeback reality | **BUG FOUND** — Phase 3 was missing. `osm_way_id` was never written to FACE attributes. `_materialize_osm_features` correctly writes to `mesh.attributes` with domain=FACE, but requires Phase 3 to have populated `osm_way_id` first. |
| C1 | Later phases delete attributes | **CLEAR** — No evidence of attribute deletion in Phase 4 or Phase 5. |
| C2 | Wrong data-block (object vs mesh) | **CLEAR** — All writes target `obj.data` (mesh) correctly. |
| C3 | Legend before features (ordering bug) | **CONFIRMED (partial)** — Phase 5 ran before Phase 3 populated data, so it ran on empty attributes. With Phase 3 restored, ordering is now correct: P3→P4→P5. |
| C4 | Active mesh only (scope bug) | **CLEAR** — `materialize_ops` collects ALL meshes from `CITYGML_TILES` collection, not just active mesh. |
| C5 | Schema mismatch (INT vs STRING for osm_id) | **CLEAR** — `ensure_face_attr` handles type normalization. Link DB stores `osm_way_id` as TEXT, Phase 3 converts to INT via `int()` for FACE attribute storage. |

---

## 3. CALL GRAPH (Critical Path)

```
UI Button [Materialize Links]
  → bpy.ops.m1dc.materialize_links()
    → M1DC_OT_MaterializeLinks.execute()      [materialize_ops.py]
      → Phase 3: ops._load_link_lookup(s)     [ops.py ~L1900]
                  ops._get_source_tile(obj)    [ops.py]
                  ops.ensure_face_attr(mesh)   [ops.py]
                  → writes: osm_way_id, link_conf, link_dist_m, link_iou, has_link
      → Phase 4: ops._materialize_osm_features(context, s, mesh_obj)  [ops.py ~L2063, ~600 LOC]
                  → reads osm_way_id per face → queries GPKG → writes feature columns
      → Phase 5: ops._materialize_legend_codes(...)  [ops.py ~L2686, ~500 LOC]
                  → reads feature columns → encodes to *_code INT attrs + CSV legend
```

---

## 4. PATCH LIST (All Modified Files)

### Critical fixes (Root Causes):
| File | Change | Lines |
|------|--------|-------|
| `pipeline/operations/materialize_ops.py` | Added Phase 3 block + fixed 3 bare imports | +65 new, 3 changed |
| `pipeline/operations/spreadsheet_ops.py` | Fixed 5 bare imports | 5 changed |
| `pipeline/operations/inspector_ops.py` | Fixed 6 bare imports + added missing StringProperty fields | 6 changed, +12 new |
| `pipeline/operations/face_attr_ops.py` | Fixed 4 bare imports | 4 changed |
| `pipeline/operations/debug_ops.py` | Fixed 11 bare imports | 11 changed |

### Supporting improvements (from same session):
| File | Change |
|------|--------|
| `pipeline/citygml/citygml_import.py` | Added XML metadata parsing, tile proof logging, z-offset tracking |
| `pipeline/operations/workflow_ops.py` | Implemented OBJ artifact terrain import (was placeholder) |
| `pipeline/terrain/m1_basemap.py` | Enhanced `_find_first_obj` priority logic, added import proof logs |
| `pipeline/terrain/terrain_validation.py` | Made MIN_COVERAGE configurable from scene settings |
| `settings.py` | Added `m1dc_verbose_debug` and `min_terrain_coverage` properties |
| `utils/logging_system.py` | Enhanced `is_verbose_debug()` to check scene property first |

---

## 5. STALE / SHADOW FILE IDENTIFICATION

| Concern | Status |
|---------|--------|
| `ops.py` (5520 LOC) | **NOT stale** — contains all helper functions called by operators. No operator classes remain here (all moved to `pipeline/operations/`). |
| `pipeline/operations/__init__.py` | **Clean** — imports all operator submodules correctly, no duplicates. |
| `docs/legacy/` | Contains archived docs only; no code conflict. |
| `__pycache__/` dirs | Should be cleared after patching to avoid stale bytecode. |

---

## 6. ACCEPTANCE TEST CHECKLIST

### Pre-test: Clear stale bytecode
```
Delete all __pycache__/ folders under M1_DC_V6/
```

### Test A: Materialize Pipeline (Golden Path)
1. Open Blender, enable M1_DC_V6 addon
2. Set CityGML folder (containing .gml tiles)
3. Set GPKG path (OSM GeoPackage)
4. Run Pipeline (`m1dc.run_pipeline`)
5. Run Materialize Links (`m1dc.materialize_links`)
6. **VERIFY in Console:** See `[Materialize] P3 PROOF: N meshes, M linked faces` with M > 0
7. **VERIFY in Console:** See `[Materialize] P4:` messages showing feature writeback
8. **VERIFY in Console:** See `[Materialize] P5:` messages showing legend code writeback

### Test B: Spreadsheet FACE Domain
1. After Test A, open Spreadsheet editor in Blender
2. Select a CityGML mesh object
3. Switch domain to FACE
4. **VERIFY columns exist:** `osm_way_id`, `link_conf`, `link_dist_m`, `link_iou`, `has_link`
5. **VERIFY columns exist:** Feature columns (e.g., `building`, `amenity`, `name`)
6. **VERIFY columns exist:** Legend code columns (e.g., `building_code`, `amenity_code`)
7. **VERIFY values:** `osm_way_id` should contain non-zero integers for linked faces
8. **VERIFY values:** `has_link` should be 1 for linked faces, 0 for unlinked

### Test C: Operator Import Resolution
1. Open Blender Python Console
2. Run: `from M1_DC_V6.pipeline.operations import spreadsheet_ops`
3. **VERIFY:** No ImportError
4. Run: `from M1_DC_V6.pipeline.operations import inspector_ops`
5. **VERIFY:** No ImportError
6. Invoke any inspector/spreadsheet operator — should NOT report "X logic not available"

### Test D: Persistence
1. After Test A+B, save .blend file
2. Close and reopen Blender
3. Load saved .blend
4. Open Spreadsheet → FACE domain
5. **VERIFY:** All materialized attributes persist (osm_way_id, features, codes)

### Test E: No Regression
1. Reload addon (Preferences → toggle off/on)
2. **VERIFY:** No registration errors in console
3. **VERIFY:** All panels appear in the N-panel (Sidebar → M1DC)
4. Run Validate → should complete without errors
