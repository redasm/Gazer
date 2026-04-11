import pytest

from bus.events import OutboundMessage
from channels.base import ChannelAdapter
from channels.signal_channel import SignalChannel


class _DummyChannel(ChannelAdapter):
    channel_name = "dummy"

    @classmethod
    def from_config(cls, config, **kwargs):
        return None

    async def start(self):
        return None

    async def stop(self):
        return None

    async def send(self, msg):
        return None


def test_partial_tool_progress_text_only_for_tool_events():
    msg = OutboundMessage(
        channel="dummy",
        chat_id="c1",
        content='exec: "npm install"',
        is_partial=True,
        metadata={"stream_event": "tool_call", "event_type": "call"},
    )
    assert _DummyChannel._get_partial_tool_progress_text(msg) == 'exec: "npm install"'

    regular_partial = OutboundMessage(
        channel="dummy",
        chat_id="c1",
        content="hello",
        is_partial=True,
        metadata={},
    )
    assert _DummyChannel._get_partial_tool_progress_text(regular_partial) == ""


@pytest.mark.asyncio
async def test_signal_channel_sends_tool_progress_partial(monkeypatch):
    channel = SignalChannel(api_url="http://signal.test", phone_number="+10000000000")
    captured = []

    async def _fake_send_text(to, text, quote_timestamp="", metadata=None):
        captured.append(
            {
                "to": to,
                "text": text,
                "quote_timestamp": quote_timestamp,
                "metadata": metadata,
            }
        )
        return True

    monkeypatch.setattr(channel, "_send_text", _fake_send_text)

    await channel.send(
        OutboundMessage(
            channel="signal",
            chat_id="+12223334444",
            content='find_files: "*.py"',
            reply_to="123456",
            is_partial=True,
            metadata={"stream_event": "tool_call", "event_type": "call"},
        )
    )

    assert captured == [
        {
            "to": "+12223334444",
            "text": 'find_files: "*.py"',
            "quote_timestamp": "123456",
            "metadata": {"stream_event": "tool_call", "event_type": "call"},
        }
    ]
