"""Tests for soul.personality.personality_vector — Issue-06 acceptance criteria.

Verifies:
  - All dimensions clamped to [0.0, 1.0]
  - ``to_affect_baseline()`` output in [-1.0, 1.0]
  - ``apply_feedback()`` stability after many iterations
"""

import pytest

from soul.affect.affective_state import AffectiveState
from soul.personality.personality_vector import (
    FeedbackSignal,
    PersonalityVector,
)


class TestPersonalityVectorCreation:
    def test_default_values(self) -> None:
        # Defaults are tuned to Gazer's persona (calm/conscientious), not a
        # flat 0.5 baseline.
        pv = PersonalityVector()
        assert pv.conscientiousness == 0.75
        assert pv.neuroticism == 0.3
        # All dimensions stay within bounds.
        for v in pv.to_dict().values():
            assert 0.0 <= v <= 1.0

    def test_neutral_personality_yields_near_neutral_affect(self) -> None:
        # A flat 0.5 personality should map to a near-zero (calm/neutral)
        # affect baseline, not a spuriously "alert" state.
        neutral = PersonalityVector(
            openness=0.5,
            conscientiousness=0.5,
            extraversion=0.5,
            agreeableness=0.5,
            neuroticism=0.5,
        )
        baseline = neutral.to_affect_baseline()
        assert abs(baseline.valence) < 1e-9
        assert abs(baseline.arousal) < 1e-9
        assert abs(baseline.dominance) < 1e-9
        assert baseline.to_label() == "中性"

    def test_clamping(self) -> None:
        pv = PersonalityVector(openness=2.0, agreeableness=-1.0)
        assert pv.openness == 1.0
        assert pv.agreeableness == 0.0


class TestToAffectBaseline:
    def test_output_range(self) -> None:
        pv = PersonalityVector()
        baseline = pv.to_affect_baseline()
        assert -1.0 <= baseline.valence <= 1.0
        assert -1.0 <= baseline.arousal <= 1.0
        assert -1.0 <= baseline.dominance <= 1.0
        assert 0.0 <= baseline.inertia <= 1.0

    def test_high_agreeableness_positive_valence(self) -> None:
        pv = PersonalityVector(agreeableness=1.0, neuroticism=0.0)
        baseline = pv.to_affect_baseline()
        assert baseline.valence > 0

    def test_high_neuroticism_lower_inertia(self) -> None:
        stable = PersonalityVector(neuroticism=0.0)
        volatile = PersonalityVector(neuroticism=1.0)
        assert stable.to_affect_baseline().inertia > volatile.to_affect_baseline().inertia


class TestApplyFeedback:
    def test_positive_feedback_increases_agreeableness(self) -> None:
        pv = PersonalityVector(agreeableness=0.5)
        signal = FeedbackSignal(positive=True)
        new_pv = pv.apply_feedback(signal)
        assert new_pv.agreeableness > pv.agreeableness

    def test_negative_feedback_decreases_agreeableness(self) -> None:
        pv = PersonalityVector(agreeableness=0.5)
        signal = FeedbackSignal(positive=False)
        new_pv = pv.apply_feedback(signal)
        assert new_pv.agreeableness < pv.agreeableness

    def test_original_unchanged(self) -> None:
        pv = PersonalityVector(agreeableness=0.5)
        signal = FeedbackSignal(positive=True)
        pv.apply_feedback(signal)
        assert pv.agreeableness == 0.5

    def test_stability_after_100_iterations(self) -> None:
        """After 100 positive feedbacks, all dimensions should stay in [0.0, 1.0]."""
        pv = PersonalityVector()
        for _ in range(100):
            pv = pv.apply_feedback(FeedbackSignal(positive=True))
        assert 0.0 <= pv.openness <= 1.0
        assert 0.0 <= pv.conscientiousness <= 1.0
        assert 0.0 <= pv.extraversion <= 1.0
        assert 0.0 <= pv.agreeableness <= 1.0
        assert 0.0 <= pv.neuroticism <= 1.0
        assert 0.0 <= pv.humor_level <= 1.0
        assert 0.0 <= pv.verbosity <= 1.0
        assert 0.0 <= pv.formality <= 1.0


class TestToDict:
    def test_serialization(self) -> None:
        pv = PersonalityVector()
        d = pv.to_dict()
        assert "openness" in d
        assert "humor_level" in d
        assert "learning_rate" in d


class TestToPrompt:
    def test_prompt_contains_dimensions(self) -> None:
        pv = PersonalityVector()
        prompt = pv.to_prompt()
        assert "开放性" in prompt
        assert "幽默感" in prompt


class TestToBehavioralPrompt:
    def test_behavioral_prompt_is_descriptive(self) -> None:
        pv = PersonalityVector()  # persona-tuned defaults deviate from neutral
        prompt = pv.to_behavioral_prompt()
        assert "行为风格" in prompt
        # No raw numeric scores leak into the behavioral guidance.
        assert "0." not in prompt

    def test_neutral_personality_has_balanced_label(self) -> None:
        neutral = PersonalityVector(
            openness=0.5,
            conscientiousness=0.5,
            extraversion=0.5,
            agreeableness=0.5,
            neuroticism=0.5,
            humor_level=0.5,
            verbosity=0.5,
            formality=0.5,
        )
        assert "均衡中性" in neutral.to_behavioral_prompt()


class TestFromDict:
    def test_round_trip(self) -> None:
        pv = PersonalityVector(openness=0.7, neuroticism=0.2, humor_level=0.6)
        restored = PersonalityVector.from_dict(pv.to_dict())
        assert restored.to_dict() == pv.to_dict()

    def test_missing_keys_fall_back_to_defaults(self) -> None:
        restored = PersonalityVector.from_dict({"openness": 0.9})
        assert restored.openness == 0.9
        assert restored.conscientiousness == PersonalityVector().conscientiousness

    def test_non_dict_returns_defaults(self) -> None:
        assert PersonalityVector.from_dict(None).to_dict() == PersonalityVector().to_dict()


class TestFeedbackDeltaCompleteness:
    def test_all_dimensions_move_on_feedback(self) -> None:
        # Every relevant dimension should receive a directional nudge so no
        # trait is permanently frozen regardless of feedback volume.
        delta_pos = FeedbackSignal(positive=True).to_personality_delta()
        for field in (
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "humor_level",
            "verbosity",
        ):
            assert getattr(delta_pos, field) > 0, field
        # Neuroticism / formality move in the opposite direction on praise.
        assert delta_pos.neuroticism < 0
        assert delta_pos.formality < 0
