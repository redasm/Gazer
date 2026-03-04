"""Slack channel adapter -- routes inbound/outbound via MessageBus.

This adapter is optional and activates only when ``slack.enabled`` and
``slack.bot_token`` are configured.  Uses `slack_bolt` Socket Mode so
there is no need for a public URL / ngrok.

Environment variables (or config):
  SLACK_BOT_TOKEN      – xoxb-… Bot User OAuth Token
  SLACK_APP_TOKEN      – xapp-… App-Level Token (Socket Mode)
  SLACK_SIGNING_SECRET – (optional) for request verification
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional
import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agent.channel_command_registry import parse_channel_command
from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter, ChannelRegistry

logger = logging.getLogger("SlackChannel")

# Max message length per Slack API
_SLACK_MSG_LIMIT = 3_000


@ChannelRegistry.register("slack")
class SlackChannel(ChannelAdapter):
    """Slack bot adapter (requires ``slack_bolt`` at runtime)."""

    channel_name = "slack"

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> Optional["ChannelAdapter"]:
        import os
        bot_token = str(config.get("slack.bot_token", "") or os.getenv("SLACK_BOT_TOKEN", "")).strip()
        app_token = str(config.get("slack.app_token", "") or os.getenv("SLACK_APP_TOKEN", "")).strip()
        if config.get("slack.enabled") and bot_token and app_token:
            return cls(bot_token=bot_token, app_token=app_token)
        elif config.get("slack.enabled"):
            logger.error("Slack channel enabled but bot_token/app_token missing.")
        return None

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        signing_secret: str = "",
        allowed_channel_ids: Optional[List[str]] = None,
    ) -> None:
        super().__init__()
        self.bot_token = str(bot_token or "").strip()
        self.app_token = str(app_token or "").strip()
        self.signing_secret = str(signing_secret or "").strip()
        self.allowed_channel_ids = {
            str(item).strip()
            for item in (allowed_channel_ids or [])
            if str(item).strip()
        }
        self._app: Optional[Any] = None
        self._handler: Optional[Any] = None
        self._web_client: Optional[Any] = None
        self._handler_task: Optional[asyncio.Task] = None

    @property
    def ready(self) -> bool:
        return self._app is not None

    def _channel_allowed(self, channel_id: Optional[str]) -> bool:
        """Return True if the message channel is in the allow-list (or list is empty)."""
        if not self.allowed_channel_ids:
            return True
        return str(channel_id or "").strip() in self.allowed_channel_ids

    # ------------------------------------------------------------------
    # Inbound processing
    # ------------------------------------------------------------------

    async def ingest_message(
        self,
        *,
        content: str,
        chat_id: str,
        sender_id: str,
        channel_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Test-friendly ingress path that enforces policy + bus route."""
        if not self._channel_allowed(channel_id or chat_id):
            return

        payload_metadata: Dict[str, Any] = {
            **(metadata or {}),
        }
        if thread_ts:
            payload_metadata["thread_ts"] = thread_ts
        if channel_id:
            payload_metadata["slack_channel_id"] = channel_id

        parsed_command = parse_channel_command(content)
        if parsed_command is not None:
            command_name, command_args = parsed_command
            payload_metadata["channel_command"] = {
                "name": command_name,
                "args": command_args,
            }

        await self.publish(
            content=content,
            chat_id=chat_id,
            sender_id=sender_id,
            metadata=payload_metadata,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.bot_token:
            logger.warning("Slack channel enabled but bot_token is empty; skipping startup.")
            return
        if not self.app_token:
            logger.warning("Slack channel enabled but app_token is empty; Socket Mode requires it.")
            return

        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError as exc:
            logger.error(
                "Slack channel unavailable (missing slack_bolt): %s. "
                "Install with: pip install slack-bolt",
                exc,
            )
            return

        app = AsyncApp(
            token=self.bot_token,
            signing_secret=self.signing_secret or None,
        )
        self._app = app
        self._web_client = app.client

        # --- Event: message ---
        @app.event("message")
        async def on_message(event: dict, say: Any) -> None:
            # Ignore bot messages
            subtype = event.get("subtype", "")
            if subtype in {"bot_message", "message_changed", "message_deleted"}:
                return
            if event.get("bot_id"):
                return

            text = str(event.get("text", "") or "").strip()
            if not text:
                return

            slack_channel_id = str(event.get("channel", "") or "")
            user_id = str(event.get("user", "") or "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")

            # Use thread_ts as chat_id for thread-based conversations
            chat_id = str(thread_ts or slack_channel_id)

            await self.ingest_message(
                content=text,
                chat_id=chat_id,
                sender_id=user_id,
                channel_id=slack_channel_id,
                thread_ts=str(thread_ts) if thread_ts else None,
                metadata={
                    "author_name": user_id,  # will be resolved by Slack API if needed
                    "ts": event.get("ts", ""),
                },
            )

        # --- Event: app_mention ---
        @app.event("app_mention")
        async def on_app_mention(event: dict, say: Any) -> None:
            text = str(event.get("text", "") or "").strip()
            if not text:
                return

            slack_channel_id = str(event.get("channel", "") or "")
            user_id = str(event.get("user", "") or "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            chat_id = str(thread_ts or slack_channel_id)

            await self.ingest_message(
                content=text,
                chat_id=chat_id,
                sender_id=user_id,
                channel_id=slack_channel_id,
                thread_ts=str(thread_ts) if thread_ts else None,
                metadata={
                    "mentioned": True,
                    "ts": event.get("ts", ""),
                },
            )

        # Start Socket Mode handler in background
        handler = AsyncSocketModeHandler(app, self.app_token)
        self._handler = handler

        async def _run_handler() -> None:
            try:
                await handler.start_async()
            except Exception as exc:
                logger.error("Slack Socket Mode handler stopped: %s", exc, exc_info=True)

        self._handler_task = asyncio.create_task(_run_handler())
        logger.info("Slack channel started (Socket Mode).")

    async def stop(self) -> None:
        if self._handler is not None:
            try:
                await self._handler.close_async()
            except Exception:
                logger.debug("Slack handler close failed", exc_info=True)
            self._handler = None

        if self._handler_task is not None:
            self._handler_task.cancel()
            self._handler_task = None

        self._app = None
        self._web_client = None
        logger.info("Slack channel stopped.")

    # ------------------------------------------------------------------
    # Typing indicator
    # ------------------------------------------------------------------

    async def _on_typing(self, event: TypingEvent) -> None:
        """Slack doesn't have a public 'typing' API for bots; no-op."""
        pass

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> None:
        if self._web_client is None:
            return

        content = str(msg.content or "").strip()
        if not content and not msg.is_partial:
            return
        if msg.is_partial:
            # Slack has no typing API for bots; skip partial messages
            return

        # Determine target: use slack_channel_id from metadata if available
        metadata = msg.metadata or {}
        slack_channel_id = str(
            metadata.get("slack_channel_id", "") or ""
        ).strip()
        thread_ts = str(metadata.get("thread_ts", "") or "").strip() or None

        # Fallback: chat_id might be thread_ts or a channel id
        channel_target = slack_channel_id or msg.chat_id
        if not channel_target:
            logger.warning("Slack send skipped: no target channel.")
            return

        try:
            # Split long messages
            chunks = self._split_message(content)
            for chunk in chunks:
                kwargs: Dict[str, Any] = {
                    "channel": channel_target,
                    "text": chunk,
                }
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts

                # Add Block Kit blocks if components are present
                blocks = self._build_blocks(metadata)
                if blocks:
                    kwargs["blocks"] = blocks

                await self._web_client.chat_postMessage(**kwargs)
        except Exception as exc:
            logger.error("Failed to send Slack message: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_message(text: str, limit: int = _SLACK_MSG_LIMIT) -> List[str]:
        """Split a message into chunks that fit within Slack's character limit."""
        if len(text) <= limit:
            return [text]
        chunks: List[str] = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            # Try to split at newline
            split_at = text.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks

    @staticmethod
    def _build_blocks(metadata: dict) -> Optional[List[Dict[str, Any]]]:
        """Convert component metadata to Slack Block Kit blocks (buttons/selects)."""
        components = metadata.get("components", [])
        if not isinstance(components, list) or not components:
            return None

        actions: List[Dict[str, Any]] = []
        for item in components:
            if not isinstance(item, dict):
                continue
            ctype = str(item.get("type", "")).strip().lower()

            if ctype == "button":
                command = str(item.get("command", "") or "").strip()
                label = str(item.get("label", "Run") or "Run").strip()[:75] or "Run"
                style_name = str(item.get("style", "") or "").strip().lower()
                button: Dict[str, Any] = {
                    "type": "button",
                    "text": {"type": "plain_text", "text": label},
                    "action_id": f"gazer_btn_{command[:50]}",
                    "value": command,
                }
                if style_name == "danger":
                    button["style"] = "danger"
                elif style_name in {"primary", "success"}:
                    button["style"] = "primary"
                actions.append(button)

            elif ctype == "select":
                options_raw = item.get("options", [])
                if not isinstance(options_raw, list) or not options_raw:
                    continue
                options: List[Dict[str, Any]] = []
                for option in options_raw[:100]:
                    if not isinstance(option, dict):
                        continue
                    value = str(option.get("value", "") or "").strip()
                    label_text = str(option.get("label", "") or "").strip()[:75]
                    if not value or not label_text:
                        continue
                    options.append({
                        "text": {"type": "plain_text", "text": label_text},
                        "value": value,
                    })
                if not options:
                    continue
                placeholder = str(item.get("placeholder", "Select action") or "Select action")[:150]
                actions.append({
                    "type": "static_select",
                    "placeholder": {"type": "plain_text", "text": placeholder},
                    "action_id": f"gazer_sel_{str(item.get('id', 'default'))[:40]}",
                    "options": options,
                })

        if not actions:
            return None

        return [{"type": "actions", "elements": actions}]
