"""Tests for bus.events -- InboundMessage, OutboundMessage, TypingEvent."""

from datetime import datetime
from bus.events import InboundMessage, OutboundMessage, TypingEvent


class TestInboundMessage:
    def test_defaults(self):
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="hi")
        assert msg.channel == "telegram"
        assert msg.sender_id == "u1"
        assert msg.content == "hi"
        assert isinstance(msg.timestamp, datetime)
        assert msg.media == []
        assert msg.metadata == {}

    def test_session_key(self):
        msg = InboundMessage(channel="web", sender_id="u", chat_id="main", content="x")
        assert msg.session_key == "web:main"

    def test_media_and_metadata(self):
        msg = InboundMessage(
            channel="tg", sender_id="u", chat_id="c", content="img",
            media=["http://img.png"], metadata={"source": "camera"},
        )
        assert len(msg.media) == 1
        assert msg.metadata["source"] == "camera"


class TestOutboundMessage:
    def test_defaults(self):
        msg = OutboundMessage(channel="web", chat_id="c1", content="reply")
        assert msg.is_partial is False
        assert msg.reply_to is None
        assert msg.media == []

    def test_partial(self):
        msg = OutboundMessage(channel="web", chat_id="c1", content="...", is_partial=True)
        assert msg.is_partial is True


class TestTypingEvent:
    def test_defaults(self):
        ev = TypingEvent(channel="tg", chat_id="c1")
        assert ev.is_typing is True

    def test_stop_typing(self):
        ev = TypingEvent(channel="tg", chat_id="c1", is_typing=False)
        assert ev.is_typing is False
