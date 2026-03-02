"""Tests for multi_agent.agent_pool.AgentPool."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from multi_agent.agent_pool import AgentPool, PoolConfig
from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.models import Task
from multi_agent.task_graph import TaskGraph


@pytest.fixture
def mock_provider():
    p = AsyncMock()
    p.chat = AsyncMock()
    return p


@pytest.fixture
def brain(mock_provider):
    return DualBrain(slow_provider=mock_provider, fast_provider=mock_provider)


@pytest.fixture
def bus():
    return AgentMessageBus()


@pytest.fixture
def bb():
    return Blackboard(session_id="pool-test")


@pytest.fixture
def graph():
    return TaskGraph()


@pytest.fixture
def pool(brain, bus, bb, graph):
    config = PoolConfig(max_agents=3, min_agents=1, idle_timeout=0.5)
    return AgentPool(
        dual_brain=brain,
        bus=bus,
        blackboard=bb,
        task_graph=graph,
        config=config,
    )


@pytest.mark.asyncio
class TestPoolLifecycle:
    async def test_start_spawns_min_agents(self, pool: AgentPool):
        await pool.start()
        status = pool.get_status()
        assert status["total"] == 1
        assert status["max"] == 3
        await pool.stop()

    async def test_stop_clears_workers(self, pool: AgentPool):
        await pool.start()
        await pool.stop()
        assert pool.get_status()["total"] == 0


@pytest.mark.asyncio
class TestPoolScaling:
    async def test_scale_up_on_ready_tasks(self, pool: AgentPool, graph: TaskGraph):
        for i in range(3):
            await graph.add_task(Task(task_id=f"t{i}", name=f"task-{i}"))

        await pool.start()
        await pool._evaluate_scaling()
        status = pool.get_status()
        assert status["total"] >= 2  # Should have scaled up
        await pool.stop()

    async def test_set_max_agents(self, pool: AgentPool):
        await pool.start()
        await pool.set_max_agents(10)
        assert pool._config.max_agents == 10
        await pool.stop()

    async def test_max_agents_respects_minimum(self, pool: AgentPool):
        await pool.start()
        await pool.set_max_agents(0)
        assert pool._config.max_agents == 1  # min_agents is 1
        await pool.stop()


@pytest.mark.asyncio
class TestPoolStatus:
    async def test_status_keys(self, pool: AgentPool):
        await pool.start()
        status = pool.get_status()
        assert "total" in status
        assert "busy" in status
        assert "idle" in status
        assert "max" in status
        assert "tasks_summary" in status
        await pool.stop()
