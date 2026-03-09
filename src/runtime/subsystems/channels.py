"""Channel initializer — discover, configure and bind all messaging channels."""
from __future__ import annotations

import importlib
import json
import logging
from typing import Any, List, Optional

logger = logging.getLogger("GazerBrain")


def init_channels(config, bus, ui_queue, app_context) -> List[Any]:
    """Create, bind and return all configured channel instances.

    Side-effect: whatsapp / teams / google_chat channels are written to
    *app_context* so webhook routes can reach them.
    """
    from channels.base import ChannelRegistry

    for mod_name in [
        "channels.discord",
        "channels.feishu",
        "channels.google_chat",
        "channels.signal_channel",
        "channels.slack",
        "channels.teams",
        "channels.telegram",
        "channels.web",
        "channels.whatsapp",
    ]:
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            logger.info("Skipping channel module %s (missing dependencies: %s)", mod_name, e)

    channels: List[Any] = []
    for name, channel_cls in ChannelRegistry.get_all().items():
        try:
            channel = channel_cls.from_config(config, ui_queue=ui_queue)
            if channel:
                channel.bind(bus)
                channels.append(channel)

                if name == "whatsapp":
                    app_context.whatsapp_channel = channel
                elif name == "teams":
                    app_context.teams_channel = channel
                elif name == "google_chat":
                    app_context.google_chat_channel = channel
        except Exception as e:
            logger.error("Failed to load channel %s: %s", name, e, exc_info=True)

    activated_names = [ch.channel_name for ch in channels]
    all_registered = list(ChannelRegistry.get_all().keys())
    skipped_names = [n for n in all_registered if n not in activated_names]
    if activated_names:
        logger.info("Channels activated: %s", ", ".join(activated_names))
    if skipped_names:
        logger.warning(
            "Channels registered but not activated (disabled or missing credentials): %s",
            ", ".join(skipped_names),
        )
    return channels


def init_gmail_push(config, bus, app_context):
    """Set up Gmail Pub/Sub push manager. Returns the manager or *None*."""
    if not config.get("gmail_push.enabled", False):
        return None

    from gazer_email.gmail_push import GmailPushManager
    from bus.events import InboundMessage

    async def _on_gmail_messages(messages: List[dict]):
        if not messages:
            return
        message_ids = [str(item.get("gmail_id", "")) for item in messages if item.get("gmail_id")]
        compact_messages: List[dict] = []
        lines: List[str] = []
        for idx, item in enumerate(messages[:5], start=1):
            from_address = str(item.get("from_address", "")).strip()
            from_raw = str(item.get("from", "")).strip()
            subject = str(item.get("subject", "")).strip()
            body_text = str(item.get("body_text", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            message_id = str(item.get("message_id", "")).strip()

            body_preview = (body_text or snippet).replace("\n", " ").strip()
            if len(body_preview) > 320:
                body_preview = body_preview[:320] + "..."

            compact_messages.append({
                "gmail_id": str(item.get("gmail_id", "")),
                "from": from_raw,
                "from_address": from_address,
                "subject": subject,
                "message_id": message_id,
                "body_preview": body_preview,
            })
            lines.append(f"{idx}. from={from_address or from_raw} | subject={subject or '(no subject)'}")
            if body_preview:
                lines.append(f"   preview={body_preview}")

        payload = {
            "source": "gmail",
            "event_type": "new_messages",
            "message_ids": message_ids,
            "count": len(messages),
            "messages": compact_messages,
        }
        summary = (
            f"[External Event: gmail/new_messages]\n"
            f"Detected {len(messages)} new Gmail message(s).\n"
            f"{chr(10).join(lines)}\n"
            "You can auto-reply with email_send using:\n"
            "- to = from_address\n"
            "- subject = 'Re: ' + original subject\n"
            "- reply_to = message_id\n"
            "Only reply when a response is actually needed."
        )
        msg = InboundMessage(
            channel="webhook",
            chat_id="event:gmail:main",
            sender_id="hook:gmail",
            content=summary,
            metadata=payload,
        )
        try:
            await bus.publish_inbound(msg)
        except ValueError as exc:
            logger.warning("Gmail push event dropped by bus policy: %s", exc)
        except Exception:
            logger.exception("Failed to publish Gmail push event to bus")

    mgr = GmailPushManager(
        credentials_file=config.get("gmail_push.credentials_file", "config/gmail_credentials.json"),
        token_file=config.get("gmail_push.token_file", "config/gmail_token.json"),
        topic=config.get("gmail_push.topic", ""),
        history_store=config.get("gmail_push.history_store", "data/gmail_history.json"),
        on_new_messages=_on_gmail_messages,
    )
    app_context.gmail_push_manager = mgr
    logger.info("Gmail Pub/Sub push manager configured.")
    return mgr
