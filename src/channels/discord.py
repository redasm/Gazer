"""Discord channel adapter -- routes inbound/outbound via MessageBus.

This adapter is optional and activates only when ``discord.enabled`` and
``discord.token`` are configured.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, unquote
from typing import Any, List, Optional

from agent.channel_command_registry import parse_channel_command
from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter, ChannelRegistry

logger = logging.getLogger("DiscordChannel")

_BUTTON_ID_PREFIX = "gazer_btn::"
_SELECT_ID_PREFIX = "gazer_sel::"
_MODAL_ID_PREFIX = "gazer_modal::"


@ChannelRegistry.register("discord")
class DiscordChannel(ChannelAdapter):
    """Discord bot adapter (requires ``discord.py`` at runtime)."""

    channel_name = "discord"

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> Optional["ChannelAdapter"]:
        token = config.get("discord.token", "")
        if config.get("discord.enabled") and token:
            allowed = config.get("discord.allowed_guild_ids", [])
            return cls(token, allowed)
        return None

    def __init__(self, token: str, allowed_guild_ids: Optional[List[str]] = None) -> None:
        super().__init__()
        self.token = str(token or "").strip()
        self.allowed_guild_ids = {
            str(item).strip()
            for item in (allowed_guild_ids or [])
            if str(item).strip()
        }
        self._discord: Optional[Any] = None
        self._client: Optional[Any] = None

    @property
    def ready(self) -> bool:
        return self._client is not None

    def _guild_allowed(self, guild_id: Optional[str]) -> bool:
        if not self.allowed_guild_ids:
            return True
        return str(guild_id or "").strip() in self.allowed_guild_ids

    @staticmethod
    def _encode_command(command: str) -> str:
        return quote(str(command or "").strip(), safe="")

    @staticmethod
    def _decode_command(encoded: str) -> str:
        return unquote(str(encoded or "").strip())

    @classmethod
    def _normalize_components(cls, metadata: Optional[dict]) -> List[dict]:
        raw = metadata.get("components", []) if isinstance(metadata, dict) else []
        if not isinstance(raw, list):
            return []
        normalized: List[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            ctype = str(item.get("type", "")).strip().lower()
            if ctype not in {"button", "select"}:
                continue
            normalized.append(item)
        return normalized

    def _interaction_to_command(self, interaction: Any) -> str:
        data = getattr(interaction, "data", None)
        if not isinstance(data, dict):
            return ""
        custom_id = str(data.get("custom_id", "") or "").strip()
        if custom_id.startswith(_BUTTON_ID_PREFIX):
            return self._decode_command(custom_id[len(_BUTTON_ID_PREFIX):])
        if custom_id.startswith(_SELECT_ID_PREFIX):
            values = data.get("values")
            if isinstance(values, list) and values:
                return str(values[0] or "").strip()
            return ""
        if custom_id.startswith(_MODAL_ID_PREFIX):
            command = self._decode_command(custom_id[len(_MODAL_ID_PREFIX):])
            text_parts: List[str] = []
            components = data.get("components", [])
            if isinstance(components, list):
                for group in components:
                    if not isinstance(group, dict):
                        continue
                    children = group.get("components", [])
                    if not isinstance(children, list):
                        continue
                    for child in children:
                        if not isinstance(child, dict):
                            continue
                        value = str(child.get("value", "") or "").strip()
                        if value:
                            text_parts.append(value)
            if text_parts:
                merged = "\n".join(text_parts).strip()
                if command:
                    return f"{command}\n{merged}".strip()
                return merged
            return command
        return ""

    def _build_discord_view(self, components: List[dict]) -> Any:
        if not self._discord:
            return None
        ui = getattr(self._discord, "ui", None)
        if ui is None:
            return None
        view_cls = getattr(ui, "View", None)
        button_cls = getattr(ui, "Button", None)
        select_cls = getattr(ui, "Select", None)
        option_cls = getattr(self._discord, "SelectOption", None)
        if view_cls is None or button_cls is None or select_cls is None:
            return None

        view = view_cls(timeout=300)
        for item in components:
            ctype = str(item.get("type", "")).strip().lower()
            if ctype == "button":
                command = str(item.get("command", "") or "").strip()
                if not command:
                    continue
                custom_id = f"{_BUTTON_ID_PREFIX}{self._encode_command(command)}"
                kwargs: dict[str, Any] = {
                    "label": str(item.get("label", "Run")).strip()[:80] or "Run",
                    "custom_id": custom_id,
                }
                style_name = str(item.get("style", "secondary") or "secondary").strip().lower()
                style_enum = getattr(self._discord, "ButtonStyle", None)
                if style_enum is not None:
                    mapped = {
                        "primary": "primary",
                        "secondary": "secondary",
                        "success": "success",
                        "danger": "danger",
                    }.get(style_name, "secondary")
                    style_value = getattr(style_enum, mapped, None)
                    if style_value is not None:
                        kwargs["style"] = style_value
                view.add_item(button_cls(**kwargs))
                continue

            if ctype != "select":
                continue
            options_raw = item.get("options", [])
            if not isinstance(options_raw, list) or not options_raw:
                continue
            options: List[Any] = []
            for option in options_raw[:25]:
                if not isinstance(option, dict):
                    continue
                value = str(option.get("value", "") or "").strip()
                label = str(option.get("label", "") or "").strip()[:100]
                if not value or not label:
                    continue
                if option_cls is None:
                    continue
                options.append(option_cls(label=label, value=value))
            if not options:
                continue
            select = select_cls(
                placeholder=str(item.get("placeholder", "Select action")).strip()[:100] or "Select action",
                min_values=1,
                max_values=1,
                options=options,
                custom_id=f"{_SELECT_ID_PREFIX}{str(item.get('id', 'default')).strip()[:40]}",
            )
            view.add_item(select)
        return view

    async def ingest_message(
        self,
        *,
        content: str,
        chat_id: str,
        sender_id: str,
        guild_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Test-friendly ingress path that still enforces policy + bus route."""
        if not self._guild_allowed(guild_id):
            return
        payload_metadata = {"guild_id": guild_id, **(metadata or {})}
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

    async def start(self) -> None:
        if not self.token:
            logger.warning("Discord channel enabled but token is empty; skipping startup.")
            return
        try:
            import discord
        except Exception as exc:
            logger.error("Discord channel unavailable (missing discord.py): %s", exc)
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._discord = discord
        self._client = client

        @client.event
        async def on_ready():
            logger.info("Discord bot connected as %s", getattr(client.user, "name", "unknown"))

        @client.event
        async def on_message(message):
            if getattr(message.author, "bot", False):
                return
            guild = getattr(message, "guild", None)
            guild_id = str(getattr(guild, "id", "") or "")
            if not self._guild_allowed(guild_id):
                return
            await self.ingest_message(
                content=str(getattr(message, "content", "") or ""),
                chat_id=str(getattr(message.channel, "id", "") or ""),
                sender_id=str(getattr(message.author, "id", "") or ""),
                guild_id=guild_id,
                metadata={"author_name": str(getattr(message.author, "name", "") or "")},
            )

        @client.event
        async def on_interaction(interaction):
            command = self._interaction_to_command(interaction)
            if not command:
                return
            guild = getattr(interaction, "guild", None)
            guild_id = str(getattr(guild, "id", "") or "")
            if not self._guild_allowed(guild_id):
                return
            channel = getattr(interaction, "channel", None)
            user = getattr(interaction, "user", None)
            await self.ingest_message(
                content=command,
                chat_id=str(getattr(channel, "id", "") or ""),
                sender_id=str(getattr(user, "id", "") or ""),
                guild_id=guild_id,
                metadata={
                    "interaction": True,
                    "interaction_type": str(getattr(interaction, "type", "") or ""),
                    "custom_id": str(
                        getattr(interaction, "data", {}).get("custom_id", "")
                        if isinstance(getattr(interaction, "data", None), dict)
                        else ""
                    ),
                },
            )
            response = getattr(interaction, "response", None)
            try:
                if response is not None and hasattr(response, "is_done") and not response.is_done():
                    await response.send_message("已接收命令，正在处理。", ephemeral=True)
            except Exception:
                logger.debug("Discord interaction ack failed", exc_info=True)

        try:
            await client.start(self.token)
        except Exception as exc:
            logger.error("Failed to start Discord channel: %s", exc, exc_info=True)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _on_typing(self, event: TypingEvent) -> None:
        if self._client is None:
            return
        if not event.is_typing:
            return
        try:
            channel = self._client.get_channel(int(event.chat_id))
            if channel is not None:
                async with channel.typing():
                    return
        except Exception:
            return

    async def send(self, msg: OutboundMessage) -> None:
        if self._client is None:
            return
        try:
            channel_id = int(msg.chat_id)
        except (TypeError, ValueError):
            logger.warning("Discord send skipped due to invalid channel id: %s", msg.chat_id)
            return

        try:
            channel = self._client.get_channel(channel_id)
            if channel is None and self._discord is not None:
                channel = await self._client.fetch_channel(channel_id)
            if channel is None:
                logger.warning("Discord channel not found: %s", channel_id)
                return
            if msg.is_partial:
                async with channel.typing():
                    return
            components = self._normalize_components(msg.metadata)
            view = self._build_discord_view(components) if components else None
            content = str(msg.content or "").strip()
            if content:
                if view is not None:
                    await channel.send(content=content, view=view)
                else:
                    await channel.send(content)
                return
            if view is not None:
                await channel.send(content="请选择操作：", view=view)
        except Exception as exc:
            logger.error("Failed to send Discord message: %s", exc)
