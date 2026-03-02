"""Lightweight per-turn hook manager for agent loop lifecycle events."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("AgentTurnHooks")

HookCallback = Callable[[Dict[str, Any]], Any]


class TurnHookManager:
    """Dispatches lightweight hooks around prompt build, tool results, and turn completion."""

    def __init__(self) -> None:
        self._before_prompt_build: List[HookCallback] = []
        self._after_tool_result: List[HookCallback] = []
        self._after_turn: List[HookCallback] = []

    def on_before_prompt_build(self, callback: HookCallback) -> None:
        self._before_prompt_build.append(callback)

    def on_after_tool_result(self, callback: HookCallback) -> None:
        self._after_tool_result.append(callback)

    def on_after_turn(self, callback: HookCallback) -> None:
        self._after_turn.append(callback)

    async def emit_before_prompt_build(self, payload: Dict[str, Any]) -> None:
        await self._emit(self._before_prompt_build, payload, event_name="before_prompt_build")

    async def emit_after_tool_result(self, payload: Dict[str, Any]) -> None:
        await self._emit(self._after_tool_result, payload, event_name="after_tool_result")

    async def emit_after_turn(self, payload: Dict[str, Any]) -> None:
        await self._emit(self._after_turn, payload, event_name="after_turn")

    @staticmethod
    async def _invoke(callback: HookCallback, payload: Dict[str, Any]) -> None:
        result = callback(payload)
        if asyncio.iscoroutine(result):
            await result

    async def _emit(self, callbacks: List[HookCallback], payload: Dict[str, Any], *, event_name: str) -> None:
        for callback in list(callbacks):
            try:
                await self._invoke(callback, payload)
            except Exception:
                logger.warning("Turn hook failed: event=%s callback=%r", event_name, callback, exc_info=True)
