"""Web Chat channel adapter — in-process bridge between Admin API and MessageBus."""

import asyncio
import logging
from typing import Optional, Any

from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter, ChannelRegistry

logger = logging.getLogger("WebChannel")


@ChannelRegistry.register("web")
class WebChannel(ChannelAdapter):
    """Web Chat channel — reads from the shared asyncio.Queue written by
    Admin API WebSocket/REST handlers, and broadcasts agent responses
    directly to connected WebSocket clients (same process).
    """

    channel_name = "web"

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> Optional["ChannelAdapter"]:
        ui_queue = kwargs.get("ui_queue")
        return cls(ui_queue=ui_queue)

    def _is_sender_authorized(self, sender_id: str) -> bool:
        return True

    def __init__(self, ui_queue=None) -> None:
        super().__init__()
        self.ui_queue = ui_queue
        self._running = False

    def _get_input_queue(self) -> Optional[asyncio.Queue]:
        import tools.admin.state as _state
        return _state.API_QUEUES.get("input")

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        logger.info("WebChannel started.")
        while self._running:
            q = self._get_input_queue()
            if q is None:
                await asyncio.sleep(0.5)
                continue
            try:
                msg = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                if msg.get("type") == "chat":
                    self._update_ui("Typing...")
                    media = msg.get("media", [])
                    if not isinstance(media, list):
                        media = []
                    metadata = msg.get("metadata", {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                    await self.publish(
                        content=msg.get("content", ""),
                        chat_id=msg.get("chat_id", "web-main"),
                        sender_id=msg.get("sender_id", "WebUser"),
                        media=media,
                        metadata=metadata,
                    )
            except Exception as exc:
                logger.warning("WebChannel dispatch failed: %s", exc)

    async def stop(self) -> None:
        self._running = False
        logger.info("WebChannel stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        from tools.admin.websockets import chat_manager, manager

        if msg.is_partial:
            metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
            if metadata.get("stream_event") == "tool_call":
                await chat_manager.broadcast(msg.chat_id, {
                    "type": "tool_call_event",
                    "chat_id": msg.chat_id,
                    "event_type": str(metadata.get("event_type", "")),
                    "payload": dict(metadata.get("payload", {}) or {}),
                })
                return
            await chat_manager.broadcast(msg.chat_id, {
                "type": "chat_stream", "content": msg.content, "chat_id": msg.chat_id,
            })
            self._update_ui(msg.content)
            return

        await chat_manager.broadcast(msg.chat_id, {
            "type": "chat_end", "content": msg.content, "chat_id": msg.chat_id,
        })
        self._update_ui("Sent")

    async def _on_typing(self, event: TypingEvent) -> None:
        self._update_ui("Typing..." if event.is_typing else "Idle")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_ui(self, text: str) -> None:
        if self.ui_queue:
            self.ui_queue.put({"type": "status", "data": text})
