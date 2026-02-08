"""
Input wizard operators: Guide user through pipeline input configuration.
"""
import bpy
from bpy.types import Operator
from bpy.props import StringProperty


def _settings(context):
    """Get scene settings"""
    return getattr(context.scene, "m1dc_settings", None)


class M1DC_OT_InputSetupWizard(Operator):
    """Modal wizard for configuring the 4 pipeline inputs (Basemap, CityGML, GPKG, Output)."""
    bl_idname = "m1dc.input_setup_wizard"
    bl_label = "Input Setup Wizard"
    bl_options = {"REGISTER"}

    # Wizard step counter: 0=Basemap, 1=CityGML, 2=GPKG, 3=Output
    wizard_step: bpy.props.IntProperty(default=0, min=0, max=3, options={"HIDDEN"})

    def invoke(self, context, event):
        """Initialize wizard at step 0."""
        self.wizard_step = 0
        return context.window_manager.invoke_props_dialog(self, width=500)

    def check(self, context):
        """Re-draw on property changes."""
        return True

    def execute(self, context):
        """Final step closes the wizard."""
        return {"FINISHED"}

    def draw(self, context):
        """Draw the wizard UI for the current step."""
        layout = self.layout
        s = _settings(context)
        if s is None:
            layout.label(text="Settings not found.")
            return

        step = self.wizard_step

        # Step labels and descriptions
        steps_info = [
            {
                "title": "Terrain Source Folder",
                "desc": "Folder containing raster terrain data (GeoTIFFs, DEM tiles, metadata)",
                "prop": "terrain_source_dir",
            },
            {
                "title": "CityGML Tiles/GML",
                "desc": "Folder containing CityGML .gml files or tile folders",
                "prop": "citygml_dir",
            },
            {
                "title": "GeoPackage (OSM)",
                "desc": "Path to .gpkg file (OSM features) or folder containing it",
                "prop": "gpkg_path",
            },
            {
                "title": "Output Directory",
                "desc": "Where to save processed assets (terrain meshes, merged rasters, logs)",
                "prop": "output_dir",
            },
        ]

        info = steps_info[step]

        # Header
        layout.label(text=f"Step {step + 1}/4: {info['title']}", icon="FOLDER_DATA")
        layout.label(text=info["desc"])
        layout.separator()

        # Path input
        layout.prop(s, info["prop"], text="")

        layout.separator()

        # Navigation buttons
        row = layout.row(align=True)
        if step > 0:
            row.operator("m1dc.input_setup_wizard_step", text="← Back").step_action = "BACK"
        if step < 3:
            row.operator("m1dc.input_setup_wizard_step", text="Next →").step_action = "NEXT"
        else:
            row.operator("m1dc.input_setup_wizard_step", text="Apply").step_action = "FINISH"


class M1DC_OT_InputSetupWizardStep(Operator):
    """Handle wizard navigation (next/back/finish)."""
    bl_idname = "m1dc.input_setup_wizard_step"
    bl_label = "Wizard Step"
    bl_options = {"REGISTER"}

    step_action: StringProperty(default="NEXT", options={"HIDDEN"})

    def execute(self, context):
        """Process wizard navigation."""
        s = _settings(context)
        if s is None:
            return {"FINISHED"}

        # Get current step from scene prop (or use default)
        current_step = getattr(s, "_wizard_step_internal", 0)

        if self.step_action == "NEXT":
            if current_step < 3:
                s._wizard_step_internal = current_step + 1
        elif self.step_action == "BACK":
            if current_step > 0:
                s._wizard_step_internal = current_step - 1
        elif self.step_action == "FINISH":
            return {"FINISHED"}

        return {"RUNNING_MODAL"}


class M1DC_OT_InputPickupWizard(Operator):
    """Single-dialog input pickup for all 4 paths at once (fallback UI)."""
    bl_idname = "m1dc.input_pickup_wizard"
    bl_label = "Input Pickup Wizard"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        """Show modal dialog."""
        return context.window_manager.invoke_props_dialog(self, width=600)

    def check(self, context):
        """Re-draw on changes."""
        return True

    def execute(self, context):
        """Apply and close."""
        return {"FINISHED"}

    def draw(self, context):
        """Draw all input fields in a single popup."""
        layout = self.layout
        s = _settings(context)
        if s is None:
            layout.label(text="Settings not found.")
            return

        layout.label(text="Configure Pipeline Inputs", icon="FILE_FOLDER")
        layout.separator()

        # Terrain OBJ Artifact (NEW: Primary terrain input)
        layout.label(text="0. Terrain OBJ Artifact Folder (optional)", icon="MESH_DATA")
        layout.label(text="   Folder containing prebuilt terrain OBJ + MTL + textures")
        layout.prop(s, "terrain_obj_artifact_dir", text="")
        layout.label(text="   If provided, OBJ terrain import is used and DGM/RGB fields are skipped.", icon="INFO")

        layout.separator()

        # Terrain DGM
        layout.label(text="1. Terrain DGM Source Folder (Height)", icon="IMAGE_DATA")
        layout.label(text="   Folder containing DEM/DGM GeoTIFF tiles (raster height data)")
        layout.prop(s, "terrain_dgm_dir", text="")

        layout.separator()

        # Terrain RGB
        layout.label(text="2. Terrain RGB Source Folder (Texture)", icon="IMAGE_DATA")
        layout.label(text="   Folder containing RGB/DTK GeoTIFF tiles (orthophoto/texture)")
        layout.prop(s, "terrain_rgb_dir", text="")

        layout.separator()

        # CityGML
        layout.label(text="3. CityGML Tiles/GML", icon="FILE_VOLUME")
        layout.label(text="   Folder containing CityGML .gml files or tile folders")
        layout.prop(s, "citygml_dir", text="")

        layout.separator()

        # GPKG
        layout.label(text="4. GeoPackage (OSM)", icon="FILE_BLEND")
        layout.label(text="   Path to .gpkg file (OSM features) or folder containing it")
        layout.prop(s, "gpkg_path", text="")

        layout.separator()

        # Output
        layout.label(text="5. Output Directory", icon="NEWFOLDER")
        layout.label(text="   Where to save processed assets (merged rasters, terrain mesh, logs)")
        layout.prop(s, "output_dir", text="")

        layout.separator()
        layout.label(text="Click OK to apply, or Cancel to skip.", icon="INFO")
