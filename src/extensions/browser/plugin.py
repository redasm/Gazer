"""Browser plugin — bundled Layer 2."""

from plugins.api import PluginAPI
from tools.browser_tool import BrowserTool


def setup(api: PluginAPI) -> None:
    api.register_tool(BrowserTool())
