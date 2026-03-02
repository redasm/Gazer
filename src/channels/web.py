"""Web Chat channel adapter -- bridges IPC queues through the MessageBus."""

import asyncio
import logging
from queue import Queue, Empty
from typing import Optional

from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter

logger = logging.getLogger("WebChannel")

POLL_INTERVAL = 0.05  # 50ms


class WebChannel(ChannelAdapter):
    """
    Web Chat channel that wraps the existing IPC queues.

    All inbound messages are published to the MessageBus (not sent to
    the agent directly). Outbound responses are forwarded back to the
    IPC output queue.
    """

    channel_name = "web"

    def _is_sender_authorized(self, sender_id: str) -> bool:
        """Web console is the deployer's local interface -- always authorized."""
        return True

    def __init__(
        self,
        ipc_input: Queue,
        ipc_output: Optional[Queue] = None,
        ui_queue: Optional[Queue] = None,
    ) -> None:
        super().__init__()
        self.ipc_input = ipc_input
        self.ipc_output = ipc_output
        self.ui_queue = ui_queue
        self._running = False

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        logger.info("WebChannel started.")
        while self._running:
            try:
                msg = self.ipc_input.get_nowait()
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
            except Empty:
                pass
            except Exception as exc:
                logger.warning(f"WebChannel poll failed: {exc}")
            await asyncio.sleep(POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        logger.info("WebChannel stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        if msg.is_partial:
            metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
            if metadata.get("stream_event") == "tool_call":
                if self.ipc_output:
                    self.ipc_output.put(
                        {
                            "type": "tool_call_event",
                            "chat_id": msg.chat_id,
                            "event_type": str(metadata.get("event_type", "")),
                            "payload": dict(metadata.get("payload", {}) or {}),
                        }
                    )
                return
            # Forward streaming chunks so the web frontend can display them
            if self.ipc_output:
                self.ipc_output.put(
                    {"type": "chat_stream", "content": msg.content, "chat_id": msg.chat_id}
                )
            self._update_ui(msg.content)
            return

        if self.ipc_output:
            self.ipc_output.put(
                {"type": "chat_end", "content": msg.content, "chat_id": msg.chat_id}
            )
        self._update_ui("Sent")

    async def _on_typing(self, event: TypingEvent) -> None:
        self._update_ui("Typing..." if event.is_typing else "Idle")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_ui(self, text: str) -> None:
        if self.ui_queue:
            self.ui_queue.put({"type": "status", "data": text})
