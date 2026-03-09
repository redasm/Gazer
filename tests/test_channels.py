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
    """WebChannel routes messages through MessageBus via shared asyncio.Queue."""
    import tools.admin.state as _state
    from bus.queue import MessageBus
    from channels.web import WebChannel

    ui_q = Queue()
    bus = MessageBus()
    web = WebChannel(ui_queue=ui_q)
    web.bind(bus)

    # Inject an asyncio.Queue into state (mimics init_admin_api)
    async def run():
        q = asyncio.Queue()
        _state.API_QUEUES["input"] = q

        await q.put({"type": "chat", "content": "hello from web"})

        task = asyncio.create_task(web.start())
        await asyncio.sleep(0.15)
        web._running = False
        task.cancel()

        assert not bus.inbound.empty(), "Expected an inbound message on the bus"
        msg = await bus.inbound.get()
        assert msg.channel == "web"
        assert msg.chat_id == "web-main"
        assert msg.content == "hello from web"
        assert msg.sender_id == "WebUser"

    asyncio.run(run())
    print("OK: WebChannel publishes inbound messages to MessageBus.")


def test_web_channel_publishes_media_and_metadata():
    """WebChannel should forward media/metadata to InboundMessage."""
    import tools.admin.state as _state
    from bus.queue import MessageBus
    from channels.web import WebChannel

    bus = MessageBus()
    web = WebChannel()
    web.bind(bus)

    async def run():
        q = asyncio.Queue()
        _state.API_QUEUES["input"] = q

        await q.put({
            "type": "chat",
            "content": "[User sent media]",
            "media": ["data/media/web_1.png"],
            "metadata": {"web_media": [{"source": "b64", "path": "data/media/web_1.png"}]},
            "chat_id": "web-main",
            "sender_id": "WebUser",
        })

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

    ui_q = Queue()
    web = WebChannel(ui_queue=ui_q)

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
    assert ".process_message(" not in content, "WebChannel must not call agent.process_message()"
    assert "self.publish(" in content, "WebChannel must use self.publish() via bus"
    print("OK: WebChannel routes through MessageBus, not agent directly.")


def test_single_process_architecture():
    """Verify the codebase uses single-process architecture (no IPC queues)."""
    brain_path = project_root / "src" / "runtime" / "brain.py"
    content = brain_path.read_text()
    assert "ipc_input" not in content, "brain.py should not reference ipc_input"
    assert "ipc_output" not in content, "brain.py should not reference ipc_output"
    assert "_IpcLogHandler" not in content, "_IpcLogHandler should be removed"
    assert "_start_admin_api" in content, "brain.py should start admin API in-process"

    ipc_path = project_root / "src" / "runtime" / "ipc_secure.py"
    assert not ipc_path.exists(), "ipc_secure.py should be deleted"

    print("OK: Single-process architecture verified.")


if __name__ == "__main__":
    test_channel_adapter_interface()
    test_outbound_message_has_is_partial()
    test_web_channel_publishes_to_bus()
    test_web_channel_publishes_media_and_metadata()
    test_web_channel_typing_event_updates_ui_status()
    test_brain_has_no_poll_ipc()
    test_no_direct_agent_call_in_web()
    test_single_process_architecture()
    print("\n=== ALL CHANNEL VERIFICATION PASSED ===")
