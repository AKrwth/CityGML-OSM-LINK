"""Robust class auto-loader for Blender add-ons.

Keeps a persistent ordered_classes list so unregister() never crashes even if
register() failed midway. Use register(class_list) to register in order, and
unregister() to reverse the order safely.
"""

import bpy
import traceback
from typing import Iterable, List, Sequence

ordered_classes: List[type] = []


def register(classes: Sequence[type] | None = None):
    """Register classes in order; never leave ordered_classes as None."""
    global ordered_classes
    ordered_classes = []
    if not classes:
        return
    for cls in classes:
        try:
            # If Blender still holds an older copy, unregister first to avoid ValueError.
            if hasattr(bpy.utils, "register_class"):
                try:
                    bpy.utils.unregister_class(cls)
                except Exception:
                    pass
            bpy.utils.register_class(cls)
            ordered_classes.append(cls)
        except Exception:
            traceback.print_exc()


def unregister():
    """Unregister previously registered classes without crashing."""
    global ordered_classes
    if not ordered_classes:
        ordered_classes = []
        return
    for cls in reversed(ordered_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            traceback.print_exc()
    ordered_classes = []
