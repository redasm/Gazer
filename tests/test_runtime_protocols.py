from __future__ import annotations

from runtime.config_manager import config
from runtime.protocols import ConfigProvider, ToolExecutionPort
from tools.registry import ToolRegistry


def test_config_proxy_satisfies_config_provider_protocol() -> None:
    assert isinstance(config, ConfigProvider)


def test_tool_registry_satisfies_tool_execution_protocol() -> None:
    registry = ToolRegistry()
    assert isinstance(registry, ToolExecutionPort)
