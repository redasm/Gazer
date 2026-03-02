"""Delegate Task plugin — bundled Layer 2.

Requires service: orchestrator (AgentOrchestrator instance).
"""

from plugins.api import PluginAPI
from agent.orchestrator import DelegateTaskTool


def setup(api: PluginAPI) -> None:
    orchestrator = api.get_service("orchestrator")
    if not orchestrator:
        return
    api.register_tool(DelegateTaskTool(orchestrator))
