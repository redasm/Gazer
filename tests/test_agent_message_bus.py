"""Tests for multi_agent.communication.AgentMessageBus."""

import asyncio
import time

import pytest

from multi_agent.communication import AgentMessageBus
from multi_agent.models import AgentMessage, MessageType


@pytest.fixture
def bus():
    return AgentMessageBus()


@pytest.mark.asyncio
class TestRegistration:
    async def test_register_and_unregister(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        assert "w1" in bus._mailboxes
        await bus.unregister_agent("w1")
        assert "w1" not in bus._mailboxes

    async def test_double_register_no_error(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        await bus.register_agent("w1")


@pytest.mark.asyncio
class TestSendReceive:
    async def test_point_to_point(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        await bus.register_agent("w2")

        msg = AgentMessage(sender_id="w1", target_id="w2", content="hello")
        await bus.send(msg)

        received = await bus.receive("w2", timeout=1.0)
        assert received is not None
        assert received.content == "hello"

    async def test_receive_timeout_returns_none(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        result = await bus.receive("w1", timeout=0.05)
        assert result is None

    async def test_expired_message_skipped(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        msg = AgentMessage(
            sender_id="w2",
            target_id="w1",
            content="old",
            ttl_sec=0.0,
            created_at=time.time() - 1,
        )
        await bus.send(msg)
        result = await bus.receive("w1", timeout=0.1)
        assert result is None

    async def test_broadcast(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        await bus.register_agent("w2")
        await bus.register_agent("w3")

        msg = AgentMessage(
            sender_id="w1",
            target_id=None,
            msg_type=MessageType.BROADCAST,
            content="announce",
        )
        await bus.send(msg)

        r2 = await bus.receive("w2", timeout=0.5)
        r3 = await bus.receive("w3", timeout=0.5)
        assert r2 is not None and r2.content == "announce"
        assert r3 is not None and r3.content == "announce"

        r1 = await bus.receive("w1", timeout=0.05)
        assert r1 is None  # sender should not receive own broadcast


@pytest.mark.asyncio
class TestAskReply:
    async def test_ask_reply(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        await bus.register_agent("w2")

        async def responder():
            msg = await bus.receive("w2", timeout=2.0)
            assert msg is not None
            assert msg.msg_type == MessageType.ASK
            reply = AgentMessage(
                sender_id="w2",
                target_id="w1",
                msg_type=MessageType.REPLY,
                content="pong",
                reply_to=msg.msg_id,
            )
            await bus.send(reply)

        responder_task = asyncio.create_task(responder())
        result = await bus.ask("w1", "w2", content="ping", timeout=3.0)
        await responder_task

        assert result is not None
        assert result.content == "pong"

    async def test_ask_timeout(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        await bus.register_agent("w2")
        result = await bus.ask("w1", "w2", content="ping", timeout=0.1)
        assert result is None


@pytest.mark.asyncio
class TestDrainAll:
    async def test_drain_returns_all_messages(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        for i in range(3):
            await bus.send(AgentMessage(sender_id="w0", target_id="w1", content=f"msg-{i}"))

        msgs = bus.drain_all("w1")
        assert len(msgs) == 3
        assert [m.content for m in msgs] == ["msg-0", "msg-1", "msg-2"]

    async def test_drain_skips_expired(self, bus: AgentMessageBus):
        await bus.register_agent("w1")
        await bus.send(AgentMessage(
            sender_id="w0", target_id="w1", content="expired",
            ttl_sec=0.0, created_at=time.time() - 1,
        ))
        await bus.send(AgentMessage(
            sender_id="w0", target_id="w1", content="fresh", ttl_sec=60.0,
        ))
        msgs = bus.drain_all("w1")
        assert len(msgs) == 1
        assert msgs[0].content == "fresh"
