"""VAD (Valence-Arousal-Dominance) continuous affective state model.

Replaces the discrete MentalState enum (IDLE/INTERACTING/THINKING) with a
three-dimensional continuous emotion space based on Russell's Circumplex Model
(1980):

- **Valence**: -1.0 (negative) ~ +1.0 (positive)
- **Arousal**: -1.0 (low activation) ~ +1.0 (high activation)
- **Dominance**: -1.0 (submissive) ~ +1.0 (dominant)

An additional **inertia** parameter controls how resistant the state is to
change — higher inertia means slower emotional drift.

References:
    - soul_architecture_reform.md Issue-01
    - Russell, J. A. (1980). A circumplex model of affect.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AffectiveState:
    """Immutable three-dimensional emotional state with inertia.

    All dimension values are clamped to [-1.0, 1.0].
    ``inertia`` is in [0.0, 1.0] — higher means the state changes more slowly.
    """

    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    inertia: float = 0.5

    def __post_init__(self) -> None:
        # Enforce value ranges on frozen dataclass via object.__setattr__
        object.__setattr__(self, "valence", self._clamp(self.valence, -1.0, 1.0))
        object.__setattr__(self, "arousal", self._clamp(self.arousal, -1.0, 1.0))
        object.__setattr__(self, "dominance", self._clamp(self.dominance, -1.0, 1.0))
        object.__setattr__(self, "inertia", self._clamp(self.inertia, 0.0, 1.0))

    # ------------------------------------------------------------------
    # Core transition
    # ------------------------------------------------------------------

    def transition_toward(
        self, target: AffectiveState, intensity: float = 1.0
    ) -> AffectiveState:
        """Drift toward *target* with inertia-based resistance.

        Args:
            target: The direction of the emotional shift.
            intensity: Strength of the external stimulus (0.0 ~ 1.0).
                Higher intensity overcomes more inertia.

        Returns:
            A new ``AffectiveState`` reflecting the drift.  The original
            instance is unchanged (immutable).
        """
        effective_rate = intensity * (1.0 - self.inertia)
        return AffectiveState(
            valence=self._lerp(self.valence, target.valence, effective_rate),
            arousal=self._lerp(self.arousal, target.arousal, effective_rate),
            dominance=self._lerp(self.dominance, target.dominance, effective_rate),
            inertia=self.inertia,  # inertia itself does not drift
        )

    # ------------------------------------------------------------------
    # Label helpers
    # ------------------------------------------------------------------

    def to_label(self) -> str:
        """Map current VAD coordinates to a human-readable Chinese label.

        The mapping uses simplified quadrant logic:
        - valence > 0 + arousal > 0 → 兴奋/开心
        - valence > 0 + arousal ≤ 0 → 平静/满足
        - valence ≤ 0 + arousal > 0 → 焦虑/烦躁
        - valence ≤ 0 + arousal ≤ 0 → 低落/疲惫
        """
        if self.valence > 0.3:
            if self.arousal > 0.3:
                return "兴奋"
            elif self.arousal > -0.3:
                return "开心"
            else:
                return "平静"
        elif self.valence > -0.3:
            if self.arousal > 0.3:
                return "警觉"
            elif self.arousal > -0.3:
                return "中性"
            else:
                return "放松"
        else:
            if self.arousal > 0.3:
                return "焦虑"
            elif self.arousal > -0.3:
                return "低落"
            else:
                return "疲惫"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        """Clamp *value* to [lo, hi]."""
        return max(lo, min(hi, value))

    @staticmethod
    def _lerp(a: float, b: float, t: float) -> float:
        """Linear interpolation from *a* toward *b* by factor *t*."""
        return a + (b - a) * t

    def to_dict(self) -> dict[str, float]:
        """Serialize to a plain dictionary."""
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4),
            "inertia": round(self.inertia, 4),
        }
