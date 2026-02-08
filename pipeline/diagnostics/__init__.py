"""
pipeline/diagnostics: Diagnostics and debugging domain.

Public API:
- run_diagnostic(gpkg_path, out_path) -> Path
- run_debug_report(out_path) -> Path
- run_full_gpkg_gml_report(out_path) -> Path
- write_m1dc_report_txt(...) -> Path

Placement Tripwires (Safety Checks):
- run_placement_tripwires(lods, tile_size_m, before_snapshot)
- assert_tile_scale_is_one(lods, tol)
- assert_tiles_not_collapsed(lods, min_spacing)
- assert_tiles_are_local(lods, limit)
- snapshot_tiles(lods)
- compare_snapshots(before, after)
"""

from .diagnostic import (
    run_diagnostic,
    run_debug_report,
    run_full_gpkg_gml_report,
    write_m1dc_report_txt,
    DEFAULT_EXPORT_PATH,
    DEFAULT_EXPORT_PATH_DEBUG,
    DEFAULT_EXPORT_PATH_FULL,
)

from .placement_checks import (
    run_placement_tripwires,
    assert_tile_scale_is_one,
    assert_tiles_not_collapsed,
    assert_tiles_are_local,
    snapshot_tiles,
    compare_snapshots,
)

__all__ = [
    # Existing diagnostics
    "run_diagnostic",
    "run_debug_report",
    "run_full_gpkg_gml_report",
    "write_m1dc_report_txt",
    "DEFAULT_EXPORT_PATH",
    "DEFAULT_EXPORT_PATH_DEBUG",
    "DEFAULT_EXPORT_PATH_FULL",
    # Placement tripwires
    "run_placement_tripwires",
    "assert_tile_scale_is_one",
    "assert_tiles_not_collapsed",
    "assert_tiles_are_local",
    "snapshot_tiles",
    "compare_snapshots",
]
