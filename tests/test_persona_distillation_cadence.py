"""Tests for the live-runtime Layer-2 distillation cadence and the
precomputed-emotion fast path on GazerPersonality.

Covers the two defects fixed after the initial wiring:
  - distillation cadence must use a monotonic turn counter, not the capped
    session-history length;
  - observe_turn_affect must reuse a precomputed (emotion, sentiment) instead
    of running a second LLM emotion analysis.
"""

import asyncio
from unittest.mock import MagicMock

from soul.persona import GazerPersonality


def _make_personality(monkeypatch, mapping):
    monkeypatch.setattr(
        "soul.persona.config.get",
        lambda key, default=None: mapping.get(key, default),
    )
    mock_mm = MagicMock()
    mock_mm.backend = MagicMock()
    return GazerPersonality(memory_manager=mock_mm)


def _base_mapping(every=3):
    return {
        "personality.evolution": {
            "enabled": True,
            "distill_every_turns": every,
            "feedback_sentiment_threshold": 0.5,
            "constitution": {"enable_soft_check": False, "fail_closed": False},
        }
    }


class TestDistillationCadence:
    def test_not_due_without_feedback(self, monkeypatch):
        persona = _make_personality(monkeypatch, _base_mapping(every=2))
        persona._turns_since_distill = 100  # plenty of turns…
        # …but no feedback accumulated → not due.
        assert persona.due_for_distillation() is False

    def test_due_after_threshold_turns_with_feedback(self, monkeypatch):
        persona = _make_personality(monkeypatch, _base_mapping(every=3))
        persona._session_feedback.append({"positive": True, "content": "good"})

        persona._turns_since_distill = 2
        assert persona.due_for_distillation() is False
        persona._turns_since_distill = 3
        assert persona.due_for_distillation() is True

    def test_disabled_evolution_never_due(self, monkeypatch):
        mapping = {"personality.evolution": {"enabled": False}}
        persona = _make_personality(monkeypatch, mapping)
        persona._session_feedback.append({"positive": True, "content": "x"})
        persona._turns_since_distill = 999
        assert persona.due_for_distillation() is False

    def test_counter_resets_after_session_end(self, monkeypatch):
        persona = _make_personality(monkeypatch, _base_mapping(every=3))
        persona._session_feedback.append({"positive": True, "content": "good"})
        persona._turns_since_distill = 5
        # No LLM configured → distillation skipped, but the counter must reset
        # so cadence does not run away.
        asyncio.run(persona.on_session_end([]))
        assert persona._turns_since_distill == 0
        assert persona._session_feedback == []


class TestPrecomputedEmotionFastPath:
    def test_precomputed_emotion_skips_analyzer(self, monkeypatch):
        persona = _make_personality(monkeypatch, _base_mapping())
        # Sabotage the analyzer so any call would raise; the fast path must
        # not touch it.
        boom = MagicMock(side_effect=AssertionError("analyzer should not run"))
        persona.memory_manager.emotions.analyzer.analyze = boom
        persona.memory_manager.emotions.analyzer.analyze_with_llm = boom

        affect = asyncio.run(
            persona.observe_turn_affect(
                "I'm thrilled!", "Glad to hear it.", emotion="happy", sentiment=0.8
            )
        )
        # Strong positive sentiment → positive valence drift and a counter tick.
        assert affect.valence > 0
        assert persona._turns_since_distill == 1

    def test_strong_sentiment_triggers_layer1_feedback(self, monkeypatch):
        persona = _make_personality(monkeypatch, _base_mapping())
        before = persona.personality.to_dict()
        asyncio.run(
            persona.observe_turn_affect(
                "thank you so much", "you're welcome", emotion="happy", sentiment=0.9
            )
        )
        after = persona.personality.to_dict()
        # Layer-1 feedback nudged the personality and accumulated for Layer-2.
        assert after != before
        assert len(persona._session_feedback) == 1
