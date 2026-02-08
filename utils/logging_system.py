"""
M1DC Central Logging System.

Replaces all print() calls with a centralized buffer.
Provides log_info(), log_warn(), log_error() functions.
Enables export to .txt and clearing.

Phase 13: Logging Policy "3 examples + summary"
- Reduces terminal spam for repetitive loops
- Full verbosity when running under VSCode debugger
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple


# ============================================================================
# PHASE 13: Verbose Debug Mode Detection
# ============================================================================

def is_verbose_debug() -> bool:
    """
    Detect if running under VSCode debugger (or any debugger).
    
    Returns True if sys.gettrace() is active (debugger attached).
    When True: full logging verbosity (no suppression).
    When False: apply "3 examples + progress + summary" policy.
    """
    return sys.gettrace() is not None


class M1DCLogger:
    """Central logging buffer for all M1DC operations."""

    def __init__(self):
        self.buffer: List[Tuple[str, str, str]] = []  # (level, timestamp, message)
        self.started = datetime.now()

    def info(self, msg: str):
        """Log an INFO message."""
        ts = datetime.now().isoformat()
        self.buffer.append(("INFO", ts, msg))
        print(f"[M1DC INFO] {msg}")

    def warn(self, msg: str):
        """Log a WARNING message."""
        ts = datetime.now().isoformat()
        self.buffer.append(("WARN", ts, msg))
        print(f"[M1DC WARN] {msg}")

    def error(self, msg: str):
        """Log an ERROR message."""
        ts = datetime.now().isoformat()
        self.buffer.append(("ERROR", ts, msg))
        print(f"[M1DC ERROR] {msg}")

    def clear(self):
        """Clear the log buffer."""
        self.buffer = []

    def export_txt(self, out_path: Path) -> Path:
        """Export log to .txt file."""
        lines = [
            "=" * 80,
            "M1DC SESSION LOG",
            "=" * 80,
            f"Started: {self.started.isoformat()}",
            f"Exported: {datetime.now().isoformat()}",
            f"Total entries: {len(self.buffer)}",
            "",
        ]
        for level, ts, msg in self.buffer:
            lines.append(f"[{level}] {ts}: {msg}")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    def get_summary(self) -> str:
        """Return quick status summary."""
        info_count = sum(1 for l, _, _ in self.buffer if l == "INFO")
        warn_count = sum(1 for l, _, _ in self.buffer if l == "WARN")
        error_count = sum(1 for l, _, _ in self.buffer if l == "ERROR")
        return f"Log: {info_count} INFO · {warn_count} WARN · {error_count} ERROR"


# Global instance
_logger = M1DCLogger()


def log_info(msg: str):
    """Log an INFO message."""
    _logger.info(msg)


def log_warn(msg: str):
    """Log a WARNING message."""
    _logger.warn(msg)


def log_error(msg: str):
    """Log an ERROR message."""
    _logger.error(msg)


def get_logger() -> M1DCLogger:
    """Get the global logger instance."""
    return _logger


# ============================================================================
# PHASE 13: Loop Progress Tracker
# ============================================================================

class LoopProgressTracker:
    """
    Track progress through repetitive loops with "3 examples + progress + summary" policy.
    
    Usage:
        tracker = LoopProgressTracker("CityGML Import", total_items=56, progress_interval=10)
        for i, tile in enumerate(tiles):
            if tracker.should_log_detail(i):
                # Print full detail
                print(f"[CityGML] tile={tile.name} vertices={...} faces={...}")
            elif tracker.should_log_progress(i):
                # Print compact progress
                print(f"[CityGML] progress: tile {i+1}/{total} ...")
            
            # Do work
            ...
            
        # Always print summary
        print(f"[CityGML] SUMMARY: tiles={total} ...")
    """
    
    def __init__(self, context: str, total_items: int, progress_interval: int = 10, detail_limit: int = 3):
        """
        Args:
            context: Name of the operation (e.g., "CityGML Import")
            total_items: Total number of items to process
            progress_interval: Print progress every N items after detail limit
            detail_limit: Number of items to show full detail for (default 3)
        """
        self.context = context
        self.total_items = total_items
        self.progress_interval = progress_interval
        self.detail_limit = detail_limit
        self.verbose = is_verbose_debug()
        
    def should_log_detail(self, index: int) -> bool:
        """
        Return True if should print full detail for this item.
        
        In verbose mode: always True
        In normal mode: True for first N items (detail_limit)
        """
        if self.verbose:
            return True
        return index < self.detail_limit
    
    def should_log_progress(self, index: int) -> bool:
        """
        Return True if should print progress line for this item.
        
        In verbose mode: never (full detail always shown)
        In normal mode: True every progress_interval items after detail_limit
        """
        if self.verbose:
            return False
        if index < self.detail_limit:
            return False
        return (index + 1) % self.progress_interval == 0
