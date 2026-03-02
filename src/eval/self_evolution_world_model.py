from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class CompressedState:
    progress: float
    risk: float
    remaining_steps: int
    budget_pressure: float
    failure_bias: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "progress": round(self.progress, 4),
            "risk": round(self.risk, 4),
            "remaining_steps": int(self.remaining_steps),
            "budget_pressure": round(self.budget_pressure, 4),
            "failure_bias": round(self.failure_bias, 4),
        }


@dataclass(frozen=True)
class TransitionPrediction:
    next_state: CompressedState
    success_prob: float
    expected_cost: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "next_state": self.next_state.to_dict(),
            "success_prob": round(self.success_prob, 4),
            "expected_cost": round(self.expected_cost, 4),
        }


class MinimalWorldModel:
    """A compact transition model for offline self-evolution experiments."""

    def __init__(self) -> None:
        self._action_stats: Dict[str, Dict[str, float]] = {}

    def fit(self, episodes: Iterable[Dict[str, Any]]) -> None:
        totals: Dict[str, Dict[str, float]] = {}
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            for action in list(episode.get("candidate_actions") or []):
                if not isinstance(action, dict):
                    continue
                name = str(action.get("name", "")).strip()
                if not name:
                    continue
                bucket = totals.setdefault(
                    name,
                    {
                        "count": 0.0,
                        "base_success": 0.0,
                        "delta_progress": 0.0,
                        "delta_risk": 0.0,
                        "cost": 0.0,
                    },
                )
                bucket["count"] += 1.0
                bucket["base_success"] += _to_float(action.get("base_success"), 0.6)
                bucket["delta_progress"] += _to_float(action.get("delta_progress"), 0.2)
                bucket["delta_risk"] += _to_float(action.get("delta_risk"), 0.05)
                bucket["cost"] += _to_float(action.get("cost"), 1.0)

        action_stats: Dict[str, Dict[str, float]] = {}
        for name, bucket in totals.items():
            count = max(1.0, float(bucket.get("count", 1.0)))
            action_stats[name] = {
                "base_success": round(float(bucket.get("base_success", 0.0)) / count, 4),
                "delta_progress": round(float(bucket.get("delta_progress", 0.0)) / count, 4),
                "delta_risk": round(float(bucket.get("delta_risk", 0.0)) / count, 4),
                "cost": round(float(bucket.get("cost", 0.0)) / count, 4),
            }
        self._action_stats = action_stats

    def compress_state(self, payload: Dict[str, Any]) -> CompressedState:
        progress = _clamp(_to_float(payload.get("progress"), 0.0), 0.0, 1.0)
        risk = _clamp(_to_float(payload.get("risk"), 0.2), 0.0, 1.0)
        remaining_steps_raw = payload.get("remaining_steps", payload.get("max_steps", 3))
        try:
            remaining_steps = max(0, int(remaining_steps_raw))
        except (TypeError, ValueError):
            remaining_steps = 3
        budget_pressure = _clamp(_to_float(payload.get("budget_pressure"), 0.0), 0.0, 1.0)
        failure_bias = _clamp(_to_float(payload.get("failure_bias"), 0.0), 0.0, 1.0)
        return CompressedState(
            progress=progress,
            risk=risk,
            remaining_steps=remaining_steps,
            budget_pressure=budget_pressure,
            failure_bias=failure_bias,
        )

    def predict_transition(self, state: CompressedState, action: Dict[str, Any]) -> TransitionPrediction:
        name = str(action.get("name", "")).strip()
        learned = self._action_stats.get(name, {})
        base_success = _to_float(action.get("base_success"), _to_float(learned.get("base_success"), 0.62))
        delta_progress = _to_float(action.get("delta_progress"), _to_float(learned.get("delta_progress"), 0.2))
        delta_risk = _to_float(action.get("delta_risk"), _to_float(learned.get("delta_risk"), 0.04))
        cost = max(0.0, _to_float(action.get("cost"), _to_float(learned.get("cost"), 1.0)))

        success_prob = _clamp(
            base_success
            - (state.risk * 0.35)
            - (state.budget_pressure * 0.15)
            - (state.failure_bias * 0.2),
            0.02,
            0.98,
        )
        progress_gain = max(0.0, delta_progress * (0.55 + (success_prob * 0.45)))
        next_progress = _clamp(state.progress + progress_gain, 0.0, 1.0)
        next_risk = _clamp(state.risk + delta_risk + ((1.0 - success_prob) * 0.08), 0.0, 1.0)
        next_remaining_steps = max(0, int(state.remaining_steps) - 1)
        next_budget = _clamp(state.budget_pressure + (cost * 0.08), 0.0, 1.0)
        next_failure_bias = _clamp((state.failure_bias * 0.7) + ((1.0 - success_prob) * 0.3), 0.0, 1.0)

        return TransitionPrediction(
            next_state=CompressedState(
                progress=next_progress,
                risk=next_risk,
                remaining_steps=next_remaining_steps,
                budget_pressure=next_budget,
                failure_bias=next_failure_bias,
            ),
            success_prob=success_prob,
            expected_cost=cost,
        )

    def action_stats(self) -> Dict[str, Dict[str, float]]:
        return {key: dict(value) for key, value in self._action_stats.items()}

