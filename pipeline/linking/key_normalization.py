"""
Key normalization — SINGLE SOURCE OF TRUTH for source_tile keys.

Every place that creates, stores, or looks up (source_tile, building_idx) keys
must use ``normalize_source_tile()`` from this module. No other normalization
functions should exist.

Contract:
    normalize_source_tile(x) -> stable string key
        - Strips directories  (path/to/tile.gml  → tile)
        - Strips ALL extensions (.gml, .xml, .citygml, .001, .002 …)
        - Handles None / empty → ""
        - Pure function, no Blender dependency

The only acceptable global building key is:
    (normalize_source_tile(source_tile), int(building_idx))
"""

import re
from pathlib import PurePosixPath

__all__ = ["normalize_source_tile"]

# Blender duplicate-object suffixes: .001, .002, …
_BLENDER_DUP_RE = re.compile(r"\.\d{3}$")


def normalize_source_tile(x) -> str:
    """Return a stable tile key from any representation.

    Accepts full paths, filenames with extensions, or already-normalized stems.
    Always returns a string; never raises.
    """
    if x is None:
        return ""
    s = str(x).strip()
    if not s:
        return ""
    # Normalize path separators and keep only the filename part
    s = s.replace("\\", "/").split("/")[-1]
    # Strip file extension (.gml, .xml, .citygml, etc.) via pathlib
    s = PurePosixPath(s).stem
    # Strip Blender duplicate suffixes (.001, .002, …)
    s = _BLENDER_DUP_RE.sub("", s)
    return s
