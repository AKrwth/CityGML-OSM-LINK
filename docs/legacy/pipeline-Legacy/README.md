# pipeline/legacy/

**Status:** DEPRECATED — DO NOT IMPORT FROM THIS FOLDER

This folder contains historical/deprecated code preserved for audit trail purposes only.

---

## ⚠️ CRITICAL POLICY

**NO active code may import from `pipeline/legacy/`.**

If you need functionality from this folder:
1. Check if it exists elsewhere (likely `utils/` or `pipeline/`)
2. If not, extract & refactor FIRST, then import from new location
3. **NEVER** import legacy code directly

---

## Modules

### citygml_split.py
- **Status:** Deprecated (broken import)  
- **Reason:** Old splitting logic (unused in current pipeline)  
- **Last Used:** Unknown  
- **Issue:** `from .logging_system` — missing relative path, should be `from ...utils.logging_system`  
- **Action:** DO NOT FIX — module is obsolete

### db.py
- **Status:** Deprecated (redirects to `utils.common`)  
- **Reason:** Superseded by centralized DB access (`utils.common.open_db_readonly()`)  
- **Last Used:** Phase 7-8 (2026-01)  
- **Migration Path:**
  ```python
  # OLD (deprecated):
  from pipeline.legacy.db import open_db_readonly
  
  # NEW (current):
  from utils.common import open_db_readonly
  ```

### Data_Set_Tools/
- **Status:** Deprecated  
- **Reason:** External data prep scripts (not part of addon runtime)  
- **Contents:**
  - `prepare_terrain_rgb_wcs.py` — WCS terrain RGB preparation
  - `prepare_rgb_tiles_only.py` — RGB tile extraction
  - `organize_and_downscale_dop.py` — DOP image processing
- **Note:** These are standalone preprocessing scripts, not imported by addon code

### startup/
- **Status:** **MOVED** to `utils/blender_compat.py`  
- **Date Moved:** 2026-02-08 (Phase 13 — Surgical Refactor)  
- **Reason:** Eliminate nested startup/ folder, integrate Blender compatibility shims into flat utils/ structure  
- **Original Content:**
  - `__init__.py` — Package marker
  - `init_blender_compatibility.py` — `ensure_pkg_resources()` shim for Blender's broken pkg_resources
- **Migration Path:**
  ```python
  # OLD (deprecated):
  from .utils.startup.init_blender_compatibility import ensure_pkg_resources
  
  # NEW (current):
  from .utils.blender_compat import ensure_pkg_resources
  ```

---

## Verification Commands

### Check for illegal legacy imports (PowerShell):
```powershell
# Search for imports from pipeline.legacy (excluding legacy/ itself and docs)
Select-String -Path "*.py" -Recurse -Pattern "from.*\.legacy|import.*\.legacy" `
  -Exclude @("legacy\**", "docs\**")

# Expected: 0 matches
```

### Check for imports from deleted startup/ folder:
```powershell
Select-String -Path "*.py" -Recurse -Pattern "from.*\.startup\.|import.*startup\." `
  -Exclude @("legacy\**", "docs\**")

# Expected: 0 matches
```

---

## History

| Date | Module | Action | Reason |
|------|--------|--------|--------|
| ~2025 | citygml_split.py | Moved to legacy | Unused splitting logic |
| ~2025 | db.py | Moved to legacy | Centralized to utils.common |
| ~2025 | Data_Set_Tools/ | Moved to legacy | External preprocessing scripts |
| 2026-02-08 | startup/ | Moved to legacy (after integrating into utils/blender_compat.py) | Simplify structure, eliminate nesting |

---

## Adding New Legacy Modules

If you identify a module that should be deprecated:

1. **Verify zero imports:**
   ```powershell
   Select-String -Path "*.py" -Recurse -Pattern "from.*module_name|import.*module_name" `
     -Exclude @("legacy\**", "docs\**")
   ```

2. **Move to legacy:**
   ```powershell
   Move-Item path/to/module.py pipeline/legacy/
   ```

3. **Update this README** with:
   - Module name
   - Deprecation reason
   - Last known usage date
   - Migration path (if applicable)

4. **Add tombstone comment** in original location (if needed):
   ```python
   # [DEPRECATED] module_name.py moved to pipeline/legacy/
   # Use new_location.py instead
   ```

---

## Restore Guidance

**If you absolutely must temporarily restore legacy code for debugging:**

1. **Create a feature branch** (NEVER on main!)
2. **Add explicit warning logs:**
   ```python
   import warnings
   warnings.warn("Using DEPRECATED legacy module X", DeprecationWarning)
   ```
3. **Document the reason** in commit message
4. **File an issue** to remove the temporary import ASAP

**NEVER commit legacy imports to main branch without explicit review.**

---

**Last Updated:** 2026-02-08  
**Maintained by:** Surgical Refactor Agent (Phase 13)  
**Policy Owner:** Add-on architecture team
