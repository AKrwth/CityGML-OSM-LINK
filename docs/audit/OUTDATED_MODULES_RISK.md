# Outdated Modules Risk Inventory

Date: 2026-02-08
Scope: pipeline/Legacy and duplicate function names across pipeline modules.
Inputs: __init__.py, auto_load.py, ops.py, ui.py, settings.py (import references only).

## Summary
- No direct import references to pipeline/Legacy modules were found in __init__.py, auto_load.py, ops.py, ui.py, or settings.py.
- Legacy scripts appear to be standalone utilities or archived pipelines.
- Duplicate function names exist across Legacy and non-Legacy modules, mostly generic entry points (e.g., main) and helper names (e.g., ensure_collection).

## Inventory Table
| file | lines | imported_by | risk_level | notes |
| --- | ---: | --- | --- | --- |
| pipeline/Legacy/citygml_split.py | 169 | none | MED | Legacy splitter; not referenced by entry points. Contains function name ensure_collection also present in non-Legacy modules (name-only duplicate). |
| pipeline/Legacy/db.py | 65 | none | LOW | Small helper; no import references from entry points. |
| pipeline/Legacy/Data_Set_Tools/organize_and_downscale_dop.py | 255 | none | MED | Legacy dataset tool; defines main (name-only duplicate in non-Legacy scripts). |
| pipeline/Legacy/Data_Set_Tools/prepare_rgb_tiles_only.py | 269 | none | MED | Legacy dataset tool; defines main (name-only duplicate in non-Legacy scripts). |
| pipeline/Legacy/Data_Set_Tools/prepare_terrain_rgb_wcs.py | 278 | none | MED | Legacy dataset tool; defines main (name-only duplicate in non-Legacy scripts). |

## Duplicate Function Names (Legacy vs Non-Legacy)
- ensure_collection: pipeline/Legacy/citygml_split.py also appears in:
  - pipeline/citygml/citygml_import.py
  - pipeline/terrain/basemap_tiles.py
  - pipeline/terrain/dgm_terrain_import.py
  - pipeline/terrain/rgb_basemap_import.py
- main: Legacy Data_Set_Tools scripts also define main in:
  - pipeline/linking/link_gml_to_osm.py
  - pipeline/linking/make_gml_centroids.py
  - pipeline/linking/make_osm_centroids_semantics.py
  - pipeline/terrain/basemap_tiles.py

## Recommendation (No Moves Per Request)
- Treat pipeline/Legacy as archived utilities and keep out of runtime import paths.
- If future cleanup is approved, consider moving Data_Set_Tools and citygml_split into a separate archive folder and updating documentation accordingly.
- Avoid renaming generic function names (main, ensure_collection) unless an actual conflict appears at import time.
