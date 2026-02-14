"""
Operator: Fit Terrain to CityGML BBox.

Standalone operator that scales + positions the terrain DEM to match
the union bounding box of all CityGML tiles. Can be invoked from
the pipeline or manually from the UI.
"""

import bpy
from bpy.types import Operator

try:
    from ...utils.logging_system import log_info, log_warn, log_error
except ImportError:
    from utils.logging_system import log_info, log_warn, log_error

try:
    from ...pipeline.terrain.terrain_fit import fit_terrain_to_citygml
except ImportError:
    from pipeline.terrain.terrain_fit import fit_terrain_to_citygml

try:
    from ...pipeline.terrain.terrain_validation import get_terrain_object, collect_gml_objects
except ImportError:
    from pipeline.terrain.terrain_validation import get_terrain_object, collect_gml_objects


class M1DC_OT_TerrainFitBBox(Operator):
    """
    Scale + position terrain to exactly match CityGML tile union bounding box.

    Finds terrain via m1dc_role='terrain' / TERRAIN collection / legacy names.
    Finds CityGML tiles via CITYGML_TILES collection.
    Applies non-uniform XY scale and translation. Validates within 5cm tolerance.
    """
    bl_idname = "m1dc.terrain_fit_bbox"
    bl_label = "Fit Terrain to CityGML BBox"
    bl_description = (
        "Scale and position terrain so its XY bounding box exactly matches "
        "the union bounding box of all CityGML tiles (5cm tolerance)"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        # ── Find terrain object ──
        terrain_obj = get_terrain_object()
        if terrain_obj is None:
            msg = "Terrain not found (checked m1dc_role, TERRAIN collection, legacy names)"
            log_error(f"[TERRAIN][FIT] {msg}")
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        # ── Check if already fitted ──
        if terrain_obj.get("M1DC_TERRAIN_FIT"):
            log_info(f"[TERRAIN][FIT] Terrain '{terrain_obj.name}' already fitted — re-fitting")

        # ── Find CityGML tiles ──
        gml_objs = collect_gml_objects()
        if not gml_objs:
            msg = "No CityGML tiles found in CITYGML_TILES collection"
            log_error(f"[TERRAIN][FIT] {msg}")
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        log_info(f"[TERRAIN][FIT] Found terrain='{terrain_obj.name}', citygml_tiles={len(gml_objs)}")

        # ── Find optional RGB object ──
        rgb_obj = None
        terrain_col = bpy.data.collections.get("TERRAIN")
        if terrain_col:
            for obj in terrain_col.objects:
                if obj.type == "MESH" and obj != terrain_obj and "rgb" in obj.name.lower():
                    rgb_obj = obj
                    break

        # ── Execute fit ──
        try:
            info = fit_terrain_to_citygml(
                terrain_obj=terrain_obj,
                citygml_objs=gml_objs,
                eps=0.05,
                rgb_obj=rgb_obj,
            )

            # Success
            err = info.get('error', 0)
            sx = info.get('scale_x', 1)
            sy = info.get('scale_y', 1)
            dx = info.get('dx', 0)
            dy = info.get('dy', 0)
            tgt = info.get('target_size', (0, 0))
            after = info.get('terrain_size_after', (0, 0))

            summary = (
                f"Terrain fitted: err={err:.3f}m | "
                f"target={tgt[0]:.0f}x{tgt[1]:.0f}m | "
                f"actual={after[0]:.0f}x{after[1]:.0f}m | "
                f"scale=({sx:.4f}, {sy:.4f}) | "
                f"shift=({dx:.1f}, {dy:.1f})"
            )
            log_info(f"[TERRAIN][FIT] {summary}")
            self.report({"INFO"}, summary)
            return {"FINISHED"}

        except (RuntimeError, ValueError) as ex:
            log_error(f"[TERRAIN][FIT] Failed: {ex}")
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}

        except Exception as ex:
            import traceback
            log_error(f"[TERRAIN][FIT] Unexpected error: {ex}")
            traceback.print_exc()
            self.report({"ERROR"}, f"Terrain fit failed: {ex}")
            return {"CANCELLED"}
