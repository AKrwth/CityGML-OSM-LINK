# MKDB vs MATERIALIZE — Object Scope Analysis

**Date**: 2026-02-08
**Method**: Static analysis only (no Blender execution)

---

## 1. What objects does MKDB iterate over?

**MKDB (Phase 4.5) in `M1DC_OT_MaterializeLinks.execute()`** — `ops.py:7889`:

```python
meshes_to_scan = [obj]  # Start with current mesh
```

Where `obj` is defined at `ops.py:7662`:

```python
obj = context.active_object
```

**MKDB iterates over exactly ONE object: the currently active Blender object.**

This is then passed to `_collect_unique_osm_keys_from_meshes()` at `ops.py:7893`:

```python
ids_from_meshes, mkdb_proof = _collect_unique_osm_keys_from_meshes(meshes_to_scan, mkdb_link_map)
```

---

## 2. What does `_collect_unique_osm_keys_from_meshes()` do with those objects?

**Location**: `ops.py:1384-1443`

```python
def _collect_unique_osm_keys_from_meshes(mesh_objs, link_map):
    ...
    for mesh_obj in mesh_objs:
        mesh = mesh_obj.data          # <-- uses obj.data (ORIGINAL mesh)
        ...
        for poly_idx in range(face_count):
            bidx = int(idx_attr.data[poly_idx].value)
            row = link_map.get((source_tile, bidx))
            if row:
                osm_way_id = _normalize_osm_id(row.get("osm_id"))
                if osm_way_id and osm_way_id not in ("—", "0"):
                    keys.add(osm_way_id)
```

**Key observation**: Uses `mesh_obj.data` — the **original** mesh, not an evaluated depsgraph copy.

---

## 3. What does Phase 3 (MaterializeLinks) write to?

**Location**: `ops.py:7677`

```python
mesh = obj.data
```

Phase 3 also writes to `obj.data` — the **original** mesh of the single active object.

**After `ensure_face_storage_ready()` at line 7785:**
```python
mesh = ensure_face_storage_ready(obj, attr_specs)
```
This may return a baked mesh, but it's still on the same `obj`.

---

## 4. What does `_materialize_face_attributes()` (alternative path) iterate over?

**Location**: `ops.py:7077`

```python
mesh_objs = _collect_citygml_meshes()
```

Which collects from (`ops.py:6966-6986`):

```python
def _collect_citygml_meshes():
    col_tiles = bpy.data.collections.get("CITYGML_TILES")
    col_build = bpy.data.collections.get("CITYGML_BUILDINGS")
    col_build_split = bpy.data.collections.get("CITYGML_BUILDINGS_SPLIT")

    if col_tiles and len(col_tiles.objects):
        objs = list(col_tiles.objects)
    elif col_build:
        objs = list(col_build.objects)
    elif col_build_split:
        objs = list(col_build_split.objects)
    else:
        objs = [o for o in bpy.data.objects if o.type == "MESH"]
```

**This iterates over ALL CityGML mesh objects** from the appropriate collection.

---

## 5. Object Scope Comparison

| Pipeline Step | Objects Processed | Mesh Access Method | Source |
|---|---|---|---|
| **Phase 3** (MaterializeLinks.execute) | `context.active_object` (1 object) | `obj.data` (original) | ops.py:7662, 7677 |
| **MKDB** (Phase 4.5 in MaterializeLinks) | `[obj]` (same 1 object) | `mesh_obj.data` (original) | ops.py:7889, 1400 |
| **_materialize_face_attributes()** | `_collect_citygml_meshes()` (ALL CityGML meshes) | `mesh_obj.data` (original) | ops.py:7077, 7099 |

---

## 6. Does an evaluated depsgraph come into play?

`_collect_unique_osm_keys_from_meshes()` (MKDB harvester) uses:
```python
mesh = mesh_obj.data  # ORIGINAL, not evaluated
```

Phase 3 also uses:
```python
mesh = obj.data  # ORIGINAL
```

Neither uses an evaluated depsgraph for the actual attribute read/write. The `_get_evaluated_mesh()` function exists at `ops.py:1047` but is NOT called by MKDB or Phase 3.

---

## 7. Critical Finding: Single-Object vs Multi-Object Mismatch

**Phase 3 + MKDB** both operate on `context.active_object` — a SINGLE mesh.

**`_materialize_face_attributes()`** operates on ALL CityGML meshes via `_collect_citygml_meshes()`.

The docstring in `_collect_unique_osm_keys_from_meshes()` (line 1387) says:
```
1. iterate mesh objects (same list from _collect_citygml_meshes)
```

But the actual call at line 7889 passes `[obj]` — NOT the full `_collect_citygml_meshes()` list.

**This means:**
- MKDB only harvests OSM IDs from the single active mesh
- If the scene has multiple CityGML tile meshes, MKDB will miss IDs from all non-active meshes
- The resulting mkdb.sqlite will be **incomplete** (only containing features for one tile)

### VERDICT:

**MKDB and Phase 3 share the same single mesh instance** (both use `obj.data` of `context.active_object`). They are consistent with each other.

**HOWEVER**, MKDB's scope is narrower than the alternative `_materialize_face_attributes()` path, which processes ALL CityGML meshes. If the user has a multi-tile scene, MKDB will produce an incomplete semantic snapshot.

The comment "same list from _collect_citygml_meshes" in the docstring is **MISLEADING** — the actual call passes `[obj]`, not the full collection list.
