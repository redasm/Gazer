from types import SimpleNamespace

import pytest

from channels.telegram import TelegramChannel
from bus.events import OutboundMessage


class _FakeFile:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def download_as_bytearray(self):
        return bytearray(self._data)


class _FakeVoice:
    mime_type = "audio/ogg"
    duration = 2

    async def get_file(self):
        return _FakeFile(b"voice-bytes")


class _FakeVideo:
    mime_type = "video/mp4"
    duration = 3

    async def get_file(self):
        return _FakeFile(b"video-bytes")


class _FakeDoc:
    mime_type = "application/pdf"
    file_name = "demo.pdf"

    async def get_file(self):
        return _FakeFile(b"pdf-bytes")


@pytest.mark.asyncio
async def test_telegram_on_voice_publishes_media_and_metadata(monkeypatch):
    import channels.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "save_media", lambda data, ext=".ogg", prefix="tg_voice": "data/media/tg_voice.ogg")
    channel = object.__new__(TelegramChannel)
    captured = {}

    async def _fake_publish(**kwargs):
        captured.update(kwargs)

    channel.publish = _fake_publish

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
        message=SimpleNamespace(voice=_FakeVoice(), caption=None),
    )
    await channel._on_voice(update, None)
    assert captured["media"] == ["data/media/tg_voice.ogg"]
    assert captured["metadata"]["telegram_message_type"] == "voice"


@pytest.mark.asyncio
async def test_telegram_on_video_and_file_publishes(monkeypatch):
    import channels.telegram as tg_mod

    monkeypatch.setattr(
        tg_mod,
        "save_media",
        lambda data, ext=".bin", prefix="tg": f"data/media/{prefix}{ext}",
    )
    channel = object.__new__(TelegramChannel)
    calls = []

    async def _fake_publish(**kwargs):
        calls.append(kwargs)

    channel.publish = _fake_publish

    update_video = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
        message=SimpleNamespace(video=_FakeVideo(), caption=None),
    )
    update_doc = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
        message=SimpleNamespace(document=_FakeDoc(), caption=None),
    )
    await channel._on_video(update_video, None)
    await channel._on_document_file(update_doc, None)

    assert len(calls) == 2
    assert calls[0]["metadata"]["telegram_message_type"] == "video"
    assert calls[1]["metadata"]["telegram_message_type"] == "file"


@pytest.mark.asyncio
async def test_telegram_command_passthrough_publishes_non_builtin_command():
    channel = object.__new__(TelegramChannel)
    captured = {}

    async def _fake_publish(**kwargs):
        captured.update(kwargs)

    channel.publish = _fake_publish
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
        message=SimpleNamespace(text="/model show"),
    )
    await channel._on_command_passthrough(update, None)
    assert captured["content"] == "/model show"
    assert captured["chat_id"] == "2"
    assert captured["sender_id"] == "1"


@pytest.mark.asyncio
async def test_telegram_command_passthrough_ignores_start_and_fix():
    channel = object.__new__(TelegramChannel)
    calls = []

    async def _fake_publish(**kwargs):
        calls.append(kwargs)

    channel.publish = _fake_publish
    update_start = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
        message=SimpleNamespace(text="/start"),
    )
    update_fix = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
        message=SimpleNamespace(text="/fix be concise"),
    )
    await channel._on_command_passthrough(update_start, None)
    await channel._on_command_passthrough(update_fix, None)
    assert calls == []


@pytest.mark.asyncio
async def test_telegram_send_uses_reply_to_message_id():
    calls = []
    channel = object.__new__(TelegramChannel)
    channel.app = SimpleNamespace(
        bot=SimpleNamespace(
            send_message=lambda **kwargs: calls.append(kwargs),
            send_chat_action=lambda **kwargs: None,
        )
    )

    async def _send_message(**kwargs):
        calls.append(kwargs)

    channel.app.bot.send_message = _send_message

    await channel.send(
        OutboundMessage(channel="telegram", chat_id="2", content="reply", reply_to="123")
    )

    assert calls[0]["reply_to_message_id"] == 123
