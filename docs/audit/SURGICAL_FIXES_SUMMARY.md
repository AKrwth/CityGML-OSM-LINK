# Surgical Fixes Summary
Date: 2026-02-08
Agent: GitHub Copilot (surgical mode)

## Changes Applied

### 1. Startup Import ✅ VERIFIED CORRECT
**File:** `__init__.py`
**Status:** No changes needed - import path already correct.
**Import line (L12):**
```python
from .utils.startup.init_blender_compatibility import ensure_pkg_resources  # noqa
```
**Startup log (L14):**
```python
print("[STARTUP] ensure_pkg_resources OK")
```
**Target file:** `utils/startup/init_blender_compatibility.py` (51 lines) - EXISTS on disk.

---

### 2. Phase 3 INT→FLOAT Upgrade with Permille Scaling ✅ FIXED
**File:** `ops.py`
**Location:** `ensure_face_attr()` function (L995-1022)

**Added:** Permille-to-decimal conversion for link_conf when upgrading from INT.

**Before:**
```python
preserved_values = [float(attr.data[i].value) for i in range(len(attr.data))]
```

**After:**
```python
preserved_values = []
permille_scaled = 0
for i in range(len(attr.data)):
    val_int = attr.data[i].value
    # Handle permille encoding: link_conf stored as 879 (0.879*1000)
    if name == "link_conf" and val_int > 1:
        val_float = float(val_int) / 1000.0
        permille_scaled += 1
    else:
        val_float = float(val_int)
    preserved_values.append(val_float)
```

**Log output:**
```
[ClobberGuard] UPGRADE attr=link_conf INT→FLOAT preserved_nondefault=True permille_scaled=<N> faces=<N>
```

**Impact:** Prevents TypeError when re-running Materialize on .blend files with old INT attributes. Correctly converts 879 → 0.879 for confidence values.

---

### 3. MKDB Multi-Tile Scope ✅ ALREADY CORRECT (verified)
**File:** `ops.py`
**Location:** `M1DC_OT_MaterializeLinks.execute()` (L7899-7908)

**Code:**
```python
# Harvest IDs from ALL CityGML meshes (not just active object)
meshes_to_scan = _collect_citygml_meshes()
if not meshes_to_scan:
    meshes_to_scan = [obj]  # Fallback: at least scan active object
    print("[MKDB][SCOPE] WARNING: _collect_citygml_meshes() returned empty, falling back to active object")
print(f"[MKDB][SCOPE] meshes_to_scan={len(meshes_to_scan)} names_sample={[o.name for o in meshes_to_scan[:3]]}")
```

**Proof logs:**
```python
print(f"[MKDB][PROOF] harvested_ids={len(ids_from_meshes)} sample={list(ids_from_meshes)[:5]}")
```

**Status:** Already implemented in previous iteration - no changes needed.

---

## Audit Documentation Created

1. **docs/audit/PROOF_DISK_STATE.md**
   - File existence verification
   - Line count inventory
   - Import path verification

2. **docs/audit/PHASE3_ATTR_CALLSITES.md**
   - Complete inventory of all link_conf/link_dist_m/link_iou call-sites
   - All call-sites request FLOAT (verified)
   - INT→FLOAT upgrade path documented
   - Clobber guard protection documented

3. **docs/audit/ACCEPTANCE_TEST_CHECKLIST.md**
   - 5 test scenarios with expected logs
   - Pass/fail criteria
   - Regression test guidelines
   - Troubleshooting hints

4. **docs/audit/OUTDATED_MODULES_RISK.md** (from previous iteration)
   - Legacy module inventory
   - No runtime imports found (safe)

---

## Verification Commands

**Disk state:**
```powershell
@("ops.py", "__init__.py", "utils\startup\init_blender_compatibility.py") | ForEach-Object {
    if (Test-Path $_) {
        $lines = (Get-Content $_ | Measure-Object -Line).Lines
        "EXISTS|$_|$lines"
    } else {
        "MISSING|$_"
    }
}
```

**Call-site verification:**
```powershell
Select-String -Path ops.py -Pattern 'ensure_face_attr.*link_conf|ensure_face_attr.*link_dist_m|ensure_face_attr.*link_iou'
```

**Output:**
```
ops.py:6361:    attrs["link_conf"] = ensure_face_attr(mesh, "link_conf", "FLOAT")
ops.py:6362:    attrs["link_dist_m"] = ensure_face_attr(mesh, "link_dist_m", "FLOAT")
ops.py:6363:    attrs["link_iou"] = ensure_face_attr(mesh, "link_iou", "FLOAT")
ops.py:7013:    attrs["link_conf"] = ensure_face_attr(mesh, "link_conf", "FLOAT")
ops.py:7014:    attrs["link_dist_m"] = ensure_face_attr(mesh, "link_dist_m", "FLOAT")
ops.py:7015:    attrs["link_iou"] = ensure_face_attr(mesh, "link_iou", "FLOAT")
```

All requests are FLOAT ✅

---

## Root Cause: Why INT→FLOAT TypeError Can Still Occur

Even with all call-sites requesting FLOAT, the crash can happen because:

1. **Stale .blend file**: Old INT attributes persisted from previous add-on version
2. **Blender undo/redo system**: Can restore old attribute state
3. **External scripts**: User scripts or other add-ons creating INT attributes
4. **Cache**: Blender's dependency graph cache

**Solution implemented:**
- INT→FLOAT upgrade path runs **before** clobber guard
- Permille scaling applied automatically for link_conf
- Preflight hard guard in Phase 3 aborts if upgrade fails
- Post-write proof logs verify float types

---

## Next Steps (if crash persists)

1. **Clear Blender cache**: File → Defaults → Load Factory Settings
2. **Delete CityGML objects**: Re-import from fresh .gml files
3. **Check console for upgrade logs**: Should see `[ClobberGuard] UPGRADE attr=link_conf INT→FLOAT ...`
4. **Verify float types in Inspector**: link_conf should show 0.0-1.0 range, not 0-1000

---

## Critical: ops/ Folder Name Collision Warning

**User feedback:** Do NOT create a folder named `ops/` alongside `ops.py` - this creates a Python namespace collision (module vs package).

**Recommended alternative folder names:**
- `ops_modules/`
- `ops_work/`
- `materialize/`

**Status:** No folder created - this is a future modularization concern, not part of current surgical fixes.
