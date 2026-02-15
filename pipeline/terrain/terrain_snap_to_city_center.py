"""
Snap terrain XY to CityGML tile-grid center (translation only).

Delta = city_center_xy - terrain_center_xy, Z unchanged.
Exact copy of user's tested script, wrapped as callable function.
"""
import bpy
from mathutils import Vector

CITY_COL = "CITYGML_TILES"
TERRAIN_OBJ = "Terrain_DEM_tile"  # ggf. anpassen


def world_bbox(objs):
    pts = []
    for o in objs:
        if not o or o.type != "MESH":
            continue
        pts.extend([o.matrix_world @ Vector(c) for c in o.bound_box])
    if not pts:
        raise RuntimeError("world_bbox(): no mesh points found.")
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def center(mn, mx):
    return (mn + mx) * 0.5


def snap_terrain_to_city_center_xy(
    city_col_name=CITY_COL,
    terrain_obj_name=TERRAIN_OBJ,
):
    """
    Snappt Terrain XY-Center auf CityGML-Tiles XY-Center.
    Z bleibt unverändert. Keine Skalierung, keine Rotation.

    Returns dict: ok, terrain, delta, city_center, terrain_center, error
    """
    # --- City meshes from collection
    city_col = bpy.data.collections.get(city_col_name)
    if not city_col:
        return {"ok": False, "error": f"Collection not found: {city_col_name}"}

    city_meshes = [o for o in city_col.objects if o.type == "MESH"]
    if not city_meshes:
        return {"ok": False, "error": f"No mesh objects in: {city_col_name}"}

    # --- Terrain object
    terrain = bpy.data.objects.get(terrain_obj_name)
    if not terrain or terrain.type != "MESH":
        return {"ok": False, "error": f"Terrain object not found or not mesh: {terrain_obj_name}"}

    city_mn, city_mx = world_bbox(city_meshes)
    ter_mn, ter_mx = world_bbox([terrain])

    city_c = center(city_mn, city_mx)
    ter_c = center(ter_mn, ter_mx)

    delta = city_c - ter_c
    delta.z = 0.0  # XY snap only

    print("[Terrain→City Center Snap]")
    print(" city_center:", tuple(round(v, 3) for v in city_c))
    print(" ter_center :", tuple(round(v, 3) for v in ter_c))
    print(" delta_xy   :", tuple(round(v, 3) for v in delta))

    terrain.location += delta
    bpy.context.view_layer.update()

    print(" new terrain location:", tuple(round(v, 3) for v in terrain.location))

    return {
        "ok": True,
        "terrain": terrain.name,
        "delta": (round(delta.x, 3), round(delta.y, 3)),
        "city_center": (round(city_c.x, 3), round(city_c.y, 3)),
        "terrain_center": (round(ter_c.x, 3), round(ter_c.y, 3)),
    }
