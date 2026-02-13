bl_info = {
    "name": "M1_DC_V6",
    "author": "Alixnova Akhai (akhai-labs)",
    "version": (6, 11, 11),
    "blender": (4, 5, 3),
    "location": "View3D > Sidebar > M1_DC_V6",
    "description": "Minimal pipeline UI for CityGML + GPKG + BaseMap (GeoTIFF tiles) with clean status + run operator.",
    "category": "Import-Export",
}

# IMPORTANT: Blender compatibility shim first (safe no-op if not needed)
from .utils.blender_compat import ensure_pkg_resources  # noqa
ensure_pkg_resources()
print("[STARTUP] ensure_pkg_resources OK")

try:
    import bpy  # type: ignore
except ModuleNotFoundError as exc:
    raise ImportError("bpy not found; run this add-on inside Blender.") from exc

from .settings import M1DCSettings, M1DCBuildingRow, M1DCColumnOption, M1DCDecodedAttrRow
# Explicitly load terrain_merge BEFORE operators to ensure imports work
from .pipeline.terrain import terrain_merge  # noqa: F401

# Import all operators from pipeline.operations package
from .pipeline.operations import (
    M1DC_OT_ImportBasemapTerrain,
    M1DC_OT_ImportRGBBasemap,
    M1DC_OT_ImportDGMTerrain,
    M1DC_OT_AlignCityGMLToTerrainZ,
    M1DC_OT_TerrainAlignmentCheck,
    M1DC_OT_TerrainZAlignLowMedian,
    M1DC_OT_TerrainAlignToCity,
    M1DC_OT_Validate,
    M1DC_OT_RunAll,
    M1DC_OT_RunPipeline,
    M1DC_OT_LinkCityGMLtoOSM,
    M1DC_OT_ExportLinkMapping,
    M1DC_OT_ExportLog,
    M1DC_OT_ClearLog,
    M1DC_OT_ExportDiagnostic,
    M1DC_OT_ExportDebugReport,
    M1DC_OT_ExportFullReport,
    M1DC_OT_DebugFaceAttrs,
    M1DC_OT_DebugMeshAttributes,
    M1DC_OT_BakeEvalFaceIntAttrs,
    M1DC_OT_MakePresentationAttrs,
    M1DC_OT_CommitEvaluatedToOriginal,
    M1DC_OT_DebugLinkDBSchema,
    M1DC_OT_DebugGPKGTableInfo,
    M1DC_OT_DebugLinkKeyIdentity,
    M1DC_OT_DebugBuildingIdxStats,
    M1DC_OT_FindBestLinkKeyAttr,
    M1DC_OT_DebugBuildingIdCandidates,
    M1DC_OT_RepairBuildingIdxToFace,
    M1DC_OT_RemapBuildingIdxTest,
    M1DC_OT_ColorCityGMLTiles,
    M1DC_OT_SpreadsheetReload,
    M1DC_OT_SpreadsheetSyncFromSelection,
    M1DC_OT_SpreadsheetSelectRow,
    M1DC_OT_SpreadsheetDeferredSync,
    M1DC_OT_SpreadsheetColumnsSelect,
    M1DC_OT_MaterializeLinks,
    M1DC_OT_ReloadOSMTables,
    M1DC_OT_ReloadOSMColumns,
    M1DC_OT_SelectBuildingCluster,
    M1DC_OT_InputPickupWizard,
    M1DC_OT_InputSetupWizard,
    M1DC_OT_InputSetupWizardStep,
    M1DC_OT_RelocalizeCityGML,
    M1DC_OT_SQLRun,
    M1DC_OT_SQLClear,
    M1DC_OT_SQLTemplate,
    M1DC_OT_FaceAttrList,
    M1DC_OT_FaceAttrValues,
    M1DC_OT_FaceAttrSelect,
    M1DC_OT_BuildLegends,
    M1DC_OT_InspectActiveFace,
    M1DC_OT_InspectorApplyQuery,
    M1DC_OT_InspectorClearQuery,
    M1DC_OT_InspectorZoomToSelection,
    M1DC_OT_InspectorExportReport,
    M1DC_OT_FilterByLegendText,
)

# ops module is kept for helper functions only (no longer imports operators from it)
from . import ops
from .ui import (
    M1DC_PT_Pipeline,
    M1DC_UL_SpreadsheetRows,
    M1DC_UL_SpreadsheetColumns,
    M1DC_UL_DecodedAttrs,
    M1DC_MT_TableSelector,
    M1DC_MT_OSMFeatureTable,
    M1DC_OT_SelectTable,
    M1DC_OT_SelectOSMFeatureTable,
)
from . import auto_load

# Register order matters: settings first, panel last.
CLASSES = (
    M1DCBuildingRow,
    M1DCColumnOption,
    M1DCDecodedAttrRow,
    M1DCSettings,
    M1DC_OT_ImportBasemapTerrain,
    M1DC_OT_ImportRGBBasemap,
    M1DC_OT_ImportDGMTerrain,
    M1DC_OT_AlignCityGMLToTerrainZ,
    M1DC_OT_TerrainAlignmentCheck,
    M1DC_OT_TerrainZAlignLowMedian,
    M1DC_OT_Validate,
    M1DC_OT_RunAll,
    M1DC_OT_RunPipeline,
    M1DC_OT_LinkCityGMLtoOSM,
    M1DC_OT_ExportLinkMapping,
    M1DC_OT_ExportLog,
    M1DC_OT_ClearLog,
    M1DC_OT_DebugFaceAttrs,
    M1DC_OT_DebugMeshAttributes,
    M1DC_OT_BakeEvalFaceIntAttrs,
    M1DC_OT_MakePresentationAttrs,
    M1DC_OT_CommitEvaluatedToOriginal,
    M1DC_OT_DebugLinkDBSchema,
    M1DC_OT_DebugGPKGTableInfo,
    M1DC_OT_DebugLinkKeyIdentity,
    M1DC_OT_DebugBuildingIdxStats,
    M1DC_OT_FindBestLinkKeyAttr,
    M1DC_OT_DebugBuildingIdCandidates,
    M1DC_OT_RepairBuildingIdxToFace,
    M1DC_OT_RemapBuildingIdxTest,
    M1DC_OT_ExportDiagnostic,
    M1DC_OT_ExportDebugReport,
    M1DC_OT_ExportFullReport,
    M1DC_OT_ColorCityGMLTiles,
    M1DC_OT_SpreadsheetReload,
    M1DC_OT_SpreadsheetSyncFromSelection,
    M1DC_OT_SpreadsheetSelectRow,
    M1DC_OT_SpreadsheetDeferredSync,
    M1DC_OT_SpreadsheetColumnsSelect,
    M1DC_OT_MaterializeLinks,
    M1DC_OT_ReloadOSMTables,
    M1DC_OT_ReloadOSMColumns,
    M1DC_OT_SelectBuildingCluster,
    M1DC_OT_InputPickupWizard,
    M1DC_OT_InputSetupWizard,
    M1DC_OT_InputSetupWizardStep,
    M1DC_OT_RelocalizeCityGML,
    M1DC_OT_SQLRun,
    M1DC_OT_SQLClear,
    M1DC_OT_SQLTemplate,
    M1DC_OT_FaceAttrList,
    M1DC_OT_FaceAttrValues,
    M1DC_OT_FaceAttrSelect,
    M1DC_OT_TerrainAlignToCity,
    M1DC_OT_BuildLegends,
    M1DC_OT_InspectActiveFace,
    M1DC_OT_InspectorApplyQuery,
    M1DC_OT_InspectorClearQuery,
    M1DC_OT_InspectorZoomToSelection,
    M1DC_OT_InspectorExportReport,
    M1DC_OT_FilterByLegendText,
    M1DC_OT_SelectTable,
    M1DC_OT_SelectOSMFeatureTable,
    M1DC_UL_SpreadsheetRows,
    M1DC_UL_SpreadsheetColumns,
    M1DC_UL_DecodedAttrs,
    M1DC_MT_TableSelector,
    M1DC_MT_OSMFeatureTable,
    M1DC_PT_Pipeline,
)

ORDERED_CLASSES = CLASSES


def _ensure_scene_pointer():
    """Attach the settings pointer to Scene so UI always has data."""
    bpy.types.Scene.m1dc_settings = bpy.props.PointerProperty(type=M1DCSettings)
    bpy.types.Scene.m1dc_project = bpy.props.PointerProperty(type=M1DCSettings)


def register():
    auto_load.register(ORDERED_CLASSES)
    _ensure_scene_pointer()


def unregister():
    if hasattr(bpy.types.Scene, "m1dc_settings"):
        del bpy.types.Scene.m1dc_settings
    if hasattr(bpy.types.Scene, "m1dc_project"):
        del bpy.types.Scene.m1dc_project
    try:
        auto_load.unregister()
    except Exception as e:  # defensive: never crash on disable
        import traceback
        try:
            from .utils.logging_system import log_error
            log_error(f"unregister() failed, continuing cleanup: {e}")
        except:
            pass
        traceback.print_exc()


if __name__ == "__main__":
    register()
