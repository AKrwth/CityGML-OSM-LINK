"""
Spreadsheet Core: Building table data management helpers.

Extracted from ops.py so spreadsheet_ops.py no longer needs to reach into
ops.py for these functions. All functions delegate to ops.py internals via
lazy import so the dependency direction is: spreadsheet_core → ops (not
the reverse).

Public API (used by spreadsheet_ops.py):
    build_spreadsheet_rows(context, s) -> bool
    perform_face_sync(context, s) -> bool
    select_faces_by_building_idx(context, obj, building_idx) -> bool
    get_active_mesh(context) -> (obj, mesh) | (None, None)
    invalidate_and_rebuild(context, s, reason) -> None
    refresh_tables_only(s, reset_selection) -> None
"""


def _ops():
    """Lazy import of the ops module to avoid circular imports."""
    from ... import ops
    return ops


def get_active_mesh(context):
    """Return (obj, mesh) for the active mesh object, or (None, None)."""
    return _ops()._get_active_mesh(context)


def build_spreadsheet_rows(context, s):
    """
    Build the spreadsheet_rows collection from the active mesh's face attributes.

    Delegates to ops._build_spreadsheet_rows.

    Args:
        context: Blender context
        s: M1DCSettings property group

    Returns:
        True on success, False on error (error stored in s.spreadsheet_last_error)
    """
    fn = getattr(_ops(), "_build_spreadsheet_rows", None)
    if fn is None:
        s.spreadsheet_last_error = "Spreadsheet logic not available (ops._build_spreadsheet_rows missing)"
        return False
    return fn(context, s)


def perform_face_sync(context, s):
    """
    Sync spreadsheet row selection from the active face in Edit Mode.

    Delegates to ops._perform_face_sync.

    Returns:
        True if a matching row was found and selected, False otherwise.
    """
    fn = getattr(_ops(), "_perform_face_sync", None)
    if fn is None:
        return False
    return fn(context, s)


def select_faces_by_building_idx(context, obj, building_idx):
    """
    Select all faces in *obj* that share the given building_idx.

    Delegates to ops._select_faces_by_building_idx.

    Returns:
        True if any faces were selected.
    """
    fn = getattr(_ops(), "_select_faces_by_building_idx", None)
    if fn is None:
        return False
    return fn(context, obj, building_idx)


def invalidate_and_rebuild(context, s, reason="unknown"):
    """
    Atomically invalidate table/column caches and rebuild spreadsheet rows.

    Delegates to ops.spreadsheet_invalidate_and_rebuild.
    """
    fn = getattr(_ops(), "spreadsheet_invalidate_and_rebuild", None)
    if fn:
        fn(context, s, reason=reason)


def refresh_tables_only(s, reset_selection=False):
    """
    Refresh table list and column caches without row rebuild.

    Delegates to ops.spreadsheet_refresh_tables_only.
    """
    fn = getattr(_ops(), "spreadsheet_refresh_tables_only", None)
    if fn:
        fn(s, reset_selection=reset_selection)
