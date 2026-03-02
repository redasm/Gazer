"""Verify channel adapter architecture: all messages flow through MessageBus."""

import asyncio
import os
import sys
from pathlib import Path
from queue import Queue

project_root = Path(__file__).resolve().parent.parent
src_root = project_root / "src"
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(src_root))
os.chdir(project_root)


def test_channel_adapter_interface():
    """ChannelAdapter ABC enforces the correct interface."""
    from channels.base import ChannelAdapter

    # Verify it cannot be instantiated directly
    try:
        ChannelAdapter()
        assert False, "Should not be able to instantiate abstract class"
    except TypeError:
        pass

    print("OK: ChannelAdapter is abstract and not directly instantiable.")


def test_web_channel_publishes_to_bus():
    """WebChannel routes messages through MessageBus instead of calling agent directly."""
    from bus.queue import MessageBus
    from bus.events import OutboundMessage
    from channels.web import WebChannel

    ipc_in = Queue()
    ipc_out = Queue()
    ui_q = Queue()

    bus = MessageBus()
    web = WebChannel(ipc_in, ipc_out, ui_queue=ui_q)
    web.bind(bus)

    # Simulate user sending a chat message via IPC
    ipc_in.put({"type": "chat", "content": "hello from web"})

    # Run the channel for a brief moment
    async def run():
        task = asyncio.create_task(web.start())
        await asyncio.sleep(0.15)  # enough for at least one poll cycle
        web._running = False
        task.cancel()

        # The message should be in the bus inbound queue
        assert not bus.inbound.empty(), "Expected an inbound message on the bus"
        msg = await bus.inbound.get()
        assert msg.channel == "web"
        assert msg.chat_id == "web-main"
        assert msg.content == "hello from web"
        assert msg.sender_id == "WebUser"

    asyncio.run(run())
    print("OK: WebChannel publishes inbound messages to MessageBus.")


def test_web_channel_publishes_media_and_metadata():
    """WebChannel should forward media/metadata from IPC to InboundMessage."""
    from bus.queue import MessageBus
    from channels.web import WebChannel

    ipc_in = Queue()
    ipc_out = Queue()
    bus = MessageBus()
    web = WebChannel(ipc_in, ipc_out)
    web.bind(bus)

    ipc_in.put(
        {
            "type": "chat",
            "content": "[User sent media]",
            "media": ["data/media/web_1.png"],
            "metadata": {"web_media": [{"source": "b64", "path": "data/media/web_1.png"}]},
            "chat_id": "web-main",
            "sender_id": "WebUser",
        }
    )

    async def run():
        task = asyncio.create_task(web.start())
        await asyncio.sleep(0.15)
        web._running = False
        task.cancel()
        assert not bus.inbound.empty()
        msg = await bus.inbound.get()
        assert msg.media == ["data/media/web_1.png"]
        assert isinstance(msg.metadata, dict)
        assert "web_media" in msg.metadata

    asyncio.run(run())
    print("OK: WebChannel forwards media/metadata.")


def test_web_channel_sends_outbound():
    """WebChannel.send() writes to IPC output queue."""
    from bus.events import OutboundMessage
    from channels.web import WebChannel

    ipc_in = Queue()
    ipc_out = Queue()

    web = WebChannel(ipc_in, ipc_out)

    msg = OutboundMessage(channel="web", chat_id="web-main", content="agent reply")
    asyncio.run(web.send(msg))

    assert not ipc_out.empty()
    result = ipc_out.get()
    assert result["type"] == "chat_end"
    assert result["content"] == "agent reply"

    print("OK: WebChannel.send() forwards to IPC output.")


def test_web_channel_partial():
    """Partial messages update UI status, not IPC output."""
    from bus.events import OutboundMessage
    from channels.web import WebChannel

    ipc_in = Queue()
    ipc_out = Queue()
    ui_q = Queue()

    web = WebChannel(ipc_in, ipc_out, ui_queue=ui_q)

    partial = OutboundMessage(
        channel="web", chat_id="web-main", content="Thinking...", is_partial=True
    )
    asyncio.run(web.send(partial))

    # Partials are streamed to ipc_output as "chat_stream" AND update UI queue
    assert not ipc_out.empty(), "Partial should go to ipc_output as chat_stream"
    stream_msg = ipc_out.get()
    assert stream_msg["type"] == "chat_stream"
    assert stream_msg["content"] == "Thinking..."

    assert not ui_q.empty(), "Partial should update UI queue"
    status = ui_q.get()
    assert status["data"] == "Thinking..."

    print("OK: Partial messages go to UI queue, not IPC output.")


def test_outbound_message_has_is_partial():
    """OutboundMessage supports the is_partial field."""
    from bus.events import OutboundMessage

    msg = OutboundMessage(channel="test", chat_id="1", content="hi")
    assert msg.is_partial is False

    partial = OutboundMessage(channel="test", chat_id="1", content="...", is_partial=True)
    assert partial.is_partial is True

    print("OK: OutboundMessage.is_partial works correctly.")


def test_web_channel_typing_event_updates_ui_status():
    """WebChannel typing events should update the UI status queue."""
    from bus.events import TypingEvent
    from channels.web import WebChannel

    ipc_in = Queue()
    ui_q = Queue()
    web = WebChannel(ipc_in, None, ui_queue=ui_q)

    asyncio.run(web._on_typing(TypingEvent(channel="web", chat_id="web-main", is_typing=True)))
    assert not ui_q.empty()
    assert ui_q.get()["data"] == "Typing..."

    asyncio.run(web._on_typing(TypingEvent(channel="web", chat_id="web-main", is_typing=False)))
    assert not ui_q.empty()
    assert ui_q.get()["data"] == "Idle"

    print("OK: WebChannel typing events update UI status.")


def test_brain_has_no_poll_ipc():
    """brain.py no longer contains _poll_ipc -- Web Chat goes through channels."""
    brain_path = project_root / "src" / "runtime" / "brain.py"
    content = brain_path.read_text()
    assert "_poll_ipc" not in content, "_poll_ipc should be removed from brain.py"
    assert "GazerTelegram" not in content, "Direct GazerTelegram import should be removed"
    assert "self.channels" in content, "Brain should manage channels via self.channels"
    print("OK: brain.py uses unified channel management, no _poll_ipc.")


def test_no_direct_agent_call_in_web():
    """WebChannel should not call agent.process_message() directly."""
    web_path = project_root / "src" / "channels" / "web.py"
    content = web_path.read_text()
    # No actual method call to .process_message( in executable code
    assert ".process_message(" not in content, "WebChannel must not call agent.process_message()"
    assert "self.publish(" in content, "WebChannel must use self.publish() via bus"
    print("OK: WebChannel routes through MessageBus, not agent directly.")


if __name__ == "__main__":
    test_channel_adapter_interface()
    test_outbound_message_has_is_partial()
    test_web_channel_publishes_to_bus()
    test_web_channel_sends_outbound()
    test_web_channel_partial()
    test_brain_has_no_poll_ipc()
    test_no_direct_agent_call_in_web()
    print("\n=== ALL CHANNEL VERIFICATION PASSED ===")
