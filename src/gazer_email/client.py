"""Shared email client for Gazer -- IMAP read + SMTP send.

Uses the stdlib ``imaplib`` / ``smtplib`` modules wrapped in an asyncio
executor so that the main event loop is never blocked.  All public methods
are async-safe.
"""

import asyncio
import email as _email
import email.header
import email.utils
import imaplib
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

logger = logging.getLogger("EmailClient")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EmailSummary:
    """Lightweight representation of an email header."""

    uid: str
    subject: str
    sender: str
    date: str
    folder: str
    flags: str = ""
    snippet: str = ""  # first N chars of body


@dataclass
class EmailMessage:
    """Full email content."""

    uid: str
    subject: str
    sender: str
    to: str
    cc: str
    date: str
    body_text: str
    body_html: str = ""
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    message_id: str = ""
    in_reply_to: str = ""
    folder: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_header(raw: Optional[str]) -> str:
    """Decode an RFC-2047 encoded header value."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _extract_body(msg: _email.message.Message, max_length: int = 8000) -> tuple:
    """Return (plain_text, html) from a parsed email message."""
    text_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if ct == "text/plain" and not text_body:
                text_body = decoded[:max_length]
            elif ct == "text/html" and not html_body:
                html_body = decoded[:max_length]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = decoded[:max_length]
            else:
                text_body = decoded[:max_length]
    return text_body, html_body


def _extract_attachments(msg: _email.message.Message) -> List[Dict[str, Any]]:
    """Return a list of attachment metadata (no binary content)."""
    attachments = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in disp:
            continue
        filename = _decode_header(part.get_filename()) or "unnamed"
        size = len(part.get_payload(decode=True) or b"")
        attachments.append({
            "filename": filename,
            "content_type": part.get_content_type(),
            "size": size,
        })
    return attachments


# ---------------------------------------------------------------------------
# EmailClient
# ---------------------------------------------------------------------------

class EmailClient:
    """Async-safe IMAP/SMTP client.

    All blocking I/O runs in the default executor so the event loop stays
    responsive.
    """

    def __init__(
        self,
        imap_host: str,
        imap_port: int = 993,
        smtp_host: str = "",
        smtp_port: int = 587,
        username: str = "",
        password: str = "",
        max_body_length: int = 8000,
    ) -> None:
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host or imap_host.replace("imap", "smtp")
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.max_body_length = max_body_length

        self._imap: Optional[imaplib.IMAP4_SSL] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect_imap_sync(self) -> imaplib.IMAP4_SSL:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=ctx)
        conn.login(self.username, self.password)
        logger.info("IMAP connected to %s as %s", self.imap_host, self.username)
        return conn

    async def connect_imap(self) -> None:
        loop = asyncio.get_running_loop()
        self._imap = await loop.run_in_executor(None, self._connect_imap_sync)

    async def disconnect_imap(self) -> None:
        if self._imap:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._imap.logout)
            except Exception:
                pass
            self._imap = None

    async def _ensure_imap(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            await self.connect_imap()
        assert self._imap is not None
        return self._imap

    # ------------------------------------------------------------------
    # IMAP operations (all run in executor)
    # ------------------------------------------------------------------

    async def list_messages(
        self,
        folder: str = "INBOX",
        limit: int = 20,
        unread_only: bool = False,
    ) -> List[EmailSummary]:
        """List recent messages in *folder*."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._list_messages_sync, folder, limit, unread_only,
            )

    def _list_messages_sync(
        self, folder: str, limit: int, unread_only: bool,
    ) -> List[EmailSummary]:
        conn = self._imap
        if not conn:
            raise RuntimeError("IMAP not connected")
        conn.select(folder, readonly=True)
        criterion = "(UNSEEN)" if unread_only else "ALL"
        _typ, data = conn.search(None, criterion)
        uids = data[0].split() if data[0] else []
        uids = uids[-limit:]  # most recent

        results: List[EmailSummary] = []
        if not uids:
            return results

        uid_str = b",".join(uids)
        _typ, msg_data = conn.fetch(uid_str, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)] FLAGS)")
        if not msg_data:
            return results

        for item in msg_data:
            if not isinstance(item, tuple):
                continue
            raw = item[1]
            uid_match = item[0].split()
            uid = uid_match[0].decode() if uid_match else "?"
            flags = item[0].decode() if item[0] else ""

            parsed = _email.message_from_bytes(raw)
            results.append(EmailSummary(
                uid=uid,
                subject=_decode_header(parsed.get("Subject", "")),
                sender=_decode_header(parsed.get("From", "")),
                date=parsed.get("Date", ""),
                folder=folder,
                flags=flags,
            ))
        return results

    async def fetch_message(self, uid: str, folder: str = "INBOX") -> EmailMessage:
        """Fetch full message by UID."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._fetch_message_sync, uid, folder,
            )

    async def find_uid_by_message_id(
        self, message_id: str, folder: str = "INBOX",
    ) -> Optional[str]:
        """Find IMAP UID by Message-ID header."""
        needle = str(message_id or "").strip()
        if not needle:
            return None
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._find_uid_by_message_id_sync, needle, folder,
            )

    def _find_uid_by_message_id_sync(self, message_id: str, folder: str) -> Optional[str]:
        conn = self._imap
        if not conn:
            raise RuntimeError("IMAP not connected")
        conn.select(folder, readonly=True)

        def _search_header(value: str) -> Optional[str]:
            safe = value.replace('"', "").strip()
            if not safe:
                return None
            _typ, data = conn.search(None, f'(HEADER Message-ID "{safe}")')
            uids = data[0].split() if data and data[0] else []
            if not uids:
                return None
            return uids[-1].decode(errors="replace")

        uid = _search_header(message_id)
        if uid:
            return uid

        # Some providers store Message-ID with/without angle brackets.
        trimmed = message_id.strip("<>").strip()
        if trimmed and trimmed != message_id:
            return _search_header(trimmed)
        return None

    def _fetch_message_sync(self, uid: str, folder: str) -> EmailMessage:
        conn = self._imap
        if not conn:
            raise RuntimeError("IMAP not connected")
        conn.select(folder, readonly=True)
        _typ, data = conn.fetch(uid.encode(), "(RFC822)")
        if not data or not data[0] or not isinstance(data[0], tuple):
            raise ValueError(f"Message UID {uid} not found in {folder}")

        raw = data[0][1]
        msg = _email.message_from_bytes(raw)
        text_body, html_body = _extract_body(msg, self.max_body_length)
        attachments = _extract_attachments(msg)

        return EmailMessage(
            uid=uid,
            subject=_decode_header(msg.get("Subject", "")),
            sender=_decode_header(msg.get("From", "")),
            to=_decode_header(msg.get("To", "")),
            cc=_decode_header(msg.get("Cc", "")),
            date=msg.get("Date", ""),
            body_text=text_body,
            body_html=html_body,
            attachments=attachments,
            headers={
                "Message-ID": msg.get("Message-ID", ""),
                "In-Reply-To": msg.get("In-Reply-To", ""),
                "References": msg.get("References", ""),
            },
            message_id=msg.get("Message-ID", ""),
            in_reply_to=msg.get("In-Reply-To", ""),
            folder=folder,
        )

    async def search(
        self, query: str, folder: str = "INBOX", limit: int = 20,
    ) -> List[EmailSummary]:
        """IMAP search with a text query (SUBJECT / FROM / BODY)."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._search_sync, query, folder, limit,
            )

    def _search_sync(
        self, query: str, folder: str, limit: int,
    ) -> List[EmailSummary]:
        conn = self._imap
        if not conn:
            raise RuntimeError("IMAP not connected")
        conn.select(folder, readonly=True)
        # Try SUBJECT first, then OR with FROM and BODY
        imap_query = f'(OR OR (SUBJECT "{query}") (FROM "{query}") (BODY "{query}"))'
        _typ, data = conn.search(None, imap_query)
        uids = data[0].split() if data[0] else []
        uids = uids[-limit:]

        results: List[EmailSummary] = []
        if not uids:
            return results

        uid_str = b",".join(uids)
        _typ, msg_data = conn.fetch(uid_str, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)] FLAGS)")
        if not msg_data:
            return results

        for item in msg_data:
            if not isinstance(item, tuple):
                continue
            raw = item[1]
            uid_match = item[0].split()
            uid = uid_match[0].decode() if uid_match else "?"
            parsed = _email.message_from_bytes(raw)
            results.append(EmailSummary(
                uid=uid,
                subject=_decode_header(parsed.get("Subject", "")),
                sender=_decode_header(parsed.get("From", "")),
                date=parsed.get("Date", ""),
                folder=folder,
            ))
        return results

    # ------------------------------------------------------------------
    # SMTP send
    # ------------------------------------------------------------------

    async def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        reply_to: str = "",
    ) -> str:
        """Send an email via SMTP.  Returns a status string."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._send_sync, to, subject, body, cc, reply_to,
        )

    def _send_sync(
        self, to: str, subject: str, body: str, cc: str, reply_to: str,
    ) -> str:
        msg = MIMEMultipart("alternative")
        msg["From"] = self.username
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if reply_to:
            msg["In-Reply-To"] = reply_to
            msg["References"] = reply_to

        msg.attach(MIMEText(body, "plain", "utf-8"))

        recipients = [a.strip() for a in to.split(",")]
        if cc:
            recipients += [a.strip() for a in cc.split(",")]

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(self.username, self.password)
                server.sendmail(self.username, recipients, msg.as_string())
            logger.info("Email sent to %s: %s", to, subject)
            return f"Email sent successfully to {to}"
        except Exception as exc:
            logger.error("Failed to send email: %s", exc)
            return f"Error sending email: {exc}"

    # ------------------------------------------------------------------
    # Poll for new messages (returns UIDs unseen since last check)
    # ------------------------------------------------------------------

    async def poll_unseen(self, folder: str = "INBOX") -> List[str]:
        """Return UIDs of UNSEEN messages in *folder*."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._poll_unseen_sync, folder,
            )

    def _poll_unseen_sync(self, folder: str) -> List[str]:
        conn = self._imap
        if not conn:
            raise RuntimeError("IMAP not connected")
        conn.select(folder, readonly=True)
        _typ, data = conn.search(None, "(UNSEEN)")
        uids = data[0].split() if data[0] else []
        return [u.decode() for u in uids]
