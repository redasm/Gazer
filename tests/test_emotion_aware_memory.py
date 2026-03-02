"""Tests for emotion-aware memory retrieval — Issue-05 acceptance criteria.

Verifies:
  - ``EmotionAwareMemoryPort`` adds bias when valence < -0.5
  - Neutral state returns same as base port
  - ``current_affect=None`` degrades to plain query
  - ``_build_bias_prompt`` and ``_build_affect_filter`` produce correct output
"""

import pytest

from soul.affect.affective_state import AffectiveState
from soul.memory.memory_port import (
    EmotionAwareMemoryPort,
    InMemoryMemoryPort,
    MemoryPort,
)


class RecordingPort(InMemoryMemoryPort):
    """InMemoryMemoryPort that records the raw query string it receives."""

    def __init__(self) -> None:
        super().__init__()
        self.last_query: str = ""

    async def query(
        self,
        query: str,
        current_affect: "AffectiveState | None" = None,
        top_k: int = 5,
        slot: str = "",
    ) -> list[str]:
        self.last_query = query
        return await super().query(
            query, current_affect=current_affect, top_k=top_k, slot=slot
        )


class TestEmotionAwareMemoryPort:
    """Issue-05 acceptance criteria."""

    @pytest.mark.asyncio
    async def test_negative_valence_appends_bias(self) -> None:
        inner = RecordingPort()
        await inner.store("k1", {"data": "memory"})
        port = EmotionAwareMemoryPort(inner)

        negative = AffectiveState(valence=-0.8)
        await port.query("test", current_affect=negative)
        assert "negative" in inner.last_query
        assert "sad" in inner.last_query

    @pytest.mark.asyncio
    async def test_positive_valence_appends_bias(self) -> None:
        inner = RecordingPort()
        await inner.store("k1", {"data": "memory"})
        port = EmotionAwareMemoryPort(inner)

        positive = AffectiveState(valence=0.8)
        await port.query("test", current_affect=positive)
        assert "positive" in inner.last_query
        assert "happy" in inner.last_query

    @pytest.mark.asyncio
    async def test_neutral_valence_no_bias(self) -> None:
        inner = RecordingPort()
        await inner.store("k1", {"data": "memory"})
        port = EmotionAwareMemoryPort(inner)

        neutral = AffectiveState(valence=0.0)
        await port.query("test", current_affect=neutral)
        assert inner.last_query == "test"

    @pytest.mark.asyncio
    async def test_none_affect_no_error(self) -> None:
        inner = RecordingPort()
        await inner.store("k1", {"data": "memory"})
        port = EmotionAwareMemoryPort(inner)

        results = await port.query("test", current_affect=None)
        assert inner.last_query == "test"
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_store_and_delete_delegate(self) -> None:
        inner = InMemoryMemoryPort()
        port = EmotionAwareMemoryPort(inner)

        await port.store("k1", {"data": "hello"})
        assert inner.count() == 1
        deleted = await port.delete("k1")
        assert deleted is True
        assert inner.count() == 0


class TestBuildBiasPrompt:
    def test_negative(self) -> None:
        affect = AffectiveState(valence=-0.7)
        assert EmotionAwareMemoryPort._build_bias_prompt(affect) == "negative sad difficult"

    def test_positive(self) -> None:
        affect = AffectiveState(valence=0.7)
        assert EmotionAwareMemoryPort._build_bias_prompt(affect) == "positive happy pleasant"

    def test_neutral(self) -> None:
        affect = AffectiveState(valence=0.2)
        assert EmotionAwareMemoryPort._build_bias_prompt(affect) == ""


class TestBuildAffectFilter:
    def test_strong_negative(self) -> None:
        affect = AffectiveState(valence=-0.8)
        result = EmotionAwareMemoryPort._build_affect_filter(affect)
        assert result == {"metadata.emotional_polarity": "negative"}

    def test_strong_positive(self) -> None:
        affect = AffectiveState(valence=0.8)
        result = EmotionAwareMemoryPort._build_affect_filter(affect)
        assert result == {"metadata.emotional_polarity": "positive"}

    def test_weak_valence_returns_none(self) -> None:
        affect = AffectiveState(valence=0.3)
        assert EmotionAwareMemoryPort._build_affect_filter(affect) is None

    def test_none_affect_returns_none(self) -> None:
        assert EmotionAwareMemoryPort._build_affect_filter(None) is None
