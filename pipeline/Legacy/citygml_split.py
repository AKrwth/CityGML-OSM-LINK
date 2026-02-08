import bpy
import bmesh
from collections import defaultdict, Counter
from typing import List, Dict, Sequence, Optional
from .logging_system import log_info, log_warn, log_error

# ---------------- CONFIG DEFAULTS (safe for testing) ----------------
DEFAULT_TARGET_COLLECTION = "CITYGML_BUILDINGS_SPLIT"
NAME_PREFIX = "bldg"
NAME_PAD = 5
DEFAULT_CRS = "EPSG:25832"
LIMIT = 25              # 0 = all building_idx; keep small for safety by default
MIN_FACES_PER_BLDG = 3  # skip tiny junk groups
HIDE_SOURCE_TILE = True
DRY_RUN = True          # default to dry-run for safety; set False to create objects


def ensure_collection(name: str) -> bpy.types.Collection:
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def unlink_from_all_collections(obj: bpy.types.Object):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)


def link_exclusive(obj: bpy.types.Object, col: bpy.types.Collection):
    unlink_from_all_collections(obj)
    if obj.name not in col.objects:
        col.objects.link(obj)


def get_face_attr(mesh: bpy.types.Mesh, attr_name: str):
    attr = mesh.attributes.get(attr_name)
    if attr is None:
        return None
    if attr.domain != 'FACE':
        raise RuntimeError(f"Attribute '{attr_name}' exists but is domain={attr.domain}, expected FACE")
    return attr


def unique_building_ids(mesh: bpy.types.Mesh, attr) -> List[int]:
    ids = set()
    for i, _poly in enumerate(mesh.polygons):
        ids.add(int(attr.data[i].value))
    return sorted(ids)


def build_submesh_from_face_ids(src_obj: bpy.types.Object, face_ids: List[int]) -> Optional[bpy.types.Mesh]:
    """Create a new mesh containing only the faces with indices in face_ids."""
    src_me = src_obj.data
    bm_src = bmesh.new()
    bm_src.from_mesh(src_me)
    bm_src.faces.ensure_lookup_table()
    bm_src.verts.ensure_lookup_table()

    faces = [bm_src.faces[i] for i in face_ids if i < len(bm_src.faces)]
    if not faces:
        bm_src.free()
        return None

    bm_new = bmesh.new()
    vert_map: Dict[int, bmesh.types.BMVert] = {}

    for f in faces:
        new_verts = []
        for v in f.verts:
            nv = vert_map.get(v.index)
            if nv is None:
                nv = bm_new.verts.new(v.co)
                vert_map[v.index] = nv
            new_verts.append(nv)
        try:
            bm_new.faces.new(new_verts)
        except ValueError:
            pass

    bm_new.verts.ensure_lookup_table()
    bm_new.faces.ensure_lookup_table()

    new_me = bpy.data.meshes.new(name="tmp_building_mesh")
    bm_new.to_mesh(new_me)
    new_me.validate(verbose=False)
    new_me.update()

    bm_src.free()
    bm_new.free()
    return new_me


def summarize_optional_attrs(face_indices: List[int], attr_func, attr_poly):
    func_id = None
    poly_stats = None

    if attr_func is not None:
        vals = [int(attr_func.data[i].value) for i in face_indices if i < len(attr_func.data)]
        if vals:
            func_id = Counter(vals).most_common(1)[0][0]

    if attr_poly is not None:
        vals = [int(attr_poly.data[i].value) for i in face_indices if i < len(attr_poly.data)]
        if vals:
            poly_stats = {
                "min": min(vals),
                "max": max(vals),
                "count": len(vals),
            }

    return func_id, poly_stats


def split_citygml_tiles(
    tiles: Optional[Sequence[bpy.types.Object]] = None,
    attr_name: str = "building_idx",
    limit: int = LIMIT,
    min_faces_per_bldg: int = MIN_FACES_PER_BLDG,
    hide_source: bool = HIDE_SOURCE_TILE,
    dry_run: bool = DRY_RUN,
    target_collection_name: str = DEFAULT_TARGET_COLLECTION,
) -> int:
    """Split CityGML tile meshes into per-building objects.

    - No edit-mode ops; pure bmesh duplication per building_idx.
    - New objects go exclusively into target_collection_name.
    - Returns total buildings created.
    """

    target_col = ensure_collection(target_collection_name)
    if tiles is None:
        tiles = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    else:
        tiles = [o for o in tiles if o and o.type == 'MESH']

    total_created = 0
    tiles_processed = 0

    for tile in tiles:
        src_me = tile.data
        attr = get_face_attr(src_me, attr_name)
        if attr is None:
            log_warn(f"[split][skip] {tile.name}: missing '{attr_name}' face attribute")
            continue
        if len(src_me.polygons) == 0:
            log_warn(f"[split][skip] {tile.name}: no polygons")
            continue

        # Optional attrs
        attr_func = get_face_attr(src_me, "building_function_id") if "building_function_id" in src_me.attributes else None
        attr_poly = get_face_attr(src_me, "gml_polygon_idx") if "gml_polygon_idx" in src_me.attributes else None

        ids = unique_building_ids(src_me, attr)
        if limit and limit > 0:
            ids = ids[:limit]

        faces_by_id: Dict[int, List[int]] = defaultdict(list)
        for face_i, _poly in enumerate(src_me.polygons):
            bid = int(attr.data[face_i].value)
            if limit and bid not in ids:
                continue
            faces_by_id[bid].append(face_i)

        created_here = 0
        log_info(f"{tile.name}: polys={len(src_me.polygons)}, groups={len(ids)}, limit={limit or 'all'}")

        for bid in ids:
            face_indices = faces_by_id.get(bid, [])
            if len(face_indices) < min_faces_per_bldg:
                continue

            if dry_run:
                log_info(f"  [dry] id={bid} faces={len(face_indices)}")
                continue

            new_me = build_submesh_from_face_ids(tile, face_indices)
            if new_me is None:
                continue

            new_obj = bpy.data.objects.new(f"{NAME_PREFIX}_{tile.name}__b{bid:0{NAME_PAD}d}", new_me)

            try:
                from pathlib import Path
                new_obj["source_tile"] = Path(str(tile.get("source_tile", tile.name))).stem
            except Exception:
                new_obj["source_tile"] = str(tile.get("source_tile", tile.name))
            new_obj["building_idx"] = int(bid)
            func_id, poly_stats = summarize_optional_attrs(face_indices, attr_func, attr_poly)
            if func_id is not None:
                new_obj["building_function_id"] = int(func_id)
            if poly_stats is not None:
                new_obj["gml_polygon_idx_min"] = int(poly_stats["min"])
                new_obj["gml_polygon_idx_max"] = int(poly_stats["max"])
                new_obj["gml_polygon_count"] = int(poly_stats["count"])
            new_obj["crs"] = tile.get("crs", DEFAULT_CRS)

            new_obj.matrix_world = tile.matrix_world.copy()

            link_exclusive(new_obj, target_col)

            created_here += 1

        if not dry_run and hide_source and created_here > 0:
            tile.hide_viewport = True
            tile.hide_render = True

        tiles_processed += 1
        total_created += created_here
        log_info(f"{tile.name}: created {created_here} → {target_collection_name}")

    log_info(f"Done. Tiles processed={tiles_processed}, buildings created={total_created}, dry_run={dry_run}")
    return total_created
