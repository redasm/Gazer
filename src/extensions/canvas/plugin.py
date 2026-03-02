"""Canvas Tools plugin — bundled Layer 2.

Requires service: canvas_state (CanvasState instance).
Only registers tools if canvas_state is available.
"""

from plugins.api import PluginAPI
from tools.canvas import (
    A2UIApplyTool,
    CanvasSnapshotTool,
    CanvasResetTool,
)


def setup(api: PluginAPI) -> None:
    canvas_state = api.get_service("canvas_state")
    if not canvas_state:
        return  # Canvas not configured, skip silently
    for tool in [
        A2UIApplyTool(canvas_state),
        CanvasSnapshotTool(canvas_state),
        CanvasResetTool(canvas_state),
    ]:
        api.register_tool(tool)
