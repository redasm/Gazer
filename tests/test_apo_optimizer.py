"""Tests for soul.evolution.apo_optimizer — Issue-07 acceptance criteria.

Verifies:
  - Insufficient feedback returns ``None``
  - Optimization runs when feedback meets threshold
  - No GPU/external service dependency
"""

import pytest

from soul.evolution.apo_optimizer import APOOptimizer


class FakeLLM:
    async def call(self, prompt: str) -> str:
        return "improved prompt template"


class TestAPOOptimizer:
    @pytest.mark.asyncio
    async def test_insufficient_feedback_returns_none(self) -> None:
        optimizer = APOOptimizer(llm_client=FakeLLM(), min_feedback_count=50)
        result = await optimizer.optimize_prompt(
            current_prompt="test prompt",
            feedback_batch=[{"label": "positive", "content": "good"}],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_sufficient_feedback_returns_improved(self) -> None:
        optimizer = APOOptimizer(llm_client=FakeLLM(), min_feedback_count=5)
        feedback = [
            {"label": "positive", "content": f"liked {i}"} for i in range(3)
        ] + [
            {"label": "negative", "content": f"disliked {i}"} for i in range(3)
        ]
        result = await optimizer.optimize_prompt(
            current_prompt="original prompt",
            feedback_batch=feedback,
        )
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_empty_llm_response_returns_none(self) -> None:
        class EmptyLLM:
            async def call(self, prompt: str) -> str:
                return ""

        optimizer = APOOptimizer(llm_client=EmptyLLM(), min_feedback_count=1)
        result = await optimizer.optimize_prompt(
            current_prompt="test",
            feedback_batch=[{"label": "positive", "content": "ok"}] * 5,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self) -> None:
        class FailLLM:
            async def call(self, prompt: str) -> str:
                raise RuntimeError("LLM error")

        optimizer = APOOptimizer(llm_client=FailLLM(), min_feedback_count=1)
        result = await optimizer.optimize_prompt(
            current_prompt="test",
            feedback_batch=[{"label": "positive", "content": "ok"}] * 5,
        )
        assert result is None
