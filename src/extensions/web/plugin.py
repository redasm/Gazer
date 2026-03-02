"""Web Tools plugin — bundled Layer 2 plugin.

Registers web_search / web_fetch / web_report tools.
"""

from plugins.api import PluginAPI
from tools.web_tools import WebSearchTool, WebFetchTool, WebReportTool


def setup(api: PluginAPI) -> None:
    """Plugin entry point — register web tools."""
    api.register_tool(WebSearchTool())
    api.register_tool(WebFetchTool())
    api.register_tool(WebReportTool(memory_manager=api.memory))
