import bpy
import json
import os
from bpy.types import Panel, UIList
from .utils.common import ensure_world_origin
from .utils.common import get_terrain_cache_dir
from . import ops


def _settings(context):
    return getattr(context.scene, "m1dc_settings", None)


def _get_path_status(path_str):
    """
    Return (icon, status_text) for a given path.
    - path_str is empty: ('X', '✗')
    - path_str exists: ('CHECKMARK', '✓')
    - path_str non-empty but doesn't exist: ('ERROR', '⚠')
    """
    if not path_str or path_str.strip() == "":
        return "X", "✗"
    if os.path.exists(path_str):
        return "CHECKMARK", "✓"
    else:
        return "ERROR", "⚠"


def _selected_columns(settings):
    return [opt.name for opt in getattr(settings, "spreadsheet_columns_available", []) if getattr(opt, "selected", False)]


def _inspector_cached(settings):
    if settings is None:
        return None
    try:
        features = json.loads(getattr(settings, "inspector_feature_json", "{}") or "{}")
    except Exception:
        features = {}
    return {
        "message": getattr(settings, "inspector_message", "") or "",
        "object": getattr(settings, "inspector_object", "") or "",
        "source_tile": getattr(settings, "inspector_source_tile", "") or "",
        "building_idx": getattr(settings, "inspector_building_idx", -1),
        "gml_polygon_idx": getattr(settings, "inspector_gml_polygon_idx", -1),
        "osm_id": getattr(settings, "inspector_osm_id", 0),
        "link_conf": getattr(settings, "inspector_link_conf", 0.0),
        "link_dist_m": getattr(settings, "inspector_link_dist", 0.0),
        "link_iou": getattr(settings, "inspector_link_iou", 0.0),
        "sel_count": getattr(settings, "inspector_sel_count", 0),
        "building_set": getattr(settings, "inspector_building_set", ""),
        "features": features,
    }


def _load_legend_csv(output_dir, attr_name):
    """
    Load legend CSV for an attribute, return dict: code -> decoded_value.

    Handles attribute names like:
    - osm_building_code -> look for *_building_legend.csv
    - building_code -> look for *_building_legend.csv

    Legend files are named like: osm_multipolygons_building_legend.csv
    """
    if not output_dir:
        return {}
    try:
        from .pipeline.diagnostics.legend_encoding import get_legend_cache_dir
        import csv
        import glob
        legends_dir = get_legend_cache_dir(output_dir)

        if not os.path.isdir(legends_dir):
            return {}

        # Extract the core key from attr_name
        # osm_building_code -> building
        # building_code -> building
        base_name = attr_name.replace("_code", "")
        if base_name.startswith("osm_"):
            core_key = base_name[4:]  # Remove osm_ prefix
        else:
            core_key = base_name

        # Try exact matches first
        candidates = [
            os.path.join(legends_dir, f"{base_name}_legend.csv"),
            os.path.join(legends_dir, f"{core_key}_legend.csv"),
            os.path.join(legends_dir, f"osm_{core_key}_legend.csv"),
        ]

        # Then try glob pattern for table-prefixed files: *_<key>_legend.csv
        glob_pattern = os.path.join(legends_dir, f"*_{core_key}_legend.csv")
        candidates.extend(glob.glob(glob_pattern))

        for csv_path in candidates:
            if os.path.exists(csv_path):
                legend = {}
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            code = int(row.get("code", 0))
                        except (ValueError, TypeError):
                            continue
                        value = row.get("value", "") or row.get("decoded", "") or row.get("name", "")
                        legend[code] = value
                return legend
    except Exception:
        pass
    return {}


def _decode_value(code, legend):
    """Decode a code value using legend. 0 = __NULL__, missing = __UNKNOWN__."""
    if code == 0:
        return "__NULL__"
    return legend.get(code, "__UNKNOWN__")


def _get_decoded_face_attrs(context, settings):
    """
    Build list of decoded face attributes for the active face.
    Returns list of dicts: [{"attr": name, "code": int, "decoded": str}, ...]
    """
    result = []
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        return result

    mesh = obj.data
    if not mesh.polygons:
        return result

    # Get active face index
    if context.mode != "EDIT_MESH":
        return result

    import bmesh
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()

    # Find active/selected face
    active_face = bm.faces.active
    if active_face is None:
        # Try first selected face
        selected = [f for f in bm.faces if f.select]
        if not selected:
            return result
        active_face = selected[0]

    face_idx = active_face.index
    output_dir = getattr(settings, "output_dir", "").strip()

    # Collect all *_code attributes + key IDs
    target_attrs = []
    for attr in mesh.attributes:
        if attr.domain != "FACE":
            continue
        name = attr.name
        if name.endswith("_code") or name in ("osm_id_int", "osm_way_id", "building_idx"):
            target_attrs.append((name, attr))

    # Read values and decode
    for attr_name, attr in sorted(target_attrs, key=lambda x: x[0]):
        try:
            if attr.data_type == "INT":
                code = attr.data[face_idx].value
            elif attr.data_type == "FLOAT":
                code = int(attr.data[face_idx].value)
            else:
                code = 0
        except (IndexError, AttributeError):
            code = 0

        # Decode if it's a _code attribute
        if attr_name.endswith("_code"):
            legend = _load_legend_csv(output_dir, attr_name)
            decoded = _decode_value(code, legend)
        else:
            decoded = str(code) if code else ""

        result.append({
            "attr": attr_name,
            "code": code,
            "decoded": decoded,
        })

    return result


class M1DC_UL_SpreadsheetRows(UIList):
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt_flags = []
        flt_neworder = []
        flt_text = getattr(data, "spreadsheet_filter", "").lower()
        columns = _selected_columns(data)
        for item in items:
            if not flt_text:
                flt_flags.append(self.bitflag_filter_item)
                continue
            hay = [str(item.building_idx), str(item.osm_id or ""), json.loads(item.attrs_json or "{}")]
            match = False
            for h in hay:
                if isinstance(h, dict):
                    for col in columns:
                        if flt_text in str(h.get(col, "")).lower():
                            match = True
                            break
                elif flt_text in str(h).lower():
                    match = True
            flt_flags.append(self.bitflag_filter_item if match else 0)
        return flt_flags, flt_neworder

    def draw_item(self, context, layout, data, item, _icon, _active_data, _active_propname, _index=0):
        s = getattr(context.scene, "m1dc_settings", None)
        columns = _selected_columns(s) if s else []
        attrs = {}
        try:
            attrs = json.loads(item.attrs_json or "{}") if item.attrs_json else {}
        except Exception:
            attrs = {}

        row = layout.row(align=True)
        # Fixed columns (always visible, proof-of-linking)
        row.prop(item, "selected", text="")
        row.label(text=str(item.building_idx))
        row.label(text=str(item.citygml_centroid))
        row.label(text=f"{item.link_conf:.2f}")
        row.label(text=str(item.osm_centroid))
        row.label(text=str(item.osm_id))
        # Dynamic feature columns
        for col in columns:
            row.label(text=str(attrs.get(col, "—")))


class M1DC_UL_SpreadsheetColumns(UIList):
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt = getattr(data, "spreadsheet_column_filter", "").lower()
        flt_flags = []
        flt_neworder = []
        for item in items:
            name = (item.name or "").lower()
            match = not flt or flt in name
            flt_flags.append(self.bitflag_filter_item if match else 0)
        return flt_flags, flt_neworder

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index=0):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.name)


class M1DC_UL_DecodedAttrs(UIList):
    """UIList for displaying decoded face attributes."""

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index=0):
        row = layout.row(align=True)
        # Attribute name
        row.label(text=item.attr_name)
        # Code value
        row.label(text=str(item.code_value))
        # Decoded value (also serves as tooltip content)
        decoded = item.decoded_value or ""
        row.label(text=decoded)


class M1DC_MT_TableSelector(bpy.types.Menu):
    """Dynamic table selector menu."""
    bl_label = "Select Table"
    bl_idname = "M1DC_MT_TableSelector"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        
        try:
            tables_list = json.loads(s.spreadsheet_tables_cache or "[]")
        except Exception:
            tables_list = []
        
        if not tables_list:
            layout.label(text="(no tables available)")
            return
        
        for table_name in tables_list:
            props = layout.operator("m1dc_spreadsheet.select_table", text=table_name)
            props.table_name = table_name


class M1DC_MT_OSMFeatureTable(bpy.types.Menu):
    """Dynamic OSM feature table selector menu."""
    bl_label = "Select OSM Table"
    bl_idname = "M1DC_MT_OSMFeatureTable"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        try:
            tables_list = json.loads(s.osm_feature_tables_cache or "[]")
        except Exception:
            tables_list = []
        if not tables_list:
            layout.label(text="(no tables available)")
            return
        for table_name in tables_list:
            props = layout.operator("m1dc_osm.select_table", text=table_name)
            props.table_name = table_name


class M1DC_OT_SelectTable(bpy.types.Operator):
    """Select a table from dropdown menu."""
    bl_idname = "m1dc_spreadsheet.select_table"
    bl_label = "Select Table"
    bl_options = {"REGISTER", "UNDO"}
    
    table_name: bpy.props.StringProperty(default="")
    
    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        
        s.spreadsheet_table = self.table_name
        return {"FINISHED"}


class M1DC_OT_SelectOSMFeatureTable(bpy.types.Operator):
    """Select an OSM feature table from dropdown menu."""
    bl_idname = "m1dc_osm.select_table"
    bl_label = "Select OSM Table"
    bl_options = {"REGISTER", "UNDO"}

    table_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}
        s.osm_feature_table = self.table_name
        try:
            ops.refresh_osm_feature_columns(s, reset_selection=True)
        except Exception:
            pass
        return {"FINISHED"}


class M1DC_PT_Pipeline(Panel):
    bl_label = "M1_DC Pipeline"
    bl_idname = "M1DC_PT_pipeline"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "M1_DC"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)

        if s is None:
            layout.label(text="Settings pointer missing. Re-enable the add-on.", icon="ERROR")
            return

        layout.label(text="M1_DC – City-Scale Geospatial Pipeline")

        # ================================================================
        # INPUT SETUP: Wizard + Run button (compact, always visible)
        # ================================================================
        input_box = layout.box()
        input_box.label(text="Pipeline Inputs", icon="FILE_FOLDER")

        # Wizard + Run Pipeline on same row
        action_row = input_box.row(align=True)
        action_row.operator("m1dc.input_pickup_wizard", text="Setup Wizard", icon="FILE_FOLDER")
        action_row.operator("m1dc.run_all", text="Run Pipeline", icon="PLAY")

        # Collapsible "Input Advanced" (merged Summary + Terrain)
        adv_row = input_box.row()
        icon_adv = "TRIA_DOWN" if getattr(s, "ui_show_input_advanced", False) else "TRIA_RIGHT"
        adv_row.prop(s, "ui_show_input_advanced", text="", icon=icon_adv, emboss=False)
        adv_row.label(text="Input Advanced")

        if getattr(s, "ui_show_input_advanced", False):
            adv_box = input_box.box()

            # === INPUT SUMMARY ===
            adv_box.label(text="Input Summary:", icon="INFO")

            # Helper to draw one input status row
            def draw_input_status(parent, label, prop_name):
                path_str = getattr(s, prop_name, "")
                icon, status_text = _get_path_status(path_str)
                row = parent.row(align=False)
                row.label(text=label, icon=icon)
                display_path = path_str if len(path_str) < 50 else "..." + path_str[-47:]
                try:
                    row.label(text=display_path)
                except:
                    pass

            # Terrain: Show new terrain_root_dir (preferred) or fallback to old paths
            terrain_root = getattr(s, "terrain_root_dir", "").strip()
            if terrain_root:
                draw_input_status(adv_box, "Terrain (Prepared)", "terrain_root_dir")
                if getattr(s, "terrain_validation_ok", False):
                    val_row = adv_box.row()
                    val_row.label(text="", icon="CHECKMARK")
                    val_row.label(text=getattr(s, "terrain_validation_summary", ""))
                elif getattr(s, "terrain_validation_summary", ""):
                    val_row = adv_box.row()
                    val_row.label(text="", icon="ERROR")
                    val_row.label(text=getattr(s, "terrain_validation_summary", ""))
            else:
                draw_input_status(adv_box, "Terrain DGM (deprecated)", "terrain_dgm_dir")
                draw_input_status(adv_box, "Terrain RGB (deprecated)", "terrain_rgb_dir")

            draw_input_status(adv_box, "CityGML", "citygml_dir")
            draw_input_status(adv_box, "GPKG", "gpkg_path")
            draw_input_status(adv_box, "Output", "output_dir")

            # === TERRAIN ADVANCED ===
            adv_box.separator()
            adv_box.label(text="Terrain Settings:", icon="MESH_GRID")

            clip_row = adv_box.row(align=True)
            clip_row.label(text="Viewport Distance")
            clip_row.prop(s, "clip_end", text="")
            clip_row.label(text="m")

            step_row = adv_box.row(align=True)
            step_row.label(text="DEM Step (0=auto)")
            step_row.prop(s, "terrain_dem_step", text="")

            cache_row = adv_box.row(align=True)
            cache_row.prop(s, "use_terrain_cache", text="Cache Terrain")

            if getattr(s, "use_terrain_cache", True):
                out_dir = getattr(s, "output_dir", "").strip()
                cache_path = get_terrain_cache_dir(out_dir) if out_dir else "(set Output)"
                adv_box.label(text=f"Cache: {cache_path}", icon="FILE_FOLDER")
        
        # ADVANCED: Individual steps (for debugging/iteration) – only in DEV mode
        if getattr(s, "ui_mode", "SIMPLE") == "DEV":
            adv_steps = input_box.box()
            adv_hdr = adv_steps.row()
            icon = "TRIA_DOWN" if getattr(s, "ui_show_advanced_steps", False) else "TRIA_RIGHT"
            adv_hdr.prop(s, "ui_show_advanced_steps", text="", icon=icon, emboss=False)
            adv_hdr.label(text="Advanced: Individual Steps")
        
            if getattr(s, "ui_show_advanced_steps", False):
                adv_steps.operator("m1dc.validate", text="Validate Inputs", icon="CHECKMARK")
                adv_steps.operator("m1dc.run_pipeline", text="Run Pipeline Only", icon="PLAY")

                # PHASE 2 & 3 & 4: Individual Imports & Alignment
                adv_steps.separator()
                adv_steps.label(text="Individual Imports", icon="IMPORT")
                adv_steps.operator("m1dc.import_rgb_basemap", text="Import RGB Basemap (DTK10)", icon="IMAGE_DATA")
                adv_steps.operator("m1dc.import_dgm_terrain", text="Import DGM Terrain", icon="MESH_PLANE")
                adv_steps.operator("m1dc.align_citygml_z", text="Align CityGML Z to Terrain", icon="ARROW_LEFTRIGHT")

                adv_steps.separator()
                row_mat = adv_steps.row(align=True)
                mat_op = row_mat.operator("m1dc.materialize_links", text="Materialize Links", icon="MODIFIER")
                mat_op.include_features = getattr(s, "materialize_include_columns", False)
                
                # DISABLED: Presentation attributes operator crashes with EXCEPTION_ACCESS_VIOLATION
                # adv_steps.operator("m1dc.make_presentation_attrs", text="Create Presentation Attributes", icon="PRESET")
                adv_steps.label(text="Presentation Attributes: DISABLED (causes crash - will be fixed)", icon="ERROR")
                
                # Nested advanced options
                adv_nested = adv_steps.box()
                adv_nested_hdr = adv_nested.row()
                icon_nested = "TRIA_DOWN" if getattr(s, "ui_show_materialize_advanced", False) else "TRIA_RIGHT"
                adv_nested_hdr.prop(s, "ui_show_materialize_advanced", text="", icon=icon_nested, emboss=False)
                adv_nested_hdr.label(text="Materialize Options")
                
                if getattr(s, "ui_show_materialize_advanced", False):
                    adv_nested.prop(s, "materialize_create_presentation_attrs", text="Create Presentation Attributes")
                    adv_nested.operator("m1dc.inspect_active_face", text="Update from Active Face", icon="FILE_REFRESH")
                    adv_nested.prop(s, "materialize_include_columns", text="Include OSM Attributes during Materialize")

        # ================================================================
        # SEMANTIC INSPECTOR (Project-specific feature inspection)
        # ================================================================
        inspector = layout.box()
        hdr = inspector.row()
        icon = "TRIA_DOWN" if getattr(s, "ui_show_inspector", False) else "TRIA_RIGHT"
        hdr.prop(s, "ui_show_inspector", text="", icon=icon, emboss=False)
        hdr.label(text="Semantic Inspector", icon="VIEWZOOM")

        if getattr(s, "ui_show_inspector", False):
            # ================================================================
            # YELLOW BOX #1: INSPECTOR QUERY (Multiline Query Editor)
            # ================================================================
            query_box = inspector.box()
            query_box.label(text="Inspector Query:", icon="VIEWZOOM")

            # Query preset dropdown
            preset_row = query_box.row(align=True)
            preset_row.prop(s, "inspector_query_preset", text="Preset")

            # Query text input (single-line - Blender 4.5 has no MULTILINE support)
            query_box.prop(s, "inspector_query_text", text="Query")
            query_box.label(text="Note: queries must be written in one line.", icon="INFO")

            # ================================================================
            # BUTTON ROWS (Run/Clear, Export/Filter)
            # ================================================================
            # Row 1: Run Query | Clear
            btn_row1 = query_box.row(align=True)
            btn_row1.operator("m1dc.inspector_apply_query", text="Run Query", icon="PLAY")
            btn_row1.operator("m1dc.inspector_clear_query", text="Clear", icon="X")

            # Row 2: Export Report | Filter Faces
            btn_row2 = query_box.row(align=True)
            query_active = getattr(s, "inspector_query_active", False)
            export_sub = btn_row2.row(align=True)
            export_sub.enabled = query_active
            export_sub.operator("m1dc.inspector_export_report", text="Export Report", icon="FILE_TICK")

            filter_op = btn_row2.operator("m1dc.filter_by_legend_text", text="Filter Faces", icon="FILTER")
            filter_op.attr_name_code = getattr(s, "legend_filter_attr", "amenity_code")
            filter_op.text_value = getattr(s, "legend_filter_text", "")

            # Show query result summary and table if active
            if query_active:
                summary = getattr(s, "inspector_query_last_summary", "")
                if summary:
                    query_box.label(text=f"Result: {summary}", icon="CHECKMARK")

                # ════════════════════════════════════════════════════════════
                # INSPECTOR QUERY RESULTS TABLE (P2-D enhancement)
                # ════════════════════════════════════════════════════════════
                stats_json = getattr(s, "inspector_query_last_stats_json", "")
                if stats_json:
                    try:
                        import json
                        stats = json.loads(stats_json)

                        result_box = query_box.box()
                        result_box.label(text="Query Results:", icon="PRESET")

                        # Query info row
                        query_col = stats.get("query_column", "")
                        query_code = stats.get("query_code", "")
                        if query_col and query_code is not None:
                            info_row = result_box.row()
                            info_row.label(text=f"Filter: {query_col}_code = {query_code}")

                        # Stats rows
                        faces_count = stats.get("faces_count", 0)
                        unique_osm = stats.get("unique_osm_ids", 0)

                        stats_row = result_box.row()
                        stats_row.label(text=f"Faces: {faces_count}")
                        stats_row.label(text=f"Unique Buildings: {unique_osm}")

                        # OSM ID list (sample up to 10)
                        osm_id_list = stats.get("osm_id_list", [])
                        if osm_id_list:
                            result_box.separator()
                            result_box.label(text=f"Sample OSM IDs ({len(osm_id_list)}):")

                            # Show OSM IDs in rows of 5
                            ids_per_row = 5
                            for i in range(0, len(osm_id_list), ids_per_row):
                                chunk = osm_id_list[i:i + ids_per_row]
                                id_row = result_box.row()
                                for osm_id in chunk:
                                    id_row.label(text=str(osm_id))

                    except Exception:
                        pass  # Silently ignore JSON parse errors

            # ================================================================
            # YELLOW BOX #2: DECODED FACE ATTRIBUTES
            # ================================================================
            inspector.separator()
            decoded_box = inspector.box()
            decoded_box.label(text="Decoded Face Attributes:", icon="MESH_DATA")

            # Check context for displaying attributes
            obj = context.active_object
            if obj is None or obj.type != "MESH":
                decoded_box.label(text="Select a CityGML mesh object.", icon="INFO")
            elif context.mode != "EDIT_MESH":
                decoded_box.label(text="Select a face in Edit Mode.", icon="INFO")
            else:
                # Get decoded attributes for active face
                decoded_attrs = _get_decoded_face_attrs(context, s)

                if not decoded_attrs:
                    decoded_box.label(text="No *_code attributes found on this face.", icon="INFO")
                else:
                    # Header row
                    header_row = decoded_box.row(align=True)
                    header_row.label(text="Attribute")
                    header_row.label(text="Code")
                    header_row.label(text="Decoded")

                    # Attribute rows
                    for attr_data in decoded_attrs:
                        row = decoded_box.row(align=True)
                        row.label(text=attr_data["attr"])
                        row.label(text=str(attr_data["code"]))
                        decoded_val = attr_data["decoded"]
                        # Show decoded value with tooltip
                        sub = row.row()
                        sub.label(text=decoded_val)

            # Filter controls for legend text (compact, always visible)
            inspector.separator()
            filter_row = inspector.row(align=True)
            filter_row.prop(s, "legend_filter_attr", text="")
            filter_row.prop(s, "legend_filter_text", text="")


# ============================================================================
# REGISTRATION
# ============================================================================
