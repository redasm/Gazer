"""Tests for soul.affect.affective_state — Issue-01 acceptance criteria.

Verifies:
  - All dimensions clamped to [-1.0, 1.0], inertia to [0.0, 1.0]
  - ``transition_toward()`` drifts with inertia-based resistance
  - ``to_label()`` returns correct Chinese labels
  - Immutability (frozen dataclass)
"""

import pytest

from soul.affect.affective_state import AffectiveState


class TestAffectiveStateCreation:
    def test_default_values(self) -> None:
        state = AffectiveState()
        assert state.valence == 0.0
        assert state.arousal == 0.0
        assert state.dominance == 0.0
        assert state.inertia == 0.5

    def test_custom_values(self) -> None:
        state = AffectiveState(valence=0.8, arousal=-0.3, dominance=0.5, inertia=0.7)
        assert state.valence == 0.8
        assert state.arousal == -0.3
        assert state.dominance == 0.5
        assert state.inertia == 0.7

    def test_clamping_upper(self) -> None:
        state = AffectiveState(valence=2.0, arousal=1.5, dominance=3.0, inertia=5.0)
        assert state.valence == 1.0
        assert state.arousal == 1.0
        assert state.dominance == 1.0
        assert state.inertia == 1.0

    def test_clamping_lower(self) -> None:
        state = AffectiveState(valence=-2.0, arousal=-1.5, dominance=-3.0, inertia=-1.0)
        assert state.valence == -1.0
        assert state.arousal == -1.0
        assert state.dominance == -1.0
        assert state.inertia == 0.0


class TestImmutability:
    def test_frozen_assignment_raises(self) -> None:
        state = AffectiveState()
        with pytest.raises(AttributeError):
            state.valence = 0.5  # type: ignore[misc]

    def test_transition_returns_new_instance(self) -> None:
        original = AffectiveState(valence=0.0)
        target = AffectiveState(valence=1.0)
        result = original.transition_toward(target, intensity=0.5)
        assert result is not original
        assert original.valence == 0.0  # unchanged


class TestTransitionToward:
    def test_zero_intensity_no_change(self) -> None:
        state = AffectiveState(valence=0.3, arousal=0.2, dominance=-0.1)
        target = AffectiveState(valence=1.0, arousal=1.0, dominance=1.0)
        result = state.transition_toward(target, intensity=0.0)
        assert result.valence == pytest.approx(0.3)
        assert result.arousal == pytest.approx(0.2)
        assert result.dominance == pytest.approx(-0.1)

    def test_full_intensity_zero_inertia(self) -> None:
        state = AffectiveState(valence=0.0, arousal=0.0, dominance=0.0, inertia=0.0)
        target = AffectiveState(valence=1.0, arousal=-1.0, dominance=0.5)
        result = state.transition_toward(target, intensity=1.0)
        assert result.valence == pytest.approx(1.0)
        assert result.arousal == pytest.approx(-1.0)
        assert result.dominance == pytest.approx(0.5)

    def test_high_inertia_resists_change(self) -> None:
        state = AffectiveState(valence=0.0, inertia=0.9)
        target = AffectiveState(valence=1.0)
        result = state.transition_toward(target, intensity=1.0)
        # effective_rate = 1.0 * (1.0 - 0.9) = 0.1
        assert result.valence == pytest.approx(0.1)

    def test_partial_intensity(self) -> None:
        state = AffectiveState(valence=0.0, inertia=0.5)
        target = AffectiveState(valence=1.0)
        result = state.transition_toward(target, intensity=0.5)
        # effective_rate = 0.5 * (1.0 - 0.5) = 0.25
        assert result.valence == pytest.approx(0.25)


class TestToLabel:
    def test_excited(self) -> None:
        assert AffectiveState(valence=0.8, arousal=0.8).to_label() == "兴奋"

    def test_happy(self) -> None:
        assert AffectiveState(valence=0.5, arousal=0.1).to_label() == "开心"

    def test_calm(self) -> None:
        assert AffectiveState(valence=0.5, arousal=-0.5).to_label() == "平静"

    def test_neutral(self) -> None:
        assert AffectiveState(valence=0.0, arousal=0.0).to_label() == "中性"

    def test_anxious(self) -> None:
        assert AffectiveState(valence=-0.5, arousal=0.8).to_label() == "焦虑"

    def test_tired(self) -> None:
        assert AffectiveState(valence=-0.5, arousal=-0.5).to_label() == "疲惫"


class TestToDict:
    def test_round_trip(self) -> None:
        state = AffectiveState(valence=0.1234, arousal=-0.5678, dominance=0.9, inertia=0.3)
        d = state.to_dict()
        assert d["valence"] == pytest.approx(0.1234, abs=1e-3)
        assert d["arousal"] == pytest.approx(-0.5678, abs=1e-3)
        assert "inertia" in d
