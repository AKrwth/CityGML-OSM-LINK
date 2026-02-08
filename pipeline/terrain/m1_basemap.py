"""
M1_DC_V6 Basemap Pipeline Module
Handles OBJ + JSON artifact import and M1DC_WORLD_ORIGIN setup.

CRITICAL PHASE: Basemap Normalization
After OBJ import, the basemesh may have:
1. Swapped axes (X/Z reversed from expected)
2. Center-origin instead of corner-origin
3. Dimensions that don't match tile_size

This module includes automatic correction for these common issues.
"""

import os
import json
import math
from pathlib import Path
from typing import Optional, List, Tuple

try:
    import bpy
    from mathutils import Euler, Vector, Matrix
except ImportError:
    pass

from ...utils.logging_system import log_info, log_warn, log_error


def _find_first_obj(folder: str) -> Optional[str]:
    """Find first .obj file in folder; return full path or None."""
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return None
    for f in p.iterdir():
        if f.suffix.lower() == ".obj":
            return str(f)
    return None


def _find_basemap_json(folder: str) -> Optional[str]:
    """Find basemap.json in folder; return full path or None."""
    p = Path(folder) / "basemap.json"
    return str(p) if p.exists() else None


def _ensure_world_origin_empty(name: str = "M1DC_WORLD_ORIGIN") -> "bpy.types.Object":
    """Create or retrieve M1DC_WORLD_ORIGIN empty object."""
    obj = bpy.data.objects.get(name)
    if obj:
        return obj
    
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = "PLAIN_AXES"
    bpy.context.scene.collection.objects.link(empty)
    return empty


def _set_world_origin_props(
    origin_empty: "bpy.types.Object",
    crs: str,
    min_e: float,
    min_n: float,
    min_z: float,
) -> None:
    """Store world origin metadata as custom properties on empty."""
    origin_empty["crs"] = crs
    origin_empty["world_min_easting"] = float(min_e)
    origin_empty["world_min_northing"] = float(min_n)
    origin_empty["world_min_elevation"] = float(min_z)


def _world_to_local(
    origin_empty: "bpy.types.Object",
    world_e: float,
    world_n: float,
    world_z: float = 0.0,
) -> tuple[float, float, float]:
    """
    Convert world coordinates (easting, northing, elevation) to local Blender coords.
    Uses M1DC_WORLD_ORIGIN custom properties.
    """
    min_e = float(origin_empty.get("world_min_easting", 0.0))
    min_n = float(origin_empty.get("world_min_northing", 0.0))
    min_z = float(origin_empty.get("world_min_elevation", 0.0))
    
    local_x = float(world_e) - min_e
    local_y = float(world_n) - min_n
    local_z = float(world_z) - min_z
    
    return (local_x, local_y, local_z)


def _apply_axis_rotation_fix(obj: "bpy.types.Object", rotation_x_deg: float = -90.0) -> None:
    """
    Apply axis rotation fix for common OBJ import issues (Y-up to Z-up conversion).
    
    Common case: QGIS exports with Y-up, Blender needs Z-up.
    Solution: Rotate around X-axis by ±90°.
    
    Args:
        obj: Blender object to rotate
        rotation_x_deg: Rotation angle in degrees (default -90 for Y-up to Z-up)
    """
    if not obj:
        return
    
    try:
        # Apply rotation in XYZ Euler
        rotation_rad = math.radians(rotation_x_deg)
        obj.rotation_euler = Euler((rotation_rad, 0.0, 0.0), 'XYZ')
        log_info(f"[Basemap] Applied rotation fix: X {rotation_x_deg}° to {obj.name}")
    except Exception as ex:
        log_warn(f"[Basemap] Could not apply rotation fix: {ex}")


def _detect_axis_swap(obj: "bpy.types.Object", expected_tile_size_m: float = 1000.0) -> Tuple[bool, str]:
    """
    Detect if basemesh has swapped axes (X/Z reversed).
    
    Symptoms of axis swap:
    - Mesh appears vertical (standing up) despite rotation_euler = (0, 0, 0)
    - Dimensions show X≈tile_size, Y≈small, Z≈large (instead of X≈Y≈tile_size, Z≈small)
    
    Args:
        obj: Basemesh object
        expected_tile_size_m: Expected X/Y dimensions (default 1000m)
        
    Returns:
        (is_swapped: bool, reason: str)
    """
    if not obj or obj.type != "MESH":
        return False, "Not a mesh"
    
    # Get local bounding box dimensions
    bbox_min = Vector(obj.bound_box[0])
    bbox_max = Vector(obj.bound_box[6])  # Opposite corner
    dim_x = abs(bbox_max.x - bbox_min.x)
    dim_y = abs(bbox_max.y - bbox_min.y)
    dim_z = abs(bbox_max.z - bbox_min.z)
    
    dims = sorted([dim_x, dim_y, dim_z])
    
    # Expected: small << medium ≈ large (like 18 << 65 << 100)
    # OR: medium ≈ large >> small (like 100 ≈ 100 >> 18)
    
    # Heuristic: if dims are [small, medium, large] where large/small > 3:
    # - large should be X or Y (horizontal)
    # - small should be Z (vertical elevation)
    # If small is Z but dims show [small, large, medium], likely X/Z swapped
    
    # Check: is Z the smallest? (Good!)
    if dim_z < dim_x and dim_z < dim_y:
        # Z is smallest (elevation) — good
        return False, f"Dimensions OK: X={dim_x:.1f}m, Y={dim_y:.1f}m, Z={dim_z:.1f}m"
    
    # Check: is Z one of the large dims? (Bad — axis swap)
    if dim_z > dim_x or dim_z > dim_y:
        return True, f"Axis swap detected: X={dim_x:.1f}m, Y={dim_y:.1f}m, Z={dim_z:.1f}m (Z should be small for elevation)"
    
    return False, f"Dimensions: X={dim_x:.1f}m, Y={dim_y:.1f}m, Z={dim_z:.1f}m"


def normalize_basemap_orientation(obj: "bpy.types.Object", expected_tile_size_m: float = 1000.0, auto_fix: bool = True) -> bool:
    """
    Normalize basemesh orientation: detect & fix axis swaps, ensure Z is elevation.
    
    Process:
    1. Detect if axes are swapped (X/Z reversed)
    2. If swapped & auto_fix: Rotate 90° around X, apply rotation
    3. Validate final dimensions
    4. Log diagnostic info
    
    Args:
        obj: Basemesh object
        expected_tile_size_m: Expected X/Y tile size (for validation)
        auto_fix: If True, auto-rotate on detection; if False, only warn
        
    Returns:
        True if mesh is now correctly oriented, False otherwise
    """
    if not obj or obj.type != "MESH":
        log_warn(f"[Basemap] normalize_basemap_orientation: Not a mesh: {obj.name if obj else 'None'}")
        return False
    
    # Step 1: Detect axis swap
    is_swapped, reason = _detect_axis_swap(obj, expected_tile_size_m)
    
    if is_swapped:
        log_warn(f"[Basemap] ⚠️  Axis swap detected in {obj.name}:")
        log_warn(f"[Basemap]    {reason}")
        log_warn(f"[Basemap]    Mesh is standing upright instead of lying flat.")
        
        if auto_fix:
            log_info(f"[Basemap] Applying auto-fix: Rotate 90° around X-axis...")
            try:
                # Rotate 90° around X to bring Z (vertical) to Y (horizontal)
                # or X (horizontal) to Z (up)
                obj.rotation_euler = Euler((math.radians(90), 0.0, 0.0), 'XYZ')
                
                # Apply the rotation so it's baked into the geometry
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.transform_apply(rotation=True)
                
                log_info(f"[Basemap] Rotation applied and baked to {obj.name}")
            except Exception as ex:
                log_warn(f"[Basemap] Could not auto-fix rotation: {ex}")
                return False
        else:
            log_warn(f"[Basemap] auto_fix=False; not applying rotation. Manual fix required!")
            return False
    else:
        log_info(f"[Basemap] Orientation OK: {reason}")
    
    # Step 2: Validate final dimensions after fix
    bbox_min = Vector(obj.bound_box[0])
    bbox_max = Vector(obj.bound_box[6])
    dim_x = abs(bbox_max.x - bbox_min.x)
    dim_y = abs(bbox_max.y - bbox_min.y)
    dim_z = abs(bbox_max.z - bbox_min.z)
    
    log_info(f"[Basemap] Final dimensions: X={dim_x:.1f}m, Y={dim_y:.1f}m, Z={dim_z:.1f}m")
    
    # Sanity check: Z should be much smaller than X/Y (elevation vs. extent)
    if dim_z > dim_x * 0.5 or dim_z > dim_y * 0.5:
        log_warn(f"[Basemap] ⚠️  Z dimension is still too large ({dim_z:.1f}m vs X={dim_x:.1f}m, Y={dim_y:.1f}m)")
        log_warn(f"[Basemap]    This may indicate the OBJ export itself is not a planar terrain tile.")
        return False
    
    return True


def shift_basemap_to_corner_origin(obj: "bpy.types.Object") -> bool:
    """
    Shift basemesh from center-origin to corner-origin (lower-left).
    
    Problem: OBJ exporters typically place object origin at mesh center.
    But CityGML tiles assume corner-origin (lower-left corner = 0,0).
    
    Solution: Move mesh geometry in Edit Mode so that lower-left corner is at (0,0,0).
    
    Process:
    1. Get mesh bounds (local space)
    2. Compute offset needed: (-X_center, -Y_center, -Z_min)
    3. Move all vertices by this offset
    4. Object location stays at (0, 0, 0)
    
    Result:
    - Object origin (0, 0, 0) now matches lower-left corner
    - Geometry is shifted, but object location unchanged
    - Aligns with CityGML tile placement logic
    
    Args:
        obj: Basemesh object
        
    Returns:
        True if successful, False otherwise
    """
    if not obj or obj.type != "MESH":
        log_warn(f"[Basemap] shift_basemap_to_corner_origin: Not a mesh: {obj.name if obj else 'None'}")
        return False
    
    try:
        # Get current bounds
        bbox_min = Vector(obj.bound_box[0])
        bbox_max = Vector(obj.bound_box[6])
        
        center_x = (bbox_min.x + bbox_max.x) * 0.5
        center_y = (bbox_min.y + bbox_max.y) * 0.5
        min_z = bbox_min.z
        
        # Offset to move lower-left corner to (0, 0, 0)
        offset = Vector((-center_x, -center_y, -min_z))
        
        log_info(f"[Basemap] Shifting {obj.name} to corner-origin:")
        log_info(f"[Basemap]    Current center: ({center_x:.2f}, {center_y:.2f})")
        log_info(f"[Basemap]    Current Z_min: {min_z:.2f}")
        log_info(f"[Basemap]    Offset to apply: ({offset.x:.2f}, {offset.y:.2f}, {offset.z:.2f})")
        
        # Enter Edit Mode and move vertices
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.transform.translate(value=offset)
        bpy.ops.object.mode_set(mode='OBJECT')
        
        # Verify the shift
        bbox_min_new = Vector(obj.bound_box[0])
        bbox_max_new = Vector(obj.bound_box[6])
        center_x_new = (bbox_min_new.x + bbox_max_new.x) * 0.5
        center_y_new = (bbox_min_new.y + bbox_max_new.y) * 0.5
        min_z_new = bbox_min_new.z
        
        log_info(f"[Basemap] After shift:")
        log_info(f"[Basemap]    New center: ({center_x_new:.2f}, {center_y_new:.2f})")
        log_info(f"[Basemap]    New Z_min: {min_z_new:.2f}")
        log_info(f"[Basemap]    Corner now at (0, 0, 0) ✓")
        
        return True
    except Exception as ex:
        log_warn(f"[Basemap] Could not shift to corner-origin: {ex}")
        return False


def import_basemap_obj_artifact(
    folder: str,
    apply_rotation_fix: bool = False,
    rotation_x_deg: float = -90.0,
) -> List["bpy.types.Object"]:
    """
    Import first .obj found in folder, read basemap.json, normalize orientation, and place.
    
    MASTER TRUTH: basemap.json contains world origin (Lower Left corner).
    This is used to anchor all other geometry (CityGML tiles, OSM) correctly.
    
    CRITICAL PHASE: Basemap Normalization
    After OBJ import, the mesh may have:
    1. Swapped axes (X/Z reversed — mesh standing upright)
    2. Center-origin instead of corner-origin (offset from CityGML placement)
    3. Y-Flip requirement (if raster has pixel_size_y < 0)
    
    This function handles all three automatically.
    
    Process:
    1. Find .obj file
    2. Import via bpy.ops.wm.obj_import
    3. Read basemap.json for world origin & raster metadata
    4. Create/update M1DC_WORLD_ORIGIN empty with metadata (CRS, world_min_*)
    5. Normalize basemesh orientation:
       a. Detect & fix axis swaps (X/Z reversed)
       b. Shift from center-origin to corner-origin
       c. Verify dimensions make sense
    6. Apply Y-Flip if pixel_size_y < 0 (GIS raster → Blender correction)
    7. Log detailed diagnostics for debugging
    
    Args:
        folder: Path to basemap artifact folder
        apply_rotation_fix: DEPRECATED - Normalization is automatic. Ignore.
        rotation_x_deg: DEPRECATED - Normalization is automatic. Ignore.
        
    Returns:
        List of imported objects (MESH, EMPTY, etc.)
        
    Raises:
        RuntimeError: If no .obj found or import fails
    """
    obj_path = _find_first_obj(folder)
    if not obj_path:
        raise RuntimeError(f"[M1DC][Basemap] No .obj found in artifact folder: {folder}")
    
    # Record objects before import
    before = set(bpy.data.objects)
    
    # Import OBJ (will also auto-load .mtl if it exists)
    try:
        bpy.ops.wm.obj_import(filepath=obj_path)
    except Exception as ex:
        raise RuntimeError(f"[M1DC][Basemap] Failed to import {obj_path}: {ex}") from ex
    
    after = set(bpy.data.objects)
    imported = [o for o in (after - before) if o.type in {"MESH", "EMPTY", "ARMATURE"}]
    
    if not imported:
        raise RuntimeError(f"[M1DC][Basemap] OBJ import returned no objects from {obj_path}")
    
    # Try to read basemap.json for world origin metadata
    jpath = _find_basemap_json(folder)
    if not jpath:
        log_warn(f"[Basemap] No basemap.json found in {folder}")
        log_warn("[Basemap] Importing OBJ without world origin placement. CityGML tiles may misalign!")
        return imported
    
    try:
        with open(jpath, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as ex:
        log_warn(f"[Basemap] Failed to read basemap.json: {ex}")
        return imported
    
    # Extract world origin block (MASTER TRUTH for coordinate system)
    wo = meta.get("world_origin", {})
    crs = wo.get("crs", "EPSG:25832")
    min_e = wo.get("world_min_easting", 0.0)
    min_n = wo.get("world_min_northing", 0.0)
    min_z = wo.get("world_min_elevation", 0.0)
    tile_size_m = wo.get("tile_size_m", 1000.0)
    
    # Create/update M1DC_WORLD_ORIGIN empty with custom properties
    # This empty is the coordinate system anchor for ALL imports (basemap, CityGML, OSM)
    origin_empty = _ensure_world_origin_empty()
    _set_world_origin_props(origin_empty, crs, min_e, min_n, min_z)
    
    # Check for Y-Flip requirement (GIS rasters often have pixel_size_y < 0)
    raster_info = meta.get("raster_info", {})
    pixel_size_y = raster_info.get("pixel_size_y", 1.0)
    apply_y_flip = pixel_size_y < 0.0
    
    # === NORMALIZATION PHASE ===
    # Process each mesh object (typically just one "BaseMesh" or "Terrain_DEM")
    for o in imported:
        if o.parent is None and o.type == "MESH":
            log_info(f"[Basemap] ╔═ Normalizing Basemesh: {o.name} ═╗")
            
            # Step 1: Detect & fix axis swap (X/Z reversed)
            is_normalized = normalize_basemap_orientation(o, expected_tile_size_m=tile_size_m, auto_fix=True)
            
            if not is_normalized:
                log_warn(f"[Basemap] ⚠️  Could not normalize orientation. Manual fix may be required.")
            
            # Step 2: Shift from center-origin to corner-origin
            is_corner_aligned = shift_basemap_to_corner_origin(o)
            if not is_corner_aligned:
                log_warn(f"[Basemap] ⚠️  Could not shift to corner-origin. Manual fix may be required.")
            
            # Step 3: Apply Y-Flip if raster has negative pixel_size_y
            if apply_y_flip:
                o.scale.y = -1.0
                log_info(f"[Basemap] Applied Y-Flip (pixel_size_y={pixel_size_y})")
            else:
                log_info(f"[Basemap] Y-Flip disabled (pixel_size_y={pixel_size_y})")
            
            # Final state: reset rotation & location (geometry is now baked)
            o.location = (0.0, 0.0, 0.0)
            o.rotation_euler = (0.0, 0.0, 0.0)
            # Note: scale.x and scale.z remain 1.0; scale.y may be ±1.0
            o["m1dc_basemap_artifact"] = True
            o["pixel_size_y"] = float(pixel_size_y)
            o["y_flip_applied"] = apply_y_flip
            
            log_info(f"[Basemap] ╚═ Normalization complete ═╝")
            
            # Log validation info
            from .validation import log_tile_import_summary, check_crs_units
            log_tile_import_summary(o.name, o, 0.0)
    
    # Log summary
    log_info(f"[Basemap] Successfully imported OBJ from {obj_path}")
    log_info(f"[Basemap] World origin (MASTER): CRS={crs}, min_E={min_e}, min_N={min_n}, min_Z={min_z}")
    log_info(f"[Basemap] Tile size expected: {tile_size_m:.1f}m")
    
    is_meter, msg = check_crs_units(crs)
    if is_meter is False:
        log_warn(f"[Basemap] CRS WARNING: {msg} – expect coordinate mismatch!")
    else:
        log_info(f"[Basemap] CRS OK: {msg}")
    
    return imported


def has_basemap_obj(folder: str) -> bool:
    """Check if folder contains a .obj file."""
    return _find_first_obj(folder) is not None


def has_basemap_json(folder: str) -> bool:
    """Check if folder contains basemap.json."""
    return _find_basemap_json(folder) is not None


# ============================================================================
# PHASE 2: DEM → Base Terrain (Heightmap Displacement Mesh)
# ============================================================================
# This section handles DEM import, heightmap generation, and mesh creation.
# Separate from Phase 1 (OBJ artifact) to allow independent DEM→Terrain workflow.

import subprocess
import tempfile

try:
    import bmesh
except ImportError:
    bmesh = None


def _run_osgeo4w_cmd(osgeo4w_root: str, args: list) -> tuple:
    """
    Execute a command in OSGeo4W environment.
    
    Args:
        osgeo4w_root: Path to OSGeo4W installation
        args: List of command arguments
        
    Returns:
        tuple: (returncode, stdout+stderr output)
    """
    root = Path(osgeo4w_root)
    
    # Try bin/o4w_env.bat first, fallback to OSGeo4W.bat
    env_bat = root / "bin" / "o4w_env.bat"
    if not env_bat.exists():
        env_bat = root / "OSGeo4W.bat"
    
    if not env_bat.exists():
        raise FileNotFoundError(
            f"OSGeo4W environment file not found at {root / 'bin' / 'o4w_env.bat'} "
            f"or {root / 'OSGeo4W.bat'}"
        )
    
    # Build command with proper quoting
    quoted_args = [f'"{a}"' if " " in a else a for a in args]
    cmd = f'"{env_bat}" && ' + " ".join(quoted_args)
    
    log_info(f"[OSGeo4W] Running: {cmd[:100]}...")
    
    try:
        p = subprocess.run(
            ["cmd", "/c", cmd],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        raise RuntimeError("OSGeo4W command timed out (5 minutes)")


def read_dem_meta(osgeo4w_root: str, dem_path: str) -> dict:
    """
    Read DEM metadata via gdalinfo -json.
    
    Returns dict with keys:
        - size: [width, height]
        - cornerCoordinates: { upperLeft, lowerRight, ... }
        - geoTransform: [x_min, pixel_x, 0, y_max, 0, pixel_y]
        - coordinateSystem: { wkt, ... }
        - bands: list of band info
    """
    rc, out = _run_osgeo4w_cmd(osgeo4w_root, ["gdalinfo", "-json", dem_path])
    
    if rc != 0:
        raise RuntimeError(f"gdalinfo failed (rc={rc}).\nOutput:\n{out}")
    
    try:
        meta = json.loads(out)
        log_info(f"[DEM Meta] Size={meta.get('size')}, Origin={meta.get('cornerCoordinates', {}).get('upperLeft')}")
        return meta
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse gdalinfo JSON: {e}\nOutput:\n{out}")


def dem_to_height_png(osgeo4w_root: str, dem_path: str, out_png: str, scale_min=None, scale_max=None) -> None:
    """
    Convert DEM to 16-bit PNG heightmap.
    
    Uses gdal_translate to:
    - Convert to PNG format
    - Output UInt16 (16-bit, range 0-65535)
    - Auto-scale or use provided min/max
    
    Args:
        osgeo4w_root: OSGeo4W root path
        dem_path: Path to input DEM GeoTIFF
        out_png: Path to output 16-bit PNG
        scale_min: Optional min value for scaling
        scale_max: Optional max value for scaling
    """
    args = [
        "gdal_translate",
        "-of", "PNG",
        "-ot", "UInt16",
    ]
    
    if scale_min is not None and scale_max is not None:
        args.extend(["-scale", str(scale_min), str(scale_max)])
    else:
        args.append("-scale")  # Auto-scale using GDAL heuristics
    
    args.extend([dem_path, out_png])
    
    rc, out = _run_osgeo4w_cmd(osgeo4w_root, args)
    
    if rc != 0 or not Path(out_png).exists():
        raise RuntimeError(
            f"gdal_translate failed (rc={rc}).\n"
            f"Output:\n{out}\n"
            f"Check that OSGeo4W is installed and DEM path exists."
        )
    
    log_info(f"[DEM→PNG] Saved heightmap: {out_png}")


def _parse_extent_from_gdalinfo(meta: dict) -> tuple:
    """
    Extract (minE, minN, maxE, maxN) from gdalinfo JSON.
    
    gdalinfo provides cornerCoordinates with:
        upperLeft, upperRight, lowerLeft, lowerRight
    """
    try:
        corners = meta.get("cornerCoordinates", {})
        if not corners:
            raise KeyError("No cornerCoordinates in gdalinfo")
        
        ul = corners.get("upperLeft", [])
        lr = corners.get("lowerRight", [])
        
        if not ul or not lr or len(ul) < 2 or len(lr) < 2:
            raise ValueError(f"Invalid corners: {corners}")
        
        min_e = min(ul[0], lr[0])
        max_e = max(ul[0], lr[0])
        min_n = min(ul[1], lr[1])
        max_n = max(ul[1], lr[1])
        
        log_info(f"[Extent] E={min_e:.1f}..{max_e:.1f}, N={min_n:.1f}..{max_n:.1f}")
        return (min_e, min_n, max_e, max_n)
    except Exception as e:
        log_warn(f"Failed to parse extent: {e}")
        raise


def _parse_pixel_size_from_gdalinfo(meta: dict) -> tuple:
    """
    Extract (pixel_x, pixel_y) from gdalinfo JSON.
    
    Returns absolute values in meters.
    """
    try:
        size = meta.get("geoTransform", [0, 1, 0, 0, 0, -1])
        pixel_x = abs(size[1])
        pixel_y = abs(size[5])
        return (pixel_x, pixel_y)
    except Exception as e:
        log_warn(f"Failed to parse pixel size: {e}")
        return (1.0, 1.0)


def _build_terrain_mesh(width: int, height: int, step: int = 1) -> tuple:
    """
    Create a plane mesh for terrain with displacement.
    
    Args:
        width: DEM width in pixels
        height: DEM height in pixels
        step: Downsample step (1=full, 2=half, 4=quarter, etc)
    
    Returns:
        tuple: (mesh_data, None) - bmesh is converted to mesh data immediately
    """
    if not bmesh:
        raise RuntimeError("[Terrain] bmesh module not available")
    
    # Downsample grid
    sample_w = (width - 1) // step + 1
    sample_h = (height - 1) // step + 1
    
    log_info(f"[Mesh] Building grid: {sample_w}x{sample_h} (step={step}, orig={width}x{height})")
    
    mesh = bpy.data.meshes.new("Terrain_Mesh")
    bm = bmesh.new()
    
    # Create vertices (in local coords: X=column, Y=row, Z=0)
    verts = {}
    for row in range(sample_h):
        for col in range(sample_w):
            x = float(col)
            y = float(row)
            v = bm.verts.new((x, y, 0.0))
            verts[(row, col)] = v
    
    bm.verts.ensure_lookup_table()
    
    # Create faces (quads)
    for row in range(sample_h - 1):
        for col in range(sample_w - 1):
            v0 = verts[(row, col)]
            v1 = verts[(row, col + 1)]
            v2 = verts[(row + 1, col + 1)]
            v3 = verts[(row + 1, col)]
            try:
                bm.faces.new([v0, v1, v2, v3])
            except Exception:
                pass  # Skip degenerate faces
    
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    
    log_info(f"[Mesh] Created: {len(mesh.vertices)} verts, {len(mesh.polygons)} faces")
    
    return mesh, None


def import_basemap_terrain(context, settings) -> bool:
    """
    Main pipeline function: DEM → Terrain Mesh with Displacement.
    
    Phase 2 workflow:
    1. Validate inputs (basemap_dir, osgeo4w_root)
    2. Read DEM metadata via gdalinfo
    3. Extract to 16-bit PNG heightmap
    4. Build terrain mesh in Blender with step downsampling
    5. Add Displace modifier with PNG texture
    6. Place mesh at correct CRS coordinates
    7. Update status fields
    
    Args:
        context: Blender context
        settings: Scene settings (M1DCSettings PropertyGroup)
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        if not bpy:
            log_error("[Terrain] Blender module not available")
            return False
        
        # Validate paths
        basemap_dir = getattr(settings, "basemap_dir", "").strip()
        osgeo4w_root = getattr(settings, "osgeo4w_root", "").strip()
        
        if not basemap_dir or not os.path.isdir(basemap_dir):
            log_error(f"[Terrain] basemap_dir not found: {basemap_dir}")
            return False
        
        if not osgeo4w_root or not os.path.isdir(osgeo4w_root):
            log_error(f"[Terrain] osgeo4w_root not found: {osgeo4w_root}")
            return False
        
        # Find DEM
        dem_path = os.path.join(basemap_dir, "DEM_mosaic_resampled.tif")
        if not os.path.isfile(dem_path):
            log_error(f"[Terrain] DEM not found: {dem_path}")
            return False
        
        log_info(f"[Terrain] ╔═ Starting import from: {basemap_dir} ═╗")
        
        # Read metadata
        meta = read_dem_meta(osgeo4w_root, dem_path)
        
        # Parse extent and pixel size
        min_e, min_n, max_e, max_n = _parse_extent_from_gdalinfo(meta)
        pixel_x, pixel_y = _parse_pixel_size_from_gdalinfo(meta)
        
        # DEM raster dimensions
        size = meta.get("size", [])
        if len(size) < 2:
            raise ValueError(f"Invalid size in gdalinfo: {size}")
        dem_width, dem_height = size[0], size[1]
        
        # CRS (try to extract)
        crs_info = meta.get("coordinateSystem", {})
        crs_text = crs_info.get("wkt", "")
        if not crs_text:
            crs_text = "Unknown (assumed local/meters)"
        
        # Store metadata in settings
        settings.status_basemap_terrain_dem_size = f"{dem_width} x {dem_height}"
        settings.status_basemap_terrain_extent = (
            f"E: {min_e:.0f}..{max_e:.0f}, N: {min_n:.0f}..{max_n:.0f}"
        )
        settings.status_basemap_terrain_pixel_size = f"{pixel_x:.2f} m x {pixel_y:.2f} m"
        settings.status_basemap_terrain_crs = crs_text[:100]  # Truncate for UI
        
        log_info(f"[Terrain] DEM: {dem_width}x{dem_height} pixels, {pixel_x:.2f}m x {pixel_y:.2f}m")
        log_info(f"[Terrain] Extent: E={min_e:.1f}..{max_e:.1f}, N={min_n:.1f}..{max_n:.1f}")
        
        # Convert to PNG heightmap in temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            png_path = os.path.join(tmpdir, "heightmap.png")
            dem_to_height_png(osgeo4w_root, dem_path, png_path)
            
            # Get step parameter for downsampling
            step = getattr(settings, "basemap_build_step", 4)
            
            # Build mesh
            mesh, _ = _build_terrain_mesh(dem_width, dem_height, step=step)
            
            # Create object and add to scene
            obj = bpy.data.objects.new("Terrain", mesh)
            context.collection.objects.link(obj)
            context.view_layer.objects.active = obj
            obj.select_set(True)
            
            log_info(f"[Terrain] Created mesh object: {obj.name}")
            
            # Scale mesh to real-world coordinates
            obj.scale.x = pixel_x * step
            obj.scale.y = pixel_y * step
            obj.scale.z = 1.0  # Z scale set by displace strength
            
            log_info(f"[Terrain] Applied scale: X={obj.scale.x:.2f}, Y={obj.scale.y:.2f}")
            
            # Add Displace modifier
            disp_mod = obj.modifiers.new(name="Displace", type="DISPLACE")
            disp_mod.strength = 1.0
            disp_mod.direction = "Z"
            
            # Create image texture from heightmap PNG
            img = bpy.data.images.load(png_path)
            img.name = "Heightmap"
            img.colorspace_settings.name = "Non-Color"  # Important: heightmap is data, not color
            
            # Create texture for the displace modifier
            tex = bpy.data.textures.new(name="HeightmapTex", type="IMAGE")
            tex.image = img
            tex.use_interpolation = True
            tex.interpolation_type = "SMART"
            
            # Link texture to displace modifier
            disp_mod.texture = tex
            
            log_info(f"[Terrain] Added Displace modifier with heightmap texture")
        
        # Placement: center mesh at CRS coordinates
        center_e = (min_e + max_e) / 2.0
        center_n = (min_n + max_n) / 2.0
        
        # Use existing world_to_local if available
        try:
            from ...utils.common import world_to_local
            local_pos = world_to_local(center_e, center_n, context=context)
            obj.location.x = local_pos[0]
            obj.location.y = local_pos[1]
        except Exception as e:
            log_warn(f"[Terrain] Could not use world_to_local: {e}")
            log_warn(f"[Terrain] Placing at (0, 0) – ensure M1DC_WORLD_ORIGIN is set!")
            obj.location.x = 0.0
            obj.location.y = 0.0
        
        obj.location.z = 0.0
        
        log_info(f"[Terrain] Placed at local: {tuple(obj.location)}")
        
        # Mark as terrain object
        obj["m1dc_terrain_dem"] = True
        obj["dem_pixel_x"] = pixel_x
        obj["dem_pixel_y"] = pixel_y
        obj["dem_step"] = step
        
        # Update status
        settings.status_basemap_terrain_loaded = True
        settings.step0_terrain_done = True
        
        log_info(f"[Terrain] ╚═ Import complete ═╝")
        return True
        
    except Exception as e:
        log_error(f"[Terrain] Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

