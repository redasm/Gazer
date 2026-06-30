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

        All eight dimensions receive a (small) directional nudge so that no
        trait is permanently frozen regardless of how much feedback arrives.
        """
        direction = 1.0 if self.positive else -1.0
        return PersonalityDelta(
            openness=direction * 0.04,
            conscientiousness=direction * 0.04,
            extraversion=direction * 0.05,
            agreeableness=direction * 0.1,
            neuroticism=-direction * 0.05,
            humor_level=direction * 0.05,
            verbosity=direction * 0.03,
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

    Defaults reflect Gazer's canonical persona (``assets/SOUL.md``): calm and
    grounded (low neuroticism), reliable and precise (high conscientiousness),
    curious (moderately high openness), warm but not effusive (moderate
    extraversion/agreeableness). The baseline mapping in
    ``to_affect_baseline`` is centred on 0.5, so these values yield a resting
    affect close to calm/neutral rather than agitated.
    """

    # OCEAN five factors — tuned to Gazer's persona, not a flat 0.5 baseline
    openness: float = 0.65
    conscientiousness: float = 0.75
    extraversion: float = 0.45
    agreeableness: float = 0.6
    neuroticism: float = 0.3

    # Interaction style (learned from user feedback)
    humor_level: float = 0.4
    verbosity: float = 0.35
    formality: float = 0.45

    # Learning rate: controls how fast personality changes (lower = more stable)
    learning_rate: float = 0.03

    def __post_init__(self) -> None:
        """Enforce [0.0, 1.0] bounds on all dimensions."""
        for field_name in (
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "neuroticism",
            "humor_level",
            "verbosity",
            "formality",
        ):
            object.__setattr__(self, field_name, self._clamp(getattr(self, field_name)))
        object.__setattr__(self, "learning_rate", max(0.0, min(1.0, self.learning_rate)))

    def to_affect_baseline(self) -> AffectiveState:
        """Derive the emotional baseline from personality.

        This binds personality to emotion — the two systems are connected
        through this method.

        The OCEAN dimensions are centred on 0.5 before mapping, so a neutral
        personality (all 0.5) produces a near-zero VAD baseline (calm/neutral)
        rather than a spuriously "alert" state. Traits shift the baseline
        relative to that neutral centre.

        Returns:
            An ``AffectiveState`` representing the resting emotional tone.
        """
        return AffectiveState(
            valence=(self.agreeableness - 0.5) * 0.8 - (self.neuroticism - 0.5) * 0.6,
            arousal=(self.extraversion - 0.5) * 0.7 + (self.openness - 0.5) * 0.3,
            dominance=(self.conscientiousness - 0.5) * 0.8,
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
            extraversion=self._clamp(self.extraversion + delta.extraversion * self.learning_rate),
            agreeableness=self._clamp(
                self.agreeableness + delta.agreeableness * self.learning_rate
            ),
            neuroticism=self._clamp(self.neuroticism + delta.neuroticism * self.learning_rate),
            humor_level=self._clamp(self.humor_level + delta.humor_level * self.learning_rate),
            verbosity=self._clamp(self.verbosity + delta.verbosity * self.learning_rate),
            formality=self._clamp(self.formality + delta.formality * self.learning_rate),
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
            extraversion=self._clamp(self.extraversion + delta.extraversion * self.learning_rate),
            agreeableness=self._clamp(
                self.agreeableness + delta.agreeableness * self.learning_rate
            ),
            neuroticism=self._clamp(self.neuroticism + delta.neuroticism * self.learning_rate),
            humor_level=self._clamp(self.humor_level + delta.humor_level * self.learning_rate),
            verbosity=self._clamp(self.verbosity + delta.verbosity * self.learning_rate),
            formality=self._clamp(self.formality + delta.formality * self.learning_rate),
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

    def to_behavioral_prompt(self) -> str:
        """Serialize personality into behavioral guidance (not raw numbers).

        Raw OCEAN scores are low-signal for an LLM. This renders each trait
        as a short behavioral instruction only when it deviates meaningfully
        from neutral (0.5), so the prompt stays terse and actionable.
        """
        lines: list[str] = []

        def _describe(value: float, high: str, low: str, margin: float = 0.15) -> None:
            if value >= 0.5 + margin:
                lines.append(f"- {high}")
            elif value <= 0.5 - margin:
                lines.append(f"- {low}")

        _describe(self.openness, "对新想法保持好奇与开放", "聚焦务实、避免发散")
        _describe(self.conscientiousness, "严谨可靠，注重准确与条理", "灵活随性，不拘泥细节")
        _describe(self.extraversion, "表达主动、有活力", "克制内敛，言简意赅")
        _describe(self.agreeableness, "友善体贴，照顾对方感受", "直接坦率，不为礼貌而委婉")
        _describe(self.neuroticism, "对情绪与风险更敏感", "情绪稳定、沉着")
        _describe(self.humor_level, "适时使用干练的幽默", "保持严肃、少用玩笑")
        _describe(self.verbosity, "可展开充分说明", "默认简短，点到为止")
        _describe(self.formality, "用语更正式", "用语轻松口语化")

        if not lines:
            return "行为风格：均衡中性。"
        return "行为风格：\n" + "\n".join(lines)

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalityVector":
        """Reconstruct a ``PersonalityVector`` from a ``to_dict`` payload.

        Unknown keys are ignored and missing keys fall back to class
        defaults, so persisted snapshots remain forward/backward compatible.
        """
        if not isinstance(data, dict):
            return cls()

        def _f(key: str, default: float) -> float:
            try:
                return float(data.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            openness=_f("openness", 0.65),
            conscientiousness=_f("conscientiousness", 0.75),
            extraversion=_f("extraversion", 0.45),
            agreeableness=_f("agreeableness", 0.6),
            neuroticism=_f("neuroticism", 0.3),
            humor_level=_f("humor_level", 0.4),
            verbosity=_f("verbosity", 0.35),
            formality=_f("formality", 0.45),
            learning_rate=_f("learning_rate", 0.03),
        )
