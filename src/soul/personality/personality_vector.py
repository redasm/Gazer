"""OCEAN personality vector with three-layer evolution mechanism.

The Big Five (OCEAN) model is the most widely accepted framework in
personality psychology:

- **O** (Openness): curiosity and acceptance of new experiences
- **C** (Conscientiousness): organization, reliability, self-discipline
- **E** (Extraversion): social activity, energy, outgoingness
- **A** (Agreeableness): cooperation, trust, caring for others
- **N** (Neuroticism): emotional instability, anxiety, volatility

Three-layer evolution:
  - Layer 1: Immediate feedback (``apply_feedback``) — per-turn adjustment
  - Layer 2: Session distillation (in ``evolution_service.py``)
  - Layer 3: APO optimization (in ``apo_optimizer.py``)

References:
    - soul_architecture_reform.md Issue-06
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from soul.affect.affective_state import AffectiveState


@dataclass
class FeedbackSignal:
    """A single piece of user feedback mapped to personality deltas.

    Attributes:
        positive: Whether the feedback is positive (True) or negative (False).
        content: The message content that was liked/disliked.
        source: Where the feedback came from (e.g. ``"thumbs_up"``, ``"correction"``).
    """

    positive: bool
    content: str = ""
    source: str = "unknown"

    def to_personality_delta(self) -> "PersonalityDelta":
        """Convert feedback into directional personality adjustment hints.

        Positive feedback → reinforce current style.
        Negative feedback → shift toward opposite style.
        """
        direction = 1.0 if self.positive else -1.0
        return PersonalityDelta(
            openness=0.0,
            conscientiousness=0.0,
            extraversion=0.0,
            agreeableness=direction * 0.1,
            neuroticism=-direction * 0.05,
            humor_level=direction * 0.05,
            verbosity=0.0,
            formality=-direction * 0.03,
        )


@dataclass
class PersonalityDelta:
    """Directional change to apply to a ``PersonalityVector``."""

    openness: float = 0.0
    conscientiousness: float = 0.0
    extraversion: float = 0.0
    agreeableness: float = 0.0
    neuroticism: float = 0.0
    humor_level: float = 0.0
    verbosity: float = 0.0
    formality: float = 0.0


@dataclass
class PersonalityVector:
    """OCEAN five-factor personality model with interaction style.

    All dimension values are in [0.0, 1.0].
    """

    # OCEAN five factors
    openness: float = 0.5
    conscientiousness: float = 0.5
    extraversion: float = 0.5
    agreeableness: float = 0.5
    neuroticism: float = 0.5

    # Interaction style (learned from user feedback)
    humor_level: float = 0.5
    verbosity: float = 0.5
    formality: float = 0.5

    # Learning rate: controls how fast personality changes (lower = more stable)
    learning_rate: float = 0.03

    def __post_init__(self) -> None:
        """Enforce [0.0, 1.0] bounds on all dimensions."""
        for field_name in (
            "openness", "conscientiousness", "extraversion",
            "agreeableness", "neuroticism",
            "humor_level", "verbosity", "formality",
        ):
            object.__setattr__(
                self, field_name, self._clamp(getattr(self, field_name))
            )
        object.__setattr__(
            self, "learning_rate", max(0.0, min(1.0, self.learning_rate))
        )

    def to_affect_baseline(self) -> AffectiveState:
        """Derive the emotional baseline from personality.

        This binds personality to emotion — the two systems are connected
        through this method.

        Returns:
            An ``AffectiveState`` representing the resting emotional tone.
        """
        return AffectiveState(
            valence=self.agreeableness * 0.6 - self.neuroticism * 0.4,
            arousal=self.extraversion * 0.5 + self.openness * 0.3,
            dominance=self.conscientiousness * 0.5,
            inertia=1.0 - self.neuroticism * 0.5,
        )

    def apply_feedback(self, signal: FeedbackSignal) -> PersonalityVector:
        """Layer 1: Immediate per-turn feedback adjustment.

        Returns a new ``PersonalityVector`` — the original is unchanged.
        """
        delta = signal.to_personality_delta()
        return PersonalityVector(
            openness=self._clamp(self.openness + delta.openness * self.learning_rate),
            conscientiousness=self._clamp(
                self.conscientiousness + delta.conscientiousness * self.learning_rate
            ),
            extraversion=self._clamp(
                self.extraversion + delta.extraversion * self.learning_rate
            ),
            agreeableness=self._clamp(
                self.agreeableness + delta.agreeableness * self.learning_rate
            ),
            neuroticism=self._clamp(
                self.neuroticism + delta.neuroticism * self.learning_rate
            ),
            humor_level=self._clamp(
                self.humor_level + delta.humor_level * self.learning_rate
            ),
            verbosity=self._clamp(
                self.verbosity + delta.verbosity * self.learning_rate
            ),
            formality=self._clamp(
                self.formality + delta.formality * self.learning_rate
            ),
            learning_rate=self.learning_rate,
        )

    def apply_delta(self, delta: PersonalityDelta) -> PersonalityVector:
        """Apply an arbitrary ``PersonalityDelta`` (used by session distillation).

        Returns a new ``PersonalityVector``.
        """
        return PersonalityVector(
            openness=self._clamp(self.openness + delta.openness * self.learning_rate),
            conscientiousness=self._clamp(
                self.conscientiousness + delta.conscientiousness * self.learning_rate
            ),
            extraversion=self._clamp(
                self.extraversion + delta.extraversion * self.learning_rate
            ),
            agreeableness=self._clamp(
                self.agreeableness + delta.agreeableness * self.learning_rate
            ),
            neuroticism=self._clamp(
                self.neuroticism + delta.neuroticism * self.learning_rate
            ),
            humor_level=self._clamp(
                self.humor_level + delta.humor_level * self.learning_rate
            ),
            verbosity=self._clamp(
                self.verbosity + delta.verbosity * self.learning_rate
            ),
            formality=self._clamp(
                self.formality + delta.formality * self.learning_rate
            ),
            learning_rate=self.learning_rate,
        )

    def to_prompt(self) -> str:
        """Serialize personality to a natural-language prompt fragment."""
        return (
            f"人格特征：开放性={self.openness:.2f} 尽责性={self.conscientiousness:.2f} "
            f"外倾性={self.extraversion:.2f} 宜人性={self.agreeableness:.2f} "
            f"神经质={self.neuroticism:.2f}\n"
            f"交互风格：幽默感={self.humor_level:.2f} 话语量={self.verbosity:.2f} "
            f"正式度={self.formality:.2f}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "openness": round(self.openness, 4),
            "conscientiousness": round(self.conscientiousness, 4),
            "extraversion": round(self.extraversion, 4),
            "agreeableness": round(self.agreeableness, 4),
            "neuroticism": round(self.neuroticism, 4),
            "humor_level": round(self.humor_level, 4),
            "verbosity": round(self.verbosity, 4),
            "formality": round(self.formality, 4),
            "learning_rate": round(self.learning_rate, 4),
        }

    @staticmethod
    def _clamp(v: float) -> float:
        """Clamp *v* to [0.0, 1.0]."""
        return max(0.0, min(1.0, v))
