"""Tests for multi_agent.worker_agent.WorkerAgent."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.models import Task, TaskPriority, TaskStatus, WorkerResult
from multi_agent.task_graph import TaskGraph
from multi_agent.worker_agent import WorkerAgent, WorkerConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_slow_provider():
    p = AsyncMock()
    p.chat = AsyncMock()
    return p


@pytest.fixture
def mock_fast_provider():
    p = AsyncMock()
    p.chat = AsyncMock()
    return p


@pytest.fixture
def dual_brain(mock_slow_provider, mock_fast_provider):
    return DualBrain(
        slow_provider=mock_slow_provider,
        fast_provider=mock_fast_provider,
        fast_model="fast-model",
    )


@pytest.fixture
def graph():
    return TaskGraph()


@pytest.fixture
def bus():
    return AgentMessageBus()


@pytest.fixture
def bb():
    return Blackboard(session_id="test")


@pytest.fixture
def worker(dual_brain, graph, bus, bb):
    return WorkerAgent(
        agent_id="w-test",
        dual_brain=dual_brain,
        task_graph=graph,
        bus=bus,
        blackboard=bb,
        config=WorkerConfig(max_iterations=3, max_error_recovery=1),
    )


# ---------------------------------------------------------------------------
# Work stealing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestWorkStealing:
    async def test_claim_ready_task(self, worker: WorkerAgent, graph: TaskGraph, bus: AgentMessageBus):
        await bus.register_agent(worker.agent_id)
        t = Task(task_id="t1", name="simple")
        await graph.add_task(t)

        claimed = await worker._claim_task()
        assert claimed is not None
        assert claimed.task_id == "t1"
        assert graph.get_task("t1").status == TaskStatus.RUNNING

    async def test_claim_skips_skill_mismatch(self, worker: WorkerAgent, graph: TaskGraph, bus: AgentMessageBus):
        await bus.register_agent(worker.agent_id)
        t = Task(task_id="t1", name="specialist", required_skills=["python-expert"])
        await graph.add_task(t)

        claimed = await worker._claim_task()
        assert claimed is None

    async def test_claim_returns_none_when_empty(self, worker: WorkerAgent, graph: TaskGraph, bus: AgentMessageBus):
        await bus.register_agent(worker.agent_id)
        claimed = await worker._claim_task()
        assert claimed is None


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

class TestOutputParsing:
    def test_parse_json_output(self):
        raw = json.dumps({"result": "done", "need_planner": False, "artifacts": {"file": "a.txt"}})
        wr = WorkerAgent._parse_worker_output(raw)
        assert wr.result == "done"
        assert wr.artifacts == {"file": "a.txt"}

    def test_parse_json_in_code_block(self):
        raw = '```json\n{"result": "done", "spawn_subtasks": true, "subtasks": [{"name": "sub"}]}\n```'
        wr = WorkerAgent._parse_worker_output(raw)
        assert wr.result == "done"
        assert wr.spawn_subtasks is True

    def test_parse_plain_text_fallback(self):
        raw = "This is just a plain text result."
        wr = WorkerAgent._parse_worker_output(raw)
        assert wr.result == raw
        assert wr.need_planner is False

    def test_parse_invalid_json_fallback(self):
        raw = "{broken json"
        wr = WorkerAgent._parse_worker_output(raw)
        assert wr.result == raw


# ---------------------------------------------------------------------------
# BrainHint routing (replaces old _estimate_complexity tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBrainHintRouting:
    async def test_normal_execution_uses_fast(
        self, worker: WorkerAgent, graph: TaskGraph, bus: AgentMessageBus,
        mock_fast_provider, mock_slow_provider,
    ):
        """Normal task execution should route through fast brain (depth=1)."""
        await bus.register_agent(worker.agent_id)
        t = Task(task_id="t1", name="simple", instruction="do it")
        await graph.add_task(t)

        mock_fast_provider.chat = AsyncMock(
            return_value=MagicMock(content='{"result": "done"}', tool_calls=[], has_tool_calls=False),
        )
        mock_slow_provider.chat = AsyncMock(
            return_value=MagicMock(content='{"result": "done"}', tool_calls=[], has_tool_calls=False),
        )

        claimed = await worker._claim_task()
        assert claimed is not None
        await worker._execute_task(claimed)

        assert mock_fast_provider.chat.await_count >= 1

    async def test_error_recovery_escalates_to_slow(
        self, worker: WorkerAgent, graph: TaskGraph, bus: AgentMessageBus,
        mock_fast_provider, mock_slow_provider,
    ):
        """After an error, the worker should switch to slow brain (quality_critical)."""
        await bus.register_agent(worker.agent_id)
        t = Task(task_id="t2", name="error-task", instruction="do it")
        await graph.add_task(t)

        call_count = 0

        async def mock_chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated error")
            return MagicMock(content='{"result": "recovered"}', tool_calls=[], has_tool_calls=False)

        mock_fast_provider.chat = mock_chat
        mock_slow_provider.chat = AsyncMock(
            return_value=MagicMock(content='{"result": "recovered"}', tool_calls=[], has_tool_calls=False),
        )

        claimed = await worker._claim_task()
        assert claimed is not None
        await worker._execute_task(claimed)

        assert mock_slow_provider.chat.await_count >= 1


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestPromptBuilding:
    def test_system_prompt_includes_boundaries(self):
        t = Task(name="x", tool_guidance="use search", boundaries="no code repos")
        prompt = WorkerAgent._build_system_prompt(t)
        assert "use search" in prompt
        assert "no code repos" in prompt

    def test_user_prompt_includes_deps(self):
        t = Task(name="analyze", objective="find papers", instruction="search arxiv")
        dep_results = {"t0": "prior findings"}
        prompt = WorkerAgent._build_user_prompt(t, dep_results)
        assert "find papers" in prompt
        assert "prior findings" in prompt


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestLifecycle:
    async def test_start_stop(self, worker: WorkerAgent, bus: AgentMessageBus):
        await worker.start()
        assert worker.is_running
        await asyncio.sleep(0.1)
        await worker.stop()
        assert not worker.is_running
