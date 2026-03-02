"""Hardware Tools plugin — bundled Layer 2.

Requires services: body (BodyDriver), spatial (SpatialPerceiver, optional).
"""

from plugins.api import PluginAPI
from tools.hardware import HardwareControlTool, VisionTool


def setup(api: PluginAPI) -> None:
    body = api.get_service("body")
    spatial = api.get_service("spatial")
    if body:
        api.register_tool(HardwareControlTool(body))
    api.register_tool(VisionTool(spatial))
