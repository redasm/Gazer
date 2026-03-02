from __future__ import annotations

from eval.offpolicy_estimator import OffPolicyEstimator


def _sample(
    *,
    tool_success: float,
    feedback: float,
    eval_passed: bool | None,
    terminal_error: bool,
    persona: float | None = None,
):
    return {
        "reward_proxy": {
            "tool_success_rate": tool_success,
            "feedback_score": feedback,
            "eval_passed": eval_passed,
            "has_terminal_error": terminal_error,
            "persona_consistency_score": persona,
        }
    }


def test_offpolicy_estimator_reward_and_risk_interval() -> None:
    estimator = OffPolicyEstimator(bootstrap_rounds=120, seed=7, min_reward_threshold=0.55)
    candidate_samples = [
        _sample(tool_success=0.9, feedback=1.0, eval_passed=True, terminal_error=False, persona=0.9),
        _sample(tool_success=0.85, feedback=0.8, eval_passed=True, terminal_error=False, persona=0.88),
        _sample(tool_success=0.8, feedback=0.6, eval_passed=True, terminal_error=False, persona=0.86),
    ] * 10
    baseline_samples = [
        _sample(tool_success=0.45, feedback=-0.3, eval_passed=False, terminal_error=True, persona=0.6),
        _sample(tool_success=0.55, feedback=0.0, eval_passed=False, terminal_error=True, persona=0.62),
        _sample(tool_success=0.5, feedback=-0.1, eval_passed=False, terminal_error=True, persona=0.58),
    ] * 10

    report = estimator.estimate(
        candidate_samples=candidate_samples,
        baseline_samples=baseline_samples,
        context={"candidate_id": "opc_test"},
    )

    assert report["state"] == "evaluated"
    assert report["method"] == "reward_proxy_bootstrap"
    assert report["reward_estimate"]["candidate_expected_reward"] is not None
    assert report["reward_estimate"]["baseline_expected_reward"] is not None
    assert report["reward_estimate"]["expected_reward_delta"] is not None
    assert float(report["reward_estimate"]["expected_reward_delta"]) > 0.0
    assert report["risk_interval"]["downside_probability"] is not None
    assert 0.0 <= float(report["risk_interval"]["downside_probability"]) <= 1.0
    assert report["coverage"]["sample_coverage"] > 0.0


def test_offpolicy_estimator_from_exports_without_baseline() -> None:
    estimator = OffPolicyEstimator(bootstrap_rounds=80, seed=11)
    export = {
        "export_id": "bridge_candidate_1",
        "dataset_id": "ds_policy",
        "samples": [
            _sample(tool_success=0.8, feedback=0.5, eval_passed=True, terminal_error=False, persona=0.85),
            _sample(tool_success=0.7, feedback=0.2, eval_passed=True, terminal_error=False, persona=0.8),
        ],
    }
    report = estimator.estimate_from_exports(candidate_export=export, baseline_export=None, compare=None)

    assert report["state"] == "evaluated"
    assert report["reward_estimate"]["candidate_expected_reward"] is not None
    assert report["reward_estimate"]["baseline_expected_reward"] is None
    assert report["reward_estimate"]["expected_reward_delta"] is None
    assert report["context"]["candidate_export_id"] == "bridge_candidate_1"

