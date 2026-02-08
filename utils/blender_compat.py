"""Blender compatibility shims for pkg_resources and other Python stdlib gaps.

This module provides compatibility fixes for Blender's bundled Python environment,
which sometimes ships with incomplete or broken standard library modules.

CRITICAL: This must be imported FIRST in __init__.py before any other imports
that might depend on pkg_resources.

Migrated from: utils/startup/init_blender_compatibility.py (Phase 13, 2026-02-08)
"""

import sys
import types
import warnings


def ensure_pkg_resources():
    """
    Blender ships a sometimes-broken pkg_resources situation.
    Some libs try: from pkg_resources import get_distribution
    We patch that to avoid hard crashes + suppress transitive import warnings.
    """
    # Suppress pkg_resources deprecation warnings from external libraries (brickschema, sqlalchemy, etc.)
    warnings.filterwarnings("ignore", message=".*pkg_resources.*", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message=".*get_distribution.*", category=ImportWarning)
    
    try:
        import pkg_resources  # noqa
        if hasattr(pkg_resources, "get_distribution"):
            return True

        # fallback: stub get_distribution via importlib.metadata
        try:
            import importlib.metadata as _imd
        except Exception:
            _imd = None

        def _get_distribution(name):
            if _imd is None:
                raise Exception("importlib.metadata not available")

            class _D:
                def __init__(self, n):
                    self.project_name = n
                    self.version = _imd.version(n)

            return _D(name)

        pkg_resources.get_distribution = _get_distribution
        return True

    except Exception:
        # pkg_resources missing entirely -> create stub module
        try:
            import importlib.metadata as _imd
        except Exception:
            _imd = None

        def _get_distribution(name):
            if _imd is None:
                raise Exception("pkg_resources missing and no importlib.metadata")

            class _D:
                def __init__(self, n):
                    self.project_name = n
                    self.version = _imd.version(n)

            return _D(name)

        mod = types.ModuleType("pkg_resources")
        mod.get_distribution = _get_distribution
        sys.modules["pkg_resources"] = mod
        return True
