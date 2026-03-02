"""Tests for multi_agent.assessor — TaskComplexityAssessor."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from multi_agent.assessor import (
    WORKER_COUNT_MAP,
    AssessmentResult,
    TaskComplexityAssessor,
)
from multi_agent.brain_router import DualBrainRouter


def _make_router(response_text: str) -> DualBrainRouter:
    """Build a DualBrainRouter with a mocked fast provider."""
    slow = AsyncMock()
    fast = AsyncMock()
    fast.chat = AsyncMock(return_value=MagicMock(content=response_text))
    return DualBrainRouter(slow_provider=slow, fast_provider=fast, fast_model="fast-v1")


def _make_failing_router() -> DualBrainRouter:
    slow = AsyncMock()
    fast = AsyncMock()
    fast.chat = AsyncMock(side_effect=RuntimeError("network error"))
    return DualBrainRouter(slow_provider=slow, fast_provider=fast, fast_model="fast-v1")


class TestWorkerCountMap:
    def test_score_0_and_1_are_zero(self):
        assert WORKER_COUNT_MAP[0] == 0
        assert WORKER_COUNT_MAP[1] == 0

    def test_score_2_gives_2(self):
        assert WORKER_COUNT_MAP[2] == 2

    def test_score_4_gives_neg1(self):
        assert WORKER_COUNT_MAP[4] == -1


@pytest.mark.asyncio
class TestAssess:
    async def test_simple_task_score_0(self):
        router = _make_router(json.dumps({"score": 0, "reason": "simple chat"}))
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=5)
        result = await assessor.assess("你好")
        assert result.use_multi_agent is False
        assert result.score == 0
        assert result.worker_hint == 1

    async def test_complex_task_score_3(self):
        router = _make_router(json.dumps({"score": 3, "reason": "multi-source research"}))
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=8)
        result = await assessor.assess("research quantum computing advances across arxiv, pubmed, and patents")
        assert result.use_multi_agent is True
        assert result.score == 3
        assert result.worker_hint == 4

    async def test_max_score_uses_limit(self):
        router = _make_router(json.dumps({"score": 4, "reason": "fully parallel"}))
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=10)
        result = await assessor.assess("massive analysis")
        assert result.use_multi_agent is True
        assert result.worker_hint == 10

    async def test_score_clamped_to_4(self):
        router = _make_router(json.dumps({"score": 99, "reason": "invalid"}))
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=5)
        result = await assessor.assess("x")
        assert result.score == 4

    async def test_score_clamped_to_0(self):
        router = _make_router(json.dumps({"score": -5, "reason": "negative"}))
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=5)
        result = await assessor.assess("x")
        assert result.score == 0
        assert result.use_multi_agent is False


@pytest.mark.asyncio
class TestAssessFallback:
    async def test_llm_failure_returns_fallback(self):
        router = _make_failing_router()
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=5)
        result = await assessor.assess("any task")
        assert result.use_multi_agent is False
        assert result.worker_hint == 1

    async def test_invalid_json_returns_fallback(self):
        router = _make_router("this is not json at all")
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=5)
        result = await assessor.assess("any task")
        assert result.use_multi_agent is False
        assert result.worker_hint == 1

    async def test_partial_json_in_text(self):
        router = _make_router('Here is my answer: {"score": 2, "reason": "parallel ok"} done.')
        assessor = TaskComplexityAssessor(router=router, max_workers_limit=5)
        result = await assessor.assess("moderate task")
        assert result.use_multi_agent is True
        assert result.score == 2
