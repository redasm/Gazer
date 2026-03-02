"""Tests for soul.cognitive.context_budget_manager — Issue-09 acceptance criteria.

Verifies:
  - ``build_prompt()`` total tokens <= sum(SlotBudget) * 1.1
  - Lost-in-the-Middle layout: personality at head, user input at tail
  - Truncation works correctly
  - ``_render_history()`` preserves most recent turns within budget
"""

import pytest

from soul.affect.affective_state import AffectiveState
from soul.cognitive.context_budget_manager import ContextBudgetManager, SlotBudget
from soul.memory.working_context import WorkingContext


class TestContextBudgetManager:
    def test_empty_context(self) -> None:
        manager = ContextBudgetManager()
        ctx = WorkingContext()
        prompt = manager.build_prompt(ctx)
        assert "中性" in prompt  # default affect label

    def test_personality_at_head(self) -> None:
        manager = ContextBudgetManager()
        ctx = WorkingContext(
            agent_context=("我是一个温暖的 AI 伴侣",),
            user_input="你好",
        )
        prompt = manager.build_prompt(ctx, personality_prompt="OCEAN人格")
        # Personality should appear before user input
        personality_pos = prompt.find("OCEAN人格")
        input_pos = prompt.find("你好")
        assert personality_pos < input_pos

    def test_user_input_at_tail(self) -> None:
        manager = ContextBudgetManager()
        ctx = WorkingContext(
            agent_context=("persona",),
            session_context=("session_memory",),
            user_input="最终用户输入",
        )
        prompt = manager.build_prompt(ctx)
        # User input should be near the end
        assert prompt.strip().endswith("最终用户输入")

    def test_total_tokens_within_budget(self) -> None:
        budget = SlotBudget(total_max=100)
        manager = ContextBudgetManager(budget=budget)
        ctx = WorkingContext(
            user_context=("x" * 10000,),
            agent_context=("y" * 10000,),
            session_context=("z" * 10000,),
            user_input="w" * 10000,
        )
        prompt = manager.build_prompt(ctx)
        tokens = manager.estimate_tokens(prompt)
        assert tokens <= int(budget.total_max * 1.1)

    def test_truncation_marker(self) -> None:
        budget = SlotBudget(agent_context=10)
        manager = ContextBudgetManager(budget=budget)
        ctx = WorkingContext(
            agent_context=("很长的人格描述 " * 100,),
        )
        prompt = manager.build_prompt(ctx)
        assert "截断" in prompt

    def test_extra_head_and_tail(self) -> None:
        manager = ContextBudgetManager()
        ctx = WorkingContext(user_input="正文")
        prompt = manager.build_prompt(
            ctx, extra_head="系统指令", extra_tail="格式要求"
        )
        assert "系统指令" in prompt
        assert "格式要求" in prompt

    def test_history_in_prompt(self) -> None:
        manager = ContextBudgetManager()
        ctx = WorkingContext(user_input="你觉得呢？")
        history = [
            {"user": "今天天气真好", "assistant": "是的，适合出去走走"},
            {"user": "我想去公园", "assistant": "好主意！"},
        ]
        prompt = manager.build_prompt(ctx, history=history)
        assert "今天天气" in prompt
        assert "我想去公园" in prompt
        # History should be before user input
        hist_pos = prompt.find("今天天气")
        input_pos = prompt.find("你觉得呢")
        assert hist_pos < input_pos

    def test_history_budget_keeps_recent(self) -> None:
        """When history exceeds budget, most recent turns are kept."""
        budget = SlotBudget(history=30)  # very small budget
        manager = ContextBudgetManager(budget=budget)
        ctx = WorkingContext()
        history = [
            {"user": f"old_turn_{i}" * 20, "assistant": f"old_reply_{i}" * 20}
            for i in range(10)
        ]
        history.append({"user": "最近的问题", "assistant": "最近的回答"})
        prompt = manager.build_prompt(ctx, history=history)
        assert "最近的问题" in prompt


class TestSlotBudget:
    def test_defaults(self) -> None:
        budget = SlotBudget()
        assert budget.total_max == 2500
        assert budget.agent_context == 300
        assert budget.history == 600

    def test_custom(self) -> None:
        budget = SlotBudget(total_max=5000, agent_context=500)
        assert budget.total_max == 5000
        assert budget.agent_context == 500
