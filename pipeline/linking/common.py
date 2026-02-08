"""
Shim module for backward compatibility with old imports.

This module re-exports the stable public API from pipeline.linking.linking_cache
so that old code like 'from pipeline.linking.common import ensure_link_dbs' continues to work.

It is a pure re-export; no new functionality is added.
"""

from .linking_cache import ensure_link_dbs

__all__ = ["ensure_link_dbs"]
