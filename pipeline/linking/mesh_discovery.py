"""
CityGML mesh discovery: collect CityGML mesh objects from the Blender scene.

Centralised discovery logic used by linking, materialize, export, and diagnostics.
All callers should use collect_citygml_meshes() instead of rolling their own logic.
"""
import bpy
from typing import List, Tuple

try:
    from ...utils.logging_system import log_info, log_warn
except ImportError:
    log_info = print
    log_warn = lambda m: print(f"[WARN] {m}")


def collect_citygml_meshes(*, log_prefix: str = "[Discovery]") -> List:
    """
    Reliably collect all CityGML mesh objects from the scene.

    Strategy (in priority order):
      1. Objects in the 'CITYGML_TILES' collection.
      2. Mesh objects whose name starts with 'LoD2_'.
      3. Mesh objects tagged with custom property 'source_tile'.

    Deduplicates by object pointer.

    Returns:
        list of bpy.types.Object (MESH only)
    """
    seen = set()
    result = []

    def _add(obj):
        if obj and obj.type == "MESH" and id(obj) not in seen:
            seen.add(id(obj))
            result.append(obj)

    # Strategy 1: CITYGML_TILES collection
    col = bpy.data.collections.get("CITYGML_TILES")
    if col:
        for obj in col.objects:
            _add(obj)

    # Strategy 2: LoD2_ prefix (common CityGML naming)
    for obj in bpy.data.objects:
        if obj.name.startswith("LoD2_"):
            _add(obj)

    # Strategy 3: source_tile custom property
    for obj in bpy.data.objects:
        if "source_tile" in obj:
            _add(obj)

    # Proof log
    sample_names = [o.name for o in result[:5]]
    total_faces = sum(len(o.data.polygons) for o in result if o.data)
    log_info(
        f"{log_prefix} Discovered {len(result)} CityGML meshes, "
        f"total_faces={total_faces}, samples={sample_names}"
    )

    # Count meshes that have building_idx attribute
    meshes_with_bidx = 0
    for obj in result:
        mesh = obj.data
        if mesh:
            for cand in ("gml_building_idx", "gml__building_idx", "building_idx"):
                a = mesh.attributes.get(cand)
                if a and a.domain == "FACE":
                    meshes_with_bidx += 1
                    break
    log_info(f"{log_prefix} Meshes with building_idx: {meshes_with_bidx}/{len(result)}")

    return result


def collect_building_candidates(mesh_objs=None, *, log_prefix="[Discovery]") -> List[dict]:
    """
    Aggregate building entries from CityGML meshes for export/linking.

    Each entry:
      {
          "source_tile": str (normalized),
          "building_idx": int,
          "obj": bpy.types.Object,
          "face_indices": list[int],
      }
    """
    if mesh_objs is None:
        mesh_objs = collect_citygml_meshes(log_prefix=log_prefix)

    from ..linking.key_normalization import normalize_source_tile as norm_source_tile

    entries = []
    for obj in mesh_objs:
        mesh = obj.data
        if not mesh:
            continue
        face_count = len(mesh.polygons)
        if face_count == 0:
            continue

        # Find building_idx attribute
        idx_attr = None
        for cand in ("gml_building_idx", "gml__building_idx", "building_idx"):
            a = mesh.attributes.get(cand)
            if a and a.domain == "FACE" and a.data_type == "INT" and len(a.data) == face_count:
                idx_attr = a
                break

        if idx_attr is None:
            continue

        st = norm_source_tile(obj.get("source_tile", obj.name))

        # Group faces by building_idx
        bidx_faces = {}
        for fi in range(face_count):
            try:
                bidx = int(idx_attr.data[fi].value)
            except Exception:
                continue
            if bidx < 0:
                continue
            bidx_faces.setdefault(bidx, []).append(fi)

        for bidx, faces in bidx_faces.items():
            entries.append({
                "source_tile": st,
                "building_idx": bidx,
                "obj": obj,
                "face_indices": faces,
            })

    log_info(
        f"{log_prefix} Aggregated {len(entries)} building candidates "
        f"from {len(mesh_objs)} meshes"
    )
    return entries
