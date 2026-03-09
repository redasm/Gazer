"""Gmail Pub/Sub push manager for real-time email notifications.

When configured, this enables near-instant push notifications from Google
Cloud Pub/Sub. New-message signals are fetched via the Gmail REST API and
routed into the automation pipeline (webhook/event flow).

**Setup requirements:**

1. Google Cloud project with Gmail API + Pub/Sub API enabled
2. OAuth2 credentials (``credentials_file``) with Gmail read scope
3. A Pub/Sub topic that the Gmail API can publish to
4. A push subscription pointing to ``https://<host>/hooks/gmail``

The ``GmailPushManager`` handles:
- OAuth2 token management (with refresh)
- ``users.watch()`` registration + periodic renewal
- Pub/Sub notification parsing
- History-based incremental message fetching
"""

import asyncio
import base64
import json
import logging
import os
import email.utils
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger("GmailPush")


def _decode_base64url_text(data: str) -> str:
    """Decode Gmail API base64url payload into UTF-8 text."""
    if not data:
        return ""
    try:
        padded = data + "=" * (-len(data) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _header_value(headers: Any, name: str) -> str:
    """Get a header value (case-insensitive) from Gmail payload headers."""
    if not isinstance(headers, list):
        return ""
    target = name.strip().lower()
    for item in headers:
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip().lower() == target:
            return str(item.get("value", "")).strip()
    return ""


def _extract_text_body(payload: Dict[str, Any], max_length: int = 4000) -> str:
    """Extract best-effort text body from Gmail message payload."""
    if not isinstance(payload, dict):
        return ""

    mime = str(payload.get("mimeType", "")).lower()
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    body_data = str(body.get("data", "")).strip()

    if mime == "text/plain" and body_data:
        return _decode_base64url_text(body_data)[:max_length]

    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = _extract_text_body(part, max_length=max_length)
            if text:
                return text

    if mime == "text/html" and body_data:
        return _decode_base64url_text(body_data)[:max_length]

    return ""


class GmailPushManager:
    """Manages Gmail API watch/renew cycle and push notification processing."""

    def __init__(
        self,
        credentials_file: str = "config/gmail_credentials.json",
        token_file: str = "config/gmail_token.json",
        topic: str = "",
        history_store: str = "data/gmail_history.json",
        on_new_messages: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None,
    ) -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.topic = topic
        self.history_store = history_store
        self.on_new_messages = on_new_messages

        self._service = None
        self._history_id: Optional[str] = None
        self._renew_task: Optional[asyncio.Task] = None

        # Load persisted history ID
        self._load_history_id()

    # ------------------------------------------------------------------
    # Setup & auth
    # ------------------------------------------------------------------

    async def setup(self) -> bool:
        """Authenticate and register Gmail push watch.

        Returns True if successful, False if dependencies are missing.
        """
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GRequest
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            logger.warning(
                "Gmail push requires: pip install google-api-python-client "
                "google-auth-httplib2 google-auth-oauthlib"
            )
            return False

        SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, creds.refresh, GRequest())
            else:
                if not os.path.exists(self.credentials_file):
                    logger.error("Gmail credentials file not found: %s", self.credentials_file)
                    return False
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES,
                )
                loop = asyncio.get_running_loop()
                creds = await loop.run_in_executor(None, flow.run_local_server, 0)

            # Save token for next run
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
            with open(self.token_file, "w") as f:
                f.write(creds.to_json())

        loop = asyncio.get_running_loop()
        self._service = await loop.run_in_executor(
            None, lambda: build("gmail", "v1", credentials=creds),
        )

        # Register watch
        await self._register_watch()

        # Start periodic watch renewal (every 6 days, watch expires in 7)
        self._renew_task = asyncio.create_task(self._renew_loop())
        logger.info("Gmail Pub/Sub push manager initialized.")
        return True

    async def _register_watch(self) -> None:
        """Call users.watch() to register push notifications."""
        if not self._service or not self.topic:
            return
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._service.users().watch(
                    userId="me",
                    body={
                        "topicName": self.topic,
                        "labelIds": ["INBOX"],
                    },
                ).execute(),
            )
            new_hid = str(result.get("historyId", ""))
            if not self._history_id:
                self._history_id = new_hid
                self._save_history_id()
            logger.info("Gmail watch registered. historyId=%s, expiration=%s", new_hid, result.get('expiration'))
        except Exception as exc:
            logger.error("Failed to register Gmail watch: %s", exc)

    async def _renew_loop(self) -> None:
        """Renew watch every 6 days."""
        while True:
            await asyncio.sleep(6 * 24 * 3600)  # 6 days
            try:
                await self._register_watch()
                logger.info("Gmail watch renewed.")
            except Exception as exc:
                logger.error("Gmail watch renewal failed: %s", exc)

    # ------------------------------------------------------------------
    # Notification handling
    # ------------------------------------------------------------------

    async def handle_notification(self, data: Dict[str, Any]) -> str:
        """Process a Pub/Sub push notification.

        ``data`` is the raw POST body from Google Pub/Sub:
        ``{"message": {"data": "<base64>", "messageId": "...", ...}, "subscription": "..."}``

        Returns a status string.
        """
        message = data.get("message", {})
        encoded = message.get("data", "")
        if not encoded:
            return "no data in notification"

        try:
            decoded = json.loads(base64.b64decode(encoded))
        except Exception:
            return "failed to decode notification data"

        email_address = decoded.get("emailAddress", "")
        new_history_id = str(decoded.get("historyId", ""))

        if not new_history_id:
            return "no historyId in notification"

        logger.info("Gmail push: email=%s, historyId=%s", email_address, new_history_id)

        # Fetch new messages since our last known history ID
        message_ids = await self._fetch_history(new_history_id)
        message_details = await self._fetch_message_details(message_ids)

        # Update stored history ID
        self._history_id = new_history_id
        self._save_history_id()

        if message_ids and self.on_new_messages:
            if message_details:
                await self.on_new_messages(message_details)
            else:
                await self.on_new_messages([{"gmail_id": mid} for mid in message_ids])
            return f"processed {len(message_ids)} new message(s)"

        return f"no new messages (historyId={new_history_id})"

    async def _fetch_history(self, new_history_id: str) -> List[str]:
        """Use Gmail history.list() to find new message IDs."""
        if not self._service or not self._history_id:
            return []

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._service.users().history().list(
                    userId="me",
                    startHistoryId=self._history_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                ).execute(),
            )
        except Exception as exc:
            logger.error("Gmail history.list failed: %s", exc)
            return []

        message_ids = []
        for record in result.get("history", []):
            for msg_added in record.get("messagesAdded", []):
                msg = msg_added.get("message", {})
                msg_id = msg.get("id")
                if msg_id:
                    message_ids.append(msg_id)

        return message_ids

    async def _fetch_message_details(self, message_ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch lightweight details for Gmail message IDs."""
        if not self._service or not message_ids:
            return []

        loop = asyncio.get_running_loop()
        details: List[Dict[str, Any]] = []
        for msg_id in message_ids:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda mid=msg_id: self._service.users().messages().get(
                        userId="me",
                        id=mid,
                        format="full",
                    ).execute(),
                )
            except Exception as exc:
                logger.warning("Failed to fetch Gmail message details for %s: %s", msg_id, exc)
                continue

            payload = response.get("payload", {}) if isinstance(response, dict) else {}
            headers = payload.get("headers", []) if isinstance(payload, dict) else []

            from_raw = _header_value(headers, "From")
            from_name, from_address = email.utils.parseaddr(from_raw)
            subject = _header_value(headers, "Subject")
            message_id = _header_value(headers, "Message-ID")
            in_reply_to = _header_value(headers, "In-Reply-To")
            date = _header_value(headers, "Date")
            snippet = str(response.get("snippet", "")).strip()
            body_text = _extract_text_body(payload, max_length=4000)
            if not body_text:
                body_text = snippet

            details.append(
                {
                    "gmail_id": str(response.get("id", msg_id)),
                    "thread_id": str(response.get("threadId", "")),
                    "label_ids": list(response.get("labelIds", []))
                    if isinstance(response.get("labelIds"), list)
                    else [],
                    "from": from_raw,
                    "from_name": from_name,
                    "from_address": from_address,
                    "to": _header_value(headers, "To"),
                    "subject": subject,
                    "date": date,
                    "message_id": message_id,
                    "in_reply_to": in_reply_to,
                    "snippet": snippet,
                    "body_text": body_text,
                }
            )

        return details

    # ------------------------------------------------------------------
    # History ID persistence
    # ------------------------------------------------------------------

    def _load_history_id(self) -> None:
        if os.path.exists(self.history_store):
            try:
                with open(self.history_store, "r") as f:
                    data = json.load(f)
                self._history_id = data.get("history_id")
            except Exception:
                pass

    def _save_history_id(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.history_store), exist_ok=True)
            with open(self.history_store, "w") as f:
                json.dump({"history_id": self._history_id}, f)
        except Exception as exc:
            logger.warning("Failed to save Gmail history ID: %s", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        if self._renew_task:
            self._renew_task.cancel()
