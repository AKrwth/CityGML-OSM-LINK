# FINAL VERDICT — Forensic Audit

**Date**: 2026-02-08
**Method**: Read-only file analysis, no code execution, no modifications

---

## Claim-by-Claim Verification

### CLAIM: INT-to-FLOAT upgrade logic was added to `ensure_face_attr()`
```
STATUS: VERIFIED
EVIDENCE: ops.py:994-1012 — Upgrade path exists, correctly positioned before clobber guard,
          restricted to {link_conf, link_dist_m, link_iou}. Returns upgraded FLOAT attr.
```

### CLAIM: `_ensure_face_attrs_for_materialization()` requests FLOAT for link attrs
```
STATUS: VERIFIED
EVIDENCE: ops.py:7003-7005 — All three attrs request "FLOAT".
          ops.py:7776-7778 — Phase 3 attr_specs also request "FLOAT".
          No INT remnants found for link_conf/link_dist_m/link_iou.
```

### CLAIM: MKDB logic was removed from linking and relocated after Phase 3
```
STATUS: VERIFIED
EVIDENCE: ops.py:6271-6275 — Old location in _link_gpkg_to_citygml() is stubbed out (comment + print only).
          ops.py:7882-7934 — MKDB now runs inside M1DC_OT_MaterializeLinks.execute(),
          AFTER Phase 3 write loop (lines 7811-7848).
```

### CLAIM: MKDB routing is correct (runs on the same mesh Phase 3 writes to)
```
STATUS: PARTIAL
EVIDENCE: ops.py:7889 — meshes_to_scan = [obj] (single active object).
          Phase 3 also writes to obj.data of the same active object.
          MKDB and Phase 3 share the SAME mesh instance — consistent.
          BUT: MKDB only scans 1 object vs _materialize_face_attributes()
          which scans ALL CityGML meshes via _collect_citygml_meshes().
          In multi-tile scenes, MKDB will produce an INCOMPLETE snapshot.
```

### CLAIM: Startup folder was cleaned up / flattened
```
STATUS: FALSE (BROKEN)
EVIDENCE: __init__.py:12 imports from .utils.init_blender_compatibility (flattened path).
          utils/init_blender_compatibility.py DOES NOT EXIST.
          utils/startup/init_blender_compatibility.py STILL EXISTS (64 lines, unchanged).
          utils/startup/ directory STILL EXISTS with __init__.py and __pycache__/.
          Import path was updated but file was NOT moved = addon registration WILL FAIL.
```

### CLAIM: ops.py is still ~13,403 lines (no meaningful net change)
```
STATUS: VERIFIED
EVIDENCE: wc -l reports 13,402 lines. Size = 556,713 bytes.
          Last modified 2026-02-08 01:05:56 — file WAS touched.
          Line count is essentially the claimed ~13.4k.
```

### CLAIM: Net line count change ~+30 / -50 lines
```
STATUS: CANNOT VERIFY (no git history)
EVIDENCE: No git repository. No baseline to diff against.
          Current line count (13,402) is consistent with the claim
          that the file is approximately the same size.
```

---

## Summary Table

| Claim | Status |
|---|---|
| INT-to-FLOAT upgrade in ensure_face_attr | **VERIFIED** |
| FLOAT types in _ensure_face_attrs_for_materialization | **VERIFIED** |
| FLOAT types in Phase 3 attr_specs | **VERIFIED** |
| MKDB removed from _link_gpkg_to_citygml | **VERIFIED** |
| MKDB relocated after Phase 3 | **VERIFIED** |
| MKDB uses same mesh as Phase 3 | **VERIFIED** (single active object) |
| MKDB covers all CityGML meshes | **FALSE** (only scans active object) |
| Startup cleanup applied | **FALSE** (BROKEN import) |
| ops.py ~13.4k lines | **VERIFIED** (13,402 lines) |

---

## Bottom Line

**The ops.py surgical changes (INT-to-FLOAT upgrade, MKDB relocation) WERE applied to disk and are verifiable.**

**The startup cleanup was NOT completed** — the `__init__.py` import path was changed to a flattened path, but the file was never moved. This leaves the addon in a **BROKEN** state where it will fail to register in Blender (unless a stale bytecode cache masks the issue).

**MKDB scope is narrow** — it only harvests IDs from the single active object, not all CityGML meshes. This is consistent with Phase 3 (which also operates on a single object) but inconsistent with the broader `_materialize_face_attributes()` path.

---

*Reality > Intent. This audit reports what IS on disk, not what was INTENDED.*
