# Add-on Architecture Overview

This document explains the operational structure of the CityGML–OSM-LINK add-on in plain terms.

The pipeline follows a fixed sequence:

Terrain → CityGML → OSM → Linking → Materialize → Legend → Inspector

Each stage produces explicit artifacts and prepares the next stage.

---

## 1. Terrain

Purpose:
Import and prepare terrain or raster-based ground data.

Responsibilities:
- Merge terrain tiles if necessary.
- Apply correct scale.
- Validate placement.
- Align with WORLD_ORIGIN (EPSG:25832).

Output:
A validated terrain mesh anchored in a stable coordinate system.

---

## 2. CityGML

Purpose:
Import building geometry and semantic identifiers.

Responsibilities:
- Parse CityGML tiles.
- Extract geometry.
- Assign `building_idx` per tile.
- Preserve `source_tile`.

Output:
Mesh objects with stable tile-local identifiers.

---

## 3. OSM (GeoPackage)

Purpose:
Load contextual infrastructure data.

Responsibilities:
- Open GeoPackage in read-only mode.
- Detect relevant tables.
- Extract centroid information.
- Prepare linking database.

Output:
Spatial reference data for semantic matching.

---

## 4. Linking

Purpose:
Match CityGML buildings to OSM features.

Method:
- Centroid-based spatial matching.
- Confidence scoring.
- Optional IoU-based refinement.

Output:
Link database associating:
(source_tile, building_idx) ↔ osm_id

---

## 5. Materialize

Purpose:
Write linking results back to Blender meshes.

Responsibilities:
- Create face-level attributes.
- Store `osm_id`, confidence, distance, IoU.
- Ensure deterministic write-once behavior.

Output:
Semantic attributes physically stored in the scene.

---

## 6. Legend Encoding

Purpose:
Convert categorical OSM attributes into stable integer codes.

Method:
- Deterministic mapping (alphabetical order).
- 0 reserved for NULL.
- Store legend CSV for reproducibility.

Output:
Numeric attribute layers ready for filtering and analysis.

---

## 7. Inspector

Purpose:
Enable semantic exploration inside Blender.

Features:
- Query face attributes.
- Filter by codes.
- Aggregate by building.
- Export reports (CSV/Markdown).

---

## Design Principles

- Deterministic processing.
- No stochastic elements.
- Explicit pipeline stages.
- Reconstructable state.
- No hidden global side effects.
- Clear separation of geometry and semantics.
