"""Tests for multi_agent.planner.PlannerAgent."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from multi_agent.agent_pool import AgentPool, PoolConfig
from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.models import Task, TaskPriority, TaskStatus
from multi_agent.planner import PlannerAgent
from multi_agent.task_graph import TaskGraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_provider():
    p = AsyncMock()
    p.chat = AsyncMock()
    return p


@pytest.fixture
def brain(mock_provider):
    return DualBrain(slow_provider=mock_provider, fast_provider=mock_provider)


@pytest.fixture
def graph():
    return TaskGraph()


@pytest.fixture
def bus():
    return AgentMessageBus()


@pytest.fixture
def bb():
    return Blackboard(session_id="plan-test")


@pytest.fixture
def pool(brain, bus, bb, graph):
    return AgentPool(
        dual_brain=brain,
        bus=bus,
        blackboard=bb,
        task_graph=graph,
        config=PoolConfig(max_agents=2, min_agents=1),
    )


@pytest.fixture
def planner(brain, graph, pool, bus, bb):
    return PlannerAgent(
        dual_brain=brain,
        task_graph=graph,
        pool=pool,
        bus=bus,
        blackboard=bb,
    )


# ---------------------------------------------------------------------------
# Plan JSON parsing
# ---------------------------------------------------------------------------

class TestPlanParsing:
    def test_parse_clean_json(self):
        raw = json.dumps({
            "summary": "test plan",
            "complexity": "simple",
            "tasks": [{"name": "t1", "description": "do stuff"}],
        })
        plan = PlannerAgent._parse_plan_json(raw)
        assert plan is not None
        assert plan["summary"] == "test plan"

    def test_parse_json_in_code_block(self):
        raw = '```json\n{"summary": "plan", "complexity": "medium", "tasks": []}\n```'
        plan = PlannerAgent._parse_plan_json(raw)
        assert plan is not None
        assert plan["summary"] == "plan"

    def test_parse_json_with_preamble(self):
        raw = 'Here is the plan:\n\n{"summary": "plan", "tasks": []}'
        plan = PlannerAgent._parse_plan_json(raw)
        assert plan is not None

    def test_parse_invalid_json(self):
        raw = "This is not JSON at all."
        plan = PlannerAgent._parse_plan_json(raw)
        assert plan is None


# ---------------------------------------------------------------------------
# Task graph building
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBuildTaskGraph:
    async def test_build_from_plan(self, planner: PlannerAgent, graph: TaskGraph):
        plan = {
            "summary": "research plan",
            "complexity": "medium",
            "tasks": [
                {
                    "name": "search",
                    "description": "search papers",
                    "instruction": "use arxiv",
                    "objective": "find recent papers",
                    "output_format": "list of papers",
                    "tool_guidance": "web search",
                    "boundaries": "only 2024+",
                    "depends_on": [],
                    "priority": "high",
                    "required_skills": [],
                    "allow_subtask_spawn": True,
                },
                {
                    "name": "summarize",
                    "description": "summarize papers",
                    "instruction": "write summary",
                    "depends_on": ["search"],
                    "priority": "normal",
                },
            ],
        }
        await planner._build_task_graph(plan)

        tasks = graph.tasks
        assert len(tasks) == 2

        search_task = None
        summary_task = None
        for t in tasks.values():
            if t.name == "search":
                search_task = t
            elif t.name == "summarize":
                summary_task = t

        assert search_task is not None
        assert search_task.priority == TaskPriority.HIGH
        assert search_task.objective == "find recent papers"

        assert summary_task is not None
        assert search_task.task_id in summary_task.depends_on

    async def test_build_with_empty_tasks(self, planner: PlannerAgent, graph: TaskGraph):
        await planner._build_task_graph({"summary": "empty", "tasks": []})
        assert len(graph.tasks) == 0


# ---------------------------------------------------------------------------
# Emotion vector
# ---------------------------------------------------------------------------

class TestEmotionVector:
    def test_default_emotion(self, planner: PlannerAgent):
        assert "excitement" in planner.emotion_vector
        assert "frustration" in planner.emotion_vector
        assert "confidence" in planner.emotion_vector

    def test_custom_emotion(self, brain, graph, pool, bus, bb):
        p = PlannerAgent(
            dual_brain=brain,
            task_graph=graph,
            pool=pool,
            bus=bus,
            blackboard=bb,
            emotion_vector={"excitement": 0.8, "frustration": 0.1, "confidence": 0.9},
        )
        assert p.emotion_vector["excitement"] == 0.8
