"""Geometry utilities for WKB parsing, spatial operations, and coordinate transformations.

This module provides low-level geometry functions used throughout the pipeline:
- WKB (Well-Known Binary) parsing for GeoPackage geometries
- Spatial predicates (point-in-polygon, ring area, distance calculations)
- Bounding box utilities for Blender objects

Migrated from ops.py (Phase 13, 2026-02-08) to consolidate geometry operations.
"""

import struct
from math import inf, sqrt
from mathutils import Vector


# ============================================================================
# WKB (Well-Known Binary) Parsing for GeoPackage
# ============================================================================

def extract_wkb_from_gpkg(blob: bytes) -> bytes:
    """Strip GeoPackage header, return inner WKB bytes.
    
    GeoPackage stores geometries with a custom header before the standard WKB data.
    This function strips that header and returns the raw WKB bytes.
    
    Args:
        blob: Raw GeoPackage geometry blob
        
    Returns:
        WKB bytes (without GeoPackage envelope)
    """
    if not blob:
        return b""
    mv = memoryview(blob)
    if len(mv) < 8:
        return bytes(blob)
    if mv[0:2].tobytes() != b"GP":
        return bytes(blob)
    flags = mv[3]
    envelope_indicator = flags & 0x0F
    offset = 8
    envelope_len = {0: 0, 1: 32, 2: 48, 3: 64, 4: 48}.get(envelope_indicator, 0)
    offset += envelope_len
    return mv[offset:].tobytes()


def read_uint32(mv, idx, endian):
    """Read 32-bit unsigned integer from memoryview at given index.
    
    Args:
        mv: memoryview of WKB data
        idx: Byte offset to read from
        endian: '<' for little-endian, '>' for big-endian
        
    Returns:
        uint32 value
    """
    return struct.unpack_from(f"{endian}I", mv, idx)[0]


def parse_wkb_polygon(mv: memoryview, idx: int, endian: str):
    """Parse a WKB Polygon geometry.
    
    A polygon consists of 1+ rings (first is outer, rest are holes).
    Each ring is a closed linestring (list of (x, y) points).
    
    Args:
        mv: memoryview of WKB data
        idx: Current byte offset
        endian: '<' for little-endian, '>' for big-endian
        
    Returns:
        (rings, new_idx) where rings is list of lists of (x, y) tuples
    """
    rings = []
    num_rings = read_uint32(mv, idx, endian)
    idx += 4
    for _ in range(num_rings):
        num_points = read_uint32(mv, idx, endian)
        idx += 4
        pts = []
        for _ in range(num_points):
            x, y = struct.unpack_from(f"{endian}dd", mv, idx)
            idx += 16
            pts.append((x, y))
        rings.append(pts)
    return rings, idx


def parse_wkb_geoms(data: bytes):
    """Parse WKB geometry data (Polygon or MultiPolygon).
    
    Returns list of polygons, each polygon is a list of rings (outer + holes).
    
    Args:
        data: WKB bytes (typically after stripping GeoPackage header)
        
    Returns:
        List of polygons, where each polygon is:
        [
            [(x1,y1), (x2,y2), ...],  # outer ring
            [(x1,y1), (x2,y2), ...],  # hole 1 (optional)
            ...
        ]
    """
    if not data:
        return []
    mv = memoryview(data)
    idx = 0
    if len(mv) < 5:
        return []
    endian_flag = mv[0]
    endian = "<" if endian_flag == 1 else ">"
    geom_type = read_uint32(mv, 1, endian)
    idx = 5

    polygons = []
    if geom_type == 3:  # Polygon
        rings, _ = parse_wkb_polygon(mv, idx, endian)
        polygons.append(rings)
    elif geom_type == 6:  # MultiPolygon
        num_polys = read_uint32(mv, idx, endian)
        idx += 4
        for _ in range(num_polys):
            if idx >= len(mv):
                break
            endian_flag = mv[idx]
            endian = "<" if endian_flag == 1 else ">"
            p_type = read_uint32(mv, idx + 1, endian)
            idx += 5
            if p_type != 3:
                break
            rings, idx = parse_wkb_polygon(mv, idx, endian)
            polygons.append(rings)
    else:
        return []
    return polygons


# ============================================================================
# Spatial Predicates & Distance Calculations
# ============================================================================

def ring_area(ring):
    """Calculate signed area of a ring (positive if CCW, negative if CW).
    
    Uses the shoelace formula.
    
    Args:
        ring: List of (x, y) tuples
        
    Returns:
        Signed area (positive = CCW outer ring, negative = CW hole)
    """
    if len(ring) < 3:
        return 0.0
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def point_in_ring(pt, ring):
    """Ray-casting algorithm for point-in-ring test.
    
    Args:
        pt: (x, y) tuple
        ring: List of (x, y) tuples (closed ring)
        
    Returns:
        True if point is inside ring
    """
    x, y = pt
    inside = False
    n = len(ring)
    if n < 3:
        return False
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1):
            inside = not inside
    return inside


def point_in_polygon(pt, rings):
    """Test if point is inside a polygon (outer ring - holes).
    
    Args:
        pt: (x, y) tuple
        rings: List of rings, first is outer, rest are holes
        
    Returns:
        True if point is inside outer ring and not in any hole
    """
    if not rings:
        return False
    outer = rings[0]
    if not point_in_ring(pt, outer):
        return False
    # Check holes
    for hole in rings[1:]:
        if point_in_ring(pt, hole):
            return False
    return True


def point_segment_dist_sq(px, py, x1, y1, x2, y2):
    """Squared distance from point to line segment.
    
    Uses parametric form with clamping to segment endpoints.
    
    Args:
        px, py: Point coordinates
        x1, y1: Segment start
        x2, y2: Segment end
        
    Returns:
        Squared distance (avoids sqrt for performance)
    """
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return (px - x1) ** 2 + (py - y1) ** 2
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = x1 + t * dx
    cy = y1 + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def ring_min_dist_sq(pt, ring):
    """Minimum squared distance from point to any segment in ring.
    
    Args:
        pt: (x, y) tuple
        ring: List of (x, y) tuples
        
    Returns:
        Minimum squared distance to ring boundary
    """
    px, py = pt
    mind = inf
    n = len(ring)
    if n < 2:
        return mind
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        d = point_segment_dist_sq(px, py, x1, y1, x2, y2)
        if d < mind:
            mind = d
    return mind


# ============================================================================
# Blender Object Bounding Box Utilities
# ============================================================================

def bbox_world_minmax_xy(obj):
    """Compute bounding box in world space (actual geometry position).
    
    Transforms object's bounding box corners to world space and finds min/max.
    
    Args:
        obj: Blender object (must be MESH type)
        
    Returns:
        (min_x, min_y, max_x, max_y) in world coordinates (meters)
    """
    if obj is None or obj.type != 'MESH':
        return (0.0, 0.0, 0.0, 0.0)

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    for c in obj.bound_box:
        wc = obj.matrix_world @ Vector(c)
        min_x = min(min_x, wc.x)
        max_x = max(max_x, wc.x)
        min_y = min(min_y, wc.y)
        max_y = max(max_y, wc.y)

    return (min_x, min_y, max_x, max_y)


def detect_dem_placement_mode(dem_obj: object) -> str:
    """Detect if DEM is in global (georeferenced) or local coordinates.
    
    Uses bounding box heuristic to determine coordinate system.
    This fixes issues where object origin is at (0,0,0) but vertices are at global UTM coords.
    
    Args:
        dem_obj: Blender mesh object (DEM terrain)
        
    Returns:
        "GLOBAL_BBOX" - Bbox coords look like UTM meters (>1e6)
        "GLOBAL_LIKELY" - Bbox coords are large but not quite UTM (>1e5)
        "LOCAL" - Bbox coords are reasonable local values
        "UNKNOWN" - Cannot determine
    """
    if dem_obj is None or dem_obj.type != 'MESH':
        return "UNKNOWN"

    try:
        min_x, min_y, max_x, max_y = bbox_world_minmax_xy(dem_obj)
        max_coord = max(abs(min_x), abs(min_y), abs(max_x), abs(max_y))

        # UTM-like coordinates (e.g., EPSG:25832 ~ 3e7 m)
        if max_coord > 1e6:
            return "GLOBAL_BBOX"

        # Large but not quite UTM (safety margin)
        if max_coord > 1e5:
            return "GLOBAL_LIKELY"

        # Reasonable local coordinates
        return "LOCAL"

    except Exception:
        return "UNKNOWN"


# ============================================================================
# Coordinate Transformation Utilities
# ============================================================================

def localize_mesh_data_to_world_min(obj, world_min_e, world_min_n, flip_northing=False):
    """Localize mesh geometry by translating vertices in object space.
    
    This fixes issues where BlenderGIS imports create an object at (0,0,0)
    but the vertices themselves are at global UTM coordinates.
    
    Strategy:
    - Compute translation delta: dx = -world_min_e, dy = -world_min_n
    - Apply matrix transformation to mesh data (not object location)
    - Keep object location at origin (0,0,0)
    
    Args:
        obj: Mesh object to localize
        world_min_e: World origin easting (meters, to subtract)
        world_min_n: World origin northing (meters, to subtract)
        flip_northing: If True, negate Y after translation (NOT CURRENTLY USED)
        
    Returns:
        ("mesh_translate", dx, dy) on success
        ("ERROR", 0, 0) on failure
    """
    import bpy
    from mathutils import Matrix

    if obj is None or obj.type != 'MESH':
        return ("ERROR", 0.0, 0.0)

    try:
        dx = -float(world_min_e)
        dy = -float(world_min_n)

        # Ensure object is at origin before transforming mesh data
        # (this avoids double-offset from object location + mesh data)
        obj_loc_before = obj.location.copy()
        obj.location = (0.0, 0.0, obj.location.z)
        bpy.context.view_layer.update()

        # Transform mesh data in object local space
        T = Matrix.Translation((dx, dy, 0.0))
        obj.data.transform(T)
        obj.data.update()

        # Keep object at origin
        obj.location = (0.0, 0.0, obj.location.z)
        bpy.context.view_layer.update()

        # logging would go here if we had a logger import
        # For now, return success tuple
        return ("mesh_translate", dx, dy)

    except Exception as e:
        return ("ERROR", 0.0, 0.0)


# ============================================================================
# Color & Viewport Utilities
# ============================================================================

def hash_color(name: str, seed: int = 1337) -> tuple:
    """Generate deterministic pastel color from object name using MD5 hash.
    
    Useful for assigning unique colors to tiles/objects based on their name,
    making it easy to visually distinguish them in viewport.
    
    Args:
        name: Object name or identifier
        seed: Seed for reproducibility
        
    Returns:
        (r, g, b, a) tuple with values in [0, 1], pastel range [0.35, 0.9]
    """
    import hashlib
    
    try:
        h = hashlib.md5((str(seed) + str(name)).encode("utf-8")).hexdigest()
        
        # Extract RGB components from hex
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        
        # Pastel clamp: [0, 1] → [0.35, 0.9] (brighter, more pastel)
        r = 0.35 + 0.55 * r
        g = 0.35 + 0.55 * g
        b = 0.35 + 0.55 * b
        
        return (r, g, b, 1.0)
    
    except Exception:
        # Fallback: orange
        return (0.85, 0.35, 0.1, 1.0)


def apply_viewport_solid_cavity(enable: bool = True) -> bool:
    """Set viewport shading to Solid with Cavity ON (if available).
    
    Cavity shading helps visualize tile edges and boundaries in viewport.
    Gracefully handles older Blender versions that don't support cavity shading.
    
    Args:
        enable: If True, enable cavity shading; if False, disable it
        
    Returns:
        True if successful, False if feature not available
    
    Note:
        Version-dependent; gracefully fails on older Blender versions.
    """
    import bpy
    
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        # Set shading mode to Solid
                        space.shading.type = 'SOLID'
                        
                        # Enable cavity (version-dependent)
                        try:
                            space.shading.use_cavity = enable
                        except AttributeError:
                            pass  # Not available in this Blender version
                        
                        # Try to enable shadows
                        try:
                            space.shading.use_shadows = True
                        except AttributeError:
                            pass  # Not all versions support this
        
        return True
    
    except Exception:
        return False
