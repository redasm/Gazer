"""Tests for bus.queue -- MessageBus."""

import asyncio
import pytest
from bus.events import InboundMessage, OutboundMessage, TypingEvent
from bus.queue import MessageBus


@pytest.fixture
def bus():
    return MessageBus()


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_publish_and_consume_inbound(self, bus):
        msg = InboundMessage(channel="web", sender_id="u1", chat_id="c1", content="hello")
        await bus.publish_inbound(msg)
        assert bus.inbound_size == 1
        got = await bus.consume_inbound()
        assert got.content == "hello"
        assert bus.inbound_size == 0

    @pytest.mark.asyncio
    async def test_publish_and_consume_outbound(self, bus):
        msg = OutboundMessage(channel="tg", chat_id="c1", content="reply")
        await bus.publish_outbound(msg)
        assert bus.outbound_size == 1
        got = await bus.consume_outbound()
        assert got.content == "reply"

    @pytest.mark.asyncio
    async def test_rate_limiting(self, bus):
        """Exceed the rate limit and expect ValueError."""
        from bus.queue import _RATE_LIMIT_MAX
        for i in range(_RATE_LIMIT_MAX):
            msg = InboundMessage(channel="web", sender_id="u", chat_id="c", content=str(i))
            await bus.publish_inbound(msg)
        # Next should be rejected
        with pytest.raises(ValueError, match="Rate limit exceeded"):
            await bus.publish_inbound(
                InboundMessage(channel="web", sender_id="u", chat_id="c", content="overflow")
            )

    @pytest.mark.asyncio
    async def test_subscribe_outbound(self, bus):
        received = []

        async def handler(msg):
            received.append(msg)

        bus.subscribe_outbound("web", handler)
        msg = OutboundMessage(channel="web", chat_id="c1", content="hi")
        await bus.publish_outbound(msg)

        # Manually dispatch one round
        out = await bus.consume_outbound()
        for cb in bus._outbound_subscribers.get(out.channel, []):
            await cb(out)
        assert len(received) == 1
        assert received[0].content == "hi"

    @pytest.mark.asyncio
    async def test_subscribe_typing(self, bus):
        events = []

        async def handler(ev):
            events.append(ev)

        bus.subscribe_typing("tg", handler)
        ev = TypingEvent(channel="tg", chat_id="c1", is_typing=True)
        await bus.publish_typing(ev)
        assert len(events) == 1
        assert events[0].is_typing is True

    def test_stop(self, bus):
        bus._running = True
        bus.stop()
        assert bus._running is False
