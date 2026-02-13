# PHASE 1 — Repository Reality Scan Report

**Datum:** 2026-02-08  
**Agent:** Surgical Refactor Agent  
**Ziel:** ops.py split + startup kill + legacy sweep

---

## 1.1 STRUKTUR & INVENTAR

### Python-Dateien Gesamt: 50

#### Root (5 Dateien)
- `__init__.py` — Add-on Entry, registriert 57 Operatoren
- `ops.py` — **13416 Zeilen** (Ziel: <8000) ⚠️ **MASSIVER REFACTOR NÖTIG**
- `settings.py` — Blender Properties
- `ui.py` — UI Panels + 2 Operatoren (Table selectors)
- `auto_load.py` — Auto-load helper

#### utils/ (4 Dateien + startup/)
- `common.py` — Shared utilities (world origin, DB access, CRS)
- `logging_system.py` — Zentrale Log-Infrastruktur
- `validation.py` — Validation helpers
- `__init__.py` — Package marker

**utils/startup/** (2 Dateien) ⚠️ **MUSS ELIMINIERT WERDEN**
- `init_blender_compatibility.py` — 60 Zeilen: `ensure_pkg_resources()` shim
- `__init__.py` — Package marker

#### pipeline/ (39 Dateien in 6 Ordnern)

**pipeline/citygml/** (3)
- citygml_import.py, citygml_materials.py, __init__.py

**pipeline/diagnostics/** (8)
- diagnostic.py, face_attr_tools.py, geometry_tripwires.py, legend_encoding.py,  
  placement_checks.py, spatial_debug.py, terrain_alignment_check.py, __init__.py

**pipeline/linking/** (7)
- common.py, link_gml_to_osm.py, linking_cache.py, make_gml_centroids.py,  
  make_osm_centroids_semantics.py, validation.py, __init__.py

**pipeline/osm/** (2)
- gpkg_reader.py, __init__.py

**pipeline/terrain/** (14)
- basemap_tiles.py, dgm_terrain_import.py, m1_basemap.py, m1_terrain_csv.py,  
  rgb_basemap_import.py, terrain_discovery.py, terrain_merge.py, terrain_postprocess.py,  
  terrain_scaling.py, terrain_validation.py, terrain_world_calibration.py, z_alignment.py, __init__.py

**pipeline/Legacy/** (5) ⚠️ **DEPRECATED ZONE**
- `citygml_split.py` — 215 Zeilen, broken import `from .logging_system` ❌
- `db.py` — 73 Zeilen, redirects zu utils.common ⚠️
- `Data_Set_Tools/` (3 Scripts: prepare_terrain_rgb_wcs.py, prepare_rgb_tiles_only.py, organize_and_downscale_dop.py)

---

## 1.2 IMPORT-GRAPH (Harte Evidenz)

### ops.py wird importiert von:
1. **__init__.py:80** — `from . import ops` + explizite Operatoren (57 Klassen)
2. **ui.py:7** — `from . import ops`

### startup/ wird importiert von:
1. **__init__.py:12** — `from .utils.startup.init_blender_compatibility import ensure_pkg_resources`
   - Aufruf: Zeile 13 `ensure_pkg_resources()`
   - Zweck: Blender pkg_resources shim (Startup-only)

### Legacy/ Importe:
**KEINE AKTIVEN IMPORTS GEFUNDEN** ✅  
Dokumentation bestätigt: "Zero legacy imports" (audit/OUTDATED_MODULES_RISK.md:8)

---

## 1.3 OPERATOR-INVENTAR (ops.py)

### Gesamt: 57 Operator-Klassen (alle M1DC_OT_*)

| # | Klasse | bl_idname | Zeile | Kategorie |
|---|--------|-----------|-------|-----------|
| 1 | M1DC_OT_RelocalizeCityGML | m1dc.relocalize_citygml | 5847 | CityGML |
| 2 | M1DC_OT_ReloadOSMTables | m1dc.reload_osm_tables | 6842 | Spreadsheet |
| 3 | M1DC_OT_ReloadOSMColumns | m1dc.reload_osm_columns | 6860 | Spreadsheet |
| 4 | M1DC_OT_SpreadsheetReload | m1dc_spreadsheet.reload | 6880 | Spreadsheet |
| 5 | M1DC_OT_SpreadsheetColumnsSelect | m1dc_spreadsheet.columns_select | 6898 | Spreadsheet |
| 6 | M1DC_OT_SpreadsheetSyncFromSelection | m1dc_spreadsheet.sync_from_selection | 6925 | Spreadsheet |
| 7 | M1DC_OT_SpreadsheetSelectRow | m1dc_spreadsheet.select_row | 6942 | Spreadsheet |
| 8 | M1DC_OT_MaterializeLinks | m1dc.materialize_links | 7619 | Linking |
| 9 | M1DC_OT_InspectActiveFace | m1dc.inspect_active_face | 8141 | Inspector |
| 10 | M1DC_OT_FilterByLegendText | m1dc.filter_by_legend_text | 8342 | Inspector |
| 11 | M1DC_OT_SelectBuildingCluster | m1dc.select_building_cluster | 8461 | Inspector |
| 12 | M1DC_OT_SpreadsheetDeferredSync | m1dc_spreadsheet.deferred_sync | 8537 | Spreadsheet |
| 13 | M1DC_OT_ImportBasemapTerrain | m1dc.import_basemap_terrain | 8570 | Terrain |
| 14 | M1DC_OT_ImportRGBBasemap | m1dc.import_rgb_basemap | 8628 | Terrain |
| 15 | M1DC_OT_ImportDGMTerrain | m1dc.import_dgm_terrain | 8682 | Terrain |
| 16 | M1DC_OT_AlignCityGMLToTerrainZ | m1dc.align_citygml_z | 8801 | Terrain |
| 17 | M1DC_OT_Validate | m1dc.validate | 8840 | Pipeline |
| 18 | M1DC_OT_RunAll | m1dc.run_all | 8868 | Pipeline |
| 19 | M1DC_OT_RunPipeline | m1dc.run_pipeline | 8910 | Pipeline |
| 20 | M1DC_OT_LinkCityGMLtoOSM | m1dc.link_citygml_osm | 9783 | Linking |
| 21 | M1DC_OT_ExportLinkMapping | m1dc.export_link_mapping | 9835 | Export |
| 22 | M1DC_OT_ExportLog | m1dc.export_log | 10012 | Export |
| 23 | M1DC_OT_ClearLog | m1dc.clear_log | 10037 | Utility |
| 24 | M1DC_OT_DebugFaceAttrs | m1dc.debug_face_attrs | 10053 | Debug |
| 25 | M1DC_OT_DebugMeshAttributes | m1dc.debug_mesh_attributes | 10109 | Debug |
| 26 | M1DC_OT_BakeEvalFaceIntAttrs | m1dc.bake_eval_face_int_attrs | 10201 | Debug |
| 27 | M1DC_OT_MakePresentationAttrs | m1dc.make_presentation_attrs | 10380 | Debug |
| 28 | M1DC_OT_CommitEvaluatedToOriginal | m1dc.commit_evaluated_to_original | 10521 | Debug |
| 29 | M1DC_OT_DebugLinkDBSchema | m1dc.debug_link_db_schema | 10635 | Debug |
| 30 | M1DC_OT_DebugGPKGTableInfo | m1dc.debug_gpkg_table_info | 10683 | Debug |
| 31 | M1DC_OT_DebugLinkKeyIdentity | m1dc.debug_link_key_identity | 10786 | Debug |
| 32 | M1DC_OT_InputSetupWizard | m1dc.input_setup_wizard | 10809 | Wizard |
| 33 | M1DC_OT_InputSetupWizardStep | m1dc.input_setup_wizard_step | 10889 | Wizard |
| 34 | M1DC_OT_InputPickupWizard | m1dc.input_pickup_wizard | 10927 | Wizard |
| 35 | M1DC_OT_DebugBuildingIdxStats | m1dc.debug_building_idx_stats | 11000 | Debug |
| 36 | M1DC_OT_FindBestLinkKeyAttr | m1dc.find_best_link_key_attr | 11074 | Debug |
| 37 | M1DC_OT_DebugBuildingIdCandidates | m1dc.debug_building_id_candidates | 11228 | Debug |
| 38 | M1DC_OT_RepairBuildingIdxToFace | m1dc.repair_building_idx_face | 11387 | Debug |
| 39 | M1DC_OT_RemapBuildingIdxTest | m1dc.remap_building_idx_test | 11485 | Debug |
| 40 | M1DC_OT_ExportDiagnostic | m1dc.export_diagnostic | 11637 | Export |
| 41 | M1DC_OT_ExportDebugReport | m1dc.export_debug_report | 11657 | Export |
| 42 | M1DC_OT_ExportFullReport | m1dc.export_full_report | 11677 | Export |
| 43 | M1DC_OT_ColorCityGMLTiles | m1dc.color_citygml_tiles | 11705 | Utility |
| 44 | M1DC_OT_TerrainAlignmentCheck | m1dc.terrain_alignment_check | 11839 | Diagnostic |
| 45 | M1DC_OT_TerrainZAlignLowMedian | m1dc.terrain_z_align_low_median | 11920 | Diagnostic |
| 46 | M1DC_OT_SQLRun | m1dc.sql_run | 12013 | SQL |
| 47 | M1DC_OT_SQLClear | m1dc.sql_clear | 12271 | SQL |
| 48 | M1DC_OT_SQLTemplate | m1dc.sql_template | 12295 | SQL |
| 49 | M1DC_OT_FaceAttrList | m1dc.face_attr_list | 12392 | FaceAttr |
| 50 | M1DC_OT_FaceAttrValues | m1dc.face_attr_values | 12447 | FaceAttr |
| 51 | M1DC_OT_FaceAttrSelect | m1dc.face_attr_select | 12506 | FaceAttr |
| 52 | M1DC_OT_TerrainAlignToCity | m1dc.terrain_align_to_city | 12567 | Terrain |
| 53 | M1DC_OT_BuildLegends | m1dc.legends_build | 12743 | Legend |
| 54 | M1DC_OT_InspectorApplyQuery | m1dc.inspector_apply_query | 12851 | Inspector |
| 55 | M1DC_OT_InspectorClearQuery | m1dc.inspector_clear_query | 13009 | Inspector |
| 56 | M1DC_OT_InspectorZoomToSelection | m1dc.inspector_zoom_to_selection | 13038 | Inspector |
| 57 | M1DC_OT_InspectorExportReport | m1dc.inspector_export_report | 13169 | Inspector |

---

## 1.4 HOTSPOT-ANALYSE (ops.py — 13416 Zeilen)

### Strukturübersicht

| Zeilenbereich | Inhalt | Typ | Größe |
|---------------|--------|-----|-------|
| 1-290 | Imports, Module-level helpers, globals | Infrastruktur | 290 |
| 291-3536 | Helper functions (Materialize, DB, face attrs) | Business Logic | **3245** ⚠️ |
| 3537-5846 | WKB/Geometry helpers | Business Logic | **2309** ⚠️ |
| 5847-6838 | M1DC_OT_RelocalizeCityGML (RIESEN-Klasse!) | Operator | **991** ⚠️ |
| 6839-8550 | Spreadsheet 2.0 ops (7 Operatoren) | Operatoren | 1711 |
| 8551-9781 | Terrain/Import ops (8 Operatoren) | Operatoren | 1230 |
| 9782-10806 | Linking/Export/Debug ops (13 Operatoren) | Operatoren | 1024 |
| 10807-11695 | Wizards/Debug ops (11 Operatoren) | Operatoren | 888 |
| 11696-13416 | Color/SQL/FaceAttr/Inspector ops (17 Operatoren) | Operatoren | 1720 |

### Kritische Hotspots (Kandidaten für Auslagerung)

1. **Zeilen 291-3536** (~3200 Zeilen) — Helper functions
   - Materialize Phase 3 helpers (`ensure_face_attr`, `ensure_face_storage_ready`, etc.)
   - DB helpers (`_first_table_in_gpkg`, `_list_user_tables`, etc.)
   - Face attribute tools

   **➜ Auslagerung:** `pipeline/operations/materialize_helpers.py` (< 500 LOC × mehrere Dateien)

2. **Zeilen 3537-5846** (~2300 Zeilen) — WKB/Geometry helpers
   - WKB parsing (`_extract_wkb_from_gpkg`, `_parse_wkb_polygon`, etc.)
   - Spatial queries (`_point_in_polygon`, `_point_segment_dist_sq`, etc.)

   **➜ Auslagerung:** `pipeline/operations/geometry_helpers.py` oder `utils/geometry.py`

3. **Zeilen 5847-6838** (~1000 Zeilen) — M1DC_OT_RelocalizeCityGML
   - MONSTER OPERATOR (allein fast 1000 Zeilen!)
   - CityGML-spezifisch

   **➜ Auslagerung:** `pipeline/operations/citygml_ops.py`

### Operator-Gruppen (Auslagerungskandidaten)

| Gruppe | Operatoren | Zeilen | Ziel-Script |
|--------|------------|--------|-------------|
| **Spreadsheet** | 7 | ~1700 | `pipeline/operations/spreadsheet_ops.py` |
| **Terrain** | 5 | ~1200 | `pipeline/operations/terrain_ops.py` |
| **Linking** | 2 | ~400 | `pipeline/operations/linking_ops.py` |
| **Export** | 4 | ~300 | `pipeline/operations/export_ops.py` |
| **Debug** | 12 | ~1500 | `pipeline/operations/debug_ops.py` |
| **Wizard** | 3 | ~900 | `pipeline/operations/wizard_ops.py` |
| **SQL** | 3 | ~500 | `pipeline/operations/sql_ops.py` |
| **FaceAttr** | 3 | ~300 | `pipeline/operations/face_attr_ops.py` |
| **Inspector** | 5 | ~700 | `pipeline/operations/inspector_ops.py` |
| **Legend** | 1 | ~200 | `pipeline/operations/legend_ops.py` |
| **Diagnostic** | 2 | ~300 | `pipeline/operations/diagnostic_ops.py` |
| **Pipeline** | 3 | ~300 | `pipeline/operations/pipeline_ops.py` |
| **CityGML** | 1 | ~1000 | `pipeline/operations/citygml_ops.py` |
| **Utility** | 2 | ~200 | Bleibt in ops.py (minimal) |

**Gesamt Operatoren:** 57  
**Nach Auslagerung in ops.py:** ~3-5 (nur Orchestration)

---

## 1.5 STARTUP KILL-PLAN

### Aktuelle Nutzung
- **Datei:** `utils/startup/init_blender_compatibility.py` (60 Zeilen)
- **Funktion:** `ensure_pkg_resources()` — pkg_resources shim für Blender
- **Import:** `__init__.py:12`
- **Call:** `__init__.py:13` (einmalig beim Addon-Load)

### Integration-Optionen

**Option A: Direkt nach utils/common.py integrieren**
- ✅ Bereits zentrale Utility-Datei
- ✅ Kein neuer Ordner
- ⚠️ common.py wird größer

**Option B: Neue Datei utils/blender_compat.py**
- ✅ Semantisch klar
- ✅ common.py bleibt fokussiert
- ✅ **EMPFOHLEN**

### Ablauf
1. **Erstelle:** `utils/blender_compat.py` (60 Zeilen aus startup/init_blender_compatibility.py)
2. **Ändere:** `__init__.py:12` → `from .utils.blender_compat import ensure_pkg_resources`
3. **Lösche:** `utils/startup/` komplett
4. **Verschiebe:** `utils/startup/` → `pipeline/legacy/startup/` (Historie)

---

## 1.6 LEGACY-SWEEP-PLAN

### Bereits in pipeline/Legacy/
1. **citygml_split.py** (215 Zeilen)
   - ❌ Broken import: `from .logging_system` (fehlt relativer Pfad)
   - Status: NICHT IMPORTIERT
   - Action: BELASSEN (bereits deprecated)

2. **db.py** (73 Zeilen)
   - ⚠️ Redirects zu `utils.common.open_db_readonly()`
   - Status: NICHT IMPORTIERT
   - Action: BELASSEN (bereits deprecated)

3. **Data_Set_Tools/** (3 Scripts)
   - prepare_terrain_rgb_wcs.py
   - prepare_rgb_tiles_only.py
   - organize_and_downscale_dop.py
   - Status: NICHT IMPORTIERT
   - Action: BELASSEN (bereits deprecated)

### Zusätzliche Legacy-Kandidaten (nach Import-Audit)

**Zu prüfen:**
- `pipeline/terrain/m1_terrain_csv.py` — Docs: "unused" 
- `pipeline/terrain/m1_basemap.py` — Docs: "unclear if used"

**Verifikation nötig:** Grep-Search für aktive Imports

---

## ZUSAMMENFASSUNG PHASE 1

| Kriterium | Status | Beweis |
|-----------|--------|--------|
| ✅ Struktur inventarisiert | KOMPLETT | 50 .py Dateien dokumentiert |
| ✅ Import-Graph erstellt | KOMPLETT | ops: 2 imports, startup: 1 import, legacy: 0 imports |
| ✅ Operator-Inventar | KOMPLETT | 57 Operatoren in ops.py + Zeilenbereiche |
| ✅ Hotspot-Analyse | KOMPLETT | 3 kritische Blöcke (~6500 Zeilen Helper-Code) |
| ✅ startup-Nutzung klar | KOMPLETT | Eine Funktion, 1 Import, Integration-Plan definiert |
| ✅ Legacy-Status klar | KOMPLETT | 5 Dateien bereits in Legacy, 0 aktive Imports |

**ops.py aktuell:** 13416 Zeilen  
**ops.py Ziel:** <8000 Zeilen (Reduktion: ~40%)  
**Ausgelagerter Code:** ~5400 Zeilen Helper + ~4000 Zeilen Operatoren = **~9400 Zeilen**

➜ **PHASE 1 ABGESCHLOSSEN — BEREIT FÜR PHASE 2 (Entscheidungsfindung)**
