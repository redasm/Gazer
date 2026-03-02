"""Plugin API: the capability surface exposed to plugins.

Each plugin's ``setup(api)`` entry function receives a :class:`PluginAPI`
instance that lets it register tools, hooks, channels, skill dirs, etc.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from tools.base import Tool
from tools.registry import ToolRegistry
from channels.base import ChannelAdapter
from plugins.hooks import HookRegistry

logger = logging.getLogger("PluginAPI")


class PluginAPI:
    """API object given to plugin ``setup()`` functions.

    Provides capabilities to register tools, hooks, channels, skill dirs,
    and to access config, workspace, bus, and memory.
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        config: Dict[str, Any],
        workspace: Path,
        tool_registry: ToolRegistry,
        hook_registry: HookRegistry,
        bus: Any = None,           # MessageBus (optional, may be None during tests)
        memory: Any = None,        # MemoryManager (optional)
        skill_loader: Any = None,  # SkillLoader (optional)
        services: Optional[Dict[str, Any]] = None,  # Runtime objects (capture_manager, body, etc.)
    ) -> None:
        self._plugin_id = plugin_id
        self._config = config
        self._workspace = workspace
        self._tool_registry = tool_registry
        self._hook_registry = hook_registry
        self._bus = bus
        self._memory = memory
        self._skill_loader = skill_loader
        self._services = services or {}

        # Track what this plugin registered for cleanup
        self._registered_tools: list[str] = []
        self._registered_channels: list[ChannelAdapter] = []
        self._registered_skill_dirs: list[Path] = []

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def config(self) -> Dict[str, Any]:
        """Plugin-specific config from settings.yaml (already validated)."""
        return self._config

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def bus(self) -> Any:
        """MessageBus instance for publishing events."""
        return self._bus

    @property
    def memory(self) -> Any:
        """MemoryManager instance for accessing memory subsystem."""
        return self._memory

    def get_service(self, name: str, default: Any = None) -> Any:
        """Retrieve a runtime service by name.

        Services are runtime objects (e.g. capture_manager, body, orchestrator)
        that brain.py injects for plugins that need them.
        """
        return self._services.get(name, default)

    # ------------------------------------------------------------------
    # Registration methods
    # ------------------------------------------------------------------

    def register_tool(self, tool: Tool) -> None:
        """Register a tool into the global ToolRegistry."""
        self._tool_registry.register(tool)
        self._registered_tools.append(tool.name)
        logger.debug("[%s] Registered tool: %s", self._plugin_id, tool.name)

    def register_hook(self, phase: str, handler: Callable) -> None:
        """Register a lifecycle hook (before_tool_call, after_tool_call, on_error)."""
        self._hook_registry.register(phase, handler)
        logger.debug("[%s] Registered %s hook", self._plugin_id, phase)

    def register_channel(self, channel: ChannelAdapter) -> None:
        """Register a channel adapter (will be bound to bus by the loader)."""
        self._registered_channels.append(channel)
        logger.debug("[%s] Registered channel: %s", self._plugin_id, channel.channel_name)

    def register_skill_dir(self, path: Path) -> None:
        """Register an additional skill directory for SkillLoader discovery."""
        resolved = path if path.is_absolute() else (self._workspace / path)
        self._registered_skill_dirs.append(resolved)
        if self._skill_loader:
            self._skill_loader.add_dir(resolved)
        logger.debug("[%s] Registered skill dir: %s", self._plugin_id, resolved)

    def get_tool(self, name: str) -> Optional[Tool]:
        """Look up a registered tool by name."""
        return self._tool_registry.get(name)

    # ------------------------------------------------------------------
    # Cleanup (called by PluginLoader when unloading a plugin)
    # ------------------------------------------------------------------

    def _teardown(self) -> None:
        """Unregister everything this plugin registered."""
        for name in self._registered_tools:
            self._tool_registry.unregister(name)
        self._registered_tools.clear()
        self._registered_channels.clear()
        self._registered_skill_dirs.clear()
        logger.debug("[%s] Plugin teardown complete", self._plugin_id)
