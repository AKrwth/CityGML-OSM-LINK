# pipeline/legacy/startup/

**Status:** MOVED TO `utils/blender_compat.py`  
**Date:** 2026-02-08 (Phase 13 — Surgical Refactor)  
**Reason:** Eliminate nested startup/ folder, integrate Blender compatibility shims into utils

## Original Content

This folder originally contained:
- `__init__.py` — Package marker
- `init_blender_compatibility.py` — `ensure_pkg_resources()` shim for Blender's broken pkg_resources

## Current Location

**Function:** `ensure_pkg_resources()`  
**New Location:** `utils/blender_compat.py`  
**Import:** `from .utils.blender_compat import ensure_pkg_resources`

## Why Moved?

1. **Semantic clarity:** Blender compatibility shims belong in `utils/`, not nested in `utils/startup/`
2. **Simplified structure:** Eliminates extra nesting level
3. **Consistency:** All utility functions in flat `utils/` structure

## Migration Path

If you need the original code for reference:
```python
# OLD (deprecated):
from .utils.startup.init_blender_compatibility import ensure_pkg_resources

# NEW (current):
from .utils.blender_compat import ensure_pkg_resources
```

**DO NOT restore this folder to active use.** Use `utils/blender_compat.py` instead.
