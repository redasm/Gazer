"""Gazer Plugin Infrastructure.

Provides plugin discovery, loading, and lifecycle management
inspired by OpenClaw's extension system.
"""

from plugins.manifest import PluginManifest, PluginSlot
from plugins.hooks import HookRegistry
from plugins.api import PluginAPI
from plugins.loader import PluginLoader
from plugins.groups import ToolGroupResolver

__all__ = [
    "PluginManifest",
    "PluginSlot",
    "HookRegistry",
    "PluginAPI",
    "PluginLoader",
    "ToolGroupResolver",
]
