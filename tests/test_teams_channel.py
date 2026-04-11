from types import SimpleNamespace

import pytest

from bus.events import OutboundMessage
from channels.teams import TeamsChannel


@pytest.mark.asyncio
async def test_teams_handle_activity_sets_reply_to_metadata():
    captured = {}
    channel = object.__new__(TeamsChannel)

    async def _fake_publish(**kwargs):
        captured.update(kwargs)

    channel.publish = _fake_publish
    channel._download_url = lambda *_args, **_kwargs: None

    activity = {
        "type": "message",
        "id": "activity-123",
        "text": "hello",
        "serviceUrl": "https://smba.example.com",
        "from": {"id": "user-1", "name": "User"},
        "conversation": {"id": "conv-1"},
        "attachments": [],
        "entities": [],
    }

    await channel.handle_activity(activity)

    assert captured["metadata"]["reply_to"] == "activity-123"
    assert captured["metadata"]["teams_activity_id"] == "activity-123"


@pytest.mark.asyncio
async def test_teams_send_reply_includes_reply_to_id():
    calls = []
    channel = object.__new__(TeamsChannel)
    channel._http = SimpleNamespace(post=lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(status_code=200, text="ok"))

    async def _ensure_token():
        return "token"

    channel._ensure_token = _ensure_token

    await channel._send_reply(
        OutboundMessage(
            channel="teams",
            chat_id="conv-1",
            content="reply",
            reply_to="activity-123",
            metadata={"teams_service_url": "https://smba.example.com"},
        )
    )

    payload = calls[0][1]["json"]
    assert payload["replyToId"] == "activity-123"


@pytest.mark.asyncio
async def test_teams_send_typing_includes_reply_to_id():
    calls = []
    channel = object.__new__(TeamsChannel)
    channel._http = SimpleNamespace(post=lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(status_code=200, text="ok"))

    async def _ensure_token():
        return "token"

    channel._ensure_token = _ensure_token

    await channel._send_typing(
        OutboundMessage(
            channel="teams",
            chat_id="conv-1",
            content="",
            is_partial=True,
            reply_to="activity-123",
            metadata={"teams_service_url": "https://smba.example.com"},
        )
    )

    payload = calls[0][1]["json"]
    assert payload["type"] == "typing"
    assert payload["replyToId"] == "activity-123"
