"""Integration test: emit_render_hint → OutboundMessage.metadata → web chat_end frame.

Exercises the stitched pipeline across three layers without spinning up a full
AgentLoop:

  1. A tool invoked inside a RenderHintScope emits a RenderHint.
  2. The hint is serialized onto OutboundMessage.metadata["render_hints"]
     the same way process_message._finalize_turn does it.
  3. The WebChannel.send broadcast payload carries the hints array under
     the "chat_end" frame's "render_hints" key.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from bus.events import OutboundMessage
from src.rendering.types import RenderHint
from tools.base import RenderHintScope, emit_render_hint


def _serialize_hints_into_metadata(hints: List[RenderHint]) -> Dict[str, Any]:
    """Mirror of process_message._finalize_turn hint serialization."""
    metadata: Dict[str, Any] = {}
    serialized: List[Dict[str, Any]] = []
    for hint in hints:
        to_dict = getattr(hint, "to_dict", None)
        if callable(to_dict):
            serialized.append(to_dict())
    if serialized:
        metadata["render_hints"] = serialized
    return metadata


class TestHintFlowIntegration:
    def test_tool_emitted_hint_reaches_outbound_metadata(self) -> None:
        def fake_tool() -> str:
            emit_render_hint(
                RenderHint(
                    component="WeatherCard",
                    data={"city": "Beijing", "temp": 21, "condition": "Sunny"},
                    fallback_text="Beijing 21° Sunny",
                )
            )
            return "ok"

        with RenderHintScope() as scope:
            fake_tool()

        assert len(scope.hints) == 1
        metadata = _serialize_hints_into_metadata(scope.hints)
        assert "render_hints" in metadata
        assert metadata["render_hints"][0]["component"] == "WeatherCard"
        assert metadata["render_hints"][0]["data"]["city"] == "Beijing"
        assert metadata["render_hints"][0]["fallback_text"] == "Beijing 21° Sunny"

        out = OutboundMessage(
            channel="web",
            chat_id="c1",
            content="Here's the forecast.",
            metadata=metadata,
        )
        assert out.metadata["render_hints"][0]["component"] == "WeatherCard"

    def test_multiple_tool_calls_accumulate_hints(self) -> None:
        collected: List[RenderHint] = []

        def tool_a() -> None:
            emit_render_hint(RenderHint(component="A", data={}, fallback_text="a"))

        def tool_b() -> None:
            emit_render_hint(RenderHint(component="B", data={}, fallback_text="b"))

        # Per-attempt scope — mirrors _execute_single_tool_call behavior.
        with RenderHintScope() as s1:
            tool_a()
        collected.extend(s1.hints)
        with RenderHintScope() as s2:
            tool_b()
        collected.extend(s2.hints)

        assert [h.component for h in collected] == ["A", "B"]
        metadata = _serialize_hints_into_metadata(collected)
        assert [h["component"] for h in metadata["render_hints"]] == ["A", "B"]

    def test_failed_attempt_hints_do_not_leak_across_scopes(self) -> None:
        """Only the successful attempt's hints should propagate."""
        kept: List[RenderHint] = []

        # Attempt 1: fails, hint discarded.
        with RenderHintScope():
            emit_render_hint(RenderHint(component="FAIL", data={}, fallback_text="x"))

        # Attempt 2: succeeds, hint committed.
        with RenderHintScope() as ok:
            emit_render_hint(RenderHint(component="OK", data={}, fallback_text="y"))
        kept.extend(ok.hints)

        assert [h.component for h in kept] == ["OK"]

    def test_hints_have_no_effect_when_empty(self) -> None:
        """Empty hint list shouldn't add the render_hints key."""
        metadata = _serialize_hints_into_metadata([])
        assert "render_hints" not in metadata


class TestWebChannelBroadcast:
    @pytest.mark.asyncio
    async def test_chat_end_frame_carries_render_hints(self) -> None:
        from channels.web import WebChannel

        ch = WebChannel.__new__(WebChannel)
        ch.ui_queue = None
        ch._update_ui = lambda _text: None  # type: ignore[method-assign]

        hint = RenderHint(
            component="WeatherCard",
            data={"city": "Tokyo", "temp": 18, "condition": "Cloudy"},
            fallback_text="Tokyo 18° Cloudy",
        )
        out = OutboundMessage(
            channel="web",
            chat_id="c1",
            content="Forecast",
            metadata={"render_hints": [hint.to_dict()]},
        )

        broadcast_mock = AsyncMock()
        with patch("tools.admin.websockets.chat_manager") as chat_manager, \
                patch("tools.admin.websockets.manager"):
            chat_manager.broadcast = broadcast_mock
            await ch.send(out)

        broadcast_mock.assert_awaited_once()
        args, _ = broadcast_mock.call_args
        assert args[0] == "c1"
        payload = args[1]
        assert payload["type"] == "chat_end"
        assert payload["content"] == "Forecast"
        assert payload["render_hints"][0]["component"] == "WeatherCard"

    @pytest.mark.asyncio
    async def test_chat_end_omits_render_hints_when_absent(self) -> None:
        from channels.web import WebChannel

        ch = WebChannel.__new__(WebChannel)
        ch.ui_queue = None
        ch._update_ui = lambda _text: None  # type: ignore[method-assign]

        out = OutboundMessage(channel="web", chat_id="c1", content="plain")

        broadcast_mock = AsyncMock()
        with patch("tools.admin.websockets.chat_manager") as chat_manager, \
                patch("tools.admin.websockets.manager"):
            chat_manager.broadcast = broadcast_mock
            await ch.send(out)

        payload = broadcast_mock.call_args.args[1]
        assert payload["type"] == "chat_end"
        assert "render_hints" not in payload
