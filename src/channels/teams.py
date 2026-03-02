"""Microsoft Teams channel adapter.

Uses Microsoft Bot Framework–style webhook: Teams sends activities to
a webhook endpoint; outbound messages are sent via the Bot Connector API.

Configuration (config/settings.yaml)::

    teams:
      enabled: true
      app_id: ""          # Azure AD App (client) ID
      app_secret: ""      # Azure AD App secret
      dm_policy: pairing
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter
from channels.media_utils import save_media

logger = logging.getLogger("TeamsChannel")

_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_API_BASE = "https://smba.trafficmanager.net/teams"


class TeamsChannel(ChannelAdapter):
    """Microsoft Teams channel adapter via Bot Connector API."""

    channel_name = "teams"

    def __init__(self, app_id: str, app_secret: str) -> None:
        super().__init__()
        self.app_id = app_id
        self.app_secret = app_secret
        self._http: Optional[httpx.AsyncClient] = None
        self._token: str = ""
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)
        logger.info("Teams channel started (app_id=%s)", self.app_id)

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("Teams channel stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        if msg.is_partial:
            # Send typing indicator
            await self._send_typing(msg)
            return

        # Send text reply
        if msg.content and msg.content.strip():
            await self._send_reply(msg)

    async def _on_typing(self, event: TypingEvent) -> None:
        pass

    # ------------------------------------------------------------------
    # Webhook handling (called by admin API route)
    # ------------------------------------------------------------------

    async def handle_activity(self, activity: Dict[str, Any]) -> None:
        """Process an incoming Bot Framework activity from Teams.

        Called by the admin webhook route at POST /webhooks/teams.
        """
        activity_type = activity.get("type", "")
        sender = activity.get("from", {})
        sender_id = sender.get("id", "")
        conversation = activity.get("conversation", {})
        chat_id = conversation.get("id", "")

        # Store service URL for replies
        service_url = activity.get("serviceUrl", _API_BASE)

        if activity_type == "message":
            text = activity.get("text", "").strip()
            # Remove @mention of the bot
            for mention in activity.get("entities", []):
                if mention.get("type") == "mention":
                    mentioned = mention.get("text", "")
                    if mentioned:
                        text = text.replace(mentioned, "").strip()

            # Handle attachments
            media_paths: List[str] = []
            for att in activity.get("attachments", []):
                content_url = att.get("contentUrl", "")
                content_type = att.get("contentType", "")
                if content_url:
                    try:
                        data = await self._download_url(content_url)
                        if data:
                            ext = _mime_to_ext(content_type)
                            path = save_media(data, ext=ext, prefix="teams")
                            media_paths.append(str(path))
                    except Exception as exc:
                        logger.error("Teams attachment download failed: %s", exc)

            if text or media_paths:
                await self.publish(
                    content=text or "[Media message]",
                    chat_id=chat_id,
                    sender_id=sender_id,
                    media=media_paths,
                    metadata={
                        "teams_activity_id": activity.get("id", ""),
                        "teams_service_url": service_url,
                        "teams_conversation_id": chat_id,
                        "teams_sender_name": sender.get("name", ""),
                    },
                )

        elif activity_type == "conversationUpdate":
            logger.info("Teams conversation update in %s", chat_id)

        else:
            logger.debug("Unhandled Teams activity type: %s", activity_type)

    # ------------------------------------------------------------------
    # Auth — Bot Framework OAuth
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """Get or refresh the Bot Framework access token."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        if not self._http:
            return ""

        try:
            resp = await self._http.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "scope": "https://api.botframework.com/.default",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                self._token = data.get("access_token", "")
                self._token_expiry = time.time() + data.get("expires_in", 3600)
                return self._token
            logger.error("Teams token request failed (%d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Teams token error: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def _send_reply(self, msg: OutboundMessage) -> bool:
        """Send a reply message to a Teams conversation."""
        token = await self._ensure_token()
        if not token or not self._http:
            return False

        # Try to use stored service URL from metadata
        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        service_url = metadata.get("teams_service_url", _API_BASE).rstrip("/")
        conv_id = msg.chat_id

        payload = {
            "type": "message",
            "text": msg.content,
        }

        try:
            resp = await self._http.post(
                f"{service_url}/v3/conversations/{conv_id}/activities",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code in (200, 201):
                return True
            logger.error("Teams send failed (%d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Teams send error: %s", exc)
        return False

    async def _send_typing(self, msg: OutboundMessage) -> bool:
        """Send a typing indicator to a Teams conversation."""
        token = await self._ensure_token()
        if not token or not self._http:
            return False

        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        service_url = metadata.get("teams_service_url", _API_BASE).rstrip("/")
        conv_id = msg.chat_id

        try:
            resp = await self._http.post(
                f"{service_url}/v3/conversations/{conv_id}/activities",
                json={"type": "typing"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    async def _download_url(self, url: str) -> Optional[bytes]:
        """Download a file from a URL (for attachments)."""
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
            logger.error("Teams download error: %s", exc)
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
