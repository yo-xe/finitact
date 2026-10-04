"""Runs inside Blender (``blender --factory-startup --python <this> -- <state.json>``).

Publishes the evaluation oracle's target-specific state (ADR-0014) from bpy, a channel the system
under test never reads: the UI is OpenGL-drawn, so neither UIA nor screen capture sees this data.
"""

import json
import os
import sys

import bpy

STATE_PATH = sys.argv[sys.argv.index("--") + 1]
# The splash is a modal overlay that would swallow the first click of every case.
bpy.context.preferences.view.show_splash = False


def _write_state():
    windows = bpy.context.window_manager.windows
    state = {
        "pid": os.getpid(),
        "workspaces": [window.workspace.name for window in windows],
        "objects": sorted(obj.name for obj in bpy.data.objects),
        "active_object_mode": getattr(bpy.context.view_layer.objects.active, "mode", None),
        "collections": {obj.name: sorted(c.name for c in obj.users_collection) for obj in bpy.data.objects},
    }
    temporary = STATE_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    try:
        os.replace(temporary, STATE_PATH)
    except PermissionError:
        # Windows refuses the rename while the oracle has the file open; a raising timer is
        # unregistered by bpy, which would silently stop the state and fail the oracle.
        pass
    return 0.05


bpy.app.timers.register(_write_state, first_interval=0.5, persistent=True)
