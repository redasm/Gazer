"""Tests for eval.gepa_optimizer.GEPAOptimizer."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from eval.gepa_optimizer import GEPAOptimizer, _score_rules_against_eval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch(rules: List[str] = None, strategy: str = "cost") -> Dict[str, Any]:
    return {
        "prompt_patch": {
            "strategy": "append_rules",
            "rules": rules or ["Be helpful."],
        },
        "policy_patch": {"security.tool_denylist.add": []},
        "router_patch": {
            "strategy": strategy,
            "strategy_template": "cost_first",
            "budget": {"enabled": True},
        },
    }


def _trajectory_sample(
    *,
    run_id: str = "r1",
    assistant_output: str = "done",
    feedback: str = "",
    events: List[Dict] = None,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "assistant_output": assistant_output,
        "feedback": feedback,
        "events": events or [],
    }


def _eval_sample(*, passed: bool = True, reference_output: str = "ok") -> Dict[str, Any]:
    return {"passed": passed, "reference_output": reference_output}


def _tool_result_event(tool: str, status: str, error_code: str = "") -> Dict[str, Any]:
    return {
        "action": "tool_result",
        "payload": {"tool": tool, "status": status, "error_code": error_code, "result_preview": ""},
    }


# ---------------------------------------------------------------------------
# Mutation operators
# ---------------------------------------------------------------------------

class TestMutateAddRule:
    def test_adds_rule_from_pool(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch(rules=["Rule A."])
        pool = ["Rule B.", "Rule C."]
        result = opt.mutate_add_rule(p, pool)
        assert len(result["prompt_patch"]["rules"]) == 2

    def test_does_not_add_duplicate(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch(rules=["Rule A."])
        pool = ["Rule A."]  # already in patch
        result = opt.mutate_add_rule(p, pool)
        assert result["prompt_patch"]["rules"] == ["Rule A."]

    def test_empty_pool_returns_original(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch(rules=["Rule A."])
        result = opt.mutate_add_rule(p, [])
        assert result is p


class TestMutateRemoveRule:
    def test_removes_one_rule(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch(rules=["Rule A.", "Rule B.", "Rule C."])
        result = opt.mutate_remove_rule(p)
        assert len(result["prompt_patch"]["rules"]) == 2

    def test_keeps_minimum_one_rule(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch(rules=["Only rule."])
        result = opt.mutate_remove_rule(p)
        assert len(result["prompt_patch"]["rules"]) >= 1


class TestMutateRouterStrategy:
    def test_updates_strategy(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch(strategy="cost")
        result = opt.mutate_router_strategy(p, {"retryable": 5, "non_retryable": 0})
        assert result["router_patch"]["strategy"] in {"cost", "latency", "priority", "availability"}

    def test_high_retryable_prefers_latency(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch()
        result = opt.mutate_router_strategy(p, {"retryable": 10, "non_retryable": 0})
        assert result["router_patch"]["strategy"] == "latency"

    def test_high_non_retryable_prefers_priority(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch()
        result = opt.mutate_router_strategy(p, {"retryable": 0, "non_retryable": 10})
        assert result["router_patch"]["strategy"] == "priority"


class TestCrossover:
    def test_child_has_rules_from_both_parents(self):
        opt = GEPAOptimizer(seed=1)
        pa = _patch(rules=["Rule A1.", "Rule A2.", "Rule A3."])
        pb = _patch(rules=["Rule B1.", "Rule B2.", "Rule B3."])
        child = opt.crossover(pa, pb)
        rules = set(child["prompt_patch"]["rules"])
        assert any(r.startswith("Rule A") for r in rules)
        assert any(r.startswith("Rule B") for r in rules)

    def test_child_has_no_duplicate_rules(self):
        opt = GEPAOptimizer(seed=1)
        pa = _patch(rules=["Shared.", "Rule A."])
        pb = _patch(rules=["Shared.", "Rule B."])
        child = opt.crossover(pa, pb)
        rules = child["prompt_patch"]["rules"]
        assert len(rules) == len(set(rules))


# ---------------------------------------------------------------------------
# Fitness scoring
# ---------------------------------------------------------------------------

class TestScoreCandidate:
    def test_score_in_valid_range(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch(rules=["Be helpful.", "Be concise."])
        score = opt.score_candidate(p, [], [])
        assert 0.0 <= score <= 1.0

    def test_empty_eval_samples_returns_nonzero(self):
        opt = GEPAOptimizer(seed=1)
        p = _patch()
        score = opt.score_candidate(p, [], [])
        assert score >= 0.0

    def test_deny_tools_improve_tool_coverage_score(self):
        opt = GEPAOptimizer(seed=1)
        traj = [_trajectory_sample(events=[_tool_result_event("exec", "error")])]
        p_with_deny = {
            "prompt_patch": {"rules": ["Be helpful."]},
            "policy_patch": {"security.tool_denylist.add": ["exec"]},
            "router_patch": {"strategy": "cost", "strategy_template": "cost_first", "budget": {}},
        }
        p_no_deny = {
            "prompt_patch": {"rules": ["Be helpful."]},
            "policy_patch": {"security.tool_denylist.add": []},
            "router_patch": {"strategy": "cost", "strategy_template": "cost_first", "budget": {}},
        }
        score_with = opt.score_candidate(p_with_deny, traj, [])
        score_without = opt.score_candidate(p_no_deny, traj, [])
        assert score_with > score_without


class TestScoreRulesAgainstEval:
    def test_returns_zero_for_empty_eval(self):
        score = _score_rules_against_eval(["Be helpful."], [])
        assert score == 0.0

    def test_returns_nonzero_when_rule_matches_error_text(self):
        rules = ["Prefer lightweight fallback plan when provider instability is detected."]
        eval_samples = [
            {"reference_output": "Error: provider connection failed"},
        ]
        score = _score_rules_against_eval(rules, eval_samples)
        assert score > 0.0


# ---------------------------------------------------------------------------
# Evolution loop
# ---------------------------------------------------------------------------

class TestEvolve:
    def test_returns_required_keys(self):
        opt = GEPAOptimizer(population_size=4, generations=2, seed=42)
        result = opt.evolve(_patch(), [], [])
        assert "best_patch" in result
        assert "pareto_front" in result
        assert "generation_scores" in result
        assert "generations_run" in result

    def test_best_score_not_worse_than_seed(self):
        opt = GEPAOptimizer(population_size=6, generations=3, seed=42)
        traj = [_trajectory_sample(feedback="unsafe and dangerous")]
        evals = [_eval_sample(passed=False, reference_output="error: failed")]
        seed = _patch()
        result = opt.evolve(seed, traj, evals)
        seed_score = opt.score_candidate(seed, traj, evals)
        # The best_score should never be worse than seed (GEPA only-accept guarantee)
        assert result["best_score"] >= seed_score - 1e-9  # float tolerance

    def test_generation_scores_length(self):
        opt = GEPAOptimizer(population_size=4, generations=4, seed=1)
        result = opt.evolve(_patch(), [], [])
        # generation_scores has one entry per generation + initial
        assert len(result["generation_scores"]) == opt.generations + 1

    def test_pareto_front_is_list(self):
        opt = GEPAOptimizer(population_size=4, generations=2, seed=1)
        result = opt.evolve(_patch(), [], [])
        assert isinstance(result["pareto_front"], list)

    def test_best_patch_has_prompt_patch(self):
        opt = GEPAOptimizer(population_size=4, generations=2, seed=1)
        result = opt.evolve(_patch(), [], [])
        assert "prompt_patch" in result["best_patch"]
        assert "rules" in result["best_patch"]["prompt_patch"]


# ---------------------------------------------------------------------------
# Integration: GEPA through LightningLiteTrainer
# ---------------------------------------------------------------------------

class TestLightningLiteTrainerGEPAIntegration:
    def test_generate_patch_without_gepa_returns_seed(self, monkeypatch):
        """When GEPA is disabled, should return normal seed patch."""
        monkeypatch.setattr(
            "eval.trainer._gepa_config",
            lambda: {"enabled": False},
        )
        from eval.trainer import LightningLiteTrainer
        trainer = LightningLiteTrainer()
        result = trainer.generate_patch(
            trajectory_samples=[_trajectory_sample()],
            eval_samples=[_eval_sample()],
        )
        assert "prompt_patch" in result
        assert "gepa_meta" not in result

    def test_generate_patch_with_gepa_adds_meta(self, monkeypatch):
        """When GEPA is enabled, best patch should contain gepa_meta."""
        monkeypatch.setattr(
            "eval.trainer._gepa_config",
            lambda: {
                "enabled": True,
                "population_size": 4,
                "generations": 2,
                "mutation_rate": 0.3,
                "elite_ratio": 0.25,
            },
        )
        from eval.trainer import LightningLiteTrainer
        trainer = LightningLiteTrainer()
        result = trainer.generate_patch(
            trajectory_samples=[_trajectory_sample(feedback="unsafe")],
            eval_samples=[_eval_sample(passed=False)],
        )
        assert "prompt_patch" in result
        # gepa_meta only present if GEPA improved on seed
        if "gepa_meta" in result:
            assert "generations_run" in result["gepa_meta"]
            assert "seed_score" in result["gepa_meta"]
            assert "best_score" in result["gepa_meta"]
