from pathlib import Path
from types import SimpleNamespace

import pytest

import runtime.config_manager as config_manager
import tools.admin.api_facade as admin_api
from agent.loop import AgentLoop
from bus.queue import MessageBus
from llm.base import LLMResponse, ToolCallRequest
from runtime.resilience import RetryBudget
from tools.base import Tool
from tools.batching import ToolBatchPlanner, ToolBatchingTracker


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


class _Provider:
    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return LLMResponse(content="ok", tool_calls=[])


class _EchoTool(Tool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "echo"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        }

    @property
    def owner_only(self) -> bool:
        return False

    @property
    def provider(self) -> str:
        return "web"

    async def execute(self, q: str = "", **kwargs) -> str:
        self.calls += 1
        return f"echo:{q}"


def _build_loop(monkeypatch, tmp_path: Path, config_data: dict) -> AgentLoop:
    monkeypatch.setattr(config_manager, "config", _FakeConfig(config_data))
    return AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=Path(tmp_path),
    )


def test_tool_batch_planner_dedupes_and_batches():
    planner = ToolBatchPlanner(enabled=True, max_batch_size=2, dedupe_enabled=True)
    calls = [
        ToolCallRequest(id="1", name="web_search", arguments={"q": "gazer"}),
        ToolCallRequest(id="2", name="web_search", arguments={"q": "gazer"}),
        ToolCallRequest(id="3", name="web_search", arguments={"q": "openviking"}),
    ]
    plan = planner.plan(
        calls,
        lane_resolver=lambda _name: "network",
        max_parallel_calls=4,
    )
    assert plan.requested_calls == 3
    assert plan.unique_calls == 2
    assert plan.deduped_calls == 1
    assert plan.duplicate_of["2"] == "1"
    assert plan.batch_groups == 1


def test_loop_planner_respects_dependencies(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "parallel_tool_lane_limits": {"io": 2, "device": 1, "network": 2, "default": 2},
                "tool_batching": {"enabled": True, "max_batch_size": 2, "dedupe_enabled": True},
                "tool_planner": {"enabled": True},
            }
        },
    )
    calls = [
        ToolCallRequest(id="1", name="echo_tool", arguments={"q": "a"}),
        ToolCallRequest(id="2", name="echo_tool", arguments={"q": "b", "depends_on": "1"}),
        ToolCallRequest(id="3", name="echo_tool", arguments={"q": "c"}),
    ]

    plan = loop._plan_tool_calls(calls, max_parallel_calls=4)
    assert plan.used_dependency_scheduler is True
    assert plan.dependency_levels == [["1", "3"], ["2"]]
    assert [[tc.id for tc in batch] for batch in plan.batch_plan.batches] == [["1", "3"], ["2"]]


def test_loop_compacts_tool_result_for_context(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "tool_batching": {"enabled": True, "max_batch_size": 2, "dedupe_enabled": True},
                "tool_planner": {
                    "enabled": True,
                    "compact_results": True,
                    "max_result_chars": 120,
                    "error_max_result_chars": 200,
                    "head_chars": 40,
                    "tail_chars": 30,
                },
            }
        },
    )
    compacted = loop._compact_tool_result_for_context(
        tool_name="echo_tool",
        result=("z" * 260),
    )
    assert "[planner_compacted tool=echo_tool" in compacted


@pytest.mark.asyncio
async def test_loop_execute_tool_calls_with_batching(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "parallel_tool_lane_limits": {"io": 2, "device": 1, "network": 2, "default": 2},
                "tool_batching": {"enabled": True, "max_batch_size": 2, "dedupe_enabled": True},
            }
        },
    )
    tool = _EchoTool()
    loop.tools.register(tool)

    calls = [
        ToolCallRequest(id="1", name="echo_tool", arguments={"q": "gazer"}),
        ToolCallRequest(id="2", name="echo_tool", arguments={"q": "gazer"}),
        ToolCallRequest(id="3", name="echo_tool", arguments={"q": "agent"}),
    ]

    results, plan = await loop._execute_tool_calls_with_batching(
        calls,

        policy=loop._resolve_tool_policy(),
        retry_budget=RetryBudget.from_total(4),
        sender_id="u1",
        channel="web",
        chat_id="c1",
        max_parallel_calls=4,
    )

    assert plan.requested_calls == 3
    assert plan.unique_calls == 2
    assert plan.deduped_calls == 1
    assert tool.calls == 2
    assert results == ["echo:gazer", "echo:gazer", "echo:agent"]


@pytest.mark.asyncio
async def test_tool_batching_observability_endpoint(monkeypatch):
    tracker = ToolBatchingTracker()
    tracker.record_turn(
        total_tokens=120,
        tool_rounds=2,
        parallel_rounds=1,
        tool_calls_requested=4,
        tool_calls_executed=3,
        deduped_calls=1,
        batch_groups=2,
    )

    monkeypatch.setattr(
        "tools.admin.system.get_usage_tracker",
        lambda: SimpleNamespace(summary=lambda: {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}),
    )
    monkeypatch.setattr("tools.admin.system.get_tool_batching_tracker", lambda: tracker)
    monkeypatch.setattr("tools.admin.state.get_tool_batching_tracker", lambda: tracker)
    monkeypatch.setattr("tools.admin.observability.get_tool_batching_tracker", lambda: tracker)

    usage = await admin_api.get_usage_stats()
    assert usage["status"] == "ok"
    assert usage["tool_batching"]["turns"] == 1
    assert usage["tool_batching"]["totals"]["deduped_calls"] == 1

    observability = await admin_api.get_tool_batching_observability()
    assert observability["status"] == "ok"
    assert observability["available"] is True
    assert observability["metrics"]["parallel_gain_ratio"] == 2.0
