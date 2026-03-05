"""Tests for tools.email_tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


from tools.email_tools import EmailReadTool, EmailSendTool


class _FakeEmailClient:
    def __init__(self):
        self.find_calls = []
        self.fetch_calls = []
        self.send_calls = []

    async def _ensure_imap(self):
        return None

    async def find_uid_by_message_id(self, message_id: str, folder: str = "INBOX"):
        self.find_calls.append((message_id, folder))
        if message_id == "<m1@example.com>":
            return "42"
        return None

    async def fetch_message(self, uid: str, folder: str = "INBOX"):
        self.fetch_calls.append((uid, folder))
        return SimpleNamespace(
            subject="Hello",
            sender="Alice <alice@example.com>",
            to="owner@example.com",
            cc="",
            date="Tue, 10 Feb 2026 12:00:00 +0000",
            message_id="<m1@example.com>",
            body_text="Hi there, can you help me?",
            body_html="",
            attachments=[],
        )

    async def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        reply_to: str = "",
    ):
        self.send_calls.append((to, subject, body, cc, reply_to))
        return "Email sent successfully to " + to


@pytest.mark.asyncio
async def test_email_read_by_message_id_resolves_uid():
    client = _FakeEmailClient()
    tool = EmailReadTool(client)

    result = await tool.execute(message_id="<m1@example.com>")

    assert "Subject: Hello" in result
    assert client.find_calls == [("<m1@example.com>", "INBOX")]
    assert client.fetch_calls == [("42", "INBOX")]


@pytest.mark.asyncio
async def test_email_read_by_message_id_not_found():
    client = _FakeEmailClient()
    tool = EmailReadTool(client)

    result = await tool.execute(message_id="<missing@example.com>")

    assert "not found" in result
    assert client.fetch_calls == []


@pytest.mark.asyncio
async def test_email_send_tool_is_standard_and_sends():
    client = _FakeEmailClient()
    tool = EmailSendTool(client)

    assert tool.owner_only is False
    result = await tool.execute(
        to="alice@example.com",
        subject="Re: Hello",
        body="Thanks for your email.",
        reply_to="<m1@example.com>",
    )

    assert "Email sent successfully" in result
    assert client.send_calls == [
        ("alice@example.com", "Re: Hello", "Thanks for your email.", "", "<m1@example.com>")
    ]


@pytest.mark.asyncio
async def test_email_send_missing_required_args_returns_error_code():
    client = _FakeEmailClient()
    tool = EmailSendTool(client)
    result = await tool.execute(subject="x", body="y")
    assert "EMAIL_SEND_ARGS_REQUIRED" in result
