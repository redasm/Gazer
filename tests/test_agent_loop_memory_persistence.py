from pathlib import Path
from types import SimpleNamespace

import pytest

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.events import InboundMessage
from bus.queue import MessageBus
from llm.base import LLMResponse


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
        return [
            {"role": "system", "content": "system"},
            *history,
            {"role": "user", "content": current_message},
        ]

    def add_assistant_message(self, messages, content, tool_calls):
        return [*messages, {"role": "assistant", "content": content, "tool_calls": tool_calls}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return [*messages, {"role": "tool", "content": result, "tool_call_id": tool_call_id, "name": tool_name}]


class _Provider:
    def get_default_model(self) -> str:
        return "gpt-4o-mini"

    async def chat(self, *args, **kwargs):
        return LLMResponse(content="记住了。", tool_calls=[], usage={"total_tokens": 42})


@pytest.mark.asyncio
async def test_agent_loop_persists_turn_via_callback(monkeypatch, tmp_path: Path):
    fake_cfg = _FakeConfig({"agents": {"defaults": {"model": {"primary": "openai/gpt-4o", "fallbacks": []}}}})
    monkeypatch.setattr(config_manager, "config", fake_cfg)
    monkeypatch.setattr(
        "agent.loop.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda _channel, _sender_id: False),
    )

    captured = []

    async def _persist(msg: InboundMessage, reply: str):
        captured.append((msg.channel, msg.chat_id, msg.sender_id, msg.content, reply))

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
        persist_turn_callback=_persist,
    )

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="c1", content="你还记得我吗？")
    )

    assert out is not None
    assert out.content == "记住了。"
    assert captured == [("web", "c1", "u1", "你还记得我吗？", "记住了。")]
