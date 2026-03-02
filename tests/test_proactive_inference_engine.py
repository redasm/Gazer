"""Tests for soul.cognitive.proactive_inference_engine — Issue-10 acceptance criteria.

Verifies:
  - turn < 3 returns []
  - Low-confidence signals not injected
  - Emotional distress detection works (EMOTIONAL_SUPPORT_NEEDED)
  - Declining arousal → ENERGY_DROP
  - inject_hints() appends to agent_context
  - infer() is async
"""

import pytest

from soul.affect.affective_state import AffectiveState
from soul.cognitive.proactive_inference_engine import (
    ProactiveInferenceEngine,
    ProactiveSignalType,
)
from soul.memory.working_context import WorkingContext


class TestProactiveInferenceEngine:
    @pytest.mark.asyncio
    async def test_early_turns_return_empty(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3)
        ctx = WorkingContext(turn_count=1)
        signals = await engine.infer(ctx)
        assert signals == []

    @pytest.mark.asyncio
    async def test_neutral_affect_no_signals(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3)
        ctx = WorkingContext(
            turn_count=5,
            affect=AffectiveState(valence=0.0, arousal=0.0),
            user_input="普通输入内容足够长",
        )
        signals = await engine.infer(ctx)
        assert signals == []

    @pytest.mark.asyncio
    async def test_negative_valence_emotional_support(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3, confidence_threshold=0.3)
        ctx = WorkingContext(
            turn_count=5,
            affect=AffectiveState(valence=-0.7, arousal=0.5),
            user_input="这东西太难了！",
        )
        signals = await engine.infer(ctx)
        signal_types = [s.signal_type for s in signals]
        assert ProactiveSignalType.EMOTIONAL_SUPPORT_NEEDED in signal_types

    @pytest.mark.asyncio
    async def test_negative_valence_low_arousal_support(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3, confidence_threshold=0.3)
        ctx = WorkingContext(
            turn_count=5,
            affect=AffectiveState(valence=-0.7, arousal=-0.2),
            user_input="算了吧...",
        )
        signals = await engine.infer(ctx)
        signal_types = [s.signal_type for s in signals]
        assert ProactiveSignalType.EMOTIONAL_SUPPORT_NEEDED in signal_types

    @pytest.mark.asyncio
    async def test_declining_valence_trend_detection(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3, confidence_threshold=0.5)
        ctx = WorkingContext(
            turn_count=5,
            affect=AffectiveState(valence=-0.3),
            user_input="聊点别的吧",
        )
        affect_history = [
            AffectiveState(valence=0.5),
            AffectiveState(valence=0.2),
            AffectiveState(valence=-0.1),
        ]
        signals = await engine.infer(ctx, affect_history=affect_history)
        signal_types = [s.signal_type for s in signals]
        assert ProactiveSignalType.EMOTIONAL_SUPPORT_NEEDED in signal_types

    @pytest.mark.asyncio
    async def test_energy_drop_detection(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3, confidence_threshold=0.5)
        ctx = WorkingContext(
            turn_count=5,
            affect=AffectiveState(valence=0.0, arousal=-0.5),
            user_input="嗯嗯嗯嗯嗯嗯",
        )
        affect_history = [
            AffectiveState(arousal=0.5),
            AffectiveState(arousal=0.1),
            AffectiveState(arousal=-0.3),
        ]
        signals = await engine.infer(ctx, affect_history=affect_history)
        signal_types = [s.signal_type for s in signals]
        assert ProactiveSignalType.ENERGY_DROP in signal_types

    @pytest.mark.asyncio
    async def test_session_turn_count_override(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3)
        ctx = WorkingContext(turn_count=10)  # context says 10
        signals = await engine.infer(ctx, session_turn_count=1)  # override to 1
        assert signals == []  # too few turns

    @pytest.mark.asyncio
    async def test_confidence_threshold_filters(self) -> None:
        engine = ProactiveInferenceEngine(
            min_turns=3, confidence_threshold=0.99
        )
        ctx = WorkingContext(
            turn_count=5,
            affect=AffectiveState(valence=-0.6, arousal=0.5),
            user_input="有点烦",
        )
        signals = await engine.infer(ctx)
        # High threshold should filter out most signals
        assert len(signals) == 0


class TestInjectHints:
    @pytest.mark.asyncio
    async def test_inject_appends_to_agent_context(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3, confidence_threshold=0.3)
        ctx = WorkingContext(
            turn_count=5,
            agent_context=("existing_persona",),
            affect=AffectiveState(valence=-0.8, arousal=0.6),
            user_input="什么都不想做",
        )
        signals = await engine.infer(ctx)
        assert len(signals) > 0

        new_ctx = engine.inject_hints(ctx, signals)
        assert len(new_ctx.agent_context) > len(ctx.agent_context)
        assert "主动推断" in new_ctx.agent_context[-1]

    def test_inject_no_signals_returns_same(self) -> None:
        engine = ProactiveInferenceEngine()
        ctx = WorkingContext(agent_context=("persona",))
        new_ctx = engine.inject_hints(ctx, [])
        assert new_ctx is ctx  # no change, same object returned

    @pytest.mark.asyncio
    async def test_original_context_unchanged(self) -> None:
        engine = ProactiveInferenceEngine(min_turns=3, confidence_threshold=0.3)
        ctx = WorkingContext(
            turn_count=5,
            agent_context=("original",),
            affect=AffectiveState(valence=-0.9, arousal=0.7),
            user_input="我很生气",
        )
        signals = await engine.infer(ctx)
        engine.inject_hints(ctx, signals)
        assert ctx.agent_context == ("original",)
