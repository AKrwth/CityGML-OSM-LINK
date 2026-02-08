"""
Face Attribute Tools — Helper functions for querying/filtering CityGML face attributes

This module provides utilities to discover, query, and select faces by their
mesh attributes (FACE domain). Used by operators in ops.py to implement
face attribute query/filter/highlight functionality.

Functions:
- get_citygml_mesh_objects() — Get all CityGML tile meshes
- collect_face_attributes() — Discover all face attributes across meshes
- unique_values_for_attribute() — Get unique values with counts for an attribute
- select_faces_by_attribute() — Select faces matching attribute criteria
"""

try:
    import bpy  # type: ignore
    import bmesh  # type: ignore
except ModuleNotFoundError as exc:
    raise ImportError("bpy/bmesh not found; run inside Blender.") from exc


def get_citygml_mesh_objects(context=None):
    """
    Return all mesh objects representing CityGML tiles.

    Heuristics:
    - Objects with type=='MESH' and name starting with 'LoD2_32_'
    - Prefer objects inside 'CityGML_TILES' collection if it exists
    - Return stable sorted list by name

    Args:
        context: Blender context (optional, uses bpy.context if None)

    Returns:
        list[bpy.types.Object]: Sorted list of CityGML mesh objects
    """
    if context is None:
        context = bpy.context

    candidates = []

    # Check if CityGML_TILES collection exists
    citygml_coll = bpy.data.collections.get("CityGML_TILES")

    if citygml_coll:
        # Prefer objects from collection
        for obj in citygml_coll.objects:
            if obj.type == 'MESH' and obj.name.startswith("LoD2_32_"):
                candidates.append(obj)
    else:
        # Fallback: scan all scene objects
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.name.startswith("LoD2_32_"):
                candidates.append(obj)

    # Return stable sorted list
    return sorted(candidates, key=lambda o: o.name)


def collect_face_attributes(objs):
    """
    Collect all FACE domain attributes across multiple mesh objects.

    Args:
        objs: List of mesh objects to inspect

    Returns:
        dict: Mapping of attr_name -> {
            "data_type": str,  # "INT", "FLOAT", "STRING", etc.
            "domains": set,     # Set of domains where attribute appears
            "count_meshes": int # Number of meshes with this attribute
        }
    """
    attr_map = {}

    for obj in objs:
        if obj.type != 'MESH' or not obj.data:
            continue

        mesh = obj.data

        # Inspect all attributes
        for attr in mesh.attributes:
            # Only interested in FACE domain attributes
            if attr.domain != 'FACE':
                continue

            attr_name = attr.name

            # Initialize entry if first time seeing this attribute
            if attr_name not in attr_map:
                attr_map[attr_name] = {
                    "data_type": attr.data_type,
                    "domains": set(),
                    "count_meshes": 0,
                }

            # Update info
            attr_map[attr_name]["domains"].add(attr.domain)
            attr_map[attr_name]["count_meshes"] += 1

    return attr_map


def unique_values_for_attribute(objs, attr_name, limit=200):
    """
    Get unique values and their counts for a specific face attribute.

    Memory-safe: stops collecting after limit unique values (but continues counting).

    Args:
        objs: List of mesh objects
        attr_name: Name of the attribute to query
        limit: Maximum number of unique values to collect (default 200)

    Returns:
        list[tuple[value, count]]: Sorted by count descending
    """
    value_counts = {}
    total_collected = 0
    stopped_early = False

    for obj in objs:
        if obj.type != 'MESH' or not obj.data:
            continue

        mesh = obj.data
        attr = mesh.attributes.get(attr_name)

        if not attr or attr.domain != 'FACE':
            continue

        # Iterate over face attribute data
        num_faces = len(mesh.polygons)

        for i in range(num_faces):
            try:
                value = attr.data[i].value

                # Skip empty/null-like string values
                if attr.data_type == 'STRING' and (not value or value.strip() == ""):
                    continue

                # Convert to hashable type
                if attr.data_type == 'FLOAT':
                    # Round floats to avoid floating point noise
                    value = round(value, 6)

                # Check if we've hit the unique value limit
                if value not in value_counts:
                    if total_collected >= limit:
                        stopped_early = True
                        continue  # Stop collecting new values, but keep counting existing
                    total_collected += 1

                # Count occurrence
                value_counts[value] = value_counts.get(value, 0) + 1

            except (IndexError, AttributeError):
                # Gracefully handle missing/invalid data
                continue

    # Sort by count descending
    sorted_values = sorted(value_counts.items(), key=lambda x: x[1], reverse=True)

    return sorted_values


def select_faces_by_attribute(objs, attr_name, target_value, mode="EQUAL"):
    """
    Select faces across multiple objects where attribute matches target value.

    Uses bmesh in Edit Mode for reliable face selection.

    Args:
        objs: List of mesh objects
        attr_name: Name of the face attribute
        target_value: Value to match
        mode: Comparison mode (currently only "EQUAL" supported)

    Returns:
        dict: {
            "objects_touched": int,
            "faces_selected": int
        }
    """
    stats = {
        "objects_touched": 0,
        "faces_selected": 0,
    }

    # Ensure we start in Object Mode
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Deselect all first
    bpy.ops.object.select_all(action='DESELECT')

    for obj in objs:
        if obj.type != 'MESH' or not obj.data:
            continue

        mesh = obj.data
        attr = mesh.attributes.get(attr_name)

        if not attr or attr.domain != 'FACE':
            continue

        num_faces = len(mesh.polygons)
        if num_faces == 0:
            continue

        # Find faces that match
        matching_faces = []

        for i in range(num_faces):
            try:
                value = attr.data[i].value

                # Comparison
                if mode == "EQUAL":
                    # Type-aware comparison
                    if attr.data_type == 'STRING':
                        match = (str(value).strip() == str(target_value).strip())
                    elif attr.data_type == 'FLOAT':
                        match = (abs(value - float(target_value)) < 1e-6)
                    else:  # INT or other numeric
                        match = (int(value) == int(target_value))

                    if match:
                        matching_faces.append(i)

            except (IndexError, AttributeError, ValueError, TypeError):
                # Gracefully handle type conversion errors
                continue

        # If we found matches, enter edit mode and select them
        if matching_faces:
            # Select this object
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj

            # Enter Edit Mode
            bpy.ops.object.mode_set(mode='EDIT')

            # Use bmesh for reliable face selection
            bm = bmesh.from_edit_mesh(mesh)

            # Clear existing selection first
            for face in bm.faces:
                face.select = False

            # Select matching faces
            for face_idx in matching_faces:
                if face_idx < len(bm.faces):
                    bm.faces[face_idx].select = True

            # Update mesh
            bmesh.update_edit_mesh(mesh)

            # Exit Edit Mode
            bpy.ops.object.mode_set(mode='OBJECT')

            # Update stats
            stats["objects_touched"] += 1
            stats["faces_selected"] += len(matching_faces)

    return stats
