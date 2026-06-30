"""Tests for the four soul completeness wirings:

  #1 mood-congruent recall by live affect (get_companion_context affect_valence)
  #2 opt-in proactive LLM inference layer (enable_proactive_llm)
  #4 persona enrichment bounded by ContextBudgetManager

(#3 NightlyConsolidator scheduling is exercised via brain integration and is
not unit-tested here as it needs the full runtime.)
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from soul.persona import GazerPersonality


def _make_personality(monkeypatch, mapping):
    monkeypatch.setattr(
        "soul.persona.config.get",
        lambda key, default=None: mapping.get(key, default),
    )
    mock_mm = MagicMock()
    mock_mm.backend = MagicMock()
    return GazerPersonality(memory_manager=mock_mm)


class TestProactiveLLMOptIn:
    def test_disabled_by_default(self, monkeypatch):
        persona = _make_personality(monkeypatch, {})
        # No config → llm layer stays off even if a provider is offered.
        wired = persona.enable_proactive_llm(MagicMock(), "fast-model")
        assert wired is False
        assert persona.proactive_llm_enabled is False
        assert persona.proactive_engine._llm is None

    def test_enabled_when_configured(self, monkeypatch):
        mapping = {"personality.proactive": {"llm_enabled": True, "confidence_threshold": 0.5}}
        persona = _make_personality(monkeypatch, mapping)
        assert persona.proactive_engine._confidence_threshold == 0.5
        wired = persona.enable_proactive_llm(MagicMock(), "fast-model")
        assert wired is True
        assert persona.proactive_llm_enabled is True
        assert persona.proactive_engine._llm is not None

    def test_no_provider_is_noop(self, monkeypatch):
        mapping = {"personality.proactive": {"llm_enabled": True}}
        persona = _make_personality(monkeypatch, mapping)
        assert persona.enable_proactive_llm(None) is False


class TestMoodCongruentRecall:
    def test_affect_valence_overrides_day_average(self):
        from memory.manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)  # bypass heavy __init__
        # Stub the collaborators get_companion_context touches.
        mm.emotions = MagicMock()
        mm.emotions._today_data.avg_sentiment = 0.9  # day average (should be ignored)
        mm.emotions.get_recent_mood = MagicMock(return_value=None)

        captured = {}

        async def _fake_recall(message, sentiment, **kwargs):
            captured["sentiment"] = sentiment
            return {}

        mm.recall = MagicMock()
        mm.recall.get_relevant_memories = _fake_recall
        mm.recall.format_for_prompt = MagicMock(return_value="ctx")

        wm = MagicMock()
        asyncio.run(mm.get_companion_context("hi", wm, affect_valence=-0.7))
        # Live affect (-0.7) wins over the day average (0.9).
        assert captured["sentiment"] == pytest.approx(-0.7)

    def test_falls_back_to_day_average_when_none(self):
        from memory.manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.emotions = MagicMock()
        mm.emotions._today_data.avg_sentiment = 0.4
        mm.emotions.get_recent_mood = MagicMock(return_value=None)

        captured = {}

        async def _fake_recall(message, sentiment, **kwargs):
            captured["sentiment"] = sentiment
            return {}

        mm.recall = MagicMock()
        mm.recall.get_relevant_memories = _fake_recall
        mm.recall.format_for_prompt = MagicMock(return_value="ctx")

        asyncio.run(mm.get_companion_context("hi", MagicMock()))
        assert captured["sentiment"] == pytest.approx(0.4)


class TestEnrichmentBudget:
    def test_budget_truncates_long_enrichment(self, monkeypatch):
        persona = _make_personality(monkeypatch, {})
        mgr = persona.budget_manager
        budget_chars = int(mgr._budget.agent_context * mgr._chars_per_token)
        long_text = "情" * (budget_chars * 3)
        bounded = mgr._truncate(long_text, mgr._budget.agent_context)
        assert len(bounded) <= budget_chars + len("\n[...截断...]")
        assert "截断" in bounded
