import bpy
import json
from bpy.types import PropertyGroup
from bpy.props import (
    StringProperty,
    BoolProperty,
    FloatProperty,
    IntProperty,
    CollectionProperty,
    EnumProperty,
)


def _on_spreadsheet_table_changed(self, context):
    """Callback when table selection changes: rebuild columns and rows atomically."""
    try:
        from . import ops
        ops.spreadsheet_invalidate_and_rebuild(context, self, reason="table_changed")
    except Exception:
        pass


def _on_inspector_preset_changed(self, context):
    """When inspector query preset changes, auto-fill query text."""
    preset_queries = {
        "UNIV": "amenity=university",
        "SCHOOL": "amenity=school",
        "HOSPITAL": "amenity=hospital",
        "SHOP": "shop",
        "RESIDENTIAL": "building=residential",
        "COMMERCIAL": "building=commercial",
        "AMENITY_ANY": "amenity",
    }
    preset = getattr(self, "inspector_query_preset", "CUSTOM")
    if preset != "CUSTOM" and preset in preset_queries:
        self.inspector_query_text = preset_queries[preset]


def _on_gpkg_path_changed(self, context):
    """When the GPKG path changes, refresh table/column caches outside draw()."""
    try:
        # Accept either a direct .gpkg file path or a directory containing *.gpkg.
        from .utils.common import resolve_gpkg_path, log_gpkg_resolution
        raw = getattr(self, "gpkg_path", "")
        resolved, info = resolve_gpkg_path(raw)
        log_gpkg_resolution(raw, resolved, info, prefix="[Settings][GPKG]")
        if resolved and resolved != raw:
            # Avoid infinite recursion if Blender re-triggers update; only set when changed.
            try:
                self.gpkg_path = resolved
            except Exception:
                pass

        from . import ops
        ops.spreadsheet_refresh_tables_only(self, reset_selection=True)
    except Exception:
        pass


def _on_building_row_selected(self, context):
    try:
        scene = getattr(context, "scene", None)
        s = getattr(scene, "m1dc_settings", None)
        if s is None or getattr(s, "spreadsheet_silent", False):
            return
        bpy.ops.m1dc_spreadsheet.select_row(
            "INVOKE_DEFAULT",
            building_idx=self.building_idx,
            source_tile=self.source_tile,
            value=self.selected,
        )
    except Exception:
        return
class M1DCBuildingRow(PropertyGroup):
    source_tile: StringProperty(name="Source Tile", default="")
    building_idx: IntProperty(name="Building Index", default=-1)
    citygml_centroid: StringProperty(name="CityGML Centroid", default="—")
    link_conf: FloatProperty(name="Link Confidence", default=0.0)
    osm_centroid: StringProperty(name="OSM Centroid", default="—")
    osm_id: StringProperty(name="OSM ID", default="—")
    attrs_json: StringProperty(name="Attrs JSON", default="{}", options={"HIDDEN"})
    selected: BoolProperty(name="Selected", default=False, update=_on_building_row_selected)


class M1DCColumnOption(PropertyGroup):
    name: StringProperty(name="Name", default="")
    selected: BoolProperty(name="Selected", default=False)


class M1DCDecodedAttrRow(PropertyGroup):
    """Row for decoded face attribute display in Semantic Inspector."""
    attr_name: StringProperty(name="Attribute", default="")
    code_value: IntProperty(name="Code", default=0)
    decoded_value: StringProperty(name="Decoded", default="")


class M1DCSettings(PropertyGroup):
    # -------- INPUTS --------
    citygml_dir: StringProperty(
        name="CityGML (Tiles / .gml)",
        subtype="DIR_PATH",
        default="",
    )

    gpkg_importer_script: StringProperty(
        name="GpkgImporter (Script_Version) (optional)",
        subtype="FILE_PATH",
        default="",
    )

    gpkg_path: StringProperty(
        name="GeoPackage (OSM / .gpkg)",
        subtype="FILE_PATH",
        default="",
        update=_on_gpkg_path_changed,
    )

    map_importer_script: StringProperty(
        name="MapImporter (Script_Version) (optional)",
        subtype="FILE_PATH",
        default="",
    )

    citygml_importer_script: StringProperty(
        name="CityGMLImporter (Script_Version) (optional)",
        subtype="FILE_PATH",
        default="",
    )

    # NEW: Preferred single-root terrain input (Phase 1 - Prepared Terrain)
    terrain_root_dir: StringProperty(
        name="Terrain (Prepared Dataset)",
        description=(
            "Root folder for prepared terrain dataset. Must contain:\n"
            "  DGM_Tiles/      (DEM GeoTIFF tiles)\n"
            "  RGB_Tiles/derived/  (RGB GeoTIFF tiles, 1.0m/pixel recommended)\n"
            "Pipeline validates this structure before import."
        ),
        subtype="DIR_PATH",
        default="",
    )

    # DEPRECATED: Old split-folder terrain input (kept for backwards compatibility)
    terrain_source_dir: StringProperty(
        name="[DEPRECATED] Terrain Source Folder",
        description="[DEPRECATED] Use terrain_root_dir instead. Kept for backwards compatibility only.",
        subtype="DIR_PATH",
        default="",
        options={"HIDDEN"},
    )

    terrain_dgm_dir: StringProperty(
        name="[DEPRECATED] Terrain DGM Source",
        description="[DEPRECATED] Use terrain_root_dir instead. Kept for backwards compatibility only.",
        subtype="DIR_PATH",
        default="",
        options={"HIDDEN"},
    )

    terrain_rgb_dir: StringProperty(
        name="[DEPRECATED] Terrain RGB Source",
        description="[DEPRECATED] Use terrain_root_dir instead. Kept for backwards compatibility only.",
        subtype="DIR_PATH",
        default="",
        options={"HIDDEN"},
    )

    # NEW: Prebuilt textured OBJ terrain import (Phase 0 - Authoritative Artifact)
    terrain_obj_artifact_dir: StringProperty(
        name="Terrain OBJ Artifact Folder",
        description="Folder containing prebuilt terrain OBJ + MTL + textures. If set, raster DGM/RGB inputs are ignored.",
        subtype="DIR_PATH",
        default="",
    )

    basemap_dir: StringProperty(
        name="Basemap Folder (_Merged) [INTERNAL]",
        description="[INTERNAL] Path to processed terrain output. Do not edit manually.",
        subtype="DIR_PATH",
        default="",
    )

    # PHASE 3: DGM terrain import settings
    dgm_artifact_dir: StringProperty(
        name="DGM Artifact Folder",
        description="[Optional] Path to folder containing dem_merged.obj artifact. If not set, uses basemap_dir.",
        subtype="DIR_PATH",
        default="",
    )

    dgm_tile_size_m: FloatProperty(
        name="DGM Tile Size (meters)",
        description="Tile size in meters for DGM CSV fallback (default 1000m = 1km)",
        default=1000.0,
        min=1.0,
        max=10000.0,
    )

    osgeo4w_root: StringProperty(
        name="OSGeo4W Root",
        description="Path to OSGeo4W install (contains bin/o4w_env.bat or OSGeo4W.bat)",
        subtype="DIR_PATH",
        default=r"C:\Users\Akhai\AppData\Local\Programs\OSGeo4W",
    )

    basemap_build_step: IntProperty(
        name="Terrain Grid Step",
        description="Downsample grid (1=full res, 2=half, 4=quarter). Keep >1 for huge DEMs to avoid memory issues.",
        default=4,
        min=1,
        max=64,
    )

    basemap_use_rgb: BoolProperty(
        name="Apply RGB Texture",
        description="If true and RGB_mosaic.tif exists, apply it as texture",
        default=True,
    )

    terrain_dem_step: IntProperty(
        name="DEM Import Step Override",
        description="Control DEM mesh decimation: 0=auto-heuristic, 1-32=manual. Larger=fewer vertices, faster import. Example: step=8 decimates 900MP->14MP. Set manually if auto-heuristic causes issues.",
        default=0,
        min=0,
        max=32,
    )

    use_terrain_cache: BoolProperty(
        name="Cache Terrain",
        description="If true, save/load terrain to/from cache folder to avoid rebuilding on each pipeline run",
        default=True,
    )

    terrain_cache_folder: StringProperty(
        name="[DEPRECATED] Terrain Cache Folder",
        description="[DEPRECATED] Cache location is derived from Output Directory. This field is ignored.",
        subtype="DIR_PATH",
        default="",
        options={"HIDDEN"},
    )

    # -------- Metadata-based CRS Placement (CSV/World Origin) --------
    terrain_tiles_csv: StringProperty(
        name="Terrain Tiles CSV",
        description="Optional CSV file with tile extents (columns: filename/tile, easting/x, northing/y, tile_size_m). Used to compute WORLD_ORIGIN deterministically.",
        subtype="FILE_PATH",
        default="",
    )

    tile_size_m: FloatProperty(
        name="Tile Size (m)",
        description="Tile size in meters. Auto-detected from CSV if present; fallback to 8000m. Used for BBox-center-delta alignment.",
        default=8000.0,
        min=100.0,
        max=100000.0,
    )

    world_min_e: FloatProperty(
        name="World Min Easting (m)",
        description="[READ-ONLY] Minimum easting (world origin from CSV/CityGML). Cached from last successful import.",
        default=0.0,
        options={"SKIP_SAVE"},
    )

    world_min_n: FloatProperty(
        name="World Min Northing (m)",
        description="[READ-ONLY] Minimum northing (world origin from CSV/CityGML). Cached from last successful import.",
        default=0.0,
        options={"SKIP_SAVE"},
    )

    crs_detected: StringProperty(
        name="CRS Detected",
        description="[READ-ONLY] CRS inferred from settings or CSV header (e.g., EPSG:25832)",
        default="EPSG:25832",
        options={"SKIP_SAVE"},
    )

    flip_northing: BoolProperty(
        name="Flip Northing (Y-axis)",
        description="If true, apply Y = -(northing - world_min_n) for coordinate transformation. Usually false for UTM Zone 32N.",
        default=False,
    )

    set_viewport_solid_with_cavity: BoolProperty(
        name="Viewport Cavity Shading (Tile Edges)",
        description="If true, set viewport shading to Solid mode with Cavity ON for better tile edge visibility. CityGML tiles are assigned random pastel colors.",
        default=True,
    )

    output_dir: StringProperty(
        name="Output Directory (optional)",
        subtype="DIR_PATH",
        default="",
    )

    links_db_path: StringProperty(
        name="Links DB (internal)",
        description="Path to the latest generated gml_osm_links SQLite file",
        default="",
        options={"HIDDEN"},
    )

    mkdb_path: StringProperty(
        name="mkdb Path (internal)",
        description="Path to the latest generated semantic snapshot (mkdb) SQLite file",
        default="",
        options={"HIDDEN"},
    )

    auto_clip: BoolProperty(name="Viewport Clipping (Auto)", default=True)
    clip_end: FloatProperty(name="Viewport Distance", default=1_000_000.0, min=10.0, max=10_000_000.0)

    citygml_clamp_to_ground: BoolProperty(
        name="Clamp CityGML to Ground",
        description="Shift imported CityGML tiles so their lowest Z rests on ground after XY alignment",
        default=True,
        options={"HIDDEN"},
    )

    debug_building_centroids: BoolProperty(
        name="Debug Building Centroids",
        description="When enabled, create empties at building centroids with linkage info (debug only)",
        default=False,
        options={"HIDDEN"},
    )

    allow_scene_fallback: BoolProperty(
        name="Allow Scene Fallback",
        description="DEBUG ONLY: allow building GML centroids from the Blender scene when make_gml_centroids.py has no main()",
        default=False,
        options={"HIDDEN"},
    )

    show_diagnostics: BoolProperty(name="Show Diagnostics", default=False, options={"HIDDEN"})

    # -------- UI (panel state) --------
    ui_mode: EnumProperty(
        name="UI Mode",
        description="SIMPLE shows golden path only; DEV shows diagnostics/repair/experimental tools",
        items=(
            ("SIMPLE", "Simple", "Golden path only (recommended)"),
            ("DEV", "Dev", "Show all diagnostics/repair/experimental tools"),
        ),
        default="SIMPLE",
    )

    ui_show_inspector: BoolProperty(default=False, options={"HIDDEN"})
    ui_show_diag_repair: BoolProperty(default=False, options={"HIDDEN"})
    ui_show_experimental: BoolProperty(default=False, options={"HIDDEN"})
    ui_show_materialize_advanced: BoolProperty(default=False, options={"HIDDEN"})
    ui_show_advanced_steps: BoolProperty(default=False, options={"HIDDEN"})
    ui_show_input_summary: BoolProperty(default=False, options={"HIDDEN"})
    ui_show_advanced_tools: BoolProperty(default=False, options={"HIDDEN"})
    ui_show_input_advanced: BoolProperty(
        name="Show Input Advanced",
        description="Toggle Input Advanced (summary + terrain settings)",
        default=False,
        options={"HIDDEN"},
    )
    ui_show_extra_tools: BoolProperty(
        name="Show Extra Tools",
        description="Toggle Extra Tools (discovery, legends, DB tools, UI mode)",
        default=False,
        options={"HIDDEN"},
    )
    ui_show_terrain_advanced: BoolProperty(
        name="Show Terrain Advanced",
        description="Toggle terrain advanced settings (viewport, cache, DEM step)",
        default=False,
        options={"HIDDEN"},
    )

    # -------- STATUS (filled by Validate) --------
    status_citygml_loaded: BoolProperty(default=False)
    status_citygml_tiles: IntProperty(default=0)
    status_citygml_buildings: IntProperty(default=0)

    status_gpkg_loaded: BoolProperty(default=False)
    status_gpkg_table: StringProperty(default="")
    status_gpkg_id_col: StringProperty(default="")

    status_basemap_loaded: BoolProperty(default=False)
    status_basemap_images: IntProperty(default=0)

    status_basemap_terrain_loaded: BoolProperty(default=False)
    status_basemap_terrain_dem_size: StringProperty(default="")
    status_basemap_terrain_extent: StringProperty(default="")
    status_basemap_terrain_pixel_size: StringProperty(default="")
    status_basemap_terrain_crs: StringProperty(default="")

    # Per-step progress
    step1_citygml_done: BoolProperty(default=False)
    step1_citygml_tiles: IntProperty(default=0)

    step2_gpkg_done: BoolProperty(default=False)
    step2_linked_objects: IntProperty(default=0)

    step3_basemap_done: BoolProperty(default=False)
    step3_basemap_images: IntProperty(default=0)

    step0_terrain_done: BoolProperty(default=False, description="Phase 2 DEM→Terrain mesh complete")

    world_origin_set: BoolProperty(default=False)
    world_origin_min_easting: FloatProperty(default=0.0)
    world_origin_min_northing: FloatProperty(default=0.0)
    world_origin_max_easting: FloatProperty(name="World Max Easting", default=0.0)
    world_origin_max_northing: FloatProperty(name="World Max Northing", default=0.0)
    world_origin_set_by: StringProperty(name="World Origin Source", default="")

    status_text: StringProperty(default="")

    # -------- Inspector / shared project state --------
    attr_table: StringProperty(
        name="Attribute Table",
        description="Detected table used for attribute lookup",
        default="",
    )
    id_col: StringProperty(
        name="ID Column",
        description="Detected primary ID column for attribute lookup",
        default="osm_way_id",
    )
    inspector_last_error: StringProperty(default="", options={"HIDDEN"})

    # -------- Spreadsheet 2.0 (Building Inspector) --------
    spreadsheet_rows: CollectionProperty(type=M1DCBuildingRow)
    spreadsheet_row_index: IntProperty(default=-1)
    spreadsheet_filter: StringProperty(
        name="Filter",
        description="Filter building rows by building_idx/osm_id/attrs",
        default="",
        options={"HIDDEN"},
    )
    spreadsheet_columns_available: CollectionProperty(type=M1DCColumnOption)
    spreadsheet_column_index: IntProperty(default=-1)
    spreadsheet_column_filter: StringProperty(
        name="Column Filter",
        description="Filter available columns",
        default="",
    )
    spreadsheet_tables_cache: StringProperty(default="", options={"HIDDEN"})
    spreadsheet_table: StringProperty(
        name="GPKG Table",
        description="Selected GeoPackage table for feature lookup",
        default="",
        update=_on_spreadsheet_table_changed,
    )
    inspector_filter_key: bpy.props.EnumProperty(
        name="Filter Key",
        description="Source for semantic inspector values",
        items=(
            ("mesh_faces", "mesh_faces", "Read face attributes"),
            ("selection", "selection", "Read active selection"),
        ),
        default="mesh_faces",
    )
    # Inspector cached values (filled by inspect_active_face operator)
    inspector_message: StringProperty(default="", options={"HIDDEN"})
    inspector_object: StringProperty(default="", options={"HIDDEN"})
    inspector_source_tile: StringProperty(default="", options={"HIDDEN"})
    inspector_building_idx: IntProperty(default=-1, options={"HIDDEN"})
    inspector_gml_polygon_idx: IntProperty(default=-1, options={"HIDDEN"})
    inspector_osm_id: IntProperty(default=0, options={"HIDDEN"})
    inspector_link_conf: FloatProperty(default=0.0, options={"HIDDEN"})
    inspector_link_dist: FloatProperty(default=0.0, options={"HIDDEN"})
    inspector_link_iou: FloatProperty(default=0.0, options={"HIDDEN"})
    inspector_sel_count: IntProperty(default=0, options={"HIDDEN"})
    inspector_building_set: StringProperty(default="", options={"HIDDEN"})
    materialize_include_columns: BoolProperty(
        name="Materialize OSM Columns",
        description="Also write selected OSM columns as face attributes",
        default=False,
    )

    materialize_create_presentation_attrs: BoolProperty(
        name="Create Presentation Attributes",
        description="DISABLED: Causes crash with foreach_get sequence mismatch. Feature will be re-enabled.",
        default=False,
    )
    spreadsheet_max_rows: IntProperty(
        name="Max Rows",
        description="Hard limit on spreadsheet rows (prevents UI freeze)",
        default=5000,
        min=100,
        max=50000,
        options={"HIDDEN"},
    )
    spreadsheet_table_check_frame: IntProperty(
        default=-1,
        description="Frame counter for table/column refresh gating",
        options={"HIDDEN"},
    )
    spreadsheet_cached_obj: StringProperty(default="", options={"HIDDEN"})
    spreadsheet_last_error: StringProperty(default="", options={"HIDDEN"})
    spreadsheet_silent: BoolProperty(default=False, options={"HIDDEN"})
    spreadsheet_show_dev: BoolProperty(default=False, options={"HIDDEN"})

    # -------- OSM Feature Encoding (fixed columns) --------
    inspector_feature_json: StringProperty(default="{}", options={"HIDDEN"})
    inspector_cached_osm_id: IntProperty(default=0, options={"HIDDEN"})
    inspector_cached_table: StringProperty(default="", options={"HIDDEN"})
    inspector_cached_id_col: StringProperty(default="", options={"HIDDEN"})
    osm_feature_table_used: StringProperty(default="", options={"HIDDEN"})
    osm_vocab_name: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_type: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_building: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_amenity: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_landuse: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_boundary: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_admin_level: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_aeroway: StringProperty(default="[]", options={"HIDDEN"})
    osm_vocab_way_id: StringProperty(default="[]", options={"HIDDEN"})

    # -------- Terrain Validation (Phase 1 - Prepared Terrain) --------
    terrain_validation_ok: BoolProperty(
        name="Terrain Validated",
        description="Whether terrain dataset passed validation",
        default=False,
        options={"HIDDEN"},
    )
    terrain_validation_summary: StringProperty(
        name="Terrain Validation Summary",
        description="Short summary of terrain validation result",
        default="",
        options={"HIDDEN"},
    )
    terrain_dgm_count: IntProperty(
        name="DGM Tile Count",
        description="Number of DGM tiles found during validation",
        default=0,
        options={"HIDDEN"},
    )
    terrain_rgb_count: IntProperty(
        name="RGB Tile Count",
        description="Number of RGB tiles found during validation",
        default=0,
        options={"HIDDEN"},
    )
    terrain_overlap_count: IntProperty(
        name="Overlap Tile Count",
        description="Number of tiles with both DGM and RGB",
        default=0,
        options={"HIDDEN"},
    )

    # -------- SQL Query Console --------
    sql_db_target: EnumProperty(
        name="SQL DB Target",
        description="Database to query (SQL Console)",
        items=[
            ("GPKG", "GPKG", "Query GeoPackage (osm_buildings table)"),
            ("LINKDB", "LinkDB", "Query linkdb (osm_building_link table)"),
            ("MKDB", "MKDB", "Query mkdb (features table)"),
        ],
        default="GPKG",
    )
    sql_query_text: StringProperty(
        name="SQL Query",
        description="SQL query executed read-only against the selected GPKG/SQLite file",
        default="SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 50;",
    )
    sql_result_text: StringProperty(
        name="SQL Result",
        description="Last SQL query result (preview)",
        default="",
        options={"HIDDEN"},
    )
    sql_result_rows: IntProperty(
        name="Result Rows",
        description="Number of rows in last query result",
        default=0,
        options={"HIDDEN"},
    )
    sql_result_ms: FloatProperty(
        name="Query Time (ms)",
        description="Last query execution time in milliseconds",
        default=0.0,
        options={"HIDDEN"},
    )
    sql_limit_rows: IntProperty(
        name="Row Limit",
        description="Maximum rows to fetch (prevents UI freeze)",
        default=200,
        min=1,
        max=10000,
        options={"HIDDEN"},
    )
    ui_show_sql_panel: BoolProperty(
        name="Show SQL Panel",
        description="Toggle SQL query panel visibility",
        default=False,
        options={"HIDDEN"},
    )
    ui_show_db_tools: BoolProperty(
        name="Show DB Tools",
        description="Toggle DB Tools section (GPKG queries, schema inspection)",
        default=False,
        options={"HIDDEN"},
    )
    sql_table_name: StringProperty(
        name="SQL Table Name",
        description="Table name for SQL templates (replaces <table_name> placeholder)",
        default="",
    )
    sql_column_name: StringProperty(
        name="SQL Column Name",
        description="Column name for SQL templates (replaces <column_name> placeholder)",
        default="",
    )

    # -------- Legend Filter (text-to-code) --------
    legend_filter_attr: EnumProperty(
        name="Filter Attribute",
        description="Attribute to filter by (select coded column)",
        items=[
            ("amenity_code", "amenity_code", "Filter by amenity type"),
            ("building_code", "building_code", "Filter by building type"),
            ("landuse_code", "landuse_code", "Filter by land use"),
            ("shop_code", "shop_code", "Filter by shop type"),
            ("office_code", "office_code", "Filter by office type"),
            ("tourism_code", "tourism_code", "Filter by tourism type"),
            ("highway_code", "highway_code", "Filter by highway type"),
        ],
        default="amenity_code",
    )
    legend_filter_text: StringProperty(
        name="Filter Text",
        description="Text value to search for (e.g., university, hospital)",
        default="",
    )

    # -------- Face Attribute Query --------
    m1dc_face_attr_name: StringProperty(
        name="Attribute Name",
        description="Face attribute to query (e.g. 'osm_id', 'amenity', 'building')",
        default="",
    )
    m1dc_face_attr_value: StringProperty(
        name="Attribute Value",
        description="Value to match when selecting faces",
        default="",
    )
    m1dc_face_attr_result: StringProperty(
        name="Face Attr Result",
        description="Result text from face attribute queries",
        default="",
        options={"HIDDEN"},
    )
    ui_show_face_attr_panel: BoolProperty(
        name="Show Face Attr Panel",
        description="Toggle face attribute query panel visibility",
        default=False,
        options={"HIDDEN"},
    )
    ui_show_inspector_raw_tools: BoolProperty(
        name="Show Inspector Raw Tools",
        description="Toggle raw face attribute tools in Semantic Inspector",
        default=False,
        options={"HIDDEN"},
    )

    # -------- Terrain Alignment --------
    m1dc_auto_align_terrain: BoolProperty(
        name="Auto-Align Terrain",
        description="Automatically align terrain to city extent during pipeline run (scale + center)",
        default=False,
    )

    # -------- Inspector Query (Query-first Semantic Exploration) --------
    inspector_query_text: StringProperty(
        name="Inspector Query",
        description="Query string for semantic face filtering (e.g. 'university', 'amenity=school', 'amenity_code=58')",
        default="",
    )
    inspector_query_preset: EnumProperty(
        name="Query Preset",
        description="Quick query presets for common semantic filters",
        items=[
            ("CUSTOM", "Custom", "Custom query", 0),
            ("UNIV", "University", "amenity=university", 1),
            ("SCHOOL", "School", "amenity=school", 2),
            ("HOSPITAL", "Hospital", "amenity=hospital", 3),
            ("SHOP", "Shop", "shop", 4),
            ("RESIDENTIAL", "Residential", "building=residential", 5),
            ("COMMERCIAL", "Commercial", "building=commercial", 6),
            ("AMENITY_ANY", "Any Amenity", "amenity", 7),
        ],
        default="CUSTOM",
        update=_on_inspector_preset_changed,
    )
    inspector_query_active: BoolProperty(
        name="Inspector Query Active",
        description="Whether a query is currently active (affects inspector display mode)",
        default=False,
        options={"HIDDEN"},
    )
    inspector_query_last_summary: StringProperty(
        name="Last Query Summary",
        description="Summary text from last query execution",
        default="",
        options={"HIDDEN"},
    )
    inspector_query_last_stats_json: StringProperty(
        name="Last Query Stats JSON",
        description="JSON-encoded statistics from last query",
        default="",
        options={"HIDDEN"},
    )
    inspector_legend_only: BoolProperty(
        name="Legend Only",
        description="Filter aggregated inspector to show only legend-coded attributes (*_code, osm_id, link_*)",
        default=False,
    )

    # -------- Decoded Face Attributes (Semantic Inspector Yellow Box #2) --------
    inspector_decoded_attrs: CollectionProperty(type=M1DCDecodedAttrRow)
    inspector_decoded_attrs_index: IntProperty(name="Decoded Attr Index", default=0)

    # -------- Verbose Debug Toggle (PHASE 13) --------
    m1dc_verbose_debug: BoolProperty(
        name="Verbose Debug Logging",
        description="Enable verbose per-object debug logging (disabled by default for clean output)",
        default=False,
    )

    # -------- Terrain Validation Settings --------
    min_terrain_coverage: FloatProperty(
        name="Minimum Terrain Coverage",
        description="Minimum terrain XY coverage ratio vs CityGML extent (0.6 = 60%). Lower for proof-runs with partial terrain.",
        default=0.6,
        min=0.1,
        max=1.0,
    )
