"""Hook registry for plugin lifecycle events.

Provides before_tool_call / after_tool_call / on_error hooks that plugins
can register via ``PluginAPI.register_hook()``.
"""

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("HookRegistry")

# Supported hook phases
PHASES = frozenset({"before_tool_call", "after_tool_call", "on_error"})

# Hook signatures:
#   before_tool_call(tool_name: str, params: dict) -> Optional[dict]
#       Return modified params, or None to keep original.
#       Raise ``HookAbort`` to block execution.
#   after_tool_call(tool_name: str, params: dict, result: str) -> Optional[str]
#       Return modified result, or None to keep original.
#   on_error(tool_name: str, error: Exception) -> None

HookHandler = Callable[..., Any]


class HookAbort(Exception):
    """Raised by a ``before_tool_call`` hook to block tool execution."""

    def __init__(self, reason: str = "Blocked by hook"):
        self.reason = reason
        super().__init__(reason)


class HookRegistry:
    """Central registry for lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: Dict[str, List[HookHandler]] = defaultdict(list)

    def register(self, phase: str, handler: HookHandler) -> None:
        """Register a hook handler for the given phase.

        Args:
            phase: One of ``before_tool_call``, ``after_tool_call``, ``on_error``.
            handler: Callable matching the phase's expected signature.

        Raises:
            ValueError: If phase is not recognized.
        """
        if phase not in PHASES:
            raise ValueError(f"Unknown hook phase '{phase}'. Must be one of {sorted(PHASES)}")
        self._hooks[phase].append(handler)
        logger.debug("Registered %s hook: %s", phase, handler)

    def unregister(self, phase: str, handler: HookHandler) -> None:
        """Remove a previously registered hook handler."""
        try:
            self._hooks[phase].remove(handler)
        except (KeyError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Invocation helpers (called by ToolRegistry.execute)
    # ------------------------------------------------------------------

    async def run_before_tool_call(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run all ``before_tool_call`` hooks in registration order.

        Returns:
            The (possibly modified) params dict.

        Raises:
            HookAbort: If any hook wants to block execution.
        """
        current = params
        for handler in self._hooks.get("before_tool_call", []):
            try:
                result = handler(tool_name, current)
                # Support async hooks
                if hasattr(result, "__await__"):
                    result = await result
                if isinstance(result, dict):
                    current = result
            except HookAbort:
                raise
            except Exception:
                logger.exception("before_tool_call hook %s failed", handler)
        return current

    async def run_after_tool_call(
        self, tool_name: str, params: Dict[str, Any], result: str
    ) -> str:
        """Run all ``after_tool_call`` hooks. Returns (possibly modified) result."""
        current = result
        for handler in self._hooks.get("after_tool_call", []):
            try:
                out = handler(tool_name, params, current)
                if hasattr(out, "__await__"):
                    out = await out
                if isinstance(out, str):
                    current = out
            except Exception:
                logger.exception("after_tool_call hook %s failed", handler)
        return current

    async def run_on_error(self, tool_name: str, error: Exception) -> None:
        """Run all ``on_error`` hooks (fire-and-forget, errors are logged)."""
        for handler in self._hooks.get("on_error", []):
            try:
                out = handler(tool_name, error)
                if hasattr(out, "__await__"):
                    await out
            except Exception:
                logger.exception("on_error hook %s failed", handler)

    def clear(self) -> None:
        """Remove all registered hooks."""
        self._hooks.clear()

    @property
    def hook_count(self) -> int:
        return sum(len(v) for v in self._hooks.values())
