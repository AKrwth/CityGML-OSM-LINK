"""
Operations Package: Blender operators for M1DC addon.

This package contains all operator classes split by functional category to maintain
manageable file sizes (<500 LOC per module).

Organization:
- export_log_ops.py: Export/log operations (ExportLog, ClearLog, Export*Report)
- workflow_ops.py: Pipeline workflow (RunAll, RunPipeline, Validate)
- terrain_ops.py: Terrain import and alignment operators
- citygml_ops.py: CityGML operations (Relocalize, Color)
- linking_ops.py: CityGML-OSM linking operations
- materialize_ops.py: MaterializeLinks and spreadsheet UI operators
- inspector_ops.py: Inspector/query/filter operators
- face_attr_ops.py: Face attribute manipulation operators
- debug_ops.py: Debug/diagnostic operators
- sql_ops.py: SQL query interface operators
- legend_ops.py: Legend building operators
- spreadsheet_ops.py: Spreadsheet UI operators
- wizard_ops.py: Setup/pickup wizards
"""

# Import all operators from submodules
from .export_log_ops import (
    M1DC_OT_ExportLog,
    M1DC_OT_ClearLog,
    M1DC_OT_ExportLinkMapping,
    M1DC_OT_ExportDiagnostic,
    M1DC_OT_ExportDebugReport,
    M1DC_OT_ExportFullReport,
)

from .workflow_ops import (
    M1DC_OT_Validate,
    M1DC_OT_RunAll,
    M1DC_OT_RunPipeline,
)

from .terrain_ops import (
    M1DC_OT_ImportBasemapTerrain,
    M1DC_OT_ImportRGBBasemap,
    M1DC_OT_ImportDGMTerrain,
    M1DC_OT_AlignCityGMLToTerrainZ,
    M1DC_OT_TerrainAlignmentCheck,
    M1DC_OT_TerrainZAlignLowMedian,
    M1DC_OT_TerrainAlignToCity,
    M1DC_OT_TerrainBakeScale,
    M1DC_OT_TerrainAlignXYMinCorner,
    M1DC_OT_TerrainSnapXYToTiles,
    M1DC_OT_TerrainSetZScale,
    M1DC_OT_TerrainSnapToCityCenter,
)

from .op_terrain_fit import (
    M1DC_OT_TerrainFitBBox,
)

from .citygml_ops import (
    M1DC_OT_RelocalizeCityGML,
    M1DC_OT_ColorCityGMLTiles,
)

from .linking_ops import (
    M1DC_OT_LinkCityGMLtoOSM,
)

from .materialize_ops import (
    M1DC_OT_MaterializeLinks,
    M1DC_OT_ReloadOSMTables,
    M1DC_OT_ReloadOSMColumns,
    M1DC_OT_SelectBuildingCluster,
)

from .spreadsheet_ops import (
    M1DC_OT_SpreadsheetReload,
    M1DC_OT_SpreadsheetColumnsSelect,
    M1DC_OT_SpreadsheetSyncFromSelection,
    M1DC_OT_SpreadsheetSelectRow,
    M1DC_OT_SpreadsheetDeferredSync,
)

from .inspector_ops import (
    M1DC_OT_InspectActiveFace,
    M1DC_OT_FilterByLegendText,
    M1DC_OT_InspectorApplyQuery,
    M1DC_OT_InspectorClearQuery,
    M1DC_OT_InspectorZoomToSelection,
    M1DC_OT_InspectorExportReport,
    M1DC_OT_InspectorLegendDecode,
    M1DC_OT_InspectorApplyDSL,
)

from .face_attr_ops import (
    M1DC_OT_DebugFaceAttrs,
    M1DC_OT_BakeEvalFaceIntAttrs,
    M1DC_OT_MakePresentationAttrs,
    M1DC_OT_CommitEvaluatedToOriginal,
    M1DC_OT_FaceAttrList,
    M1DC_OT_FaceAttrValues,
    M1DC_OT_FaceAttrSelect,
)

from .debug_ops import (
    M1DC_OT_DebugMeshAttributes,
    M1DC_OT_DebugLinkDBSchema,
    M1DC_OT_DebugGPKGTableInfo,
    M1DC_OT_DebugLinkKeyIdentity,
    M1DC_OT_DebugBuildingIdxStats,
    M1DC_OT_FindBestLinkKeyAttr,
    M1DC_OT_DebugBuildingIdCandidates,
    M1DC_OT_RepairBuildingIdxToFace,
    M1DC_OT_RemapBuildingIdxTest,
)

from .sql_ops import (
    M1DC_OT_SQLRun,
    M1DC_OT_SQLClear,
    M1DC_OT_SQLTemplate,
)

from .legend_ops import (
    M1DC_OT_BuildLegends,
)

from .wizard_ops import (
    M1DC_OT_InputSetupWizard,
    M1DC_OT_InputSetupWizardStep,
    M1DC_OT_InputPickupWizard,
)

__all__ = [
    # Export/Log operators (6)
    "M1DC_OT_ExportLog",
    "M1DC_OT_ClearLog",
    "M1DC_OT_ExportLinkMapping",
    "M1DC_OT_ExportDiagnostic",
    "M1DC_OT_ExportDebugReport",
    "M1DC_OT_ExportFullReport",
    # Workflow operators (3)
    "M1DC_OT_Validate",
    "M1DC_OT_RunAll",
    "M1DC_OT_RunPipeline",
    # Terrain operators (9)
    "M1DC_OT_ImportBasemapTerrain",
    "M1DC_OT_ImportRGBBasemap",
    "M1DC_OT_ImportDGMTerrain",
    "M1DC_OT_AlignCityGMLToTerrainZ",
    "M1DC_OT_TerrainAlignmentCheck",
    "M1DC_OT_TerrainZAlignLowMedian",
    "M1DC_OT_TerrainAlignToCity",
    "M1DC_OT_TerrainBakeScale",
    "M1DC_OT_TerrainAlignXYMinCorner",
    "M1DC_OT_TerrainSnapXYToTiles",
    "M1DC_OT_TerrainSetZScale",
    "M1DC_OT_TerrainSnapToCityCenter",
    "M1DC_OT_TerrainFitBBox",
    # CityGML operators (2)
    "M1DC_OT_RelocalizeCityGML",
    "M1DC_OT_ColorCityGMLTiles",
    # Linking operators (1)
    "M1DC_OT_LinkCityGMLtoOSM",
    # Materialize operators (4)
    "M1DC_OT_MaterializeLinks",
    "M1DC_OT_ReloadOSMTables",
    "M1DC_OT_ReloadOSMColumns",
    "M1DC_OT_SelectBuildingCluster",
    # Spreadsheet operators (5)
    "M1DC_OT_SpreadsheetReload",
    "M1DC_OT_SpreadsheetColumnsSelect",
    "M1DC_OT_SpreadsheetSyncFromSelection",
    "M1DC_OT_SpreadsheetSelectRow",
    "M1DC_OT_SpreadsheetDeferredSync",
    # Inspector operators (6)
    "M1DC_OT_InspectActiveFace",
    "M1DC_OT_FilterByLegendText",
    "M1DC_OT_InspectorApplyQuery",
    "M1DC_OT_InspectorClearQuery",
    "M1DC_OT_InspectorZoomToSelection",
    "M1DC_OT_InspectorExportReport",
    "M1DC_OT_InspectorLegendDecode",
    "M1DC_OT_InspectorApplyDSL",
    # Terrain snap to city center (1)
    "M1DC_OT_TerrainSnapToCityCenter",
    # Face attribute operators (7)
    "M1DC_OT_DebugFaceAttrs",
    "M1DC_OT_BakeEvalFaceIntAttrs",
    "M1DC_OT_MakePresentationAttrs",
    "M1DC_OT_CommitEvaluatedToOriginal",
    "M1DC_OT_FaceAttrList",
    "M1DC_OT_FaceAttrValues",
    "M1DC_OT_FaceAttrSelect",
    # Debug operators (9)
    "M1DC_OT_DebugMeshAttributes",
    "M1DC_OT_DebugLinkDBSchema",
    "M1DC_OT_DebugGPKGTableInfo",
    "M1DC_OT_DebugLinkKeyIdentity",
    "M1DC_OT_DebugBuildingIdxStats",
    "M1DC_OT_FindBestLinkKeyAttr",
    "M1DC_OT_DebugBuildingIdCandidates",
    "M1DC_OT_RepairBuildingIdxToFace",
    "M1DC_OT_RemapBuildingIdxTest",
    # SQL operators (3)
    "M1DC_OT_SQLRun",
    "M1DC_OT_SQLClear",
    "M1DC_OT_SQLTemplate",
    # Legend operators (1)
    "M1DC_OT_BuildLegends",
    # Wizard operators (3)
    "M1DC_OT_InputSetupWizard",
    "M1DC_OT_InputSetupWizardStep",
    "M1DC_OT_InputPickupWizard",
]
