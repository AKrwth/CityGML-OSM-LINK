# Proof: Disk State (UPDATED)

Date: 2026-02-08 (post-surgical-fix session)
Scope: Verify actual file state after Startup + MKDB fixes applied.

## File Existence and Line Counts

| File | Status | Lines | Last Modified |
|---|---|---:|---|
| ops.py | EXISTS | 13,415 | 2026-02-08 |
| __init__.py | EXISTS | 199 | 2026-02-08 |
| utils/__init__.py | EXISTS | 1 | 2026-02-01 |
| utils/startup/__init__.py | EXISTS | 1 | 2026-02-01 |
| utils/startup/init_blender_compatibility.py | EXISTS | 64 | 2026-02-06 |
| utils/init_blender_compatibility.py | **MISSING** | — | — |

## Startup Import Verification

**Import line in __init__.py (L12):**
```python
from .utils.startup.init_blender_compatibility import ensure_pkg_resources  # noqa
```

**Status:** CORRECT — Import path matches existing file on disk.

**Startup log line (L14):**
```python
print("[STARTUP] ensure_pkg_resources OK")
```

**Status:** CORRECT — Will print on addon load.

## MKDB Scope Verification

**MKDB mesh scan (ops.py:7889):**
```python
meshes_to_scan = _collect_citygml_meshes()
```

**Status:** CORRECT — Scans ALL CityGML meshes, not just active object.

**Fallback guard (ops.py:7890-7892):**
```python
if not meshes_to_scan:
    meshes_to_scan = [obj]  # Fallback: at least scan active object
```

**Proof logs (ops.py:7893-7894):**
```
[MKDB][SCOPE] meshes_to_scan=<n> (expected ~tiles count)
[MKDB][SCOPE] sample: [<first 3 names>]
```

## Fixes Applied This Session

| Fix | File | Line(s) | Change |
|---|---|---|---|
| Startup import | __init__.py | 12 | `.utils.init_blender_compatibility` → `.utils.startup.init_blender_compatibility` |
| Startup proof log | __init__.py | 14 | Added `print("[STARTUP] ensure_pkg_resources OK")` |
| MKDB scope | ops.py | 7889 | `[obj]` → `_collect_citygml_meshes()` |
| MKDB empty guard | ops.py | 7890-7892 | Added fallback to `[obj]` if empty |
| MKDB proof logs | ops.py | 7893-7894 | Added scope count + sample names |

## Items Already Present (No Change Needed)

| Item | Location | Status |
|---|---|---|
| INT→FLOAT ClobberGuard upgrade | ops.py:994-1022 | Present with permille scaling |
| Phase 3 preflight FLOAT guard | ops.py:7873-7878 | Present — cancels if not FLOAT |
| Phase 3 readback proof | ops.py:7880-7888 | Present — logs types + values |
| All callsites request FLOAT | 6361-6363, 7013-7015, 7786-7788 | Verified — zero INT requests |
