"""Lightweight off-policy estimator using reward proxies from trajectory exports."""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    clamped = min(1.0, max(0.0, float(q)))
    idx = int(round((len(ordered) - 1) * clamped))
    idx = max(0, min(idx, len(ordered) - 1))
    return ordered[idx]


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return float(sum(values) / len(values))


class OffPolicyEstimator:
    """Estimate candidate policy reward/risk using reward proxy bootstrap."""

    VERSION = "offpolicy-estimator.v1"

    def __init__(
        self,
        *,
        bootstrap_rounds: int = 300,
        seed: int = 42,
        min_reward_threshold: float = 0.6,
        min_samples_for_confidence: int = 20,
    ) -> None:
        self.bootstrap_rounds = max(20, int(bootstrap_rounds))
        self.seed = int(seed)
        self.min_reward_threshold = max(0.0, min(1.0, float(min_reward_threshold)))
        self.min_samples_for_confidence = max(1, int(min_samples_for_confidence))

    @staticmethod
    def _score_sample(sample: Dict[str, Any]) -> Optional[float]:
        reward = sample.get("reward_proxy") if isinstance(sample.get("reward_proxy"), dict) else {}
        if not isinstance(reward, dict):
            return None
        tool_success = _safe_float(reward.get("tool_success_rate"))
        feedback = _safe_float(reward.get("feedback_score"))
        eval_passed = reward.get("eval_passed")
        has_terminal_error = bool(reward.get("has_terminal_error", False))
        persona_score = _safe_float(reward.get("persona_consistency_score"))

        if tool_success is None and feedback is None and eval_passed is None and persona_score is None:
            return None

        tool_term = min(1.0, max(0.0, tool_success if tool_success is not None else 0.0))
        feedback_term = min(1.0, max(0.0, ((feedback if feedback is not None else 0.0) + 1.0) / 2.0))
        if eval_passed is None:
            eval_term = 0.5
        else:
            eval_term = 1.0 if bool(eval_passed) else 0.0
        persona_term = min(1.0, max(0.0, persona_score if persona_score is not None else 0.75))
        terminal_term = 0.0 if has_terminal_error else 1.0

        score = (
            0.35 * tool_term
            + 0.20 * feedback_term
            + 0.20 * eval_term
            + 0.15 * terminal_term
            + 0.10 * persona_term
        )
        return min(1.0, max(0.0, round(float(score), 6)))

    def _bootstrap_means(self, values: List[float]) -> List[float]:
        if not values:
            return []
        rng = random.Random(self.seed + len(values))
        count = len(values)
        dist: List[float] = []
        for _ in range(self.bootstrap_rounds):
            sample = [values[rng.randrange(count)] for _ in range(count)]
            mean_value = _mean(sample)
            if mean_value is not None:
                dist.append(mean_value)
        return dist

    @staticmethod
    def _collect_scores(samples: List[Dict[str, Any]]) -> tuple[List[float], int]:
        scores: List[float] = []
        valid_count = 0
        for item in samples:
            if not isinstance(item, dict):
                continue
            valid_count += 1
            score = OffPolicyEstimator._score_sample(item)
            if score is not None:
                scores.append(float(score))
        return scores, valid_count

    def estimate(
        self,
        *,
        candidate_samples: List[Dict[str, Any]],
        baseline_samples: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        baseline = baseline_samples if isinstance(baseline_samples, list) else []
        candidate_scores, candidate_valid = self._collect_scores(candidate_samples)
        baseline_scores, baseline_valid = self._collect_scores(baseline)

        candidate_point = _mean(candidate_scores)
        baseline_point = _mean(baseline_scores)
        candidate_boot = self._bootstrap_means(candidate_scores)
        baseline_boot = self._bootstrap_means(baseline_scores) if baseline_scores else []

        delta_boot: List[float] = []
        if candidate_boot and baseline_boot:
            n = min(len(candidate_boot), len(baseline_boot))
            delta_boot = [float(candidate_boot[idx]) - float(baseline_boot[idx]) for idx in range(n)]

        candidate_ci = {
            "p05": _quantile(candidate_boot, 0.05),
            "p50": _quantile(candidate_boot, 0.5),
            "p95": _quantile(candidate_boot, 0.95),
        }
        baseline_ci = {
            "p05": _quantile(baseline_boot, 0.05),
            "p50": _quantile(baseline_boot, 0.5),
            "p95": _quantile(baseline_boot, 0.95),
        }
        delta_ci = {
            "p05": _quantile(delta_boot, 0.05),
            "p50": _quantile(delta_boot, 0.5),
            "p95": _quantile(delta_boot, 0.95),
        }

        if delta_boot:
            downside_probability = round(
                sum(1 for value in delta_boot if float(value) < 0.0) / max(1, len(delta_boot)),
                4,
            )
        elif candidate_boot:
            downside_probability = round(
                sum(1 for value in candidate_boot if float(value) < self.min_reward_threshold)
                / max(1, len(candidate_boot)),
                4,
            )
        else:
            downside_probability = None

        reward_delta = (
            round(float(candidate_point) - float(baseline_point), 6)
            if candidate_point is not None and baseline_point is not None
            else None
        )
        confidence_level = round(
            min(1.0, len(candidate_scores) / float(self.min_samples_for_confidence)),
            4,
        ) if candidate_scores else 0.0

        return {
            "state": "evaluated",
            "version": self.VERSION,
            "method": "reward_proxy_bootstrap",
            "generated_at": time.time(),
            "reward_estimate": {
                "candidate_expected_reward": round(float(candidate_point), 6)
                if candidate_point is not None
                else None,
                "baseline_expected_reward": round(float(baseline_point), 6)
                if baseline_point is not None
                else None,
                "expected_reward_delta": reward_delta,
                "candidate_confidence_interval": candidate_ci,
                "baseline_confidence_interval": baseline_ci,
                "delta_confidence_interval": delta_ci,
            },
            "risk_interval": {
                "downside_probability": downside_probability,
                "delta_p05": delta_ci.get("p05"),
                "delta_p95": delta_ci.get("p95"),
                "confidence_level": confidence_level,
                "min_reward_threshold": self.min_reward_threshold,
            },
            "coverage": {
                "candidate_sample_count": len(candidate_samples),
                "candidate_valid_sample_count": int(candidate_valid),
                "candidate_reward_signal_coverage": round(
                    len(candidate_scores) / max(1, candidate_valid),
                    4,
                ) if candidate_valid > 0 else 0.0,
                "baseline_sample_count": len(baseline),
                "baseline_valid_sample_count": int(baseline_valid),
                "baseline_reward_signal_coverage": round(
                    len(baseline_scores) / max(1, baseline_valid),
                    4,
                ) if baseline_valid > 0 else 0.0,
                "sample_coverage": confidence_level,
            },
            "context": context if isinstance(context, dict) else {},
        }

    def estimate_from_exports(
        self,
        *,
        candidate_export: Dict[str, Any],
        baseline_export: Optional[Dict[str, Any]] = None,
        compare: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        candidate_samples = (
            list(candidate_export.get("samples", []))
            if isinstance(candidate_export.get("samples"), list)
            else []
        )
        baseline_samples = (
            list(baseline_export.get("samples", []))
            if isinstance(baseline_export, dict) and isinstance(baseline_export.get("samples"), list)
            else []
        )
        base_context = {
            "candidate_export_id": str(candidate_export.get("export_id", "")),
            "baseline_export_id": str((baseline_export or {}).get("export_id", "")),
            "dataset_id": str(candidate_export.get("dataset_id", "")),
        }
        if isinstance(compare, dict):
            base_context["compare"] = {
                "sample_delta": compare.get("sample_delta"),
                "shared_run_count": compare.get("shared_run_count"),
                "fingerprint_changed": compare.get("fingerprint_changed"),
                "reward_proxy_delta": compare.get("reward_proxy_delta", {}),
            }
        if isinstance(context, dict):
            base_context.update(context)
        return self.estimate(
            candidate_samples=candidate_samples,
            baseline_samples=baseline_samples,
            context=base_context,
        )

