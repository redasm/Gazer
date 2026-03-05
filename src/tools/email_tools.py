"""Agent-facing email tools: list, read, send, search.

All tools share an ``EmailClient`` reference which is injected at
registration time (see ``brain.py``).
"""

import json
import logging
from typing import Any, Dict, Optional

from tools.base import Tool
from gazer_email.client import EmailClient

logger = logging.getLogger("EmailTools")


class EmailToolBase(Tool):
    @property
    def provider(self) -> str:
        return "email"

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"


class EmailListTool(EmailToolBase):
    """List recent emails in the inbox (or a specified folder)."""

    def __init__(self, client: EmailClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "email_list"


    @property
    def description(self) -> str:
        return (
            "List recent emails. Returns subject, sender, date for each. "
            "Optional: folder (default INBOX), limit, unread_only."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "IMAP folder name (default: INBOX).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max emails to return (default: 20).",
                    "minimum": 1,
                    "maximum": 100,
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "Only return unread emails.",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        folder: str = "INBOX",
        limit: int = 20,
        unread_only: bool = False,
        **_: Any,
    ) -> str:
        try:
            await self._client._ensure_imap()
            messages = await self._client.list_messages(
                folder=folder, limit=limit, unread_only=unread_only,
            )
            if not messages:
                return f"No {'unread ' if unread_only else ''}emails found in {folder}."
            lines = [f"Found {len(messages)} email(s) in {folder}:\n"]
            for m in messages:
                lines.append(
                    f"  UID {m.uid} | {m.date[:22]:22s} | "
                    f"From: {m.sender[:40]:40s} | {m.subject[:60]}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return self._error("EMAIL_LIST_FAILED", f"listing emails failed: {exc}")


class EmailReadTool(EmailToolBase):
    """Read full email content by UID or Message-ID."""

    def __init__(self, client: EmailClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "email_read"


    @property
    def description(self) -> str:
        return "Read full email content by UID or Message-ID."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {
                    "type": "string",
                    "description": "Email UID from email_list results.",
                },
                "message_id": {
                    "type": "string",
                    "description": "RFC Message-ID header value (e.g. from gmail webhook event).",
                },
                "folder": {
                    "type": "string",
                    "description": "IMAP folder (default: INBOX).",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        uid: str = "",
        message_id: str = "",
        folder: str = "INBOX",
        **_: Any,
    ) -> str:
        if not uid and not message_id:
            return self._error("EMAIL_READ_ID_REQUIRED", "'uid' or 'message_id' is required.")
        try:
            await self._client._ensure_imap()
            if not uid and message_id:
                found_uid = await self._client.find_uid_by_message_id(message_id, folder)
                if not found_uid:
                    return self._error(
                        "EMAIL_READ_MESSAGE_ID_NOT_FOUND",
                        f"message_id '{message_id}' not found in {folder}.",
                    )
                uid = found_uid
            msg = await self._client.fetch_message(uid, folder)
            parts = [
                f"Subject: {msg.subject}",
                f"From: {msg.sender}",
                f"To: {msg.to}",
                f"Date: {msg.date}",
            ]
            if msg.cc:
                parts.append(f"Cc: {msg.cc}")
            if msg.message_id:
                parts.append(f"Message-ID: {msg.message_id}")
            parts.append(f"\n--- Body ---\n{msg.body_text or msg.body_html or '(empty)'}")
            if msg.attachments:
                parts.append(f"\n--- Attachments ({len(msg.attachments)}) ---")
                for att in msg.attachments:
                    parts.append(
                        f"  {att['filename']} ({att['content_type']}, {att['size']} bytes)"
                    )
            return "\n".join(parts)
        except Exception as exc:
            return self._error("EMAIL_READ_FAILED", f"reading email failed: {exc}")


class EmailSendTool(EmailToolBase):
    """Compose and send an email via SMTP."""

    def __init__(self, client: EmailClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "email_send"


    @property
    def description(self) -> str:
        return "Send an email. Requires: to, subject, body. Optional: cc, reply_to (Message-ID)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address(es), comma-separated.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "body": {
                    "type": "string",
                    "description": "Email body (plain text).",
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients, comma-separated.",
                },
                "reply_to": {
                    "type": "string",
                    "description": "Message-ID to reply to (for threading).",
                },
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(
        self,
        to: str = "",
        subject: str = "",
        body: str = "",
        cc: str = "",
        reply_to: str = "",
        **_: Any,
    ) -> str:
        if not to or not subject:
            return self._error("EMAIL_SEND_ARGS_REQUIRED", "'to' and 'subject' are required.")
        try:
            return await self._client.send_message(
                to=to, subject=subject, body=body, cc=cc, reply_to=reply_to,
            )
        except Exception as exc:
            return self._error("EMAIL_SEND_FAILED", f"sending email failed: {exc}")


class EmailSearchTool(EmailToolBase):
    """Search emails by keyword (subject, sender, body)."""

    def __init__(self, client: EmailClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "email_search"


    @property
    def description(self) -> str:
        return "Search emails by keyword across subject, sender, and body."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword.",
                },
                "folder": {
                    "type": "string",
                    "description": "IMAP folder (default: INBOX).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 20).",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self, query: str = "", folder: str = "INBOX", limit: int = 20, **_: Any,
    ) -> str:
        if not query:
            return self._error("EMAIL_SEARCH_QUERY_REQUIRED", "'query' is required.")
        try:
            await self._client._ensure_imap()
            results = await self._client.search(query, folder, limit)
            if not results:
                return f"No emails matching '{query}' in {folder}."
            lines = [f"Found {len(results)} email(s) matching '{query}':\n"]
            for m in results:
                lines.append(
                    f"  UID {m.uid} | {m.date[:22]:22s} | "
                    f"From: {m.sender[:40]:40s} | {m.subject[:60]}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return self._error("EMAIL_SEARCH_FAILED", f"searching emails failed: {exc}")
