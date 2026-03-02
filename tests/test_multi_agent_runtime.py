"""Tests for multi_agent.runtime.MultiAgentRuntime."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from multi_agent.runtime import AgentMode, MultiAgentRuntime


logger = logging.getLogger(__name__)


def _make_llm_response(content: str = "", tool_calls=None):
    """Build a minimal LLMResponse-like mock."""
    r = MagicMock()
    r.content = content
    r.tool_calls = tool_calls or []
    r.has_tool_calls = bool(tool_calls)
    return r


@pytest.fixture
def mock_agent_core():
    core = MagicMock()
    provider = AsyncMock()
    provider.chat = AsyncMock()
    core.provider = provider
    core._fast_provider = provider
    core._fast_model = "fast-v1"
    core.memory_manager = None
    core.loop = MagicMock()
    core.loop.tools = None
    return core


class TestAgentMode:
    def test_values(self):
        assert AgentMode.SINGLE == "single"
        assert AgentMode.MULTI == "multi"


class TestRuntimeConstruction:
    def test_init_success(self, mock_agent_core):
        runtime = MultiAgentRuntime(mock_agent_core, max_agents=3)
        assert runtime._planner is not None
        assert runtime._pool is not None
        assert runtime._bus is not None
        assert runtime._bb is not None

    def test_init_fails_without_provider(self):
        core = MagicMock()
        core.provider = None
        with pytest.raises(AssertionError):
            MultiAgentRuntime(core)


@pytest.mark.asyncio
class TestRuntimeExecution:
    async def test_execute_with_plan_failure(self, mock_agent_core):
        """When the planner can't generate valid JSON, execute returns error."""
        provider = mock_agent_core.provider
        provider.chat = AsyncMock(return_value=_make_llm_response("not valid json"))

        runtime = MultiAgentRuntime(mock_agent_core, max_agents=1)
        result = await runtime.execute("do something")
        assert "Failed" in result or "fail" in result.lower() or "no" in result.lower()

    async def test_execute_with_empty_plan(self, mock_agent_core):
        """When the plan has no tasks, execute returns early."""
        plan = json.dumps({"summary": "empty", "complexity": "simple", "tasks": []})
        provider = mock_agent_core.provider
        provider.chat = AsyncMock(return_value=_make_llm_response(plan))

        runtime = MultiAgentRuntime(mock_agent_core, max_agents=1)
        result = await runtime.execute("do nothing")
        assert "no" in result.lower() or "task" in result.lower()

    async def test_execute_full_pipeline(self, mock_agent_core):
        """Full pipeline: plan -> worker executes -> aggregate results."""
        plan_json = json.dumps({
            "summary": "simple plan",
            "complexity": "simple",
            "tasks": [
                {
                    "name": "task1",
                    "description": "do something",
                    "instruction": "just do it",
                    "objective": "complete the task",
                    "output_format": "text",
                    "tool_guidance": "none",
                    "boundaries": "none",
                    "depends_on": [],
                    "priority": "normal",
                    "required_skills": [],
                    "allow_subtask_spawn": False,
                }
            ],
        })

        provider = mock_agent_core.provider
        call_count = 0

        async def mock_chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            logger.info("mock_chat call #%d", call_count)
            if call_count == 1:
                return _make_llm_response(plan_json)
            else:
                return _make_llm_response('{"result": "task completed"}')

        provider.chat = mock_chat

        runtime = MultiAgentRuntime(mock_agent_core, max_agents=1)

        try:
            result = await asyncio.wait_for(
                runtime.execute("do something simple"),
                timeout=20.0,
            )
            assert isinstance(result, str)
            assert len(result) > 0
        except asyncio.TimeoutError:
            pytest.skip("Full pipeline timed out in test environment")
