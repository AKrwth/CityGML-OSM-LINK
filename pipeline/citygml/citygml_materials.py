"""
CityGML Material Utilities

Ensures CityGML objects have readable default materials for visualization.
Creates/reuses a neutral grey material for buildings without custom materials.
"""

import bpy
from typing import Optional

from ...utils.logging_system import log_info, log_warn


DEFAULT_MATERIAL_NAME = "M1DC_CityGML_Default"
DEFAULT_COLOR_RGB = (0.85, 0.35, 0.1, 1.0)  # Red-orange for CityGML buildings (consistent with obj.color)
DEFAULT_ROUGHNESS = 0.8
DEFAULT_METALLIC = 0.0


def ensure_default_material() -> bpy.types.Material:
    """
    Create or retrieve the default CityGML material.
    
    Returns:
        Material object with default grey color and neutral properties.
    """
    mat = bpy.data.materials.get(DEFAULT_MATERIAL_NAME)
    
    if mat is None:
        mat = bpy.data.materials.new(DEFAULT_MATERIAL_NAME)
        mat.use_nodes = True
        _setup_material_nodes(mat)
    
    return mat


def _setup_material_nodes(mat: bpy.types.Material) -> None:
    """Configure material nodes for neutral grey display."""
    try:
        # Clear existing nodes
        mat.node_tree.nodes.clear()
        
        # Create shader and output nodes
        bsdf = mat.node_tree.nodes.new(type='ShaderNodeBsdfPrincipled')
        output = mat.node_tree.nodes.new(type='ShaderNodeOutputMaterial')
        
        # Set BSDF properties
        bsdf.inputs['Base Color'].default_value = DEFAULT_COLOR_RGB
        bsdf.inputs['Roughness'].default_value = DEFAULT_ROUGHNESS
        bsdf.inputs['Metallic'].default_value = DEFAULT_METALLIC
        
        # Connect BSDF to output
        mat.node_tree.links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        
        log_info(f"[Material] Created default material: {DEFAULT_MATERIAL_NAME}")
    except Exception as ex:
        log_warn(f"[Material] Failed to setup material nodes: {ex}")


def assign_default_material_to_object(obj: bpy.types.Object) -> bool:
    """
    Assign default material to object if it has no materials.
    
    Args:
        obj: Blender object (must be MESH type)
    
    Returns:
        True if material was assigned, False otherwise.
    """
    if obj is None or obj.type != "MESH" or not obj.data:
        return False
    
    mesh = obj.data
    
    # If mesh already has materials, don't override
    if len(mesh.materials) > 0:
        return False
    
    # Get or create default material
    default_mat = ensure_default_material()
    
    # Assign to mesh
    try:
        mesh.materials.append(default_mat)
        log_info(f"[Material] Assigned default material to {obj.name}")
        return True
    except Exception as ex:
        log_warn(f"[Material] Failed to assign material to {obj.name}: {ex}")
        return False


def ensure_materials_for_collection(collection_name: str) -> dict:
    """
    Recursively ensure all mesh objects in a collection have materials.
    
    Args:
        collection_name: Name of the collection to process
    
    Returns:
        Dictionary with statistics:
        - 'processed': Number of objects processed
        - 'assigned': Number of materials assigned
        - 'skipped': Number of objects skipped (had materials)
        - 'failed': Number of assignment failures
    """
    col = bpy.data.collections.get(collection_name)
    if col is None:
        log_warn(f"[Material] Collection not found: {collection_name}")
        return {'processed': 0, 'assigned': 0, 'skipped': 0, 'failed': 0}
    
    stats = {'processed': 0, 'assigned': 0, 'skipped': 0, 'failed': 0}
    
    def process_collection(c):
        for obj in c.objects:
            if obj.type != "MESH" or not obj.data:
                continue
            
            stats['processed'] += 1
            
            if len(obj.data.materials) > 0:
                stats['skipped'] += 1
            else:
                if assign_default_material_to_object(obj):
                    stats['assigned'] += 1
                else:
                    stats['failed'] += 1
        
        # Recurse into child collections
        for child in c.children:
            process_collection(child)
    
    process_collection(col)
    
    log_info(
        f"[Material] Collection '{collection_name}' processed: "
        f"total={stats['processed']} assigned={stats['assigned']} "
        f"skipped={stats['skipped']} failed={stats['failed']}"
    )
    
    return stats
