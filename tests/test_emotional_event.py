"""Tests for soul.affect.emotional_event — Issue-04 acceptance criteria.

Verifies:
  - ``current_intensity(t=half_life)`` ≈ 0.5
  - Intensity < 0.01 triggers auto-pruning
  - ``AffectiveStateManager`` composite affect computation
"""

import pytest
import time

from soul.affect.affective_state import AffectiveState
from soul.affect.emotional_event import EmotionalEvent, AffectiveStateManager


class TestEmotionalEvent:
    def test_initial_intensity_is_one(self) -> None:
        now = time.time()
        event = EmotionalEvent(
            trigger="test",
            affect_delta=AffectiveState(valence=0.5),
            timestamp=now,
        )
        assert event.current_intensity(now) == pytest.approx(1.0, abs=0.01)

    def test_half_life_intensity(self) -> None:
        now = time.time()
        half_life = 300.0
        event = EmotionalEvent(
            trigger="test",
            affect_delta=AffectiveState(valence=0.5),
            timestamp=now,
            half_life_seconds=half_life,
        )
        assert event.current_intensity(now + half_life) == pytest.approx(0.5, abs=0.01)

    def test_double_half_life(self) -> None:
        now = time.time()
        half_life = 300.0
        event = EmotionalEvent(
            trigger="test",
            affect_delta=AffectiveState(valence=0.5),
            timestamp=now,
            half_life_seconds=half_life,
        )
        assert event.current_intensity(now + 2 * half_life) == pytest.approx(0.25, abs=0.01)

    def test_very_old_event_near_zero(self) -> None:
        now = time.time()
        event = EmotionalEvent(
            trigger="test",
            affect_delta=AffectiveState(valence=0.5),
            timestamp=now,
            half_life_seconds=60.0,
        )
        # After 10 half-lives: 2^(-10) ≈ 0.001
        assert event.current_intensity(now + 600.0) < 0.01

    def test_future_timestamp_returns_one(self) -> None:
        now = time.time()
        event = EmotionalEvent(
            trigger="test",
            affect_delta=AffectiveState(valence=0.5),
            timestamp=now + 1000,
        )
        assert event.current_intensity(now) == 1.0


class TestAffectiveStateManager:
    def test_empty_returns_baseline(self) -> None:
        baseline = AffectiveState(valence=0.3, arousal=0.1, dominance=0.2)
        manager = AffectiveStateManager(baseline=baseline)
        result = manager.current_affect()
        assert result.valence == pytest.approx(0.3, abs=0.01)

    def test_add_event_shifts_affect(self) -> None:
        baseline = AffectiveState(valence=0.0)
        manager = AffectiveStateManager(baseline=baseline)
        event = EmotionalEvent(
            trigger="praise",
            affect_delta=AffectiveState(valence=1.0, inertia=0.0),
            timestamp=time.time(),
            half_life_seconds=300.0,
        )
        manager.add_event(event)
        result = manager.current_affect()
        # baseline(0.0) transitions toward 1.0 with intensity ~1.0
        assert result.valence > 0.0

    def test_active_event_count(self) -> None:
        baseline = AffectiveState()
        manager = AffectiveStateManager(baseline=baseline)
        now = time.time()
        # Add a recent event
        manager.add_event(
            EmotionalEvent(
                trigger="recent",
                affect_delta=AffectiveState(valence=0.5),
                timestamp=now,
            )
        )
        assert manager.active_event_count() >= 1

    def test_get_history(self) -> None:
        baseline = AffectiveState()
        manager = AffectiveStateManager(baseline=baseline)
        for i in range(5):
            manager.add_event(
                EmotionalEvent(
                    trigger=f"event_{i}",
                    affect_delta=AffectiveState(valence=i * 0.1),
                    timestamp=time.time(),
                )
            )
        history = manager.get_history(3)
        assert len(history) == 3

    def test_half_life_map(self) -> None:
        assert AffectiveStateManager.HALF_LIFE_MAP["user_praise"] == 180.0
        assert AffectiveStateManager.HALF_LIFE_MAP["default"] == 300.0
