"""
Legend Encoding — Automatic categorical string -> integer code mapping for GPKG attributes

This module provides utilities to:
- Detect the main feature table in a READONLY GPKG
- Identify categorical TEXT columns suitable for encoding
- Build legend CSVs (value -> code mappings)
- Load legends for decoding integer codes back to strings

Design:
- FULLY AUTOMATIC: No hardcoded table names or column lists
- READONLY: Uses read-only SQLite connections, never modifies GPKG
- CODE 0 = NULL/empty (reserved)
- Deterministic: Same values -> same codes (sorted by value)
- Memory-safe: Guards against high-cardinality columns

Functions:
- detect_main_feature_table() — Find the main OSM feature table
- detect_categorical_columns() — Find encodable TEXT columns
- build_column_legend() — Build legend CSV for one column
- build_all_legends() — Build legends for all detected columns
- load_legend_mapping() — Load legend CSV into dict
- get_legend_cache_dir() — Get legends folder path
"""

import os
import csv
import sqlite3
from pathlib import Path


# ============================================================================
# CODE_KEYS: Explicit whitelist of OSM columns to encode as *_code integers
# ============================================================================
CODE_KEYS = [
    "amenity", "building", "landuse", "shop", "office",
    "tourism", "highway", "leisure", "historic",
    "man_made", "public_transport", "railway",
    "natural", "waterway", "aeroway",
]


# In-memory encoding caches (separate from Blender cache for non-Blender use)
_ENCODE_CACHE = {}  # {attr_name_code: {value: code, ...}}
_DECODE_CACHE = {}  # {attr_name_code: {code: value, ...}}

# Tile ID mapping (source_tile string -> tile_id int)
_TILE_ID_MAP = {}  # {source_tile_normalized: tile_id}
_TILE_ID_REVERSE = {}  # {tile_id: source_tile_normalized}
_NEXT_TILE_ID = 1  # Start at 1, 0 reserved for unknown


def _quote_identifier(name):
    """Quote SQL identifier for safe use in queries."""
    return '"' + name.replace('"', '""') + '"'


def _open_readonly_connection(gpkg_path):
    """Open read-only SQLite connection to GPKG."""
    # Use centralized readonly DB access
    try:
        from ...utils.common import open_db_readonly
        return open_db_readonly(gpkg_path, log_open=False)
    except ImportError:
        # Fallback if API import fails
        posix_path = Path(gpkg_path).as_posix()
        uri = f"file:{posix_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA query_only=ON;")
        return conn


def detect_main_feature_table(gpkg_path):
    """
    Detect the main OSM feature table from a GPKG.

    Strategy:
    1. Query gpkg_contents for feature/tile tables (not gpkg_* system tables)
    2. Filter for tables with OSM-like columns (osm_id, osm_way_id, geom)
    3. Pick the one with most rows (largest table = main features)

    Args:
        gpkg_path: Path to READONLY GeoPackage file

    Returns:
        str: Table name, or None if no suitable table found
    """
    try:
        conn = _open_readonly_connection(gpkg_path)
        cursor = conn.cursor()

        # Get candidate tables from gpkg_contents
        cursor.execute("""
            SELECT table_name
            FROM gpkg_contents
            WHERE data_type IN ('features', 'tiles')
              AND table_name NOT LIKE 'gpkg_%'
            ORDER BY table_name
        """)
        candidates = [row[0] for row in cursor.fetchall()]

        if not candidates:
            conn.close()
            return None

        # Filter candidates: must have OSM-like columns
        osm_tables = []
        for table_name in candidates:
            # Get column list
            cursor.execute(f"PRAGMA table_info({_quote_identifier(table_name)})")
            columns = [row[1].lower() for row in cursor.fetchall()]

            # Check for OSM markers
            has_osm_id = any(col in columns for col in ['osm_id', 'osm_way_id', 'fid'])
            has_geom = any('geom' in col for col in columns)

            if has_osm_id or has_geom:
                # Count rows
                cursor.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}")
                row_count = cursor.fetchone()[0]
                osm_tables.append((table_name, row_count))

        conn.close()

        if not osm_tables:
            return None

        # Pick table with most rows
        osm_tables.sort(key=lambda x: x[1], reverse=True)
        return osm_tables[0][0]

    except Exception as e:
        print(f"[LegendEncoding] Error detecting main table: {e}")
        return None


def detect_categorical_columns(gpkg_path, table_name, max_distinct=500):
    """
    Detect categorical TEXT columns suitable for encoding.

    Excludes:
    - Geometric columns (geom, geometry)
    - ID columns (osm_id, osm_way_id, fid)
    - High-cardinality columns (name, addr_housenumber)
    - Semi-structured (other_tags)

    Includes only:
    - TEXT-like columns (type contains TEXT/CHAR or empty)
    - With distinct_real_values > 0 and <= max_distinct

    Args:
        gpkg_path: Path to READONLY GeoPackage
        table_name: Name of the table to analyze
        max_distinct: Maximum distinct values (cardinality guard)

    Returns:
        list[dict]: List of column info dicts with keys:
            - column_name: str
            - data_type: str
            - distinct_real_values: int
    """
    try:
        conn = _open_readonly_connection(gpkg_path)
        cursor = conn.cursor()

        # Get column list with types
        cursor.execute(f"PRAGMA table_info({_quote_identifier(table_name)})")
        columns_info = cursor.fetchall()

        # Exclude list (always skip these)
        exclude_patterns = [
            'geom', 'geometry', 'wkb_geometry',
            'osm_id', 'osm_way_id', 'fid', 'ogc_fid',
            'name', 'addr_housenumber', 'other_tags'
        ]

        categorical_columns = []

        for col_info in columns_info:
            col_name = col_info[1]
            col_type = col_info[2].upper()

            # Skip excluded columns
            if any(pattern in col_name.lower() for pattern in exclude_patterns):
                continue

            # Only consider TEXT-like columns
            is_text = ('TEXT' in col_type or 'CHAR' in col_type or col_type == '')
            if not is_text:
                continue

            # Count distinct real values (excluding NULL/empty)
            try:
                query = f"""
                    SELECT COUNT(DISTINCT NULLIF(TRIM({_quote_identifier(col_name)}), '')) AS distinct_real_values
                    FROM {_quote_identifier(table_name)}
                    WHERE {_quote_identifier(col_name)} IS NOT NULL
                      AND TRIM({_quote_identifier(col_name)}) <> ''
                """
                cursor.execute(query)
                distinct_count = cursor.fetchone()[0]

                # Apply cardinality guard
                if distinct_count > 0 and distinct_count <= max_distinct:
                    categorical_columns.append({
                        'column_name': col_name,
                        'data_type': col_type if col_type else 'TEXT',
                        'distinct_real_values': distinct_count,
                    })

            except Exception as e:
                print(f"[LegendEncoding] Error analyzing column {col_name}: {e}")
                continue

        conn.close()
        return categorical_columns

    except Exception as e:
        print(f"[LegendEncoding] Error detecting categorical columns: {e}")
        return []


def build_column_legend(gpkg_path, table_name, column_name, output_dir):
    """
    Build legend CSV for one categorical column.

    Legend structure:
    - column, code, value, count
    - code 0 = __NULL__ (for NULL/empty values)
    - codes start at 1, assigned in alphabetical order (deterministic)

    Args:
        gpkg_path: Path to READONLY GeoPackage
        table_name: Name of the table
        column_name: Name of the column to encode
        output_dir: Base output directory (legends saved to output_dir/legends/)

    Returns:
        dict: Summary with keys:
            - legend_path: str (path to created CSV)
            - max_code: int
            - null_count: int
            - value_count: int (distinct non-null values)
    """
    try:
        conn = _open_readonly_connection(gpkg_path)
        cursor = conn.cursor()

        # Count NULL/empty rows
        cursor.execute(f"""
            SELECT COUNT(*) AS null_or_empty
            FROM {_quote_identifier(table_name)}
            WHERE {_quote_identifier(column_name)} IS NULL
               OR TRIM({_quote_identifier(column_name)}) = ''
        """)
        null_count = cursor.fetchone()[0]

        # Get distinct values with counts (sorted by value for determinism)
        cursor.execute(f"""
            SELECT NULLIF(TRIM({_quote_identifier(column_name)}), '') AS value, COUNT(*) AS n
            FROM {_quote_identifier(table_name)}
            WHERE {_quote_identifier(column_name)} IS NOT NULL
              AND TRIM({_quote_identifier(column_name)}) <> ''
            GROUP BY value
            ORDER BY value
        """)
        value_rows = cursor.fetchall()

        conn.close()

        # Build legend CSV
        legends_dir = get_legend_cache_dir(output_dir)
        os.makedirs(legends_dir, exist_ok=True)

        legend_filename = f"{table_name}__{column_name}_legend.csv"
        legend_path = os.path.join(legends_dir, legend_filename)

        with open(legend_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['column', 'code', 'value', 'count'])

            # Code 0 = NULL
            writer.writerow([column_name, 0, '__NULL__', null_count])

            # Assign codes starting at 1
            for code, (value, count) in enumerate(value_rows, start=1):
                writer.writerow([column_name, code, value, count])

        return {
            'legend_path': legend_path,
            'max_code': len(value_rows),
            'null_count': null_count,
            'value_count': len(value_rows),
        }

    except Exception as e:
        print(f"[LegendEncoding] Error building legend for {column_name}: {e}")
        return None


def build_all_legends(gpkg_path, output_dir, max_distinct=500):
    """
    Build legends for all detected categorical columns.

    Full workflow:
    1. Detect main feature table
    2. Detect categorical columns
    3. Build legend CSV for each column
    4. Return summary

    Args:
        gpkg_path: Path to READONLY GeoPackage
        output_dir: Base output directory
        max_distinct: Cardinality guard (default 500)

    Returns:
        dict: Summary with keys:
            - table_name: str
            - columns: list[dict] (column info + legend summary)
            - legends_dir: str
            - success: bool
    """
    try:
        # Step 1: Detect main table
        table_name = detect_main_feature_table(gpkg_path)
        if not table_name:
            return {
                'success': False,
                'error': 'No suitable feature table found in GPKG',
            }

        # Step 2: Detect categorical columns
        categorical_cols = detect_categorical_columns(gpkg_path, table_name, max_distinct)
        if not categorical_cols:
            return {
                'success': False,
                'table_name': table_name,
                'error': 'No categorical columns detected',
            }

        # Step 3: Build legends
        legends_dir = get_legend_cache_dir(output_dir)
        results = []

        for col_info in categorical_cols:
            col_name = col_info['column_name']
            legend_summary = build_column_legend(gpkg_path, table_name, col_name, output_dir)

            if legend_summary:
                results.append({
                    **col_info,
                    **legend_summary,
                })

        return {
            'success': True,
            'table_name': table_name,
            'columns': results,
            'legends_dir': legends_dir,
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }


def load_legend_mapping(legend_csv_path):
    """
    Load legend CSV into bidirectional mapping dicts.

    Args:
        legend_csv_path: Path to legend CSV file

    Returns:
        tuple: (value_to_code, code_to_value) dicts
            - value_to_code: dict[str, int]
            - code_to_value: dict[int, str]
    """
    value_to_code = {}
    code_to_value = {}

    try:
        with open(legend_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = int(row['code'])
                value = row['value']

                value_to_code[value] = code
                code_to_value[code] = value

    except Exception as e:
        print(f"[LegendEncoding] Error loading legend {legend_csv_path}: {e}")

    return value_to_code, code_to_value


def get_legend_cache_dir(output_dir):
    """
    Get the legends folder path (output_dir/legends/).

    Args:
        output_dir: Base output directory

    Returns:
        str: Path to legends directory
    """
    return os.path.join(output_dir, "legends")


# ============================================================================
# INSPECTOR QUERY SUPPORT (Query-first semantic exploration)
# ============================================================================


def parse_inspector_query(query_text):
    """
    Parse inspector query string into (column, value, is_code) tuple.

    Supported formats:
    - "university" → searches all legend strings, returns best match
    - "amenity=university" → exact column + value
    - "amenity_code=58" → exact column + code (integer)
    - "amenity:58" → shorthand for amenity_code=58

    Args:
        query_text: Query string from user input

    Returns:
        tuple: (column_name, value, is_code, needs_legend_lookup)
            - column_name: str or None (if bare word search)
            - value: str or int
            - is_code: bool (True if value is already a code)
            - needs_legend_lookup: bool (True if we need to map value → code)
    """
    query = query_text.strip()
    if not query:
        return None, None, False, False

    # Format: "column=value" or "column_code=int"
    if '=' in query:
        parts = query.split('=', 1)
        column = parts[0].strip()
        value = parts[1].strip()

        # Check if column ends with "_code" → value is already a code
        if column.endswith('_code'):
            column_base = column[:-5]  # Remove "_code" suffix
            try:
                code = int(value)
                return column_base, code, True, False
            except ValueError:
                # Not a valid int, treat as string lookup
                return column, value, False, True
        else:
            # Regular column=value, needs legend lookup
            return column, value, False, True

    # Format: "column:value" shorthand
    elif ':' in query:
        parts = query.split(':', 1)
        column = parts[0].strip()
        value = parts[1].strip()

        # Try to parse as int (code)
        try:
            code = int(value)
            return column, code, True, False
        except ValueError:
            # Treat as string lookup
            return column, value, False, True

    # Bare word search: "university"
    else:
        return None, query, False, True


def find_value_in_legends(value, legends_dir, preferred_columns=None):
    """
    Search for a value string across all legend CSVs.

    Args:
        value: String value to find
        legends_dir: Path to legends directory
        preferred_columns: List of column names to prioritize (e.g. ['amenity', 'building'])

    Returns:
        tuple: (column_name, code) or (None, None) if not found
    """
    if preferred_columns is None:
        preferred_columns = ['amenity', 'building', 'landuse', 'leisure']

    if not os.path.exists(legends_dir):
        return None, None

    # Normalize search value (case-insensitive, strip)
    search_value = value.strip().lower()

    # Search in preferred columns first
    for preferred_col in preferred_columns:
        for legend_file in os.listdir(legends_dir):
            if not legend_file.endswith('_legend.csv'):
                continue

            # Extract column name from filename: table__column_legend.csv
            parts = legend_file.replace('_legend.csv', '').split('__')
            if len(parts) != 2:
                continue

            col_name = parts[1]
            if col_name != preferred_col:
                continue

            # Load legend and search
            legend_path = os.path.join(legends_dir, legend_file)
            try:
                with open(legend_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        row_value = row['value'].strip().lower()
                        if row_value == search_value:
                            return col_name, int(row['code'])
            except Exception:
                continue

    # Search in all other columns
    for legend_file in os.listdir(legends_dir):
        if not legend_file.endswith('_legend.csv'):
            continue

        parts = legend_file.replace('_legend.csv', '').split('__')
        if len(parts) != 2:
            continue

        col_name = parts[1]
        if col_name in preferred_columns:
            continue  # Already searched

        # Load legend and search
        legend_path = os.path.join(legends_dir, legend_file)
        try:
            with open(legend_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_value = row['value'].strip().lower()
                    if row_value == search_value:
                        return col_name, int(row['code'])
        except Exception:
            continue

    return None, None


def get_legend_cache():
    """
    Get or initialize legend cache in bpy.app.driver_namespace.

    Returns:
        dict: Legend cache {(table, column): {code: value, ...}}
    """
    try:
        import bpy
        if "M1DC_LEGEND_CACHE" not in bpy.app.driver_namespace:
            bpy.app.driver_namespace["M1DC_LEGEND_CACHE"] = {}
        return bpy.app.driver_namespace["M1DC_LEGEND_CACHE"]
    except Exception:
        return {}


def load_legend_for_column(legends_dir, table_name, column_name):
    """
    Load legend for a specific column into cache.

    Args:
        legends_dir: Path to legends directory
        table_name: Name of the table
        column_name: Name of the column

    Returns:
        dict: {code: value} mapping, or None if not found
    """
    cache = get_legend_cache()
    cache_key = (table_name, column_name)

    # Check cache first
    if cache_key in cache:
        return cache[cache_key]

    # Load from CSV
    legend_filename = f"{table_name}__{column_name}_legend.csv"
    legend_path = os.path.join(legends_dir, legend_filename)

    if not os.path.exists(legend_path):
        return None

    _, code_to_value = load_legend_mapping(legend_path)
    cache[cache_key] = code_to_value

    return code_to_value


# ============================================================================
# RUNTIME ENCODE/DECODE API (for Materialize + Inspector)
# ============================================================================


def init_legend_caches(legends_dir, table_name):
    """
    Initialize encoding and decoding caches from legend CSVs.

    Args:
        legends_dir: Path to legends directory
        table_name: Name of the table (used in CSV filenames)

    Returns:
        int: Number of legend files loaded
    """
    global _ENCODE_CACHE, _DECODE_CACHE

    loaded_count = 0

    if not os.path.isdir(legends_dir):
        print(f"[LegendEncoding] Legends dir not found: {legends_dir}")
        return 0

    legend_files = sorted([f for f in os.listdir(legends_dir) if f.endswith("_legend.csv")])

    for legend_file in legend_files:
        # Parse filename: table__column_legend.csv
        stem = legend_file.replace("_legend.csv", "")
        try:
            file_table, column_name = stem.rsplit("__", 1)
        except ValueError:
            continue

        # Only load legends for the specified table
        if file_table != table_name:
            continue

        # Only load legends for CODE_KEYS columns
        if column_name not in CODE_KEYS:
            continue

        legend_path = os.path.join(legends_dir, legend_file)
        attr_name_code = f"{column_name}_code"

        try:
            value_to_code, code_to_value = load_legend_mapping(legend_path)

            # Store in caches
            _ENCODE_CACHE[attr_name_code] = value_to_code
            _DECODE_CACHE[attr_name_code] = code_to_value
            loaded_count += 1

        except Exception as ex:
            print(f"[LegendEncoding] Failed to load {legend_file}: {ex}")
            continue

    print(f"[LegendEncoding] Loaded {loaded_count} legend caches for table '{table_name}'")
    # [PROOF][LEGEND] Determinism guard: log CODE_KEYS whitelist and cache entry counts
    print(f"[PROOF][LEGEND] CODE_KEYS={CODE_KEYS}")
    print(f"[PROOF][LEGEND] encode_cache_keys={sorted(_ENCODE_CACHE.keys())} decode_cache_keys={sorted(_DECODE_CACHE.keys())}")
    for _k in sorted(_ENCODE_CACHE.keys()):
        _n = len(_ENCODE_CACHE[_k])
        print(f"[PROOF][LEGEND] cache={_k} entries={_n} sample={list(_ENCODE_CACHE[_k].items())[:3]}")
    return loaded_count


def legend_encode(attr_name_code: str, value: str) -> int:
    """
    Return integer code for a categorical string value.

    Args:
        attr_name_code: Attribute name with _code suffix (e.g., "amenity_code")
        value: String value to encode (e.g., "university")

    Returns:
        int: Integer code (0 for NULL/empty/missing)
    """
    if value is None:
        return 0

    # FIX: Blender 4.5 STRING attrs return bytes — decode first
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            value = str(value)

    # Handle string representation of bytes: "b'house'" from DB/cache
    if isinstance(value, str):
        if value.startswith("b'") and value.endswith("'"):
            value = value[2:-1]
        elif value.startswith('b"') and value.endswith('"'):
            value = value[2:-1]

    v = value.strip() if isinstance(value, str) else str(value).strip()
    if not v:
        return 0

    cache = _ENCODE_CACHE.get(attr_name_code)
    if cache is None:
        return 0

    return cache.get(v, 0)


def legend_decode(attr_name_code: str, code: int) -> str:
    """
    Return decoded string for integer code.

    Args:
        attr_name_code: Attribute name with _code suffix (e.g., "amenity_code")
        code: Integer code to decode

    Returns:
        str: Decoded string value, "__NULL__" for 0, "" if unknown
    """
    if code == 0:
        return "__NULL__"

    cache = _DECODE_CACHE.get(attr_name_code)
    if cache is None:
        return ""

    return cache.get(code, "")


def resolve_text_to_code(attr_name_code: str, text_value: str) -> int:
    """
    Return code for decoded text (for Inspector text filter).

    Args:
        attr_name_code: Attribute name with _code suffix (e.g., "amenity_code")
        text_value: Text value to look up (e.g., "university")

    Returns:
        int: Integer code, or -1 if not found
    """
    if not text_value:
        return -1

    v = text_value.strip().lower()
    if not v:
        return -1

    cache = _ENCODE_CACHE.get(attr_name_code)
    if cache is None:
        return -1

    # Case-insensitive search
    for key, code in cache.items():
        if key.lower() == v:
            return code

    return -1


def legend_export_csv(output_path: str) -> int:
    """
    Export all cached legends to a single CSV file.

    Args:
        output_path: Path to output CSV file

    Returns:
        int: Number of rows written
    """
    rows_written = 0

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['attr_name_code', 'code', 'decoded_value'])

            for attr_name_code in sorted(_DECODE_CACHE.keys()):
                code_to_value = _DECODE_CACHE[attr_name_code]
                for code in sorted(code_to_value.keys()):
                    value = code_to_value[code]
                    writer.writerow([attr_name_code, code, value])
                    rows_written += 1

        print(f"[LegendEncoding] Exported {rows_written} rows to {output_path}")
        return rows_written

    except Exception as ex:
        print(f"[LegendEncoding] Export failed: {ex}")
        return 0


def get_cached_code_keys():
    """
    Get list of attr_name_code keys currently in cache.

    Returns:
        list: List of attr_name_code strings (e.g., ["amenity_code", "building_code"])
    """
    return sorted(_ENCODE_CACHE.keys())


# ============================================================================
# TILE ID MANAGEMENT (source_tile string -> tile_id int)
# ============================================================================


def get_or_create_tile_id(source_tile_normalized: str) -> int:
    """
    Get or create a tile_id for a normalized source_tile string.

    Args:
        source_tile_normalized: Normalized tile name (stem, no extension)

    Returns:
        int: Tile ID (1-based, 0 reserved for unknown)
    """
    global _NEXT_TILE_ID

    if not source_tile_normalized:
        return 0

    # Check if already assigned
    if source_tile_normalized in _TILE_ID_MAP:
        return _TILE_ID_MAP[source_tile_normalized]

    # Assign new tile_id
    tile_id = _NEXT_TILE_ID
    _TILE_ID_MAP[source_tile_normalized] = tile_id
    _TILE_ID_REVERSE[tile_id] = source_tile_normalized
    _NEXT_TILE_ID += 1

    return tile_id


def get_source_tile_for_id(tile_id: int) -> str:
    """
    Get the source_tile string for a tile_id.

    Args:
        tile_id: Tile ID integer

    Returns:
        str: Normalized source_tile string, or "" if not found
    """
    return _TILE_ID_REVERSE.get(tile_id, "")


def get_tile_id_for_source(source_tile_normalized: str) -> int:
    """
    Get tile_id for a source_tile (returns 0 if not found).

    Args:
        source_tile_normalized: Normalized tile name

    Returns:
        int: Tile ID, or 0 if not found
    """
    return _TILE_ID_MAP.get(source_tile_normalized, 0)


def export_tile_id_map(output_path: str) -> int:
    """
    Export tile_id -> source_tile mapping to CSV.

    Args:
        output_path: Path to output CSV file

    Returns:
        int: Number of rows written
    """
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['tile_id', 'source_tile'])

            for tile_id in sorted(_TILE_ID_REVERSE.keys()):
                source_tile = _TILE_ID_REVERSE[tile_id]
                writer.writerow([tile_id, source_tile])

        rows = len(_TILE_ID_REVERSE)
        print(f"[LegendEncoding] Exported {rows} tile IDs to {output_path}")
        return rows

    except Exception as ex:
        print(f"[LegendEncoding] Tile ID export failed: {ex}")
        return 0


def load_tile_id_map(input_path: str) -> int:
    """
    Load tile_id -> source_tile mapping from CSV.

    Args:
        input_path: Path to input CSV file

    Returns:
        int: Number of rows loaded
    """
    global _NEXT_TILE_ID

    if not os.path.exists(input_path):
        return 0

    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            max_id = 0
            for row in reader:
                tile_id = int(row['tile_id'])
                source_tile = row['source_tile']
                _TILE_ID_MAP[source_tile] = tile_id
                _TILE_ID_REVERSE[tile_id] = source_tile
                if tile_id > max_id:
                    max_id = tile_id

            _NEXT_TILE_ID = max_id + 1

        rows = len(_TILE_ID_MAP)
        print(f"[LegendEncoding] Loaded {rows} tile IDs from {input_path}")
        return rows

    except Exception as ex:
        print(f"[LegendEncoding] Tile ID load failed: {ex}")
        return 0


def clear_tile_id_cache():
    """Clear the tile ID cache (for fresh run)."""
    global _NEXT_TILE_ID
    _TILE_ID_MAP.clear()
    _TILE_ID_REVERSE.clear()
    _NEXT_TILE_ID = 1
