"""Signal channel adapter via signal-cli REST API.

Requires a running ``signal-cli-rest-api`` instance (Docker or native).
See https://github.com/bbernhard/signal-cli-rest-api

Configuration (config/settings.yaml)::

    signal:
      enabled: true
      api_url: "http://localhost:8085"   # signal-cli-rest-api base URL
      phone_number: "+1234567890"        # registered Signal number
      dm_policy: pairing
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from typing import Any, Optional

from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter, ChannelRegistry
from channels.media_utils import save_media

logger = logging.getLogger("SignalChannel")

_POLL_INTERVAL = 1.0  # seconds between receive polls


@ChannelRegistry.register("signal")
class SignalChannel(ChannelAdapter):
    """Signal messenger channel via signal-cli REST API."""

    channel_name = "signal"

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> Optional["ChannelAdapter"]:
        import os
        api = str(config.get("signal.api_url", "") or os.getenv("SIGNAL_API_URL", "")).strip()
        phone = str(config.get("signal.phone_number", "") or os.getenv("SIGNAL_PHONE_NUMBER", "")).strip()
        if config.get("signal.enabled") and api and phone:
            return cls(api_url=api, phone_number=phone)
        elif config.get("signal.enabled"):
            logger.warning("Signal channel enabled but api_url/phone_number missing.")
        return None

    def __init__(
        self,
        api_url: str,
        phone_number: str,
    ) -> None:
        super().__init__()
        self.api_url = api_url.rstrip("/")
        self.phone_number = phone_number
        self._http: Optional[httpx.AsyncClient] = None
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Signal channel started (api=%s, number=%s)", self.api_url, self.phone_number
        )

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("Signal channel stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        if msg.is_partial:
            return  # Signal has no typing indicator API via REST

        # Send media
        for media_path in msg.media or []:
            await self._send_media(msg.chat_id, media_path)

        # Send text
        if msg.content and msg.content.strip():
            await self._send_text(msg.chat_id, msg.content)

    async def _on_typing(self, event: TypingEvent) -> None:
        pass

    # ------------------------------------------------------------------
    # Polling loop — receive messages from signal-cli REST API
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Poll the signal-cli REST API for incoming messages."""
        while self._running:
            try:
                await self._receive_messages()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Signal poll error: %s", exc, exc_info=True)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _receive_messages(self) -> None:
        if not self._http:
            return

        resp = await self._http.get(
            f"{self.api_url}/v1/receive/{self.phone_number}"
        )
        if resp.status_code != 200:
            return

        messages = resp.json()
        if not isinstance(messages, list):
            return

        for envelope in messages:
            data_msg = envelope.get("envelope", {}).get("dataMessage")
            if not data_msg:
                continue

            sender = envelope.get("envelope", {}).get("sourceNumber", "")
            group_id = (data_msg.get("groupInfo") or {}).get("groupId", "")
            chat_id = group_id or sender
            text = data_msg.get("message", "")

            # Handle attachments
            media_paths: List[str] = []
            for att in data_msg.get("attachments", []):
                att_id = att.get("id", "")
                content_type = att.get("contentType", "")
                if att_id:
                    try:
                        data = await self._download_attachment(att_id)
                        if data:
                            ext = _mime_to_ext(content_type)
                            path = save_media(data, ext=ext, prefix="signal")
                            media_paths.append(str(path))
                    except Exception as exc:
                        logger.error("Signal attachment download failed: %s", exc)

            if text or media_paths:
                await self.publish(
                    content=text or "[Media message]",
                    chat_id=chat_id,
                    sender_id=sender,
                    media=media_paths,
                    metadata={
                        "signal_group_id": group_id,
                        "signal_timestamp": data_msg.get("timestamp", 0),
                    },
                )

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def _send_text(self, to: str, text: str) -> bool:
        if not self._http:
            return False
        payload: Dict[str, Any] = {
            "message": text,
            "number": self.phone_number,
            "recipients": [to],
        }
        try:
            resp = await self._http.post(
                f"{self.api_url}/v2/send", json=payload
            )
            if resp.status_code in (200, 201):
                return True
            logger.error("Signal send failed (%d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Signal send error: %s", exc)
        return False

    async def _send_media(self, to: str, media_path: str) -> bool:
        """Send a media attachment via signal-cli REST API."""
        if not self._http:
            return False

        p = Path(media_path)
        if not p.is_file():
            return False

        try:
            # signal-cli REST API expects base64-encoded attachments
            import base64
            data_b64 = base64.b64encode(p.read_bytes()).decode()
            payload = {
                "message": "",
                "number": self.phone_number,
                "recipients": [to],
                "base64_attachments": [
                    f"data:{_ext_to_mime(p.suffix)};filename={p.name};base64,{data_b64}"
                ],
            }
            resp = await self._http.post(
                f"{self.api_url}/v2/send", json=payload
            )
            return resp.status_code in (200, 201)
        except Exception as exc:
            logger.error("Signal media send error: %s", exc)
        return False

    async def _download_attachment(self, attachment_id: str) -> Optional[bytes]:
        """Download an attachment from signal-cli REST API."""
        if not self._http:
            return None
        resp = await self._http.get(
            f"{self.api_url}/v1/attachments/{attachment_id}"
        )
        if resp.status_code == 200:
            return resp.content
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mime_to_ext(mime: str) -> str:
    _map = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "image/webp": ".webp", "video/mp4": ".mp4", "audio/aac": ".aac",
        "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "application/pdf": ".pdf",
    }
    return _map.get(mime, ".bin")


def _ext_to_mime(ext: str) -> str:
    _map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".mp4": "video/mp4", ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg", ".pdf": "application/pdf",
    }
    return _map.get(ext.lower(), "application/octet-stream")
