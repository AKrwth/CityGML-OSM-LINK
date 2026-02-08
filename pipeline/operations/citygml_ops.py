"""
CityGML-specific operators: re-localization, coloring.
"""
import bpy
from bpy.types import Operator

def get_world_origin_minmax():
    """Import from utils.common"""
    from ...utils.common import get_world_origin_minmax as _get_world_origin_minmax
    return _get_world_origin_minmax()

def log_info(msg):
    """Import logging"""
    try:
        from ...utils.logging_system import log_info as _log_info
        _log_info(msg)
    except ImportError:
        print(msg)

def log_warn(msg):
    """Import logging"""
    try:
        from ...utils.logging_system import log_warn as _log_warn
        _log_warn(msg)
    except ImportError:
        print(f"[WARN] {msg}")

def log_error(msg):
    """Import logging"""
    try:
        from ...utils.logging_system import log_error as _log_error
        _log_error(msg)
    except ImportError:
        print(f"[ERROR] {msg}")


class M1DC_OT_RelocalizeCityGML(Operator):
    """Re-apply world_to_local translation to existing CityGML tiles.
    
    Useful if world origin changes or was set incorrectly.
    Moves all LoD2_* objects in CITYGML_TILES collection without reimport.
    """
    bl_idname = "m1dc.relocalize_citygml"
    bl_label = "Re-Localize CityGML Tiles"

    def execute(self, context):
        min_e, min_n, _, _ = get_world_origin_minmax()
        if min_e is None or min_n is None:
            self.report({"ERROR"}, "WORLD_ORIGIN not set. Cannot relocalize.")
            return {"CANCELLED"}
        
        # Find CityGML collection
        citygml_col = bpy.data.collections.get("CITYGML_TILES")
        if not citygml_col:
            self.report({"WARNING"}, "CITYGML_TILES collection not found.")
            return {"CANCELLED"}
        
        # Find mesh objects (LoD2_* or all meshes in collection)
        mesh_objs = [o for o in citygml_col.objects if o.type == "MESH"]
        if not mesh_objs:
            self.report({"INFO"}, "No mesh objects found in CITYGML_TILES.")
            return {"FINISHED"}
        
        # Log before
        sample_before = mesh_objs[0].location.copy()
        log_info(f"[RelocalizeCityGML] Relocating {len(mesh_objs)} tiles...")
        log_info(f"[RelocalizeCityGML] sample BEFORE: ({sample_before.x:.0f}, {sample_before.y:.0f})")
        
        # Apply world_to_local
        for obj in mesh_objs:
            obj.location.x -= min_e
            obj.location.y -= min_n
        
        # Log after
        sample_after = mesh_objs[0].location.copy()
        log_info(f"[RelocalizeCityGML] sample AFTER: ({sample_after.x:.0f}, {sample_after.y:.0f})")
        
        self.report({"INFO"}, f"Re-localized {len(mesh_objs)} CityGML tiles")
        return {"FINISHED"}


class M1DC_OT_ColorCityGMLTiles(Operator):
    """
    Color all CityGML tile mesh objects with a clear red/orange material.
    
    Non-destructive: only adds a material and assigns it to all CityGML mesh objects.
    Does not modify geometry, topology, or existing attributes.
    
    Material: "M1DC_GML_RED" (Principled BSDF, visible red/orange color)
    """
    bl_idname = "m1dc.color_citygml_tiles"
    bl_label = "Color CityGML Tiles"
    bl_description = "Assign red/orange material to all CityGML tile meshes"
    bl_options = {"REGISTER", "UNDO"}

    def _ensure_gml_red_material(self):
        """Create or retrieve the M1DC_GML_RED material (red/orange Principled BSDF)."""
        mat_name = "M1DC_GML_RED"
        
        # Check if material already exists
        if mat_name in bpy.data.materials:
            return bpy.data.materials[mat_name]
        
        # Create new material
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        
        # Clear default nodes
        mat.node_tree.nodes.clear()
        
        # Create Principled BSDF
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        
        # Add Principled BSDF shader
        bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
        bsdf.name = "Principled BSDF"
        
        # Set red/orange color
        bsdf.inputs["Base Color"].default_value = (1.0, 0.5, 0.0, 1.0)  # Red-orange with full alpha
        bsdf.inputs["Metallic"].default_value = 0.0
        bsdf.inputs["Roughness"].default_value = 0.5  # Semi-matte for good visibility
        
        # Add Material Output
        output = nodes.new(type="ShaderNodeOutputMaterial")
        output.name = "Material Output"
        
        # Connect Principled BSDF to Material Output
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
        
        log_info(f"[Color] Created material '{mat_name}' (red-orange Principled BSDF)")
        return mat

    def _collect_citygml_mesh_objects(self):
        """Collect all CityGML mesh objects from standard collections."""
        meshes = []
        
        # Priority 1: CITYGML_TILES collection (preferred, no split)
        col_tiles = bpy.data.collections.get("CITYGML_TILES")
        if col_tiles and len(col_tiles.objects):
            objs = list(col_tiles.objects)
            log_info(f"[Color] Found {len(objs)} objects in CITYGML_TILES collection")
        else:
            # Priority 2: CITYGML_BUILDINGS collection
            col_build = bpy.data.collections.get("CITYGML_BUILDINGS")
            if col_build and len(col_build.objects):
                objs = list(col_build.objects)
                log_info(f"[Color] Found {len(objs)} objects in CITYGML_BUILDINGS collection")
            else:
                # Priority 3: CITYGML_BUILDINGS_SPLIT collection
                col_build_split = bpy.data.collections.get("CITYGML_BUILDINGS_SPLIT")
                if col_build_split and len(col_build_split.objects):
                    objs = list(col_build_split.objects)
                    log_info(f"[Color] Found {len(objs)} objects in CITYGML_BUILDINGS_SPLIT collection")
                else:
                    # Fallback: search by custom properties or name patterns
                    objs = []
                    for o in bpy.data.objects:
                        if o.type != "MESH":
                            continue
                        # Check for CityGML markers: custom properties or name patterns
                        if "source_tile" in o or "building_idx" in o or o.name.startswith(("LoD", "CityGML")):
                            objs.append(o)
                    if objs:
                        log_info(f"[Color] Found {len(objs)} CityGML objects by property/name search")
        
        # Filter to only mesh objects
        for obj in objs:
            if obj.type == "MESH" and obj.data:
                meshes.append(obj)
        
        return meshes

    def execute(self, context):
        try:
            # Step 1: Ensure material exists
            mat = self._ensure_gml_red_material()
            if mat is None:
                self.report({"ERROR"}, "Failed to create M1DC_GML_RED material")
                return {"CANCELLED"}
            
            # Step 2: Collect CityGML mesh objects
            mesh_objs = self._collect_citygml_mesh_objects()
            if not mesh_objs:
                self.report({"WARNING"}, "No CityGML mesh objects found in scene")
                return {"CANCELLED"}
            
            # Step 3: Assign material to all CityGML meshes
            assigned_count = 0
            for obj in mesh_objs:
                try:
                    # Ensure mesh has a material slot
                    if len(obj.data.materials) == 0:
                        obj.data.materials.append(mat)
                    else:
                        # Replace first material or add if only placeholder materials exist
                        obj.data.materials[0] = mat
                    assigned_count += 1
                except Exception as ex:
                    log_warn(f"[Color] Failed to assign material to {obj.name}: {ex}")
            
            # Summary
            if assigned_count > 0:
                log_info(f"[Color] Successfully assigned M1DC_GML_RED to {assigned_count} CityGML mesh objects")
                self.report({"INFO"}, f"✓ Colored {assigned_count} CityGML tiles with red/orange material")
                return {"FINISHED"}
            else:
                self.report({"WARNING"}, "No materials could be assigned")
                return {"CANCELLED"}
                
        except Exception as ex:
            log_error(f"[Color] Exception: {ex}")
            self.report({"ERROR"}, f"Color CityGML tiles failed: {ex}")
            import traceback
            traceback.print_exc()
            return {"CANCELLED"}
