# STARTUP SHIM STATUS — Forensic Audit

**Date**: 2026-02-08
**Method**: Read-only file inspection

---

## 1. Where is `ensure_pkg_resources()` imported from?

**`__init__.py` line 12:**
```python
from .utils.init_blender_compatibility import ensure_pkg_resources  # noqa
```

This resolves to: `utils/init_blender_compatibility.py`

---

## 2. Does that file exist?

**NO.** `utils/init_blender_compatibility.py` does **NOT** exist.

The actual file is at: `utils/startup/init_blender_compatibility.py` (64 lines, 2,122 bytes).

There is NO file, symlink, or `.pyc` cache at `utils/init_blender_compatibility.py`:
- `utils/__pycache__/` contains: `__init__`, `common`, `logging_system` — NO `init_blender_compatibility`
- The startup subfolder's cache at `utils/startup/__pycache__/init_blender_compatibility.cpython-311.pyc` exists, confirming the module was imported from the STARTUP path previously.

---

## 3. Does `ensure_pkg_resources()` run before `bpy` import?

**YES, in principle.** `__init__.py` lines 12-13 run BEFORE the `bpy` import on line 16:

```python
# Line 12: from .utils.init_blender_compatibility import ensure_pkg_resources  # noqa
# Line 13: ensure_pkg_resources()
# Line 16: import bpy
```

**HOWEVER**, the import on line 12 will FAIL because the file doesn't exist at `utils/init_blender_compatibility.py`. So in practice, `ensure_pkg_resources()` will NOT run — the addon will crash at registration with an `ImportError`.

---

## 4. Do duplicate or unused startup files remain?

| File | Status |
|---|---|
| `utils/startup/__init__.py` | EXISTS (but unreferenced by `__init__.py`) |
| `utils/startup/init_blender_compatibility.py` | EXISTS (contains the actual function) |
| `utils/init_blender_compatibility.py` | DOES NOT EXIST (import target) |

The `utils/startup/` directory is completely intact — it was never cleaned up.

---

## 5. Verdict

**The import in `__init__.py` references a flattened path (`utils.init_blender_compatibility`), but the file was never actually moved from `utils/startup/init_blender_compatibility.py` to `utils/init_blender_compatibility.py`.**

This means someone updated the IMPORT PATH but forgot to MOVE THE FILE.

### Status: **BROKEN**

- The addon's `__init__.py` will raise `ImportError` on line 12 when Blender tries to register the addon
- The `utils/startup/` folder was NOT flattened
- The `utils/startup/` folder was NOT removed
- The actual `ensure_pkg_resources()` function is intact but unreachable via the import path

### Classification:

**NEITHER flattened NOR unchanged — the import path was changed but the file was not moved, creating a BROKEN state.**

If this addon currently loads in Blender, it is ONLY because:
1. A stale `.pyc` cache exists somewhere that maps the old import, OR
2. The addon has not been reloaded since the `__init__.py` import path was changed

Without one of those conditions, this addon WILL NOT REGISTER.
