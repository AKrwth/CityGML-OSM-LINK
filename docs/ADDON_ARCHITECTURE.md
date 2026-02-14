# Academic Context

This add-on was developed as part of an academic research project at RWTH Aachen University (Design Computation).

Its purpose is to explore reproducible, phase-based integration of heterogeneous urban datasets (CityGML, OSM, terrain) inside Blender.

The architecture prioritizes:

- Deterministic artifact generation
- Explicit validation gates
- Reproducible semantic caching
- Transparent failure handling

It is not designed as a production GIS pipeline but as an experimental integration framework for research and teaching.

---

# Add-on Architecture — Deterministic Pipeline Edition

This document defines the architectural contracts of the CityGML-OSM-LINK add-on.
It supersedes informal descriptions and serves as the binding reference for pipeline behavior.

---

## 1. Separation of Concerns

### Orchestration layer

| File | Role |
| --- | --- |
| `ops.py` | Orchestration — operator registration, delegation to `pipeline/` |
| `settings.py` | Scene property definitions |
| `ui.py` | Panel layout |
| `auto_load.py` | Class auto-discovery and registration |

### Core logic layer

All operational logic lives in `pipeline/`:

| Package | Responsibility |
| --- | --- |
| `pipeline/terrain/` | Terrain import, alignment, scaling, validation |
| `pipeline/citygml/` | CityGML parsing, geometry import, material assignment |
| `pipeline/linking/` | GML↔OSM centroid generation, matching, key normalization, caching |
| `pipeline/operations/` | Blender operator implementations |
| `pipeline/diagnostics/` | Legend encoding, geometry tripwires, spatial debug, placement checks |
| `pipeline/osm/` | GeoPackage reader |
| `pipeline/spreadsheet/` | Building table data management |

### Utilities

`utils/` provides shared helpers: logging, geometry, validation, Blender compatibility shims.

---

## 2. Pipeline Stages

The pipeline follows a fixed sequence:

```text
Terrain → CityGML → OSM → Linking → Materialize → Legend → Inspector
```

Each stage produces explicit artifacts and prepares the next stage.

| Stage | Input | Output |
| --- | --- | --- |
| Terrain | DEM raster / terrain tiles | Validated terrain mesh at world origin |
| CityGML | CityGML XML tiles | Mesh objects with `building_idx` and `source_tile` |
| OSM | GeoPackage (read-only) | Spatial reference data for semantic matching |
| Linking | CityGML meshes + OSM centroids | Link database: `(source_tile, building_idx) ↔ osm_id` |
| Materialize | Link database + CityGML meshes | Face-level attributes written to scene |
| Legend | Materialized attributes | Integer-coded attribute layers + legend CSVs |
| Inspector | Scene attributes | Query, filter, aggregate, export |

---

## 3. Artifact Contract

All pipeline artifacts are written deterministically into:

```text
output_dir/
├── links/        # Link databases (SQLite)
└── legends/      # Legend CSVs
```

**Rules:**

- No artifact may be written outside `output_dir`.
- `links_db_path` (scene property, `options={"HIDDEN"}`) must always point to a file inside `output_dir/links/`.
- If `links_db_path` is empty, Materialize auto-detects the link DB by scanning `output_dir/links/` for a file matching the GPKG stem (`{gpkg_stem}_links.sqlite`).
- `legends/` contains deterministic CSV exports of category→code mappings.

**Implementation locations:**

- `links/` directory creation: `pipeline/linking/linking_cache.py` (`ensure_link_dbs`)
- `legends/` directory: `pipeline/diagnostics/legend_encoding.py`, `ops.py`
- Auto-detect logic: `pipeline/operations/materialize_ops.py` (`execute`), `ops.py` (`_load_link_lookup`)

---

## 4. Linking Determinism

### Key identity guarantee

`pipeline/linking/key_normalization.normalize_source_tile()` is the **single source of truth** for `source_tile` identity.

**Normalization steps:**

1. `None`/empty → `""`
2. Path separators normalized; filename extracted
3. File extension stripped (`PurePosixPath.stem`)
4. Blender duplicate suffixes stripped (`.001`, `.002`, etc.)

**Mandatory application points:**

| Context | Location |
| --- | --- |
| GML centroid generation | `pipeline/linking/make_gml_centroids.py` |
| CityGML import tagging | `pipeline/citygml/citygml_import.py` |
| Link DB creation | `pipeline/linking/linking_cache.py` |
| Link DB loading | `ops.py` (`_load_link_lookup`) |
| Materialize lookup | `pipeline/operations/materialize_ops.py` |
| Mesh discovery | `pipeline/linking/mesh_discovery.py` |

If `normalize_source_tile()` is not applied at any of these points, key mismatches will occur and linking will silently produce zero matches. This function is the identity guarantee of the entire linking pipeline.

### Matching method

- Centroid-based spatial matching with confidence scoring.
- Optional IoU-based refinement.
- Link database associates `(source_tile, building_idx) ↔ osm_id` with confidence, distance, and IoU metadata.

---

## 5. Failure Semantics

### Contract

All data-processing operators must return `{'CANCELLED'}` when preconditions are not met:

| Condition | Result |
| --- | --- |
| No link DB exists | `{'CANCELLED'}` |
| Link DB is empty | `{'CANCELLED'}` |
| Terrain validation fails | `{'CANCELLED'}` |
| Required settings missing | `{'CANCELLED'}` |
| No CityGML meshes found | `{'CANCELLED'}` |
| Required functions unavailable | `{'CANCELLED'}` |

**No silent `{'FINISHED'}` on failure.** If a data-processing operator cannot complete its work, it must cancel and report to the user via `self.report({'WARNING'}, ...)` or `self.report({'ERROR'}, ...)`.

UI/wizard operators (modal dialogs, property presets) may return `{'FINISHED'}` unconditionally as they do not perform data processing.

---

## 6. Terrain Policy

### DGM terrain import

| Property | Value |
| --- | --- |
| Scale | Baked to `(1, 1, 1)` — enforced and verified |
| Alignment | Min-corner shift relative to `WORLD_ORIGIN` (EPSG:25832) |
| Heuristics | None — no bounding-box center placement |

**Implementation:** `pipeline/terrain/dgm_terrain_import.py`

The terrain mesh location is computed as:

```python
loc_x = min_easting  - world_min_easting
loc_y = min_northing - world_min_northing
```

This is a pure min-corner offset. No centroid or bounding-box center calculation is involved.

### Terrain scaling (post-import)

`pipeline/terrain/terrain_scaling.py` provides `scale_and_place_terrain` for rescaling already-imported terrain meshes. This path applies a computed uniform scale and uses `world_to_local` with the bounding-box center as the reference point. It is a separate code path from DGM import.

---

## 7. ClobberGuard Policy

Attribute writes are protected by schema enforcement via `ensure_face_attr` (defined in `ops.py`).

### Schema rules

| Check | Enforcement |
| --- | --- |
| Domain | Must be `FACE`. Non-face attributes are rejected. |
| Data type | Must match expected type (`INT`, `FLOAT`, `STRING`). |
| Type upgrade | Allowed (e.g., `INT` → `FLOAT` for `link_conf`, `link_dist_m`, `link_iou`). Existing values are preserved with conversion. |
| Length | Must equal mesh face count. |

### Protected attributes

The following attributes are clobber-protected:

```text
osm_way_id, osm_id_int, osm_id, has_link,
link_conf, link_dist_m, link_iou
```

If a protected attribute already contains non-default (non-zero, non-empty) values, it is **not overwritten**. The guard scans the first 200 values for non-default entries. If found, the attribute is kept and logged as `[ClobberGuard] KEEP`.

### Additional schema enforcement

`pipeline/operations/materialize_ops.py` defines `_p3_schema` which performs explicit wrong-type detection with remove-and-recreate semantics during materialization.

---

## 8. Reproducibility

### Safe deletion

`output_dir` can be deleted safely between runs. The pipeline will recreate all artifacts deterministically from source inputs.

### Rebuild mechanism

- `pipeline/linking/linking_cache.py` uses mtime-based staleness detection (`_needs_refresh`).
- If a target DB does not exist (deleted), `_needs_refresh` returns `True` and the intermediate is regenerated:
  - OSM centroid DB rebuilt from GPKG source.
  - GML centroid DB rebuilt from scene meshes.
  - Link DB rebuilt from centroid intermediates.
- `legends/` directory is rebuilt by `build_all_legends()` from GPKG source data during pipeline execution.
- Terrain cache uses tolerance-based invalidation; if deleted, terrain is re-imported from source rasters.

### Precondition

Deterministic reproducibility requires that source inputs (CityGML files, GeoPackage, DEM rasters) remain unchanged between runs. If source inputs change, artifacts will be regenerated to reflect the new inputs.

---

## 9. Legend Encoding

- Categorical OSM attributes are converted to stable integer codes.
- Mapping is deterministic: alphabetical ordering of unique values.
- Code `0` is reserved for `NULL` / unlinked.
- Legend CSVs are exported to `output_dir/legends/` for external reproducibility.

**Implementation:** `pipeline/diagnostics/legend_encoding.py`

---

## 10. Design Principles

- Deterministic processing at every stage.
- No stochastic elements.
- Explicit pipeline stages with defined inputs and outputs.
- Reconstructable state from scene properties and generated artifacts.
- No hidden global side effects.
- Clear separation of geometry and semantics.
- Fail-loud: no silent success on failure.
