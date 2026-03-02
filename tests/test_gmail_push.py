"""Tests for gazer_email.gmail_push."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from gazer_email.gmail_push import GmailPushManager


@pytest.mark.asyncio
async def test_handle_notification_emits_message_details(tmp_path, monkeypatch):
    captured = []

    async def _on_new(messages):
        captured.extend(messages)

    manager = GmailPushManager(
        history_store=str(Path(tmp_path) / "gmail_history.json"),
        on_new_messages=_on_new,
    )
    manager._history_id = "100"

    async def _fake_fetch_history(new_history_id: str):
        assert new_history_id == "200"
        return ["g1"]

    async def _fake_fetch_details(message_ids):
        assert message_ids == ["g1"]
        return [
            {
                "gmail_id": "g1",
                "from": "Alice <alice@example.com>",
                "from_address": "alice@example.com",
                "subject": "Hello",
                "message_id": "<m1@example.com>",
                "snippet": "Hi there",
                "body_text": "Hi there, can you help me?",
            }
        ]

    monkeypatch.setattr(manager, "_fetch_history", _fake_fetch_history)
    monkeypatch.setattr(manager, "_fetch_message_details", _fake_fetch_details)

    payload = {"emailAddress": "owner@example.com", "historyId": "200"}
    data = {"message": {"data": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")}}

    result = await manager.handle_notification(data)
    assert "processed 1 new message" in result
    assert manager._history_id == "200"
    assert len(captured) == 1
    assert captured[0]["gmail_id"] == "g1"
    assert captured[0]["from_address"] == "alice@example.com"


@pytest.mark.asyncio
async def test_handle_notification_falls_back_to_gmail_ids(tmp_path, monkeypatch):
    captured = []

    async def _on_new(messages):
        captured.extend(messages)

    manager = GmailPushManager(
        history_store=str(Path(tmp_path) / "gmail_history.json"),
        on_new_messages=_on_new,
    )
    manager._history_id = "100"

    async def _fake_fetch_history(_new_history_id: str):
        return ["g1", "g2"]

    async def _fake_fetch_details(_message_ids):
        return []

    monkeypatch.setattr(manager, "_fetch_history", _fake_fetch_history)
    monkeypatch.setattr(manager, "_fetch_message_details", _fake_fetch_details)

    payload = {"emailAddress": "owner@example.com", "historyId": "201"}
    data = {"message": {"data": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")}}

    result = await manager.handle_notification(data)
    assert "processed 2 new message" in result
    assert captured == [{"gmail_id": "g1"}, {"gmail_id": "g2"}]
