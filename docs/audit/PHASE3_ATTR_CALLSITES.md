# Phase 3 Attribute Call-Sites (UPDATED)

Date: 2026-02-08 (post-fix session — accurate line numbers)
Scope: All call-sites for link_conf/link_dist_m/link_iou in ops.py (13,415 lines).

## Summary

All call-sites request FLOAT data type. No INT requests found.
`grep "link_conf".*"INT"` across ops.py returns **zero matches**.

## Call-Site Inventory

### 1. `_ensure_face_attrs()` (ops.py:6355-6369)

**Lines 6361-6363:**
```python
attrs["link_conf"] = ensure_face_attr(mesh, "link_conf", "FLOAT")
attrs["link_dist_m"] = ensure_face_attr(mesh, "link_dist_m", "FLOAT")
attrs["link_iou"] = ensure_face_attr(mesh, "link_iou", "FLOAT")
```

**Status:** CORRECT — Requests FLOAT

---

### 2. `_ensure_face_attrs_for_materialization()` (ops.py:6999-7016)

**Lines 7013-7015:**
```python
attrs["link_conf"] = ensure_face_attr(mesh, "link_conf", "FLOAT")
attrs["link_dist_m"] = ensure_face_attr(mesh, "link_dist_m", "FLOAT")
attrs["link_iou"] = ensure_face_attr(mesh, "link_iou", "FLOAT")
```

**Proof Log (L7018):** `[PROOF][ATTR_REQUEST] link_conf=FLOAT link_dist_m=FLOAT link_iou=FLOAT`

**Status:** CORRECT — Requests FLOAT

---

### 3. Phase 3 `attr_specs` in `M1DC_OT_MaterializeLinks.execute()` (ops.py:7782-7798)

**Lines 7786-7788:**
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

**Preflight function:** `ensure_face_storage_ready(obj, attr_specs)` at L7795
(calls `ensure_face_attr(me, name, dt)` for each spec)

**Status:** CORRECT — Requests FLOAT

---

## INT-to-FLOAT Upgrade Path (ClobberGuard)

**Location:** `ensure_face_attr()` — ops.py:994-1022

```python
# UPGRADE PATH: INT→FLOAT for link attributes (before clobber guard)
if (name in {"link_conf", "link_dist_m", "link_iou"}
        and attr.domain == "FACE"
        and attr.data_type == "INT"
        and data_type == "FLOAT"
        and len(attr.data) == face_count):
    try:
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
        has_nondefault = any(v != 0.0 for v in preserved_values)
        mesh.attributes.remove(attr)
        attr = mesh.attributes.new(name, "FLOAT", "FACE")
        for i, val in enumerate(preserved_values):
            attr.data[i].value = val
        print(f"[ClobberGuard] UPGRADE attr={name} INT→FLOAT preserved_nondefault={has_nondefault} permille_scaled={permille_scaled} faces={face_count}")
        return attr
    except Exception as e:
        print(f"[ClobberGuard][ERROR] UPGRADE failed for {name}: {e}")
```

**Key points:**
- Runs BEFORE clobber guard KEEP (line 1024)
- Permille scaling: `link_conf` values > 1 get divided by 1000.0 (lines 1007-1009)
- Logs count of permille-scaled values

---

## Phase 3 Hard Guards

**Preflight Guard (ops.py:7873-7878):**
```python
if a_conf.data_type != "FLOAT":
    error_msg = f"Phase 3 preflight failed: link_conf is {a_conf.data_type}, expected FLOAT"
    print(f"[ERROR] {error_msg}")
    self.report({"ERROR"}, error_msg)
    return {"CANCELLED"}
```

**Post-Write Readback Proof (ops.py:7880-7888):**
```python
face0_conf = a_conf.data[0].value
face0_dist = a_dist.data[0].value
face0_iou = a_iou.data[0].value
print(f"[PROOF][FLOAT_WRITE] face0 conf={face0_conf} dist={face0_dist} iou={face0_iou} "
      f"types=({type(face0_conf).__name__}, {type(face0_dist).__name__}, {type(face0_iou).__name__})")
```

---

## Conclusion

- All 3 call-sites request FLOAT (zero INT requests)
- ClobberGuard upgrade handles INT→FLOAT with permille scaling
- Upgrade runs BEFORE clobber guard KEEP
- Phase 3 has hard preflight guard (cancels if not FLOAT)
- Phase 3 has post-write readback proof (logs types)

**If Phase 3 still crashes with INT/FLOAT TypeError, possible causes:**
1. Stale `.blend` file with old INT attributes → re-run Linking from scratch
2. External script creating INT attributes outside of add-on
3. Blender undo/redo restoring old INT attributes after upgrade
