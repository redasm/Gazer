"""Desktop node tools plugin — bundled Layer 2.

Requires service: device_registry (DeviceRegistry instance).
"""

from plugins.api import PluginAPI
from tools.device_tools import NodeListTool, NodeDescribeTool, NodeInvokeTool, GuiTaskExecuteTool


def setup(api: PluginAPI) -> None:
    device_registry = api.get_service("device_registry")
    for tool in [
        NodeListTool(device_registry),
        NodeDescribeTool(device_registry),
        NodeInvokeTool(device_registry),
        GuiTaskExecuteTool(device_registry),
    ]:
        api.register_tool(tool)
