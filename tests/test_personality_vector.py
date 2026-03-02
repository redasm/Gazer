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
        pv = PersonalityVector()
        assert pv.openness == 0.5
        assert pv.humor_level == 0.5

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
