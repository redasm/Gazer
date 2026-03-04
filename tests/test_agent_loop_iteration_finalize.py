import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.events import InboundMessage
from bus.queue import MessageBus
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
        return LLMResponse(content="final", tool_calls=[])


class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop_tool"

    @property
    def description(self) -> str:
        return "noop"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def provider(self) -> str:
        return "core"

    async def execute(self, **kwargs) -> str:
        return "ok"


class _ConcurrencyTool(Tool):
    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def name(self) -> str:
        return "concurrency_tool"

    @property
    def description(self) -> str:
        return "tracks concurrent execution"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def provider(self) -> str:
        return "core"

    async def execute(self, **kwargs) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.05)
        self.in_flight -= 1
        return "ok"


@pytest.mark.asyncio
async def test_agent_loop_builds_final_response_after_iteration_limit(monkeypatch, tmp_path):
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
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="step1",
                tool_calls=[ToolCallRequest(id="tc1", name="noop_tool", arguments={})],
            ),
            LLMResponse(
                content="step2",
                tool_calls=[ToolCallRequest(id="tc2", name="noop_tool", arguments={})],
            ),
            LLMResponse(content="总结：已执行两步，但还缺少输入参数。", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        max_iterations=2,
        context_builder=_DummyContext(),
    )
    loop.tools.register(_NoopTool())

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="请继续")
    )
    assert out is not None
    assert "已执行两步" in out.content


@pytest.mark.asyncio
async def test_agent_loop_blocks_when_tool_calls_exceed_turn_limit(monkeypatch, tmp_path):
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
                    "max_tool_calls_per_turn": 1,
                    "max_parallel_tool_calls": 2,
                }
            }
        ),
    )
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="step1",
                tool_calls=[ToolCallRequest(id="tc1", name="noop_tool", arguments={})],
            ),
            LLMResponse(
                content="step2",
                tool_calls=[ToolCallRequest(id="tc2", name="noop_tool", arguments={})],
            ),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        max_iterations=4,
        context_builder=_DummyContext(),
    )
    loop.tools.register(_NoopTool())

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="继续执行")
    )
    assert out is not None
    assert "本轮工具调用超出上限" in out.content
    assert "limit=1" in out.content


@pytest.mark.asyncio
async def test_agent_loop_parallel_tool_execution_respects_config_limit(monkeypatch, tmp_path):
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
                    "max_tool_calls_per_turn": 20,
                    "max_parallel_tool_calls": 2,
                }
            }
        ),
    )
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="parallel tools",
                tool_calls=[
                    ToolCallRequest(id="tc1", name="concurrency_tool", arguments={}),
                    ToolCallRequest(id="tc2", name="concurrency_tool", arguments={}),
                    ToolCallRequest(id="tc3", name="concurrency_tool", arguments={}),
                    ToolCallRequest(id="tc4", name="concurrency_tool", arguments={}),
                ],
            ),
            LLMResponse(content="all done", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        max_iterations=4,
        context_builder=_DummyContext(),
    )
    tool = _ConcurrencyTool()
    loop.tools.register(tool)

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="run parallel")
    )
    assert out is not None
    assert out.content == "all done"
    assert tool.max_in_flight == 2
