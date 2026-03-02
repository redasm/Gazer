from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import asyncio
import pytest

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.events import InboundMessage, OutboundMessage
from bus.queue import MessageBus
from channels.web import WebChannel
from llm.base import LLMResponse, ToolCallRequest
from tools.base import Tool, ToolSafetyTier


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key_path: str, default=None):
        cur = self.data
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class _DummyContext:
    async def prepare_memory_context(self, _content: str):
        return None

    def build_messages(self, *, history, current_message, media=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message}]

    def add_assistant_message(self, messages, content, tool_calls):
        return [*messages, {"role": "assistant", "content": content, "tool_calls": tool_calls}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return [*messages, {"role": "tool", "content": result, "tool_call_id": tool_call_id}]


class _SequenceProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="done", tool_calls=[])


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "echo"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def provider(self) -> str:
        return "system"

    async def execute(self, **kwargs) -> str:
        return "ok"


@pytest.mark.asyncio
async def test_agent_loop_emits_tool_call_stream_events(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_max_tier": "safe",
                    "tool_groups": {},
                    "llm_max_retries": 0,
                    "llm_retry_backoff_seconds": 0.0,
                    "tool_retry_max": 0,
                    "tool_retry_backoff_seconds": 0.0,
                }
            }
        ),
    )
    monkeypatch.setattr(
        "agent.loop.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    bus = MessageBus()
    streamed = []

    async def _capture(msg):
        streamed.append(msg)

    bus.subscribe_outbound("web", _capture)
    dispatch_task = asyncio.create_task(bus.dispatch_outbound())
    provider = _SequenceProvider(
        [
            LLMResponse(
                content="calling",
                tool_calls=[ToolCallRequest(id="tc1", name="echo_tool", arguments={})],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
        max_iterations=4,
    )
    loop.tools.register(_EchoTool())

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="run tool"),
    )
    for _ in range(20):
        if bus.outbound_size == 0:
            break
        await asyncio.sleep(0.01)
    bus.stop()
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass
    assert out is not None
    assert out.content == "done"

    tool_events = [
        item
        for item in streamed
        if isinstance(item, OutboundMessage)
        and item.is_partial
        and isinstance(item.metadata, dict)
        and item.metadata.get("stream_event") == "tool_call"
    ]
    assert len(tool_events) == 2
    assert tool_events[0].metadata["event_type"] == "call"
    assert tool_events[0].metadata["payload"]["tool"] == "echo_tool"
    assert tool_events[1].metadata["event_type"] == "result"
    assert tool_events[1].metadata["payload"]["status"] == "ok"


@pytest.mark.asyncio
async def test_agent_loop_fake_tool_call_guard_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_max_tier": "safe",
                    "tool_groups": {},
                    "llm_max_retries": 0,
                    "llm_retry_backoff_seconds": 0.0,
                }
            }
        ),
    )
    monkeypatch.setattr(
        "agent.loop.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider(
        [
            LLMResponse(content="I have taken a screenshot for you.", tool_calls=[]),
            LLMResponse(content="retry success", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
        max_iterations=4,
    )
    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="please take screenshot"),
    )
    assert out is not None
    assert out.content == "retry success"
    assert provider.calls == 2


def test_web_channel_forwards_tool_call_event_to_ipc():
    ipc_in = Queue()
    ipc_out = Queue()
    channel = WebChannel(ipc_in, ipc_out)

    partial = OutboundMessage(
        channel="web",
        chat_id="web-main",
        content="",
        is_partial=True,
        metadata={
            "stream_event": "tool_call",
            "event_type": "call",
            "payload": {"tool": "read_file"},
        },
    )
    asyncio.run(channel.send(partial))
    assert not ipc_out.empty()
    msg = ipc_out.get()
    assert msg["type"] == "tool_call_event"
    assert msg["event_type"] == "call"
    assert msg["payload"]["tool"] == "read_file"
