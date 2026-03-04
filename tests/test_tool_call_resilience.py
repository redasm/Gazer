import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.queue import MessageBus
from llm.base import LLMResponse, ToolCallRequest
from runtime.resilience import RetryBudget
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


class _Provider:
    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return LLMResponse(content="ok", tool_calls=[])


class _SlowTool(Tool):
    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "slow_tool"

    @property
    def description(self) -> str:
        return "slow"

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
        self.calls += 1
        await asyncio.sleep(0.05)
        return "ok"


class _LaneProbeTool(Tool):
    def __init__(self, name: str, provider_name: str, state: dict, delay: float = 0.05):
        self._name = name
        self._provider_name = provider_name
        self._state = state
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "lane probe"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def provider(self) -> str:
        return self._provider_name

    async def execute(self, **kwargs) -> str:
        self._state["active"] = int(self._state.get("active", 0)) + 1
        self._state["max"] = max(int(self._state.get("max", 0)), int(self._state["active"]))
        await asyncio.sleep(self._delay)
        self._state["active"] = int(self._state.get("active", 1)) - 1
        return self._name


def _build_loop(monkeypatch, tmp_path, config_data: dict) -> AgentLoop:
    monkeypatch.setattr(config_manager, "config", _FakeConfig(config_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )
    return AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=Path(tmp_path),
    )


@pytest.mark.asyncio
async def test_tool_call_timeout_retries(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "tool_call_timeout_seconds": 0.01,
                "tool_retry_max": 1,
                "tool_retry_backoff_seconds": 0.0,
            }
        },
    )
    tool = _SlowTool()
    loop.tools.register(tool)

    result = await loop._execute_single_tool_call(
        ToolCallRequest(id="tc1", name="slow_tool", arguments={}),
        max_tier=ToolSafetyTier.SAFE,
        policy=loop._resolve_tool_policy(),
        retry_budget=RetryBudget.from_total(4),
        sender_id="u1",
        channel="web",
    )
    assert "TOOL_TIMEOUT" in result
    assert "Recovery Template:" in result
    assert tool.calls == 2


@pytest.mark.asyncio
async def test_tool_call_retry_budget_exhaustion(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "tool_call_timeout_seconds": 2.0,
                "tool_retry_max": 3,
                "tool_retry_backoff_seconds": 0.0,
            }
        },
    )
    calls = {"count": 0}

    async def _unstable_execute(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("network unavailable")

    loop.tools.execute = _unstable_execute  # type: ignore[assignment]

    result = await loop._execute_single_tool_call(
        ToolCallRequest(id="tc1", name="crash_tool", arguments={}),
        max_tier=ToolSafetyTier.SAFE,
        policy=loop._resolve_tool_policy(),
        retry_budget=RetryBudget.from_total(1),
        sender_id="u1",
        channel="web",
    )
    assert "Retry budget exhausted" in result
    assert "Recovery Template:" in result
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_tool_call_error_result_appends_recovery_template(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "tool_call_timeout_seconds": 2.0,
                "tool_retry_max": 0,
                "tool_retry_backoff_seconds": 0.0,
            }
        },
    )

    async def _blocked_execute(*args, **kwargs):
        return "Error [TOOL_NOT_PERMITTED]: blocked by policy"

    loop.tools.execute = _blocked_execute  # type: ignore[assignment]
    result = await loop._execute_single_tool_call(
        ToolCallRequest(id="tc2", name="web_search", arguments={}),
        max_tier=ToolSafetyTier.SAFE,
        policy=loop._resolve_tool_policy(),
        retry_budget=RetryBudget.from_total(0),
        sender_id="u1",
        channel="web",
    )
    assert "TOOL_NOT_PERMITTED" in result
    assert "Recovery Template:" in result


@pytest.mark.asyncio
async def test_tool_call_hook_blocks_repeated_identical_calls(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "tool_call_timeout_seconds": 2.0,
                "tool_retry_max": 0,
                "tool_retry_backoff_seconds": 0.0,
                "tool_call_hooks": {
                    "enabled": True,
                    "loop_detection_enabled": True,
                    "loop_max_repeats": 1,
                    "loop_window_seconds": 120.0,
                    "session_max_events": 64,
                },
            }
        },
    )
    tool = _SlowTool()
    loop.tools.register(tool)

    first = await loop._execute_single_tool_call(
        ToolCallRequest(id="tc1", name="slow_tool", arguments={}),
        max_tier=ToolSafetyTier.SAFE,
        policy=loop._resolve_tool_policy(),
        retry_budget=RetryBudget.from_total(1),
        sender_id="u1",
        channel="web",
        session_key="web:c1:u1",
    )
    second = await loop._execute_single_tool_call(
        ToolCallRequest(id="tc2", name="slow_tool", arguments={}),
        max_tier=ToolSafetyTier.SAFE,
        policy=loop._resolve_tool_policy(),
        retry_budget=RetryBudget.from_total(1),
        sender_id="u1",
        channel="web",
        session_key="web:c1:u1",
    )

    assert first == "ok"
    assert "TOOL_LOOP_BLOCKED" in second
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_parallel_tool_lanes_isolate_device_calls(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "parallel_tool_lane_limits": {
                    "io": 2,
                    "device": 1,
                    "network": 2,
                    "default": 2,
                },
            }
        },
    )
    device_state = {"active": 0, "max": 0}
    network_state = {"active": 0, "max": 0}
    loop.tools.register(_LaneProbeTool("dev_a", "devices", device_state))
    loop.tools.register(_LaneProbeTool("dev_b", "devices", device_state))
    loop.tools.register(_LaneProbeTool("net_a", "web", network_state))
    loop.tools.register(_LaneProbeTool("net_b", "web", network_state))

    results = await loop._execute_tools_parallel(
        [
            ToolCallRequest(id="1", name="dev_a", arguments={}),
            ToolCallRequest(id="2", name="dev_b", arguments={}),
            ToolCallRequest(id="3", name="net_a", arguments={}),
            ToolCallRequest(id="4", name="net_b", arguments={}),
        ],
        max_tier=ToolSafetyTier.SAFE,
        policy=loop._resolve_tool_policy(),
        retry_budget=RetryBudget.from_total(4),
        sender_id="u1",
        channel="web",
        max_parallel_calls=4,
    )
    assert len(results) == 4
    assert device_state["max"] == 1
    assert network_state["max"] <= 2


def test_classify_tool_parallel_lane(monkeypatch, tmp_path):
    loop = _build_loop(monkeypatch, tmp_path, {"security": {"tool_groups": {}}})
    assert loop._classify_tool_parallel_lane("node_invoke") == "device"
    assert loop._classify_tool_parallel_lane("web_search") == "network"
    assert loop._classify_tool_parallel_lane("read_file") == "io"


def test_plan_tool_batches_dedupes_duplicate_calls(monkeypatch, tmp_path):
    loop = _build_loop(
        monkeypatch,
        tmp_path,
        {
            "security": {
                "tool_groups": {},
                "tool_batching": {"enabled": True, "max_batch_size": 3, "dedupe_enabled": True},
            }
        },
    )
    plan = loop._plan_tool_batches(
        [
            ToolCallRequest(id="a", name="web_search", arguments={"q": "gazer"}),
            ToolCallRequest(id="b", name="web_search", arguments={"q": "gazer"}),
            ToolCallRequest(id="c", name="web_search", arguments={"q": "openclaw"}),
        ],
        max_parallel_calls=4,
    )
    assert plan.requested_calls == 3
    assert plan.unique_calls == 2
    assert plan.deduped_calls == 1
    assert plan.duplicate_of["b"] == "a"
