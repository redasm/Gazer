"""Skill Registry plugin — bundled Layer 2.

Uses config['registry_url'] and workspace to create the client.
"""

from plugins.api import PluginAPI
from skills.registry_client import SkillRegistryClient, SkillSearchTool, SkillInstallTool


def setup(api: PluginAPI) -> None:
    registry_url = api.config.get("registry_url", "")
    client = SkillRegistryClient(registry_url)
    api.register_tool(SkillSearchTool(client))
    api.register_tool(SkillInstallTool(client, api.workspace / "skills"))
