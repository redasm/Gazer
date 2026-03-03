"""WhatsApp Cloud API channel adapter.

Uses Meta's official WhatsApp Business Cloud API for message sending and
receiving.  Incoming messages arrive via a webhook (POST) registered in
the admin API; outbound messages are sent via the Graph API.

Configuration (config/settings.yaml)::

    whatsapp:
      enabled: true
      phone_number_id: "123456789"   # from Meta developer portal
      access_token: ""               # permanent system-user token
      verify_token: "my-secret"      # webhook verification token
      webhook_secret: ""             # optional HMAC signature validation
      api_version: "v21.0"
      dm_policy: pairing             # open | allowlist | pairing
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter, ChannelRegistry
from channels.media_utils import save_media

logger = logging.getLogger("WhatsAppChannel")

# Meta Graph API base
_GRAPH_BASE = "https://graph.facebook.com"


@ChannelRegistry.register("whatsapp")
class WhatsAppChannel(ChannelAdapter):
    """WhatsApp Business Cloud API channel adapter."""

    channel_name = "whatsapp"

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> Optional["ChannelAdapter"]:
        import os
        phone_id = str(config.get("whatsapp.phone_number_id", "") or os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")).strip()
        token = str(config.get("whatsapp.access_token", "") or os.getenv("WHATSAPP_ACCESS_TOKEN", "")).strip()
        if config.get("whatsapp.enabled") and phone_id and token:
            return cls(
                phone_number_id=phone_id,
                access_token=token,
                verify_token=str(config.get("whatsapp.verify_token", "") or os.getenv("WHATSAPP_VERIFY_TOKEN", "")).strip(),
                webhook_secret=str(config.get("whatsapp.webhook_secret", "") or os.getenv("WHATSAPP_WEBHOOK_SECRET", "")).strip(),
                api_version=config.get("whatsapp.api_version", "v21.0"),
            )
        elif config.get("whatsapp.enabled"):
            logger.warning("WhatsApp channel enabled but credentials are missing.")
        return None

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        verify_token: str = "",
        webhook_secret: str = "",
        api_version: str = "v21.0",
    ) -> None:
        super().__init__()
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.verify_token = verify_token
        self.webhook_secret = webhook_secret
        self.api_version = api_version
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def _api_url(self) -> str:
        return f"{_GRAPH_BASE}/{self.api_version}/{self.phone_number_id}"

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )
        logger.info(
            "WhatsApp channel started (phone_number_id=%s, api=%s)",
            self.phone_number_id,
            self.api_version,
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("WhatsApp channel stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message via the WhatsApp Cloud API."""
        if msg.is_partial:
            # WhatsApp does not support streaming; mark as read / typing
            await self._mark_read(msg.chat_id)
            return

        # Send media attachments first
        for media_path in msg.media or []:
            await self._send_media(msg.chat_id, media_path)

        # Send text
        if msg.content and msg.content.strip():
            await self._send_text(msg.chat_id, msg.content)

    async def _on_typing(self, event: TypingEvent) -> None:
        """WhatsApp has no typing indicator API — no-op."""
        pass

    # ------------------------------------------------------------------
    # Webhook handling (called by admin API route)
    # ------------------------------------------------------------------

    def verify_webhook(self, mode: str, token: str, challenge: str) -> Optional[str]:
        """Verify the webhook subscription (GET request from Meta).

        Returns the challenge string on success, ``None`` on failure.
        """
        if mode == "subscribe" and token == self.verify_token:
            logger.info("WhatsApp webhook verified.")
            return challenge
        logger.warning("WhatsApp webhook verification failed (mode=%s)", mode)
        return None

    def validate_signature(self, body: bytes, signature: str) -> bool:
        """Validate X-Hub-Signature-256 header if webhook_secret is set."""
        if not self.webhook_secret:
            return True  # no secret configured — skip validation
        expected = (
            "sha256="
            + hmac.new(
                self.webhook_secret.encode(), body, hashlib.sha256
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)

    async def handle_webhook(self, payload: Dict[str, Any]) -> None:
        """Process an incoming webhook payload from Meta.

        Extracts messages and routes them through ``self.publish()``.
        """
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if change.get("field") != "messages":
                    continue

                contacts = {
                    c["wa_id"]: c.get("profile", {}).get("name", c["wa_id"])
                    for c in value.get("contacts", [])
                }

                for msg in value.get("messages", []):
                    await self._process_inbound(msg, contacts)

    async def _process_inbound(
        self, msg: Dict[str, Any], contacts: Dict[str, str]
    ) -> None:
        """Parse a single inbound WhatsApp message and publish to the bus."""
        sender = msg.get("from", "")
        msg_type = msg.get("type", "")
        wa_msg_id = msg.get("id", "")
        chat_id = sender  # In WhatsApp, chat_id == sender phone number

        # Mark as read
        asyncio.create_task(self._mark_read(wa_msg_id))

        metadata: Dict[str, Any] = {
            "whatsapp_message_id": wa_msg_id,
            "whatsapp_message_type": msg_type,
            "whatsapp_sender_name": contacts.get(sender, ""),
        }

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
            await self.publish(
                content=text,
                chat_id=chat_id,
                sender_id=sender,
                metadata=metadata,
            )

        elif msg_type in ("image", "video", "audio", "document", "sticker"):
            await self._handle_media_message(msg, msg_type, chat_id, sender, metadata)

        elif msg_type == "location":
            loc = msg.get("location", {})
            text = (
                f"[Location: {loc.get('latitude')}, {loc.get('longitude')}]"
                f" {loc.get('name', '')} {loc.get('address', '')}".strip()
            )
            await self.publish(
                content=text,
                chat_id=chat_id,
                sender_id=sender,
                metadata=metadata,
            )

        elif msg_type == "contacts":
            contacts_data = msg.get("contacts", [])
            names = [
                c.get("name", {}).get("formatted_name", "Unknown")
                for c in contacts_data
            ]
            await self.publish(
                content=f"[Shared contacts: {', '.join(names)}]",
                chat_id=chat_id,
                sender_id=sender,
                metadata={**metadata, "whatsapp_contacts": contacts_data},
            )

        elif msg_type == "reaction":
            reaction = msg.get("reaction", {})
            emoji = reaction.get("emoji", "")
            reacted_id = reaction.get("message_id", "")
            logger.info(
                "WhatsApp reaction %s on %s from %s", emoji, reacted_id, sender
            )

        else:
            logger.info("Unhandled WhatsApp message type '%s' from %s", msg_type, sender)

    # ------------------------------------------------------------------
    # Media handling
    # ------------------------------------------------------------------

    async def _handle_media_message(
        self,
        msg: Dict[str, Any],
        msg_type: str,
        chat_id: str,
        sender: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Download media and publish with local path."""
        media_obj = msg.get(msg_type, {})
        media_id = media_obj.get("id", "")
        mime_type = media_obj.get("mime_type", "")
        caption = media_obj.get("caption", f"[User sent {msg_type}]")

        ext = _mime_to_ext(mime_type, msg_type)
        local_path: Optional[str] = None

        if media_id:
            try:
                data = await self._download_media(media_id)
                if data:
                    path = save_media(data, ext=ext, prefix=f"wa_{msg_type}")
                    local_path = str(path)
                    logger.info(
                        "WhatsApp %s from %s saved: %s (%d bytes)",
                        msg_type, sender, local_path, len(data),
                    )
            except Exception as exc:
                logger.error("Failed to download WhatsApp media %s: %s", media_id, exc)

        await self.publish(
            content=caption,
            chat_id=chat_id,
            sender_id=sender,
            media=[local_path] if local_path else [],
            metadata={**metadata, "whatsapp_mime_type": mime_type},
        )

    async def _download_media(self, media_id: str) -> Optional[bytes]:
        """Two-step media download: get URL, then fetch binary."""
        if not self._http:
            return None

        # Step 1: get media URL
        url_resp = await self._http.get(
            f"{_GRAPH_BASE}/{self.api_version}/{media_id}"
        )
        if url_resp.status_code != 200:
            logger.error("WhatsApp media URL fetch failed: %s", url_resp.text)
            return None

        media_url = url_resp.json().get("url")
        if not media_url:
            return None

        # Step 2: download binary
        data_resp = await self._http.get(media_url)
        if data_resp.status_code != 200:
            logger.error("WhatsApp media download failed: %s", data_resp.status_code)
            return None

        return data_resp.content

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def _send_text(self, to: str, text: str) -> Optional[str]:
        """Send a text message. Returns the message ID or None."""
        if not self._http:
            return None

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": True, "body": text},
        }

        try:
            resp = await self._http.post(
                f"{self._api_url}/messages", json=payload
            )
            if resp.status_code in (200, 201):
                msg_id = (
                    resp.json().get("messages", [{}])[0].get("id", "")
                )
                return msg_id
            logger.error("WhatsApp send failed (%d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("WhatsApp send error: %s", exc)
        return None

    async def _send_media(self, to: str, media_path: str) -> Optional[str]:
        """Upload and send a media file."""
        if not self._http:
            return None

        p = Path(media_path)
        if not p.is_file():
            logger.warning("WhatsApp media file not found: %s", media_path)
            return None

        mime = _ext_to_mime(p.suffix.lower())
        media_type = _mime_to_wa_type(mime)

        try:
            # Upload media
            upload_resp = await self._http.post(
                f"{self._api_url}/media",
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (p.name, p.read_bytes(), mime)},
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            if upload_resp.status_code not in (200, 201):
                logger.error("WhatsApp media upload failed: %s", upload_resp.text)
                return None

            media_id = upload_resp.json().get("id", "")
            if not media_id:
                return None

            # Send media message
            payload: Dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": media_type,
                media_type: {"id": media_id},
            }

            resp = await self._http.post(
                f"{self._api_url}/messages", json=payload
            )
            if resp.status_code in (200, 201):
                return resp.json().get("messages", [{}])[0].get("id", "")
            logger.error("WhatsApp media send failed: %s", resp.text)
        except Exception as exc:
            logger.error("WhatsApp media send error: %s", exc)
        return None

    async def _mark_read(self, message_id: str) -> None:
        """Mark a message as read (blue ticks)."""
        if not self._http or not message_id:
            return
        try:
            await self._http.post(
                f"{self._api_url}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                },
            )
        except Exception:
            pass  # best-effort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mime_to_ext(mime: str, fallback_type: str = "") -> str:
    """Convert MIME type to file extension."""
    _map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/3gpp": ".3gp",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/opus": ".opus",
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    }
    if mime in _map:
        return _map[mime]
    # Fallback by message type
    _type_ext = {
        "image": ".jpg",
        "video": ".mp4",
        "audio": ".ogg",
        "sticker": ".webp",
        "document": ".bin",
    }
    return _type_ext.get(fallback_type, ".bin")


def _ext_to_mime(ext: str) -> str:
    """Convert file extension to MIME type."""
    _map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".3gp": "video/3gpp",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".opus": "audio/opus",
        ".pdf": "application/pdf",
    }
    return _map.get(ext, "application/octet-stream")


def _mime_to_wa_type(mime: str) -> str:
    """Map MIME type to WhatsApp media message type."""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"
