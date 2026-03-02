"""Tests for soul.cognitive.cognitive_step — Issue-03 / Issue-09 acceptance criteria.

Verifies:
  - ``CognitiveStep`` ABC cannot be instantiated
  - ``MockCognitiveStep`` executes deterministically without LLM
  - ``ReflectStep`` uses ``ContextBudgetManager`` for prompt assembly
"""

import pytest

from soul.cognitive.cognitive_step import CognitiveStep, MockCognitiveStep, ReflectStep
from soul.cognitive.context_budget_manager import ContextBudgetManager
from soul.affect.affective_state import AffectiveState
from soul.memory.working_context import WorkingContext


class TestCognitiveStepABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            CognitiveStep()  # type: ignore[abstract]


class TestMockCognitiveStep:
    @pytest.mark.asyncio
    async def test_returns_mock_response(self) -> None:
        step = MockCognitiveStep(response="hello world")
        ctx = WorkingContext(user_input="test", turn_count=0)
        result, new_ctx = await step.execute(ctx)
        assert result == "hello world"
        assert new_ctx.turn_count == 1

    @pytest.mark.asyncio
    async def test_call_count(self) -> None:
        step = MockCognitiveStep()
        ctx = WorkingContext()
        await step.execute(ctx)
        await step.execute(ctx)
        assert step.call_count == 2

    @pytest.mark.asyncio
    async def test_last_context_captured(self) -> None:
        step = MockCognitiveStep()
        ctx = WorkingContext(user_input="captured")
        await step.execute(ctx)
        assert step.last_context is not None
        assert step.last_context.user_input == "captured"

    @pytest.mark.asyncio
    async def test_original_context_unchanged(self) -> None:
        step = MockCognitiveStep()
        ctx = WorkingContext(turn_count=5)
        _, new_ctx = await step.execute(ctx)
        assert ctx.turn_count == 5
        assert new_ctx.turn_count == 6


class TestReflectStep:
    @pytest.mark.asyncio
    async def test_uses_budget_manager(self) -> None:
        class FakeLLM:
            last_prompt: str = ""

            async def call(self, prompt: str) -> str:
                self.last_prompt = prompt
                return f"response to: {prompt[:20]}"

        llm = FakeLLM()
        budget_mgr = ContextBudgetManager()
        step = ReflectStep(llm_client=llm, budget_manager=budget_mgr)
        ctx = WorkingContext(
            user_input="你好",
            affect=AffectiveState(valence=0.5, arousal=0.1),
        )
        result, new_ctx = await step.execute(ctx)
        assert "response to:" in result
        assert new_ctx.turn_count == 1
        # Verify the prompt was assembled by the budget manager (contains affect info)
        assert "当前情绪" in llm.last_prompt
        assert "你好" in llm.last_prompt

    @pytest.mark.asyncio
    async def test_history_passed_to_budget_manager(self) -> None:
        class FakeLLM:
            last_prompt: str = ""

            async def call(self, prompt: str) -> str:
                self.last_prompt = prompt
                return "ok"

        llm = FakeLLM()
        budget_mgr = ContextBudgetManager()
        step = ReflectStep(llm_client=llm, budget_manager=budget_mgr)
        step.history = [
            {"user": "早上好", "assistant": "早！"},
        ]
        ctx = WorkingContext(user_input="午安")
        await step.execute(ctx)
        assert "早上好" in llm.last_prompt
