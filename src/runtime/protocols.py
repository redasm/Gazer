from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConfigProvider(Protocol):
    """Minimal configuration access contract for runtime subsystems."""

    def get(self, key_path: str, default: Any = None) -> Any:
        ...

    def _resolve_workspace_root(self) -> Path:
        ...


@runtime_checkable
class ToolExecutionPort(Protocol):
    """Tool execution boundary shared by flow and multi-agent runtimes."""

    async def execute(self, name: str, params: dict[str, Any], **kwargs: Any) -> str:
        ...

    def get_definitions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class AgentRuntimePort(Protocol):
    """Lifecycle boundary for the orchestrating runtime."""

    async def start(self) -> None:
        ...

    def stop(self) -> None:
        ...
