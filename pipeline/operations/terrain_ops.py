"""
Terrain import and alignment operators for M1DC add-on.

Contains operators for importing terrain data (DEM, RGB basemap, DGM) and
performing terrain-to-CityGML alignment operations.
"""

import os
import traceback
from pathlib import Path

import bpy
from bpy.types import Operator

# Import utilities
try:
    from ...utils.common import (
        get_output_dir,
        link_exclusively_to_collection,
    )
except ImportError:
    from utils.common import (
        get_output_dir,
        link_exclusively_to_collection,
    )

try:
    from ...utils.logging_system import log_info, log_warn, log_error
except ImportError:
    from utils.logging_system import log_info, log_warn, log_error

try:
    from ...pipeline.terrain import (
        m1_basemap,
        rgb_basemap_import,
        dgm_terrain_import,
        z_alignment,
    )
except ImportError:
    from pipeline.terrain import (
        m1_basemap,
        rgb_basemap_import,
        dgm_terrain_import,
        z_alignment,
    )

try:
    from ...pipeline.diagnostics.geometry_tripwires import run_geometry_tripwires
    from ...pipeline.diagnostics import face_attr_tools
except ImportError:
    from pipeline.diagnostics.geometry_tripwires import run_geometry_tripwires
    from pipeline.diagnostics import face_attr_tools


def _settings(context):
    """Get M1DC settings from scene."""
    return getattr(context.scene, "m1dc_settings", None)


class M1DC_OT_ImportBasemapTerrain(Operator):
    """
    Phase 2: Import DEM → Terrain Mesh with Displacement.
    
    Workflow:
    1. Read DEM metadata via gdalinfo (OSGeo4W subprocess)
    2. Convert to 16-bit PNG heightmap via gdal_translate
    3. Build terrain mesh with step downsampling
    4. Add Displace modifier with heightmap texture
    5. Place in correct CRS coordinates
    
    Status fields updated:
    - status_basemap_terrain_loaded
    - status_basemap_terrain_dem_size
    - status_basemap_terrain_extent
    - status_basemap_terrain_pixel_size
    - status_basemap_terrain_crs
    - step0_terrain_done
    """
    bl_idname = "m1dc.import_basemap_terrain"
    bl_label = "Import DEM → Terrain"
    bl_description = "Convert DEM to displacement terrain mesh"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        # Validate basemap folder
        basemap_dir = getattr(s, "basemap_dir", "").strip()
        if not basemap_dir or not os.path.isdir(basemap_dir):
            self.report({"ERROR"}, f"Basemap folder not found: {basemap_dir}")
            return {"CANCELLED"}

        # Validate OSGeo4W root
        osgeo4w_root = getattr(s, "osgeo4w_root", "").strip()
        if not osgeo4w_root or not os.path.isdir(osgeo4w_root):
            self.report({"ERROR"}, f"OSGeo4W root not found: {osgeo4w_root}")
            return {"CANCELLED"}

        # Check DEM exists
        dem_path = os.path.join(basemap_dir, "DEM_mosaic_resampled.tif")
        if not os.path.isfile(dem_path):
            self.report({"ERROR"}, f"DEM not found: DEM_mosaic_resampled.tif in {basemap_dir}")
            return {"CANCELLED"}

        # Import basemap terrain
        try:
            ok = m1_basemap.import_basemap_terrain(context, s)
            if not ok:
                self.report({"ERROR"}, "Terrain import failed (see log for details)")
                return {"CANCELLED"}
            self.report({"INFO"}, "Terrain imported successfully ✓")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Terrain import exception: {ex}")
            traceback.print_exc()
            return {"CANCELLED"}


class M1DC_OT_ImportRGBBasemap(Operator):
    """
    PHASE 2: Import RGB Basemap Tiles (DTK10).

    Imports RGB tiles as reference basemap (XY-only, no height).
    - Reads dtk10_*.tif from terrain_rgb_dir
    - Creates planes with real meter dimensions (scale=1,1,1)
    - Correctly mosaiked in EPSG:25832 meters
    - Collection: M1DC_RGB_BASEMAP

    This is pure RGB imagery for visual reference/QA.
    No DEM, no height, no terrain deformation.
    """
    bl_idname = "m1dc.import_rgb_basemap"
    bl_label = "Import RGB Basemap (DTK10)"
    bl_description = "Import RGB tiles as reference basemap (XY-only, no height)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        # Validate RGB tiles folder
        rgb_dir = getattr(s, "terrain_rgb_dir", "").strip()
        if not rgb_dir or not os.path.isdir(rgb_dir):
            self.report({"ERROR"}, f"RGB tiles folder not found: {rgb_dir}")
            self.report({"INFO"}, "Set 'Terrain RGB Source (Tiles)' in settings.")
            return {"CANCELLED"}

        # Import RGB basemap tiles
        try:
            ok, count = rgb_basemap_import.import_rgb_basemap_tiles(rgb_dir)
            if not ok:
                self.report({"ERROR"}, "RGB basemap import failed (see log for details)")
                return {"CANCELLED"}

            # PHASE 5: Geometry tripwires (post-import checks)
            try:
                run_geometry_tripwires()
            except RuntimeError as tripwire_error:
                self.report({"ERROR"}, f"Geometry check failed: {tripwire_error}")
                return {"CANCELLED"}

            self.report({"INFO"}, f"RGB basemap imported: {count} tiles ✓")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"RGB basemap import exception: {ex}")
            traceback.print_exc()
            return {"CANCELLED"}


class M1DC_OT_ImportDGMTerrain(Operator):
    """
    PHASE 3: Import DGM Terrain Artifact.

    Imports pre-merged DEM terrain (dem_merged.obj) with deterministic XY placement.
    - Reads dem_merged.obj from artifact folder
    - Uses JSON (basemap.json) or CSV fallback for extents
    - Deterministic XY placement (no trial-and-error)
    - Scale=(1,1,1) enforced, Z preserved from OBJ
    - Collection: M1DC_TERRAIN_DGM

    This is the height reference for Z-alignment (Phase 4).
    """
    bl_idname = "m1dc.import_dgm_terrain"
    bl_label = "Import DGM Terrain"
    bl_description = "Import DGM terrain artifact with deterministic XY placement"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        # Validate DGM artifact folder
        # Try basemap_dir first (contains merged outputs), then dgm_artifact_dir if exists
        dgm_dir = getattr(s, "basemap_dir", "").strip()
        if not dgm_dir:
            dgm_dir = getattr(s, "dgm_artifact_dir", "").strip()

        if not dgm_dir or not os.path.isdir(dgm_dir):
            self.report({"ERROR"}, f"DGM artifact folder not found: {dgm_dir}")
            self.report({"INFO"}, "Set 'Basemap Folder (_Merged)' in settings.")
            return {"CANCELLED"}

        # Get tile size setting (for CSV fallback)
        tile_size_m = getattr(s, "dgm_tile_size_m", 1000.0)

        # Import DGM terrain
        try:
            ok, terrain_obj = dgm_terrain_import.import_dgm_terrain(dgm_dir, tile_size_m)
            if not ok:
                self.report({"ERROR"}, "DGM terrain import failed (see log for details)")
                return {"CANCELLED"}

            # PHASE 4.5: Terrain post-processing (NaN repair, UV generation)
            if terrain_obj:
                try:
                    from ...pipeline.terrain.terrain_postprocess import repair_nan_vertices, ensure_uv_xy

                    # A1: Repair NaN/Inf vertices (make raycastable)
                    repair_result = repair_nan_vertices(terrain_obj, mode="SET_Z_TO_MIN")
                    if repair_result["bad_count"] > 0:
                        log_info(f"[Pipeline] Terrain NaN repair: {repair_result['bad_count']} vertices fixed")

                    # A2: Generate UV mapping from XY
                    uv_result = ensure_uv_xy(terrain_obj, uv_name="M1DC_UV", flip_v=True)
                    if uv_result["loop_count"] > 0:
                        log_info(f"[Pipeline] Terrain UV generated: {uv_result['loop_count']} loops")

                except Exception as postprocess_ex:
                    log_warn(f"[Pipeline] Terrain post-processing failed: {postprocess_ex}")
                    # Non-fatal, continue with import
            
            # PHASE 4: Terrain XY Recenter to CityGML
            if terrain_obj:
                try:
                    from mathutils import Vector
                    
                    # Find all CityGML tile objects
                    gml_objs = [obj for obj in bpy.data.objects 
                               if obj.type == 'MESH' and obj.name.startswith("LoD2_")]
                    
                    if not gml_objs:
                        print("[TERRAIN][RECENTER_XY][SKIP] reason=no_citygml_tiles_found")
                    else:
                        # Compute bbox centers (world coords)
                        def bbox_center_world(obj):
                            cs = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
                            mn = Vector((min(v.x for v in cs), min(v.y for v in cs), min(v.z for v in cs)))
                            mx = Vector((max(v.x for v in cs), max(v.y for v in cs), max(v.z for v in cs)))
                            return (mn + mx) * 0.5
                        
                        gml_center = sum((bbox_center_world(o) for o in gml_objs), Vector()) / len(gml_objs)
                        trn_center = bbox_center_world(terrain_obj)
                        
                        dx = gml_center.x - trn_center.x
                        dy = gml_center.y - trn_center.y
                        
                        # Apply XY translation via matrix_world
                        mw = terrain_obj.matrix_world.copy()
                        mw.translation.x += dx
                        mw.translation.y += dy
                        terrain_obj.matrix_world = mw
                        
                        new_loc = terrain_obj.location
                        print(f"[TERRAIN] recenter_xy applied dx={dx:.2f} dy={dy:.2f} terrain={terrain_obj.name} new_loc=({new_loc.x:.2f},{new_loc.y:.2f},{new_loc.z:.2f})")
                
                except Exception as recenter_ex:
                    log_warn(f"[Pipeline] Terrain XY recenter failed: {recenter_ex}")
                    traceback.print_exc()
                    # Non-fatal, continue

            # PHASE 5: Geometry tripwires (post-import checks)
            try:
                run_geometry_tripwires()
            except RuntimeError as tripwire_error:
                self.report({"ERROR"}, f"Geometry check failed: {tripwire_error}")
                return {"CANCELLED"}

            self.report({"INFO"}, f"DGM terrain imported: {terrain_obj.name if terrain_obj else 'unknown'} ✓")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"DGM terrain import exception: {ex}")
            traceback.print_exc()
            return {"CANCELLED"}


class M1DC_OT_AlignCityGMLToTerrainZ(Operator):
    """
    PHASE 4: Align CityGML Z to Terrain.

    Vertical alignment of CityGML buildings to DGM terrain.
    - Analyzes Z offset between buildings and terrain
    - Classifies as GLOBAL_OFFSET or PER_BUILDING_SNAP
    - Applies adjustment to location.z only (XY unchanged)
    - Scale and rotation unchanged

    This is the final geometric alignment step.
    """
    bl_idname = "m1dc.align_citygml_z"
    bl_label = "Align CityGML Z to Terrain"
    bl_description = "Align CityGML buildings vertically to DGM terrain (Z-only)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Scene settings not registered; reload add-on.")
            return {"CANCELLED"}

        # Check prerequisites
        citygml_col = bpy.data.collections.get("CITYGML_TILES")
        if not citygml_col:
            self.report({"ERROR"}, "CityGML collection not found. Import CityGML first (Phase 1).")
            return {"CANCELLED"}

        from ...pipeline.terrain.terrain_validation import get_terrain_object
        terrain_obj = get_terrain_object()
        if not terrain_obj:
            self.report({"ERROR"}, "Terrain not found (checked m1dc_role, TERRAIN collection, legacy names). Import terrain first.")
            return {"CANCELLED"}

        # Execute Z-alignment
        try:
            ok, msg, stats = z_alignment.align_citygml_to_terrain_z()
            if not ok:
                self.report({"ERROR"}, f"Z-alignment failed: {msg}")
                return {"CANCELLED"}

            self.report({"INFO"}, f"Z-alignment complete: {msg} ✓")
            return {"FINISHED"}
        except Exception as ex:
            self.report({"ERROR"}, f"Z-alignment exception: {ex}")
            traceback.print_exc()
            return {"CANCELLED"}


class M1DC_OT_TerrainAlignmentCheck(Operator):
    """Check Terrain ↔ CityGML alignment (diagnostic, read-only)"""
    bl_idname = "m1dc.terrain_alignment_check"
    bl_label = "Terrain Alignment Check"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            from ...pipeline.diagnostics.terrain_alignment_check import report_terrain_citygml_alignment

            log_info("[AlignmentCheck] ═══════════════════════════════════")
            log_info("[AlignmentCheck] Running Terrain ↔ CityGML Alignment Check")
            log_info("[AlignmentCheck] ═══════════════════════════════════")

            result = report_terrain_citygml_alignment(context.scene)

            # Format summary for console and UI
            summary_lines = []
            summary_lines.append("─" * 60)
            summary_lines.append("TERRAIN ↔ CITYGML ALIGNMENT CHECK RESULTS")
            summary_lines.append("─" * 60)
            summary_lines.append(f"CRS: {result['crs']}")
            summary_lines.append(f"World Origin: E={result['world_min_e']}, N={result['world_min_n']}")
            summary_lines.append("")
            summary_lines.append(f"Terrain Object: {result['terrain_obj'] or 'NOT FOUND'}")
            summary_lines.append(f"CityGML Meshes: {result['citygml_mesh_count']}")
            summary_lines.append("")

            if result["delta_xy_m"] is not None:
                summary_lines.append(f"XY Center Delta: {result['delta_xy_m']:.2f}m")
                summary_lines.append(f"  ΔX: {result.get('delta_x_m', 0):.2f}m")
                summary_lines.append(f"  ΔY: {result.get('delta_y_m', 0):.2f}m")
                summary_lines.append(f"XY Overlap: {result['overlap_xy']}")
                summary_lines.append("")

                # Interpretation
                delta = result['delta_xy_m']
                overlap = result['overlap_xy']

                if delta < 10.0 and overlap:
                    summary_lines.append("✓ ALIGNMENT: GOOD (delta < 10m, overlap OK)")
                    status = "INFO"
                elif delta < 50.0 and overlap:
                    summary_lines.append("⚠ ALIGNMENT: FAIR (10m < delta < 50m)")
                    status = "WARNING"
                else:
                    summary_lines.append("✗ ALIGNMENT: POOR (delta large or no overlap)")
                    status = "WARNING"
            else:
                summary_lines.append("⚠ Cannot compute alignment (missing terrain or CityGML)")
                status = "WARNING"

            if result["warnings"]:
                summary_lines.append("")
                summary_lines.append("Warnings:")
                for warn in result["warnings"]:
                    summary_lines.append(f"  - {warn}")

            summary_lines.append("─" * 60)

            # Print to console
            for line in summary_lines:
                log_info(f"[AlignmentCheck] {line}")

            # Report to user
            if result["delta_xy_m"] is not None:
                self.report({status}, f"Alignment check complete: ΔXY={result['delta_xy_m']:.2f}m (see console)")
            else:
                self.report({"WARNING"}, "Alignment check incomplete (missing objects, see console)")

            return {"FINISHED"}

        except Exception as ex:
            log_error(f"[AlignmentCheck] Exception: {ex}")
            self.report({"ERROR"}, f"Alignment check failed: {ex}")
            traceback.print_exc()
            return {"CANCELLED"}


class M1DC_OT_TerrainZAlignLowMedian(Operator):
    """Apply Z-offset to terrain based on low-vertices raycast median"""
    bl_idname = "m1dc.terrain_z_align_low_median"
    bl_label = "Terrain Z Align (Low-Median)"
    bl_description = "Compute Z-offset via raycast from CityGML low vertices to terrain, then apply to terrain"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            from ...pipeline.terrain.terrain_postprocess import (
                compute_z_offset_raycast_low_vertices,
                apply_z_offset,
            )

            log_info("[TerrainZAlign] ═══════════════════════════════════")
            log_info("[TerrainZAlign] TERRAIN Z ALIGNMENT (LOW-MEDIAN)")
            log_info("[TerrainZAlign] ═══════════════════════════════════")

            # Find terrain object (unified lookup)
            from ...pipeline.terrain.terrain_validation import get_terrain_object
            terrain_obj = get_terrain_object()

            if not terrain_obj:
                self.report({"ERROR"}, "Terrain not found (checked m1dc_role, TERRAIN collection, legacy names)")
                log_error("[TerrainZAlign] Terrain object not found")
                return {"CANCELLED"}

            log_info(f"[TerrainZAlign] Terrain: {terrain_obj.name}")

            # Find CityGML objects (via source_tile prop or collection)
            gml_objs = []
            for obj in bpy.data.objects:
                if obj.type == 'MESH' and obj.get("source_tile"):
                    gml_objs.append(obj)

            if not gml_objs:
                self.report({"ERROR"}, "No CityGML objects found (need source_tile property)")
                log_error("[TerrainZAlign] No CityGML objects found")
                return {"CANCELLED"}

            log_info(f"[TerrainZAlign] Found {len(gml_objs)} CityGML objects")

            # Compute Z-offset
            result = compute_z_offset_raycast_low_vertices(
                terrain_obj=terrain_obj,
                gml_objs=gml_objs,
                N=400,
                low_fraction=0.08,
                origin_up=5000.0,
                max_dist=20000.0,
            )

            if result["dz_median"] is None:
                self.report({"ERROR"}, "Z-offset computation failed (no successful raycasts)")
                log_error("[TerrainZAlign] Cannot compute dz_median (no hits)")
                return {"CANCELLED"}

            dz = result["dz_median"]
            hits = result["hits"]
            total = result["hits"] + result["misses"]
            hit_rate = hits / total if total > 0 else 0.0

            log_info(f"[TerrainZAlign] Computed dz={dz:.2f}m (hits={hits}/{total}, {hit_rate:.1%})")

            # Apply Z-offset to terrain
            new_z = apply_z_offset(terrain_obj, dz, clamp=(-5000.0, 5000.0))

            log_info(f"[TerrainZAlign] ✓ Applied dz={dz:.2f}m to terrain: new Z={new_z:.2f}m")
            log_info("[TerrainZAlign] ═══════════════════════════════════")

            self.report({"INFO"}, f"Terrain Z-aligned: dz={dz:.2f}m, new_z={new_z:.2f}m (hits={hits}/{total})")
            return {"FINISHED"}

        except Exception as ex:
            log_error(f"[TerrainZAlign] Exception: {ex}")
            self.report({"ERROR"}, f"Terrain Z-align failed: {ex}")
            traceback.print_exc()
            return {"CANCELLED"}


class M1DC_OT_TerrainAlignToCity(Operator):
    """Align terrain to city extent (scale + center)"""
    bl_idname = "m1dc.terrain_align_to_city"
    bl_label = "Align Terrain to City"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        try:
            # Check if already aligned
            if context.scene.get("M1DC_TERRAIN_ALIGNED", False):
                last_align = context.scene.get("M1DC_TERRAIN_ALIGN_LAST", "unknown")
                log_warn(f"[Terrain] Already aligned: {last_align}")
                self.report({"WARNING"}, f"Terrain already aligned: {last_align}")
                return {"CANCELLED"}

            # Find terrain object (unified lookup)
            from ...pipeline.terrain.terrain_validation import get_terrain_object
            terrain_obj = get_terrain_object()

            if not terrain_obj:
                error_msg = "Terrain not found (checked m1dc_role, TERRAIN collection, legacy names)"
                log_error(f"[Terrain] {error_msg}")
                self.report({"ERROR"}, error_msg)
                return {"CANCELLED"}

            log_info(f"[Terrain] Found terrain: {terrain_obj.name}")

            # Get CityGML meshes to compute city extent
            city_objs = face_attr_tools.get_citygml_mesh_objects(context)

            if not city_objs:
                error_msg = "No CityGML meshes found (cannot compute city extent)"
                log_error(f"[Terrain] {error_msg}")
                self.report({"ERROR"}, error_msg)
                return {"CANCELLED"}

            # Compute city bounding box (union of all CityGML meshes)
            city_min_x = city_min_y = float('inf')
            city_max_x = city_max_y = float('-inf')

            for obj in city_objs:
                for corner in obj.bound_box:
                    world_corner = obj.matrix_world @ corner
                    city_min_x = min(city_min_x, world_corner.x)
                    city_max_x = max(city_max_x, world_corner.x)
                    city_min_y = min(city_min_y, world_corner.y)
                    city_max_y = max(city_max_y, world_corner.y)

            city_size_x = city_max_x - city_min_x
            city_size_y = city_max_y - city_min_y
            city_center_x = (city_min_x + city_max_x) / 2.0
            city_center_y = (city_min_y + city_max_y) / 2.0

            log_info(f"[Terrain] City extent: {city_size_x:.1f}m × {city_size_y:.1f}m")

            # Compute terrain bounding box
            terrain_min_x = terrain_min_y = float('inf')
            terrain_max_x = terrain_max_y = float('-inf')

            for corner in terrain_obj.bound_box:
                world_corner = terrain_obj.matrix_world @ corner
                terrain_min_x = min(terrain_min_x, world_corner.x)
                terrain_max_x = max(terrain_max_x, world_corner.x)
                terrain_min_y = min(terrain_min_y, world_corner.y)
                terrain_max_y = max(terrain_max_y, world_corner.y)

            terrain_size_x = terrain_max_x - terrain_min_x
            terrain_size_y = terrain_max_y - terrain_min_y
            terrain_center_x = (terrain_min_x + terrain_max_x) / 2.0
            terrain_center_y = (terrain_min_y + terrain_max_y) / 2.0

            log_info(f"[Terrain] Terrain size: {terrain_size_x:.1f}m × {terrain_size_y:.1f}m")

            # Compute scale ratios
            if terrain_size_x < 0.01 or terrain_size_y < 0.01:
                error_msg = "Terrain size too small (degenerate geometry)"
                log_error(f"[Terrain] {error_msg}")
                self.report({"ERROR"}, error_msg)
                return {"CANCELLED"}

            sx = city_size_x / terrain_size_x
            sy = city_size_y / terrain_size_y
            s_avg = (sx + sy) * 0.5

            log_info(f"[Terrain] Scale ratios: sx={sx:.3f}, sy={sy:.3f}, avg={s_avg:.3f}")

            # SAFETY: Only apply scaling if ratio is extreme
            scale_applied = False
            if s_avg < 0.25 or s_avg > 4.0:
                log_info(f"[Terrain] Applying scale: {s_avg:.3f}x (ratio outside safe 0.25-4.0 band)")

                # Ensure object mode
                if bpy.context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')

                # Apply scale
                terrain_obj.scale = (s_avg, s_avg, s_avg)
                bpy.ops.object.select_all(action='DESELECT')
                terrain_obj.select_set(True)
                bpy.context.view_layer.objects.active = terrain_obj
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

                scale_applied = True
                log_info(f"[Terrain] ✓ Scale applied and baked: {s_avg:.3f}x")

                # Recompute terrain bounds after scaling
                terrain_min_x = terrain_min_y = float('inf')
                terrain_max_x = terrain_max_y = float('-inf')

                for corner in terrain_obj.bound_box:
                    world_corner = terrain_obj.matrix_world @ corner
                    terrain_min_x = min(terrain_min_x, world_corner.x)
                    terrain_max_x = max(terrain_max_x, world_corner.x)
                    terrain_min_y = min(terrain_min_y, world_corner.y)
                    terrain_max_y = max(terrain_max_y, world_corner.y)

                terrain_center_x = (terrain_min_x + terrain_max_x) / 2.0
                terrain_center_y = (terrain_min_y + terrain_max_y) / 2.0
            else:
                log_info(f"[Terrain] Scale ratio {s_avg:.3f} is acceptable, no scaling needed")

            # Center terrain to city center (XY only, preserve Z)
            delta_x = city_center_x - terrain_center_x
            delta_y = city_center_y - terrain_center_y

            if abs(delta_x) > 0.01 or abs(delta_y) > 0.01:
                terrain_obj.location.x += delta_x
                terrain_obj.location.y += delta_y
                log_info(f"[Terrain] ✓ Centered: shifted by ({delta_x:.2f}, {delta_y:.2f}, 0)")
            else:
                log_info("[Terrain] Already centered, no shift needed")

            # Mark as aligned
            summary = f"scale={s_avg:.3f}" if scale_applied else "centered only"
            context.scene["M1DC_TERRAIN_ALIGNED"] = True
            context.scene["M1DC_TERRAIN_ALIGN_LAST"] = summary

            result_msg = f"Terrain aligned to city: {summary}"
            log_info(f"[Terrain] {result_msg}")
            self.report({"INFO"}, result_msg)

        except Exception as e:
            error_msg = f"Terrain alignment failed: {e}"
            log_error(f"[Terrain] {error_msg}")
            self.report({"ERROR"}, error_msg)
            return {"CANCELLED"}

        return {"FINISHED"}


# Operator registration
CLASSES = [
    M1DC_OT_ImportBasemapTerrain,
    M1DC_OT_ImportRGBBasemap,
    M1DC_OT_ImportDGMTerrain,
    M1DC_OT_AlignCityGMLToTerrainZ,
    M1DC_OT_TerrainAlignmentCheck,
    M1DC_OT_TerrainZAlignLowMedian,
    M1DC_OT_TerrainAlignToCity,
]


def register():
    """Register all operators in this module."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister all operators in this module."""
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
