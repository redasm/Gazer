import asyncio
from pathlib import Path

import pytest

import multi_agent.runtime as multi_agent_runtime
import runtime.config_manager as config_manager
from agent.adapter import GazerAgent
from agent.loop import AgentLoop
from bus.events import InboundMessage
from bus.queue import MessageBus
from config.defaults import DEFAULT_CONFIG
from llm.base import LLMResponse
from tools.registry import ToolPolicy


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
        return [*messages, {"role": "tool", "content": result, "tool_call_id": tool_call_id, "name": tool_name}]


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
        return LLMResponse(content="single-agent-fallback", tool_calls=[])


@pytest.mark.asyncio
async def test_agent_loop_auto_route_callback_short_circuits_single_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig({"security": {}, "models": {"prompt_cache": {"enabled": False}}}),
    )
    provider = _SequenceProvider([LLMResponse(content="should-not-run", tool_calls=[])])

    async def _auto_route(_msg, _execution_context):
        return "multi-agent-result"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
        auto_route_turn_callback=_auto_route,
    )

    async def _boom(*args, **kwargs):
        raise AssertionError("single-agent turn should be skipped after auto-route")

    monkeypatch.setattr(loop, "_build_turn_context", _boom)

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat-1", content="complex task"),
    )

    assert out is not None
    assert out.content == "multi-agent-result"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_agent_loop_auto_route_callback_receives_execution_context(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig({"security": {}, "models": {"prompt_cache": {"enabled": False}}}),
    )
    provider = _SequenceProvider([LLMResponse(content="should-not-run", tool_calls=[])])
    captured = {}

    async def _auto_route(_msg, execution_context):
        captured["ctx"] = execution_context
        return "multi-agent-result"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
        auto_route_turn_callback=_auto_route,
    )
    expected_policy = ToolPolicy(allow_names={"safe_tool"})
    monkeypatch.setattr(loop, "_resolve_tool_policy", lambda: expected_policy)

    async def _boom(*args, **kwargs):
        raise AssertionError("single-agent turn should be skipped after auto-route")

    monkeypatch.setattr(loop, "_build_turn_context", _boom)

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="owner-1", chat_id="chat-1", content="complex task"),
    )

    assert out is not None
    assert out.content == "multi-agent-result"
    assert captured["ctx"].tool_policy == expected_policy
    assert captured["ctx"].sender_id == "owner-1"
    assert captured["ctx"].channel == "web"


def test_auto_route_skips_internal_channels():
    agent = GazerAgent.__new__(GazerAgent)
    agent._fast_provider = object()
    agent._get_multi_agent_config = lambda: {"allow_multi": True, "max_workers": 5}

    assert agent._should_auto_route_inbound_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat-1", content="hi"),
    ) is True
    assert agent._should_auto_route_inbound_message(
        InboundMessage(channel="gazer", sender_id="u1", chat_id="chat-1", content="hi"),
    ) is False


def test_default_config_removes_legacy_agent_orchestrator_config():
    assert set(DEFAULT_CONFIG["agents"].keys()) == {"defaults"}


@pytest.mark.asyncio
async def test_process_multi_agent_respects_global_worker_budget(monkeypatch):
    agent = GazerAgent.__new__(GazerAgent)
    agent._get_multi_agent_config = lambda: {"allow_multi": True, "max_workers": 2}
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    class _FakeRuntime:
        def __init__(self, _agent_core, max_agents, execution_context=None):
            self.max_agents = max_agents
            self.execution_context = execution_context

        async def execute(self, goal: str) -> str:
            if goal == "first":
                first_started.set()
                await release_first.wait()
                return "first-done"
            second_started.set()
            await release_second.wait()
            return "second-done"

    monkeypatch.setattr(multi_agent_runtime, "MultiAgentRuntime", _FakeRuntime)

    first_task = asyncio.create_task(agent.process_multi_agent("first", max_workers=2))
    await first_started.wait()

    second_task = asyncio.create_task(agent.process_multi_agent("second", max_workers=2))
    await asyncio.sleep(0.05)
    assert second_started.is_set() is False

    release_first.set()
    assert await first_task == "first-done"
    await second_started.wait()
    release_second.set()
    assert await second_task == "second-done"
