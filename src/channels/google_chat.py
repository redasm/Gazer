"""Google Chat channel adapter.

Uses Google Chat API for message handling.  Incoming messages arrive
via a webhook endpoint; outbound replies use the Google Chat REST API.

Configuration (config/settings.yaml)::

    google_chat:
      enabled: true
      service_account_file: ""    # path to service account JSON
      project_id: ""              # Google Cloud project ID
      dm_policy: pairing
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import logging
import time

import httpx

from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter, ChannelRegistry
from channels.media_utils import save_media

logger = logging.getLogger("GoogleChatChannel")

_CHAT_API_BASE = "https://chat.googleapis.com/v1"


@ChannelRegistry.register("google_chat")
class GoogleChatChannel(ChannelAdapter):
    """Google Chat channel adapter via Chat API."""

    channel_name = "google_chat"

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> Optional["ChannelAdapter"]:
        import os
        sa = str(config.get("google_chat.service_account_file", "") or os.getenv("GOOGLE_CHAT_SA_FILE", "")).strip()
        project = str(config.get("google_chat.project_id", "") or os.getenv("GOOGLE_CHAT_PROJECT_ID", "")).strip()
        if config.get("google_chat.enabled"):
            return cls(service_account_file=sa, project_id=project)
        return None

    def __init__(
        self,
        service_account_file: str = "",
        project_id: str = "",
    ) -> None:
        super().__init__()
        self.service_account_file = service_account_file
        self.project_id = project_id
        self._http: Optional[httpx.AsyncClient] = None
        self._token: str = ""
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)
        logger.info("Google Chat channel started (project=%s)", self.project_id)

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("Google Chat channel stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        if msg.is_partial:
            partial_text = self._get_partial_tool_progress_text(msg)
            if partial_text:
                partial_msg = OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=partial_text,
                    reply_to=msg.reply_to,
                    metadata=msg.metadata,
                )
                await self._send_message(partial_msg)
            return  # Google Chat has no typing indicator API

        if msg.content and msg.content.strip():
            await self._send_message(msg)

    async def _on_typing(self, event: TypingEvent) -> None:
        pass

    # ------------------------------------------------------------------
    # Webhook handling (called by admin API route)
    # ------------------------------------------------------------------

    async def handle_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process an incoming Google Chat event.

        Called by the admin webhook route at POST /webhooks/google_chat.
        Returns an optional synchronous reply payload.
        """
        event_type = event.get("type", "")
        message = event.get("message", {})
        sender = message.get("sender", {})
        sender_id = sender.get("name", "")
        space = event.get("space", {})
        space_name = space.get("name", "")
        thread_name = (message.get("thread") or {}).get("name", "")
        chat_id = thread_name or space_name

        if event_type == "MESSAGE":
            text = message.get("argumentText", "") or message.get("text", "")
            text = text.strip()

            # Handle attachments
            media_paths: List[str] = []
            for att in message.get("attachment", []):
                download_uri = att.get("downloadUri", "")
                content_type = att.get("contentType", "")
                if download_uri:
                    try:
                        data = await self._download_url(download_uri)
                        if data:
                            ext = _mime_to_ext(content_type)
                            path = save_media(data, ext=ext, prefix="gchat")
                            media_paths.append(str(path))
                    except Exception as exc:
                        logger.error("Google Chat attachment download failed: %s", exc)

            if text or media_paths:
                await self.publish(
                    content=text or "[Media message]",
                    chat_id=chat_id,
                    sender_id=sender_id,
                    media=media_paths,
                    metadata={
                        "google_chat_space": space_name,
                        "google_chat_thread": thread_name,
                        "google_chat_message_name": message.get("name", ""),
                        "google_chat_sender_display": sender.get("displayName", ""),
                    },
                )

        elif event_type == "ADDED_TO_SPACE":
            logger.info("Bot added to Google Chat space: %s", space_name)

        elif event_type == "REMOVED_FROM_SPACE":
            logger.info("Bot removed from Google Chat space: %s", space_name)

        return None

    # ------------------------------------------------------------------
    # Auth — Service account token
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """Get or refresh the service account access token."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        if not self.service_account_file:
            return ""

        try:
            # Use google-auth library if available
            from google.oauth2 import service_account as sa
            from google.auth.transport.requests import Request as AuthRequest

            credentials = sa.Credentials.from_service_account_file(
                self.service_account_file,
                scopes=["https://www.googleapis.com/auth/chat.bot"],
            )
            credentials.refresh(AuthRequest())
            self._token = credentials.token or ""
            if credentials.expiry:
                self._token_expiry = credentials.expiry.timestamp()
            else:
                self._token_expiry = time.time() + 3500
            return self._token
        except ImportError:
            logger.error(
                "google-auth not installed. Install with: pip install google-auth"
            )
        except Exception as exc:
            logger.error("Google Chat token error: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def _send_message(self, msg: OutboundMessage) -> bool:
        """Send a text message to a Google Chat space."""
        token = await self._ensure_token()
        if not self._http:
            return False

        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        space_name = metadata.get("google_chat_space", "")
        thread_name = metadata.get("google_chat_thread", "")

        if not space_name:
            # Fallback: use chat_id as space name
            space_name = msg.chat_id

        payload: Dict[str, Any] = {"text": msg.content}
        if thread_name:
            payload["thread"] = {"name": thread_name}

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = await self._http.post(
                f"{_CHAT_API_BASE}/{space_name}/messages",
                json=payload,
                headers=headers,
            )
            if resp.status_code in (200, 201):
                return True
            logger.error(
                "Google Chat send failed (%d): %s", resp.status_code, resp.text
            )
        except Exception as exc:
            logger.error("Google Chat send error: %s", exc)
        return False

    async def _download_url(self, url: str) -> Optional[bytes]:
        """Download file from a URL."""
        if not self._http:
            return None
        token = await self._ensure_token()
        try:
            resp = await self._http.get(
                url,
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
            if resp.status_code == 200:
                return resp.content
        except Exception as exc:
            logger.error("Google Chat download error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mime_to_ext(mime: str) -> str:
    _map = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "video/mp4": ".mp4", "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
        "application/pdf": ".pdf",
    }
    return _map.get(mime, ".bin")
