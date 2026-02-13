# PHASE 2 — Entscheidungsfindung & Move-Table

**Datum:** 2026-02-08  
**Basierend auf:** PHASE1_REPO_SCAN_REPORT.md  
**Ziel:** ops.py von 13416 → <8000 Zeilen (idealerweise deutlich weniger)

---

## 2.1 ZIELSTRUKTUR (Nicht verhandelbar)

```
M1_DC_V6/
├── __init__.py              # Thin orchestrator (Imports + Registration)
├── ops.py                   # Minimal: ~500-1000 Zeilen (nur kritische wrapper)
├── settings.py              # Bleibt unverändert
├── ui.py                    # Bleibt unverändert
├── auto_load.py             # Bleibt unverändert
│
├── utils/
│   ├── __init__.py
│   ├── common.py            # Bleibt unverändert
│   ├── logging_system.py    # Bleibt unverändert
│   ├── validation.py        # Bleibt unverändert
│   ├── blender_compat.py    # ✨ NEU: aus startup/ integriert
│   └── geometry.py          # ✨ NEU: WKB/Geometry helpers aus ops.py
│
├── pipeline/
│   ├── __init__.py
│   │
│   ├── operations/          # ✨ NEU: Ausgelagerte Operatoren
│   │   ├── __init__.py
│   │   ├── spreadsheet_ops.py      # 7 Operatoren (~1700 LOC)
│   │   ├── terrain_ops.py          # 5 Operatoren (~1200 LOC)
│   │   ├── linking_ops.py          # 2 Operatoren (~400 LOC)
│   │   ├── export_ops.py           # 4 Operatoren (~300 LOC)
│   │   ├── debug_ops.py            # 12 Operatoren (~1500 LOC)
│   │   ├── wizard_ops.py           # 3 Operatoren (~900 LOC)
│   │   ├── sql_ops.py              # 3 Operatoren (~500 LOC)
│   │   ├── face_attr_ops.py        # 3 Operatoren (~300 LOC)
│   │   ├── inspector_ops.py        # 5 Operatoren (~700 LOC)
│   │   ├── legend_ops.py           # 1 Operator (~200 LOC)
│   │   ├── diagnostic_ops.py       # 2 Operatoren (~300 LOC)
│   │   ├── pipeline_ops.py         # 3 Operatoren (~300 LOC)
│   │   ├── citygml_ops.py          # 1 Operator (~1000 LOC — RelocalizeCityGML)
│   │   ├── utility_ops.py          # 2 Operatoren (~200 LOC)
│   │   │
│   │   └── helpers/         # ✨ NEU: Helper-Funktionen aus ops.py
│   │       ├── __init__.py
│   │       ├── materialize.py      # Materialize Phase 3 helpers (~1500 LOC)
│   │       ├── db_utils.py         # DB helpers (~500 LOC)
│   │       └── face_attrs.py       # Face attribute tools (~1200 LOC)
│   │
│   ├── legacy/              # Erweitert: +startup/
│   │   ├── README.md        # ✨ NEU: Legacy-Policy dokumentiert
│   │   ├── startup/         # ✨ NEU: historisches startup/ verschoben
│   │   │   ├── __init__.py
│   │   │   └── init_blender_compatibility.py
│   │   ├── citygml_split.py # Bestehendes Legacy
│   │   ├── db.py            # Bestehendes Legacy
│   │   └── Data_Set_Tools/  # Bestehendes Legacy
│   │
│   ├── citygml/             # Bleibt unverändert
│   ├── diagnostics/         # Bleibt unverändert
│   ├── linking/             # Bleibt unverändert
│   ├── osm/                 # Bleibt unverändert
│   └── terrain/             # Bleibt unverändert
│
└── docs/                    # 3 Dateien aktualisiert (siehe 2.5)
```

---

## 2.2 OPS.PY → PIPELINE/OPERATIONS (Move-Table)

### PHASE 2A: Helper-Funktionen auslagern (Zeilen 291-5846)

| Source (ops.py) | Ziel | Zeilen | Inhalt |
|-----------------|------|--------|--------|
| 291-3536 | `pipeline/operations/helpers/materialize.py` | ~1500 | Materialize Phase 3 (`ensure_face_attr`, `ensure_face_storage_ready`, `_proof_attr`, etc.) |
| 291-3536 | `pipeline/operations/helpers/db_utils.py` | ~500 | DB helpers (`_first_table_in_gpkg`, `_list_user_tables`, etc.) |
| 291-3536 | `pipeline/operations/helpers/face_attrs.py` | ~1200 | Face attribute allocation tools |
| 3537-5846 | `utils/geometry.py` | ~2300 | WKB/Geometry helpers (`_extract_wkb_from_gpkg`, `_parse_wkb_polygon`, `_point_in_polygon`, etc.) |

**Gesamt ausgelagert Helper:** ~5500 Zeilen

### PHASE 2B: Operatoren auslagern (Zeilen 5847-13416)

| Operator(en) | Zeilen | Ziel | Kategorie |
|--------------|--------|------|-----------|
| M1DC_OT_RelocalizeCityGML | 5847-6838 (~991) | `pipeline/operations/citygml_ops.py` | CityGML |
| 7 Spreadsheet ops | 6839-8550 (~1711) | `pipeline/operations/spreadsheet_ops.py` | Spreadsheet |
| M1DC_OT_ImportBasemapTerrain | 8551-8614 | `pipeline/operations/terrain_ops.py` | Terrain |
| M1DC_OT_ImportRGBBasemap | 8615-8668 | ditto | Terrain |
| M1DC_OT_ImportDGMTerrain | 8669-8788 | ditto | Terrain |
| M1DC_OT_AlignCityGMLToTerrainZ | 8789-8838 | ditto | Terrain |
| M1DC_OT_TerrainAlignToCity | 12565-12740 | ditto | Terrain |
| **Gesamt Terrain:** | ~1200 | `pipeline/operations/terrain_ops.py` | Terrain |
| M1DC_OT_Validate | 8839-8855 | `pipeline/operations/pipeline_ops.py` | Pipeline |
| M1DC_OT_RunAll | 8856-8908 | ditto | Pipeline |
| M1DC_OT_RunPipeline | 8909-9781 | ditto | Pipeline |
| **Gesamt Pipeline:** | ~940 | `pipeline/operations/pipeline_ops.py` | Pipeline |
| M1DC_OT_LinkCityGMLtoOSM | 9782-9833 | `pipeline/operations/linking_ops.py` | Linking |
| M1DC_OT_MaterializeLinks | 7619-8140 | ditto | Linking |
| **Gesamt Linking:** | ~570 | `pipeline/operations/linking_ops.py` | Linking |
| M1DC_OT_ExportLinkMapping | 9834-10010 | `pipeline/operations/export_ops.py` | Export |
| M1DC_OT_ExportLog | 10011-10035 | ditto | Export |
| M1DC_OT_ExportDiagnostic | 11636-11656 | ditto | Export |
| M1DC_OT_ExportDebugReport | 11657-11676 | ditto | Export |
| M1DC_OT_ExportFullReport | 11677-11695 | ditto | Export |
| **Gesamt Export:** | ~360 | `pipeline/operations/export_ops.py` | Export |
| 12 Debug ops | Diverse (~1500) | `pipeline/operations/debug_ops.py` | Debug |
| 3 Wizard ops | 10807-11073 (~900) | `pipeline/operations/wizard_ops.py` | Wizard |
| 3 SQL ops | 12011-12389 (~500) | `pipeline/operations/sql_ops.py` | SQL |
| 3 FaceAttr ops | 12390-12564 (~300) | `pipeline/operations/face_attr_ops.py` | FaceAttr |
| 5 Inspector ops | 8141-8540 + 12849-13416 (~700) | `pipeline/operations/inspector_ops.py` | Inspector |
| M1DC_OT_BuildLegends | 12741-12848 (~200) | `pipeline/operations/legend_ops.py` | Legend |
| 2 Diagnostic ops | 11837-12010 (~300) | `pipeline/operations/diagnostic_ops.py` | Diagnostic |
| M1DC_OT_ClearLog | 10036-10051 | `pipeline/operations/utility_ops.py` | Utility |
| M1DC_OT_ColorCityGMLTiles | 11696-11836 | ditto | Utility |
| **Gesamt Utility:** | ~200 | `pipeline/operations/utility_ops.py` | Utility |

**Gesamt ausgelagert Operatoren:** ~9000 Zeilen (57 Operatoren in 14 Dateien)

### PHASE 2C: ops.py bleibt (~800-1000 Zeilen)

| Inhalt | Zeilen | Begründung |
|--------|--------|------------|
| Imports | ~100 | Imports von ausgelagerten Modulen |
| Module-level globals | ~50 | CRITICAL_ATTR_SPECS, FEATURE_TABLE_FALLBACK, etc. |
| Minimal-Wrapper | ~50 | Falls nötig für Kompatibilität |
| Legacy-Kompatibilität | ~100 | Falls alte Operator-Namen erhalten bleiben müssen |

**ops.py Zielgröße:** ~800 Zeilen (93% Reduktion!) ✅

---

## 2.3 STARTUP-KILL-PLAN (Detailliert)

### Schritt-für-Schritt

1. **Erstelle `utils/blender_compat.py`**
   ```python
   """Blender compatibility shims (pkg_resources, etc.)"""
   # Copy entire content from utils/startup/init_blender_compatibility.py (60 Zeilen)
   ```

2. **Ändere `__init__.py` Import (Zeile 12)**
   ```python
   # ALT:
   from .utils.startup.init_blender_compatibility import ensure_pkg_resources
   
   # NEU:
   from .utils.blender_compat import ensure_pkg_resources
   ```

3. **Lösche `utils/startup/` komplett**
   ```powershell
   Remove-Item -Recurse utils/startup/
   ```

4. **Verschiebe nach Legacy** (nur für Historie)
   ```powershell
   New-Item -ItemType Directory -Path pipeline/legacy/startup
   # (Re-create from git history if needed for audit trail)
   ```

5. **Update `utils/__init__.py`** (falls vorhanden)
   - Entferne Referenzen auf `startup`

### Verifikation
- ✅ `__init__.py` lädt ohne Fehler
- ✅ `ensure_pkg_resources()` funktioniert
- ✅ Kein Verzeichnis `utils/startup/` mehr vorhanden
- ✅ `pipeline/legacy/startup/` existiert (mit Hinweis-Datei)

---

## 2.4 LEGACY-SWEEP-PLAN (Abgeschlossen)

### Bereits in pipeline/legacy/ (BELASSEN)
1. **citygml_split.py** — Deprecated, 0 Imports ✅
2. **db.py** — Deprecated redirect, 0 Imports ✅
3. **Data_Set_Tools/** — Deprecated scripts, 0 Imports ✅

### NEU nach pipeline/legacy/ verschieben
4. **startup/** — Nach Integration in utils/blender_compat.py ✅

### NICHT nach Legacy (waren fälschlich verdächtigt)
- ❌ `pipeline/terrain/m1_terrain_csv.py` — AKTIV (ops.py:5410 importiert)
- ❌ `pipeline/terrain/m1_basemap.py` — AKTIV (pipeline/terrain/__init__.py:12 + ops.py:8601 importiert)

### pipeline/legacy/README.md erstellen
```markdown
# pipeline/legacy/

**Status:** DEPRECATED — DO NOT IMPORT FROM THIS FOLDER

This folder contains historical/deprecated code preserved for audit trail.

## Modules

### citygml_split.py
- **Status:** Deprecated (broken import)
- **Reason:** Old splitting logic (unused)
- **Last Used:** Unknown

### db.py
- **Status:** Deprecated (redirects to utils.common)
- **Reason:** Superseded by centralized DB access
- **Last Used:** Phase 7-8 (2026-01)

### Data_Set_Tools/
- **Status:** Deprecated
- **Reason:** External data prep scripts (not part of addon runtime)
- **Contents:** prepare_terrain_rgb_wcs.py, prepare_rgb_tiles_only.py, organize_and_downscale_dop.py

### startup/
- **Status:** Moved to utils/blender_compat.py
- **Reason:** Eliminate nested startup/ folder, integrate into utils
- **Date Moved:** 2026-02-08 (Phase 13)
- **Original Content:** init_blender_compatibility.py (ensure_pkg_resources shim)

## Policy

**CRITICAL RULE:** No active code may import from pipeline/legacy/.

If you need functionality from this folder:
1. Check if it exists elsewhere (likely utils/ or pipeline/)
2. If not, extract & refactor FIRST, then import from new location
3. NEVER import legacy code directly

## Verification

```powershell
# Check for illegal legacy imports:
Select-String -Path "**/*.py" -Pattern "from.*legacy|import.*legacy" -Exclude "legacy/**"
# Expected: 0 matches (except docs)
```
```

---

## 2.5 DOCS AKTUALISIERUNG (3 Dateien)

### Datei 1: `docs/ADDON_ARCHITECTURE.md` (Hauptdokumentation)

**Änderungen:**
1. **Struktur-Diagram:** Füge `pipeline/operations/` und `pipeline/legacy/startup/` hinzu
2. **Operator-Organisation:** Neue Aufteilung dokumentieren (14 Operator-Dateien)
3. **Helper-Funktionen:** Verweis auf `pipeline/operations/helpers/` und `utils/geometry.py`
4. **Startup:** `utils/blender_compat.py` statt `utils/startup/`
5. **Legacy-Policy:** Verweis auf `pipeline/legacy/README.md`

### Datei 2: `docs/README.md` (Quick-Start)

**Änderungen:**
1. **How to add a new operator:**
   ```markdown
   ### Adding a New Operator
   
   1. Choose correct category in `pipeline/operations/`:
      - Terrain → `terrain_ops.py`
      - Debug → `debug_ops.py`
      - Inspector → `inspector_ops.py`
      - etc.
   
   2. Create operator class:
      ```python
      class M1DC_OT_YourOperator(bpy.types.Operator):
          bl_idname = "m1dc.your_operator"
          bl_label = "Your Operator"
          
          def execute(self, context):
              # Your code here
              return {'FINISHED'}
      ```
   
   3. Add to `__init__.py` imports (two places):
      - Import section (~line 25)
      - CLASSES tuple (~line 95)
   
   4. Keep individual operator files <500 LOC
   ```

2. **File size policy:** 
   - ops.py: <1000 LOC (orchestration only)
   - Individual operator files: <500 LOC
   - Helper modules: <500 LOC (split if larger)

### Datei 3: `PHASE1_REPO_SCAN_REPORT.md` → Archive

**Aktion:** Verschiebe nach `docs/audit/PHASE1_REPO_SCAN_REPORT.md`

---

## 2.6 DEPENDENCIES & IMPORT-STRATEGIE

### Import-Chain (nach Refactor)

```
__init__.py
├─→ utils.blender_compat (ensure_pkg_resources)
├─→ settings
├─→ pipeline.operations.* (alle Operator-Module)
│   ├─→ utils.* (common, logging, geometry)
│   ├─→ pipeline.operations.helpers.* (materialize, db_utils, face_attrs)
│   └─→ pipeline.* (citygml, terrain, linking, diagnostics, osm)
├─→ ui
└─→ auto_load
```

### Kritische Import-Regeln

1. **Keine zirkulären Imports:**
   - `ops.py` (neu) importiert NICHTS aus `pipeline.operations.*`
   - `pipeline.operations.*` importiert von `utils.*` und `pipeline.*` (nicht ops)

2. **Helper-Zugriff:**
   - All Operatoren importieren Helper aus `pipeline.operations.helpers.*`
   - Nicht direkt aus ops.py!

3. **Legacy-Verbot:**
   - KEIN Import aus `pipeline.legacy.*` erlaubt
   - Ausnahme: Only for migration/refactor work

### Beispiel Import-Header (neues Operator-Modul)

```python
"""pipeline/operations/terrain_ops.py — Terrain import operators."""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty

# Utils
from ...utils.common import get_world_origin_minmax, get_output_dir
from ...utils.logging_system import log_info, log_warn, log_error
from ...utils.geometry import bbox_iou_xy  # NEU: aus ops.py ausgelagert

# Pipeline modules
from ..terrain import basemap_tiles, rgb_basemap_import, dgm_terrain_import
from ..terrain.terrain_world_calibration import calibrate_terrain_to_world_bounds

# Helpers (NEU: aus ops.py ausgelagert)
from .helpers.materialize import ensure_face_storage_ready
from .helpers.db_utils import first_table_in_gpkg
```

---

## 2.7 MIGRATION-STRATEGIE (Minimal-Invasiv)

### Operator-Klassen: 1:1 Copy (KEINE Änderungen)

- ✅ Operator-Code bleibt identisch
- ✅ `bl_idname` unverändert
- ✅ `bl_label` unverändert
- ✅ `execute()` Logik unverändert
- ✅ Properties unverändert

➜ **Nur Location ändert sich, nicht Verhalten**

### Helper-Funktionen: Direct Move

- ✅ Funktionen 1:1 kopieren
- ✅ Signatures unverändert
- ✅ Logging/Error-Handling unverändert
- ⚠️ Imports anpassen (relative Pfade)

### __init__.py: Import-Update

**ALT:**
```python
from .ops import (
    M1DC_OT_ImportBasemapTerrain,
    M1DC_OT_ImportRGBBasemap,
    # ... 55 weitere
)
```

**NEU:**
```python
from .pipeline.operations.terrain_ops import (
    M1DC_OT_ImportBasemapTerrain,
    M1DC_OT_ImportRGBBasemap,
    M1DC_OT_ImportDGMTerrain,
    M1DC_OT_AlignCityGMLToTerrainZ,
    M1DC_OT_TerrainAlignToCity,
)
from .pipeline.operations.spreadsheet_ops import (
    M1DC_OT_ReloadOSMTables,
    M1DC_OT_ReloadOSMColumns,
    M1DC_OT_SpreadsheetReload,
    # ... etc
)
# ... weitere 12 Module
```

---

## 2.8 RISIKO-ANALYSE & MITIGATION

| Risiko | Wahrscheinlichkeit | Impact | Mitigation |
|--------|-------------------|--------|------------|
| Zirkuläre Imports | Mittel | Hoch | Import-Graph vorher prüfen, Helper-Ordner verwenden |
| Operator-IDs ändern sich | Niedrig | Kritisch | EXPLIZIT VERBOTEN — IDs bleiben identisch |
| Import-Pfad-Fehler | Mittel | Mittel | Test-Load nach jedem Move, Syntax-Check |
| Helper-Funktion-Duplikate | Niedrig | Niedrig | Konsolidierung in helpers/ Ordner |
| Legacy-Code reaktiviert | Niedrig | Mittel | README + Policy + Grep-Check |
| __init__.py zu groß | Niedrig | Niedrig | Imports nach Kategorie gruppieren |
| Blender lädt nicht | Mittel | Kritisch | Incremental migration mit Test nach jedem Schritt |

---

## 2.9 IMPLEMENTATION-REIHENFOLGE (Phase 3)

1. **Startup Kill** (Schnell, isoliert, geringes Risiko)
   - utils/blender_compat.py erstellen
   - __init__.py anpassen
   - utils/startup/ löschen
   - Test: Addon load

2. **Helper-Funktionen auslagern** (Mittleres Risiko)
   - utils/geometry.py erstellen (WKB helpers)
   - pipeline/operations/helpers/ erstellen (materialize, db_utils, face_attrs)
   - ops.py Helper-Code löschen
   - Test: Import-Checks

3. **Operatoren auslagern** (Schrittweise, je Kategorie)
   - Reihenfolge (aufsteigend nach Abhängigkeiten):
     a) Utility-Ops (unabhängig)
     b) Export-Ops (wenige Dependencies)
     c) Terrain-Ops
     d) CityGML-Ops
     e) Linking-Ops
     f) Spreadsheet-Ops
     g) Inspector-Ops
     h) Debug-Ops (viele Dependencies)
     i) Wizard-Ops (komplex)
     j) SQL/FaceAttr/Legend/Diagnostic-Ops

4. **__init__.py refactor** (Bulk-Import-Update)
   - Imports nach Kategorien organisieren
   - CLASSES tuple aktualisieren
   - Test: Addon register()

5. **Legacy finalisieren**
   - pipeline/legacy/README.md erstellen
   - pipeline/legacy/startup/ erstellen (Historie)
   - Grep-Check: Keine Legacy-Imports

6. **Docs update**
   - ADDON_ARCHITECTURE.md
   - README.md (How-to)
   - PHASE1_REPO_SCAN_REPORT.md → docs/audit/

---

## ZUSAMMENFASSUNG PHASE 2

| Entscheidung | Status | Begründung |
|--------------|--------|------------|
| ✅ Zielstruktur definiert | FINAL | 14 Operator-Dateien + 3 Helper-Dateien + utils/geometry.py |
| ✅ Move-Table erstellt | FINAL | 57 Operatoren → 14 Dateien, ~5500 LOC Helper → 4 Dateien |
| ✅ Startup-Kill-Plan | FINAL | utils/blender_compat.py, 4 Schritte |
| ✅ Legacy-Sweep | FINAL | Nur startup/ neu, Rest bleibt |
| ✅ Docs-Update-Plan | FINAL | 3 Dateien (ARCH, README, PHASE1→audit) |
| ✅ Import-Strategie | FINAL | Keine zirkulären Imports, Helper-Ordner |
| ✅ Risiko-Mitigation | FINAL | 7 Risiken identifiziert + Gegenmaßnahmen |
| ✅ Implementation-Order | FINAL | 6 Phasen (Startup → Helper → Ops → Init → Legacy → Docs) |

**ops.py Zielgröße:** ~800 Zeilen (aktuell 13416 → **94% Reduktion!**) ✅  
**pipeline/operations:** 14 neue Dateien à <500 LOC ✅  
**startup/ eliminiert:** Integriert in utils/blender_compat.py ✅  
**Legacy-Policy:** Dokumentiert & durchgesetzt ✅

➜ **PHASE 2 ABGESCHLOSSEN — BEREIT FÜR PHASE 3 (Umsetzung)**
