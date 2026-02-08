# pipeline/diagnostics/spatial_debug.py
"""
Helpers for spatial diagnostics and debug flags for CityGML/DEM pipeline.
"""

import logging

# Debug flags
VISUAL_SNAP_CITYGML_TO_DEM = False  # Set True to enable visual alignment

# --- Logging helpers ---
def log_spatial_diag(msg):
    logging.getLogger("spatial_diag").info(msg)

def log_citygml_forensics(msg):
    logging.getLogger("citygml_forensics").info(msg)
