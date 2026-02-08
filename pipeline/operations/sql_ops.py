"""
SQL query operators for GPKG/LinkDB/MKDB exploration.
"""
import bpy
import sqlite3
import os
import time
import hashlib
from pathlib import Path
from bpy.types import Operator
from bpy.props import StringProperty


def log_info(msg):
    try:
        from ...utils.logging_system import log_info as _log_info
        _log_info(msg)
    except ImportError:
        print(msg)

def log_warn(msg):
    try:
        from ...utils.logging_system import log_warn as _log_warn
        _log_warn(msg)
    except ImportError:
        print(f"[WARN] {msg}")

def log_error(msg):
    try:
        from ...utils.logging_system import log_error as _log_error
        _log_error(msg)
    except ImportError:
        print(f"[ERROR] {msg}")

def _settings(context):
    """Get scene settings"""
    return getattr(context.scene, "m1dc_settings", None)


class M1DC_OT_SQLRun(Operator):
    """Execute SQL query against GPKG/SQLite database (read-only)"""
    bl_idname = "m1dc.sql_run"
    bl_label = "Run SQL Query"
    bl_options = {"REGISTER"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            self.report({"ERROR"}, "Settings not found")
            return {"CANCELLED"}

        # Get query and validate
        query = getattr(s, "sql_query_text", "").strip()

        # DETERMINISM PROOF LOGGING
        query_sha1 = hashlib.sha1(query.encode("utf-8")).hexdigest()
        print(f"[M1DC SQL] query_repr={repr(query)}")
        print(f"[M1DC SQL] query_sha1={query_sha1}")
        log_info(f"[SQL DEBUG] query from sql_query_text: {repr(query)}")
        log_info(f"[SQL DEBUG] query length: {len(query)} chars, sha1={query_sha1}")

        if not query:
            s.sql_result_text = "ERROR: Empty query"
            s.sql_result_rows = 0
            s.sql_result_ms = 0.0
            self.report({"WARNING"}, "Query is empty")
            return {"CANCELLED"}

        # Safety check: only allow read-only queries (SELECT, WITH, PRAGMA)
        query_lower = query.lstrip().lower()

        # CRITICAL SAFETY: Scan entire query for destructive keywords
        destructive_keywords = [
            "insert", "update", "delete", "drop", "alter", "create",
            "attach", "detach", "vacuum", "reindex", "replace"
        ]
        for kw in destructive_keywords:
            import re
            if re.search(rf'\b{kw}\b', query_lower):
                error_msg = (
                    f"ERROR: Destructive keyword '{kw.upper()}' detected.\n"
                    f"Only read-only queries allowed (SELECT, WITH, read-only PRAGMA).\n"
                    f"Blocked for safety."
                )
                s.sql_result_text = error_msg
                s.sql_result_rows = 0
                s.sql_result_ms = 0.0
                self.report({"ERROR"}, f"Destructive keyword '{kw}' blocked")
                log_warn(f"[SQL] Blocked destructive keyword '{kw}' in query: {query[:80]}")
                return {"CANCELLED"}

        # Get database path based on target
        db_target = getattr(s, "sql_db_target", "GPKG")
        
        if db_target == "GPKG":
            db_path = getattr(s, "gpkg_path", "").strip()
            if not db_path or not os.path.isfile(db_path):
                s.sql_result_text = "ERROR: GPKG path not set or file not found.\nSet GeoPackage path in Input Wizard first."
                s.sql_result_rows = 0
                self.report({"ERROR"}, "GPKG path not configured")
                return {"CANCELLED"}
        elif db_target == "LINKDB":
            linkdb_path = getattr(s, "links_db_path", "").strip()
            if not linkdb_path or not os.path.isfile(linkdb_path):
                s.sql_result_text = "ERROR: LinkDB not found.\nRun pipeline Phase 2 (linking) first."
                s.sql_result_rows = 0
                self.report({"ERROR"}, "LinkDB not found")
                return {"CANCELLED"}
            db_path = linkdb_path
        elif db_target == "MKDB":
            mkdb_path_setting = getattr(s, "mkdb_path", "").strip()
            if not mkdb_path_setting or not os.path.isfile(mkdb_path_setting):
                s.sql_result_text = "ERROR: MKDB not found.\nRun pipeline Phase 4.5 (build mkdb) first."
                s.sql_result_rows = 0
                self.report({"ERROR"}, "MKDB not found")
                return {"CANCELLED"}
            db_path = mkdb_path_setting
        else:
            s.sql_result_text = f"ERROR: Unknown DB target: {db_target}"
            s.sql_result_rows = 0
            self.report({"ERROR"}, f"Unknown target: {db_target}")
            return {"CANCELLED"}

        print(f"[SQL] target={db_target} db={db_path} readonly=ON")
        log_info(f"[SQL] target={db_target} db={db_path}")

        # Execute query
        conn = None
        try:
            uri = f"file:{Path(db_path).as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.execute("PRAGMA query_only = ON;")
            conn.execute("PRAGMA busy_timeout = 5000;")

            log_info(f"[SQL] Executing query against: {db_path}")
            log_info(f"[SQL] Query preview: {query[:100]}{'...' if len(query) > 100 else ''}")

            start_time = time.time()
            cursor = conn.execute(query)

            # Fetch limited rows
            limit = getattr(s, "sql_limit_rows", 200)
            rows = cursor.fetchmany(limit)
            elapsed_ms = (time.time() - start_time) * 1000.0

            # Get column names
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            # Format result as text table
            if not rows:
                result_text = "(no rows returned)"
                row_count = 0
            else:
                # Header row
                lines = ["\t".join(columns)]

                # Data rows
                for row in rows:
                    line = "\t".join(str(val) if val is not None else "NULL" for val in row)
                    lines.append(line)

                result_text = "\n".join(lines)
                row_count = len(rows)

            # Store results
            s.sql_result_text = result_text
            s.sql_result_rows = row_count
            s.sql_result_ms = elapsed_ms

            log_info(f"[SQL] ✓ Query completed: {row_count} rows in {elapsed_ms:.1f} ms")
            self.report({"INFO"}, f"Query executed: {row_count} rows in {elapsed_ms:.1f} ms")
            return {"FINISHED"}

        except sqlite3.Error as ex:
            error_msg = f"SQL Error: {str(ex)}"
            s.sql_result_text = f"ERROR: {error_msg}"
            s.sql_result_rows = 0
            s.sql_result_ms = 0.0
            log_error(f"[SQL] Query failed: {ex}")
            self.report({"ERROR"}, error_msg)
            return {"CANCELLED"}

        except Exception as ex:
            error_msg = f"Unexpected error: {str(ex)}"
            s.sql_result_text = f"ERROR: {error_msg}"
            s.sql_result_rows = 0
            s.sql_result_ms = 0.0
            log_error(f"[SQL] Exception: {ex}")
            self.report({"ERROR"}, error_msg)
            import traceback
            traceback.print_exc()
            return {"CANCELLED"}

        finally:
            if conn:
                conn.close()


class M1DC_OT_SQLClear(Operator):
    """Clear SQL query results"""
    bl_idname = "m1dc.sql_clear"
    bl_label = "Clear SQL Results"
    bl_options = {"REGISTER"}

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        s.sql_result_text = ""
        s.sql_result_rows = 0
        s.sql_result_ms = 0.0

        log_info("[SQL] Results cleared")
        return {"FINISHED"}


def _quote_sql_identifier(name):
    """Quote SQL identifier for safe use in queries (escape internal quotes)."""
    return '"' + name.replace('"', '""') + '"'


class M1DC_OT_SQLTemplate(Operator):
    """Insert SQL query template"""
    bl_idname = "m1dc.sql_template"
    bl_label = "Insert SQL Template"
    bl_options = {"REGISTER"}

    template: StringProperty(default="tables")

    TEMPLATES = {
        # Discovery templates
        "tables": "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 200;",
        "gpkg_tables": """SELECT table_name, data_type, identifier, description
FROM gpkg_contents
WHERE table_name NOT LIKE 'gpkg_%'
ORDER BY data_type, table_name
LIMIT 200;""",
        "pragma_cols": "PRAGMA table_info(<table_name>);",
        "rowcount": "SELECT COUNT(*) AS n FROM <table_name>;",
        "sample": "SELECT * FROM <table_name> LIMIT 50;",
    }

    def execute(self, context):
        s = _settings(context)
        if s is None:
            return {"CANCELLED"}

        template_query = self.TEMPLATES.get(self.template, "")
        if not template_query:
            return {"CANCELLED"}

        # Check if template requires placeholders
        requires_table = "<table_name>" in template_query

        # Validate table name if required
        if requires_table:
            table_name = getattr(s, "sql_table_name", "").strip()
            if not table_name:
                s.sql_result_text = "⚠ Set SQL Table Name field first, then click template again."
                s.sql_result_rows = 0
                log_warn("[SQL] Template requires table name, but none set")
                self.report({"WARNING"}, "Set table name first")
                return {"CANCELLED"}

            # Replace table placeholder with quoted identifier
            template_query = template_query.replace("<table_name>", _quote_sql_identifier(table_name))

        # Insert template into query field
        s.sql_query_text = template_query

        # Log success
        if requires_table:
            log_info(f"[SQL] Inserted template: {self.template} (table: {table_name})")
        else:
            log_info(f"[SQL] Inserted template: {self.template}")

        return {"FINISHED"}
