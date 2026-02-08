"""
pipeline/m1_terrain_csv.py

Robust CSV parser for terrain tile extents (metadata-based placement).

Public API:
- load_tile_csv(path: str) -> list[dict]
  Parse CSV with tile coordinates; auto-detect separator and normalize columns.

- compute_world_origin_from_csv(rows: list, tile_size_m: float = 8000.0) -> (min_e: float, min_n: float)
  Compute deterministic WORLD_ORIGIN from tile grid using BBox-center-delta heuristic.

- detect_csv_separator(first_line: str) -> str
  Auto-detect ';' or ',' separator.

Expected CSV columns (case-insensitive, stripped):
  filename / tile / name
  easting / x / east / e
  northing / y / north / n
  Optional: tile_size_m / size / pixel_size

Examples:
  filename; easting; northing; tile_size_m
  dtk10_32288_5624_2.tif; 32288; 5624; 2000
  
  name, x, y, size
  LoD2_32_298_5624_1, 32298, 5624, 1000
"""

import csv
import json
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional

log = logging.getLogger(__name__)


def detect_csv_separator(first_line: str) -> str:
    """
    Heuristic: detect ';' or ',' as separator.
    Priority: ';' if more abundant, else ',', else default ';'.
    """
    semicolon_count = first_line.count(';')
    comma_count = first_line.count(',')
    
    if semicolon_count > comma_count:
        return ';'
    elif comma_count > 0:
        return ','
    else:
        return ';'  # Fallback


def normalize_column_name(col: str) -> str:
    """Lowercase and strip whitespace."""
    return str(col).strip().lower()


def load_tile_csv(path: str) -> List[Dict[str, any]]:
    """
    Load and parse terrain tile CSV file robustly.
    
    Args:
        path: Path to CSV file
    
    Returns:
        List of dicts with keys: filename, easting, northing, tile_size_m (if present)
    
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If required columns missing
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"CSV file not found: {p}")
    
    try:
        with open(p, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
        
        sep = detect_csv_separator(first_line)
        log.info(f"[TerrainCSV] Auto-detected separator: '{sep}'")
        
        rows = []
        with open(p, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f, delimiter=sep)
            
            if not reader.fieldnames:
                raise ValueError("CSV file is empty")
            
            # Normalize header names
            headers_normalized = {
                normalize_column_name(h): h for h in reader.fieldnames
            }
            log.info(f"[TerrainCSV] Headers (normalized): {list(headers_normalized.keys())}")
            
            # Find column mappings
            col_filename = None
            col_easting = None
            col_northing = None
            col_tile_size = None
            
            for norm, orig in headers_normalized.items():
                if norm in ('filename', 'tile', 'name'):
                    col_filename = orig
                elif norm in ('easting', 'x', 'east', 'e'):
                    col_easting = orig
                elif norm in ('northing', 'y', 'north', 'n'):
                    col_northing = orig
                elif norm in ('tile_size_m', 'size', 'pixel_size'):
                    col_tile_size = orig
            
            # Validate required columns
            if not col_filename:
                raise ValueError(f"Missing filename/tile/name column. Available: {list(headers_normalized.keys())}")
            if not col_easting:
                raise ValueError(f"Missing easting/x/east/e column. Available: {list(headers_normalized.keys())}")
            if not col_northing:
                raise ValueError(f"Missing northing/y/north/n column. Available: {list(headers_normalized.keys())}")
            
            log.info(f"[TerrainCSV] Column mapping: filename={col_filename}, e={col_easting}, n={col_northing}, size={col_tile_size or 'N/A'}")
            
            # Parse rows
            row_count = 0
            for row_dict in reader:
                try:
                    filename = str(row_dict[col_filename]).strip()
                    easting = float(row_dict[col_easting])
                    northing = float(row_dict[col_northing])
                    tile_size = float(row_dict[col_tile_size]) if col_tile_size and row_dict.get(col_tile_size) else None
                    
                    rows.append({
                        'filename': filename,
                        'easting': easting,
                        'northing': northing,
                        'tile_size_m': tile_size,
                    })
                    row_count += 1
                except (ValueError, KeyError) as e:
                    log.warning(f"[TerrainCSV] Skipping row {row_count + 1}: {e}")
                    continue
            
            log.info(f"[TerrainCSV] Loaded {row_count} tiles from {p.name}")
            return rows
    
    except Exception as e:
        log.error(f"[TerrainCSV] Failed to load CSV: {e}")
        raise


def most_common_positive_step(values: List[float]) -> Optional[float]:
    """
    Find most common positive step between sorted unique values.
    Used for tile grid spacing heuristic.
    """
    unique = sorted(set(values))
    if len(unique) < 2:
        return None
    
    diffs = [b - a for a, b in zip(unique, unique[1:]) if (b - a) > 0]
    if not diffs:
        return None
    
    # Count occurrences
    counts = {}
    for d in diffs:
        counts[d] = counts.get(d, 0) + 1
    
    # Return most common
    return max(counts.items(), key=lambda kv: kv[1])[0]


def compute_world_origin_from_csv(
    rows: List[Dict[str, any]],
    tile_size_m: float = 8000.0,
    corner_type: str = "CORNER"
) -> Tuple[float, float]:
    """
    Compute deterministic WORLD_ORIGIN from tile CSV.
    
    Strategy:
    1. Assume CSV easting/northing are tile coordinates (either CORNER or CENTER).
    2. Heuristic: Try both interpretations and pick the one with best grid consistency.
    3. Grid consistency = most common step matches tile_size_m (or clean divisor).
    
    Args:
        rows: List of dicts with 'easting', 'northing', 'tile_size_m' keys
        tile_size_m: Expected tile size in meters (default 8000m)
        corner_type: "CORNER" or "CENTER" (hint; we validate both)
    
    Returns:
        (min_e, min_n) tuple for WORLD_ORIGIN
    """
    if not rows:
        log.warning("[TerrainCSV] Empty rows; returning (0, 0)")
        return 0.0, 0.0
    
    # Extract tile coordinates and sizes
    e_values = []
    n_values = []
    tile_sizes = []
    
    for row in rows:
        e_values.append(float(row['easting']))
        n_values.append(float(row['northing']))
        if row.get('tile_size_m'):
            tile_sizes.append(float(row['tile_size_m']))
    
    # Infer tile size if not in CSV
    inferred_tile_size = tile_size_m
    if tile_sizes:
        # Use most common tile size
        size_counts = {}
        for ts in tile_sizes:
            size_counts[ts] = size_counts.get(ts, 0) + 1
        inferred_tile_size = max(size_counts.items(), key=lambda kv: kv[1])[0]
        log.info(f"[TerrainCSV] Inferred tile_size_m from CSV: {inferred_tile_size}")
    
    # Candidate 1: CSV coords as CORNER (world_min = min(e), min(n))
    min_e_candidate1 = min(e_values)
    min_n_candidate1 = min(n_values)
    
    # Candidate 2: CSV coords as CENTER (world_min = min(e - size/2), min(n - size/2))
    half_size = inferred_tile_size / 2.0
    min_e_candidate2 = min(e - half_size for e in e_values)
    min_n_candidate2 = min(n - half_size for n in n_values)
    
    # Validate grid consistency for both candidates
    def grid_consistency(e_vals, n_vals, tile_size):
        """Check if most common step matches tile_size or is a clean divisor."""
        e_step = most_common_positive_step(e_vals)
        n_step = most_common_positive_step(n_vals)
        
        if not e_step or not n_step:
            return -1, e_step, n_step  # Invalid (no step detected)
        
        # Score: how close is e_step/n_step to tile_size?
        # Also accept clean divisors (e.g., tile_size / 2, tile_size / 4)
        e_score = 1.0 if abs(e_step - tile_size) < 0.1 else 0.1
        n_score = 1.0 if abs(n_step - tile_size) < 0.1 else 0.1
        
        # Check if tile_size is divisible
        if tile_size > 0:
            if abs(tile_size % e_step) < 0.1 and abs(tile_size % n_step) < 0.1:
                e_score = max(e_score, 0.5)
                n_score = max(n_score, 0.5)
        
        return e_score + n_score, e_step, n_step
    
    score1, e_step1, n_step1 = grid_consistency(e_values, n_values, inferred_tile_size)
    
    # For candidate 2, recompute steps with shifted coords
    shifted_e = [e - half_size for e in e_values]
    shifted_n = [n - half_size for n in n_values]
    score2, e_step2, n_step2 = grid_consistency(shifted_e, shifted_n, inferred_tile_size)
    
    log.info(f"[TerrainCSV] Grid consistency: CORNER_score={score1:.2f} (e_step={e_step1}, n_step={n_step1}), CENTER_score={score2:.2f} (e_step={e_step2}, n_step={n_step2})")
    
    # Pick candidate with best score
    if score1 >= score2:
        min_e = min_e_candidate1
        min_n = min_n_candidate1
        method = "CORNER"
    else:
        min_e = min_e_candidate2
        min_n = min_n_candidate2
        method = "CENTER"
    
    log.info(f"[TerrainCSV] Selected {method} interpretation: world_origin = ({min_e:.1f}, {min_n:.1f})")
    return float(min_e), float(min_n)


def load_tile_csv_and_compute_origin(
    csv_path: str,
    tile_size_m: float = 8000.0
) -> Tuple[List[Dict], float, float]:
    """
    Convenience function: load CSV and compute WORLD_ORIGIN in one call.
    
    Returns:
        (rows, min_e, min_n)
    """
    rows = load_tile_csv(csv_path)
    min_e, min_n = compute_world_origin_from_csv(rows, tile_size_m)
    return rows, min_e, min_n
