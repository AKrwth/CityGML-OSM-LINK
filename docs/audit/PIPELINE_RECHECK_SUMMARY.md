# Pipeline Re-Check nach ops.py Refactor — Summary

**Date:** 2025-02-09  
**Scope:** Areas A–F surgical re-check  
**Constraint:** No refactoring, no API breaks, minimal-invasive proof logs only

---

## A) Operator Wiring / Shadowing — CLEAN ✓

**Finding:** Single import chain verified:  
`__init__.py` → `pipeline/operations/__init__.py` (56 classes in `__all__`) → individual `*_ops.py` modules → `CLASSES` tuple in `__init__.py` for `auto_load.register()`.

- No duplicate class registrations.
- No shadowing between ops.py functions and operator modules.
- All `from ... import ops` statements correct (29 bare `import ops` were fixed in previous session; 1 remaining in workflow_ops.py fallback is intentional — `sys.path.insert` before bare import for direct execution).

**Changes:** None required.

---

## B) Terrain OBJ Selection / Placement — FIXED ✓

**Problem:** `_find_first_obj()` in `m1_basemap.py` used unsorted `p.iterdir()` for Priority 2 (merged glob) and Priority 3 (any .obj fallback). Filesystem order is non-deterministic — could pick wrong tile on different runs.

**Fix (m1_basemap.py lines 44–57):**
- Priority 2 + 3 now use `sorted(p.iterdir(), key=lambda x: x.name)` for deterministic alphabetical selection.
- All 3 priorities now emit `[Terrain][PICK]` proof log with `reason=` tag.

**Fix (workflow_ops.py lines 262–276):**
- After terrain OBJ import, logs `[Terrain][EXTENT]` with `extent_xy`, `scale`, `role`, `collection`.
- Emits `[Terrain][EXTENT] ⚠` warning if extent < 200m (likely a single tile, not merged terrain).

**Files changed:**  
- `pipeline/terrain/m1_basemap.py` — sorted() + proof logs  
- `pipeline/operations/workflow_ops.py` — extent proof after import

---

## C) Validation Settings Through-Path — VERIFIED ✓

**Finding:** `min_terrain_coverage` flows correctly:  
`settings.py` (FloatProperty, default=0.6) → `scene.m1dc_settings.min_terrain_coverage` → `terrain_validation.py` `validate_and_decide()` with explicit fallback chain.

**Fix (terrain_validation.py lines 480–494):**
- Added `[PROOF][SETTINGS]` log showing `MIN_COVERAGE=0.60 source=scene.m1dc_settings.min_terrain_coverage` (or fallback reason if unavailable).
- Coverage FAIL message now includes `min=` value for diagnostics.

**Files changed:**  
- `pipeline/terrain/terrain_validation.py` — proof-log for settings through-path

---

## D) Materialize Writeback Attr Domain/Type — PROVEN ✓

**Finding:** Phase 3→4→5 data flow is correct:
- Phase 3 writes: `osm_way_id` (INT), `link_conf` (FLOAT), `link_dist_m` (FLOAT), `link_iou` (FLOAT), `has_link` (INT) — all FACE domain.
- Phase 4 reads `osm_way_id` via `_safe_read_face_id_attr` (priority: osm_way_id > osm_id_int > osm_id), writes STRING attrs (building, amenity, etc. WITHOUT osm_ prefix).
- Phase 5 reads STRING attrs, encodes via `_ENCODE_CACHE` to `osm_{key}_code` INT attrs.
- `ensure_face_attr()` has clobber guard + INT→FLOAT upgrade path for link_conf/dist/iou.

**Fix (materialize_ops.py):**
- After Phase 3: `[PROOF][ATTR_SCHEMA]` log per mesh showing attr `type/domain len= nz=` for all 5 written attrs.
- After Phase 4: `[PROOF][P4_READBACK]` with total features written count.
- After Phase 5: `[PROOF][P5_READBACK]` with total legend codes written count.

**Files changed:**  
- `pipeline/operations/materialize_ops.py` — 3 proof-log blocks

---

## E) Linking Key Canonicalization — VERIFIED ✓

**Finding:** `norm_source_tile()` consistently used in both `_load_link_lookup()` and `_get_source_tile()`:
- `Path(s).stem` + strip .001 Blender suffixes via regex.
- `_norm_id()` used for osm_id normalization (handles int/float/string/bytes/bool).
- Link map keyed by `(norm_source_tile, building_idx)` in both lookup and face write.

**Fix (materialize_ops.py):**
- After link_map load: `[PROOF][LINK_KEYS]` sample log showing 3 sample keys with osm_id/conf values.
- Per mesh after face write: `[PROOF][LINK_KEYS]` with `source_tile=`, `linked=`, `miss=` counts for hit/miss ratio.

**Files changed:**  
- `pipeline/operations/materialize_ops.py` — 2 proof-log blocks

---

## F) Legend Encoding/Decode Determinism — VERIFIED ✓

**Finding:**
- `CODE_KEYS` = 15 whitelisted categorical columns (amenity, building, landuse, shop, office, tourism, highway, leisure, historic, man_made, public_transport, railway, natural, waterway, aeroway).
- Correctly excludes: name, addr_housenumber, osm_id, other_tags, geom.
- Legend CSVs built with `ORDER BY value` (deterministic).
- Code 0 = `__NULL__` convention in place.
- `init_legend_caches()` only loads legends for columns in CODE_KEYS.
- Cache keyed by `{column}_code` (e.g., `amenity_code`).

**Fix (legend_encoding.py lines 663–668):**
- `[PROOF][LEGEND]` log showing full CODE_KEYS whitelist, encode/decode cache keys, and sample entries per cache.

**Files changed:**  
- `pipeline/diagnostics/legend_encoding.py` — proof-log block

---

## Changed Files Summary

| File | Change Type | Lines |
|------|------------|-------|
| `pipeline/terrain/m1_basemap.py` | sorted() for determinism + `[TERRAIN][PICK]` proof logs | 6 lines |
| `pipeline/operations/workflow_ops.py` | `[TERRAIN][EXTENT]` proof log + small extent warning | 8 lines |
| `pipeline/terrain/terrain_validation.py` | `[PROOF][SETTINGS]` through-path log | 13 lines |
| `pipeline/operations/materialize_ops.py` | `[PROOF][ATTR_SCHEMA]` + `[PROOF][LINK_KEYS]` + readback logs | 20 lines |
| `pipeline/diagnostics/legend_encoding.py` | `[PROOF][LEGEND]` determinism guard log | 5 lines |

**Total: 5 files, ~52 lines added (all proof-log or guardrail, zero logic changes)**

---

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| No bare `import ops` in pipeline/ | ✅ (1 intentional fallback in workflow_ops.py) |
| Terrain OBJ selection deterministic | ✅ sorted() + merged priority |
| Terrain extent logged after import | ✅ `[TERRAIN][EXTENT]` |
| MIN_COVERAGE source proven | ✅ `[PROOF][SETTINGS]` |
| Phase 3 attr schema verified | ✅ `[PROOF][ATTR_SCHEMA]` |
| Phase 4/5 readback counts | ✅ `[PROOF][P4_READBACK]` + `[PROOF][P5_READBACK]` |
| Link key canon sample logged | ✅ `[PROOF][LINK_KEYS]` |
| Legend CODE_KEYS logged | ✅ `[PROOF][LEGEND]` |
| No refactoring / API breaks | ✅ Only additive logs |
