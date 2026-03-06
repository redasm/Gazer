"""Tests for multi_agent.worker_agent.WorkerAgent."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.models import MultiAgentExecutionContext, Task, TaskPriority, TaskStatus, WorkerResult
from multi_agent.task_graph import TaskGraph
from multi_agent.worker_agent import WorkerAgent, WorkerConfig
from tools.registry import ToolPolicy


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

    async def test_concurrent_claim_allows_only_one_worker(
        self,
        dual_brain: DualBrain,
        graph: TaskGraph,
        bus: AgentMessageBus,
        bb: Blackboard,
    ):
        await bus.register_agent("w-1")
        await bus.register_agent("w-2")
        worker1 = WorkerAgent(
            agent_id="w-1",
            dual_brain=dual_brain,
            task_graph=graph,
            bus=bus,
            blackboard=bb,
            config=WorkerConfig(max_iterations=3, max_error_recovery=1),
        )
        worker2 = WorkerAgent(
            agent_id="w-2",
            dual_brain=dual_brain,
            task_graph=graph,
            bus=bus,
            blackboard=bb,
            config=WorkerConfig(max_iterations=3, max_error_recovery=1),
        )
        await graph.add_task(Task(task_id="t1", name="race"))

        original_mark_running = graph.mark_running
        barrier = asyncio.Event()
        waiting = 0

        async def delayed_mark_running(task_id: str, agent_id: str) -> None:
            nonlocal waiting
            waiting += 1
            if waiting >= 2:
                barrier.set()
            await barrier.wait()
            await original_mark_running(task_id, agent_id)

        with patch.object(graph, "mark_running", side_effect=delayed_mark_running):
            claimed1, claimed2 = await asyncio.gather(
                worker1._claim_task(),
                worker2._claim_task(),
            )

        claimed = [task for task in (claimed1, claimed2) if task is not None]
        assert len(claimed) == 1
        assert claimed[0].task_id == "t1"


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


class _ToolRegistrySpy:
    def __init__(self):
        self.definition_kwargs = None
        self.execute_kwargs = None

    def get_definitions(self, **kwargs):
        self.definition_kwargs = kwargs
        return [{"name": "safe_tool"}]

    async def execute(self, name, params, **kwargs):
        self.execute_kwargs = {"name": name, "params": params, **kwargs}
        return f"ok:{name}"


@pytest.mark.asyncio
class TestToolPolicyContext:
    async def test_worker_passes_execution_context_to_tool_registry(
        self,
        dual_brain: DualBrain,
        graph: TaskGraph,
        bus: AgentMessageBus,
        bb: Blackboard,
    ):
        registry = _ToolRegistrySpy()
        context = MultiAgentExecutionContext(
            tool_policy=ToolPolicy(allow_names={"safe_tool"}),
            sender_id="owner-1",
            channel="web",
            model_provider="openai",
            model_name="gpt-4o-mini",
        )
        worker = WorkerAgent(
            agent_id="w-policy",
            dual_brain=dual_brain,
            task_graph=graph,
            bus=bus,
            blackboard=bb,
            tool_registry=registry,
            execution_context=context,
            config=WorkerConfig(max_iterations=3, max_error_recovery=1),
        )

        definitions = worker._get_tool_definitions()
        result = await worker._execute_tool("safe_tool", {"value": 1})

        assert definitions == [{"name": "safe_tool"}]
        assert result == "ok:safe_tool"
        assert registry.definition_kwargs["policy"] == context.tool_policy
        assert registry.definition_kwargs["sender_id"] == "owner-1"
        assert registry.definition_kwargs["channel"] == "web"
        assert registry.execute_kwargs["policy"] == context.tool_policy
        assert registry.execute_kwargs["sender_id"] == "owner-1"
        assert registry.execute_kwargs["channel"] == "web"

    async def test_need_planner_marks_task_waiting_for_planner(
        self,
        worker: WorkerAgent,
        graph: TaskGraph,
        bus: AgentMessageBus,
    ):
        await bus.register_agent(worker.agent_id)
        await bus.register_agent("planner")
        task = Task(task_id="t-need-planner", name="stuck")
        await graph.add_task(task)
        await graph.mark_running(task.task_id, worker.agent_id)

        await worker._complete_task(
            task,
            json.dumps(
                {
                    "need_planner": True,
                    "need_planner_reason": "requires decomposition",
                }
            ),
        )

        updated = graph.get_task(task.task_id)
        assert updated.status == TaskStatus.WAITING_PLANNER
        assert updated.retry_count == 0


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
