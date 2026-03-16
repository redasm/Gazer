"""Lightweight per-turn hook manager for agent loop lifecycle events."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("AgentTurnHooks")

HookCallback = Callable[[Dict[str, Any]], Any]


class TurnHookManager:
    """Dispatches lightweight hooks around agent lifecycle events.

    Existing events
    ~~~~~~~~~~~~~~~
    * ``before_prompt_build``  -- fired before the prompt messages are assembled.
    * ``after_tool_result``    -- fired after each tool call result is processed.
    * ``after_turn``           -- fired when the agent turn finishes.

    Extended events (OpenClaw parity)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    * ``message:received``  -- inbound message consumed from the bus.
    * ``message:sent``      -- outbound response published to the bus.
    * ``session:start``     -- first message in a fresh session.
    * ``session:reset``     -- session was explicitly reset.
    * ``session:end``       -- turn completed; carries ``message_count`` and ``agent_id``.
    """

    def __init__(self) -> None:
        self._before_prompt_build: List[HookCallback] = []
        self._after_tool_result: List[HookCallback] = []
        self._after_turn: List[HookCallback] = []
        # Extended event lists
        self._message_received: List[HookCallback] = []
        self._message_sent: List[HookCallback] = []
        self._session_start: List[HookCallback] = []
        self._session_reset: List[HookCallback] = []
        self._session_end: List[HookCallback] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def on_before_prompt_build(self, callback: HookCallback) -> None:
        self._before_prompt_build.append(callback)

    def on_after_tool_result(self, callback: HookCallback) -> None:
        self._after_tool_result.append(callback)

    def on_after_turn(self, callback: HookCallback) -> None:
        self._after_turn.append(callback)

    def on_message_received(self, callback: HookCallback) -> None:
        """Register a callback for ``message:received`` events."""
        self._message_received.append(callback)

    def on_message_sent(self, callback: HookCallback) -> None:
        """Register a callback for ``message:sent`` events."""
        self._message_sent.append(callback)

    def on_session_start(self, callback: HookCallback) -> None:
        """Register a callback for ``session:start`` events."""
        self._session_start.append(callback)

    def on_session_reset(self, callback: HookCallback) -> None:
        """Register a callback for ``session:reset`` events."""
        self._session_reset.append(callback)

    def on_session_end(self, callback: HookCallback) -> None:
        """Register a callback for ``session:end`` events."""
        self._session_end.append(callback)

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    async def emit_before_prompt_build(self, payload: Dict[str, Any]) -> None:
        await self._emit(self._before_prompt_build, payload, event_name="before_prompt_build")

    async def emit_after_tool_result(self, payload: Dict[str, Any]) -> None:
        await self._emit(self._after_tool_result, payload, event_name="after_tool_result")

    async def emit_after_turn(self, payload: Dict[str, Any]) -> None:
        await self._emit(self._after_turn, payload, event_name="after_turn")

    async def emit_message_received(self, payload: Dict[str, Any]) -> None:
        """Emit ``message:received`` — called when an inbound message is consumed."""
        await self._emit(self._message_received, payload, event_name="message:received")

    async def emit_message_sent(self, payload: Dict[str, Any]) -> None:
        """Emit ``message:sent`` — called after the outbound response is published."""
        await self._emit(self._message_sent, payload, event_name="message:sent")

    async def emit_session_start(self, payload: Dict[str, Any]) -> None:
        """Emit ``session:start`` — called on the first message of a new session."""
        await self._emit(self._session_start, payload, event_name="session:start")

    async def emit_session_reset(self, payload: Dict[str, Any]) -> None:
        """Emit ``session:reset`` — called when the session is explicitly reset."""
        await self._emit(self._session_reset, payload, event_name="session:reset")

    async def emit_session_end(self, payload: Dict[str, Any]) -> None:
        """Emit ``session:end`` — called after each turn completes."""
        await self._emit(self._session_end, payload, event_name="session:end")

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
