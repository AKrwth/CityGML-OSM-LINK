# OPS.PY CLAIM VERIFICATION — Forensic Audit

**Date**: 2026-02-08
**Method**: Read-only code inspection, no execution

---

## A. ensure_face_attr() — INT-to-FLOAT Upgrade Path

**Location**: `ops.py:983-1044`

### Does an INT-to-FLOAT upgrade path exist?

**YES.** The upgrade path exists at lines 994-1012.

### Exact code (ops.py:994-1012):

```python
            # UPGRADE PATH: INT→FLOAT for link attributes (before clobber guard)
            if (name in {"link_conf", "link_dist_m", "link_iou"}
                    and attr.domain == "FACE"
                    and attr.data_type == "INT"
                    and data_type == "FLOAT"
                    and len(attr.data) == face_count):
                # Preserve existing values and upgrade type
                try:
                    preserved_values = [float(attr.data[i].value) for i in range(len(attr.data))]
                    has_nondefault = any(v != 0.0 for v in preserved_values)
                    mesh.attributes.remove(attr)
                    attr = mesh.attributes.new(name, "FLOAT", "FACE")
                    for i, val in enumerate(preserved_values):
                        attr.data[i].value = val
                    print(f"[ClobberGuard] UPGRADE attr={name} INT→FLOAT ...")
                    return attr
                except Exception as e:
                    print(f"[ClobberGuard][ERROR] UPGRADE failed for {name}: {e}")
                    # Fall through to normal handling
```

### Is it executed before any KEEP return?

**YES.** The upgrade path is at lines 994-1012. The CLOBBER GUARD "KEEP" return is at lines 1014-1020. The upgrade executes FIRST.

### Is it restricted to link_conf, link_dist_m, link_iou?

**YES.** Line 995: `name in {"link_conf", "link_dist_m", "link_iou"}`

### Verdict on ensure_face_attr:

**VERIFIED** — The INT-to-FLOAT upgrade path exists, is correctly positioned before the clobber guard, and is restricted to the three link attributes.

---

## B. _ensure_face_attrs_for_materialization()

**Location**: `ops.py:6989-7016`

### What data types are requested?

```python
# ops.py:7003-7005
attrs["link_conf"] = ensure_face_attr(mesh, "link_conf", "FLOAT")
attrs["link_dist_m"] = ensure_face_attr(mesh, "link_dist_m", "FLOAT")
attrs["link_iou"] = ensure_face_attr(mesh, "link_iou", "FLOAT")
```

**All three request FLOAT.** This is correct.

### Does Phase 3 in MaterializeLinks also request FLOAT?

**YES.** In `M1DC_OT_MaterializeLinks.execute()` at `ops.py:7772-7778`:

```python
attr_specs = [
    ("osm_id_int", "INT"),
    ("osm_id", "INT"),
    ("osm_way_id", "INT"),
    ("link_conf", "FLOAT"),     # Must be FLOAT for confidence (0..1)
    ("link_dist_m", "FLOAT"),
    ("link_iou", "FLOAT"),
    ("building_idx", "INT"),
    ("gml_building_idx", "INT"),
    ("has_link", "BOOLEAN"),
]
```

These are passed to `ensure_face_storage_ready()` (line 7785) which calls `ensure_face_attr()` for each. **link_conf, link_dist_m, link_iou all request FLOAT**.

### PROOF log at runtime (ops.py:7008):

```python
print(f"[PROOF][ATTR_REQUEST] link_conf=FLOAT link_dist_m=FLOAT link_iou=FLOAT")
```

### Verdict on _ensure_face_attrs_for_materialization:

**VERIFIED** — Both `_ensure_face_attrs_for_materialization` AND the Phase 3 attr_specs in `MaterializeLinks.execute()` correctly request FLOAT for all three link attributes. No INT remnants found.

---

## C. MKDB LOCATION CHECK

### MKDB Block 1: OLD LOCATION (Linking phase) — REMOVED

**File**: `ops.py:6271-6275`
**Function**: `_link_gpkg_to_citygml(s)`
**Status**: **STUB ONLY** — MKDB logic removed, replaced with comment + skip message

```python
        # ========================================
        # PHASE 4.5: MKDB — REMOVED (moved to Materialize AFTER Phase 3)
        # ========================================
        # MKDB now runs in M1DC_OT_MaterializeLinks AFTER Phase 3 writes IDs to meshes
        print(f"\\n[MKDB][ROUTING] Skipping MKDB build here (will run in Materialize after Phase 3)")
```

**Timing**: This is inside `_link_gpkg_to_citygml()` which runs DURING linking (before Phase 3).
**VERDICT**: MKDB correctly removed from linking. Only a print statement remains.

### MKDB Block 2: NEW LOCATION (After Phase 3) — ACTIVE

**File**: `ops.py:7882-7934`
**Function**: `M1DC_OT_MaterializeLinks.execute()`
**Status**: **ACTIVE** — This is where MKDB now runs

```python
            # ========================================
            # PHASE 4.5: Build MKDB from linkdb AFTER Phase 3
            # ========================================
            print(f"\n[MKDB] PHASE 4.5 — Building semantic snapshot (mkdb)")
            print(f"[MKDB][ROUTING] Running MKDB AFTER Phase 3 link writeback (expected: IDs present)")

            # Harvest IDs from meshes (now populated by Phase 3)
            meshes_to_scan = [obj]  # Start with current mesh
```

**Timing**: This code is at line 7882, which is AFTER Phase 3's write loop (lines 7811-7848) and AFTER Phase 3 completion proof (lines 7863-7878).

**VERDICT**: MKDB routing is **PARTIALLY CORRECT**:
- It runs AFTER Phase 3 (correct)
- It runs inside `MaterializeLinks.execute()` (correct)
- BUT: `meshes_to_scan = [obj]` only includes the SINGLE active object (see Task 3)

### MKDB Block 3: build_mkdb_from_linkdb() function

**File**: `ops.py:1565-1703`
**Function**: `build_mkdb_from_linkdb()` (standalone builder)
**Status**: Unchanged utility function. Called from Block 2.

### MKDB Block 4: load_feature_map_from_mkdb()

**File**: `ops.py:1948-1992`
**Status**: Unchanged loader function.

### MKDB Block 5: SQL Inspector MKDB target

**File**: `ops.py:12139-12164`
**Function**: Inside SQL inspector UI handler
**Status**: UI-only, not pipeline-critical.

### Is MKDB still in _link_gpkg_to_citygml()?

**NO** — only a comment stub remains (lines 6271-6275). The actual MKDB build logic was removed from this function.

### Does MKDB execute before Phase 3?

**NO** — MKDB now executes at line 7882, which is after Phase 3 write (lines 7811-7848).

### ROUTING VERDICT:

**VALID** — MKDB has been correctly relocated to after Phase 3. The old location is stubbed out. However, there is an object scope issue (see Task 3).
