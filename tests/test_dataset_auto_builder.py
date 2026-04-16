"""Tests for eval.dataset_auto_builder.DatasetAutoBuilder."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from eval.dataset_auto_builder import DatasetAutoBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _bridge_sample(
    *,
    run_id: str = "r1",
    user_content: str = "do something",
    assistant_output: str = "done",
    final_status: str = "done",
    tool_success_rate: float = 1.0,
    feedback_score: float = 1.0,
    has_terminal_error: bool = False,
    eval_passed: bool = True,
    tool_events: List[Dict] = None,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "state": {
            "user_content": user_content,
            "channel": "test",
            "final_status": final_status,
        },
        "action": {"assistant_output": assistant_output},
        "tool_result": {
            "events": tool_events or [],
            "total_count": len(tool_events) if tool_events else 0,
        },
        "reward_proxy": {
            "tool_success_rate": tool_success_rate,
            "feedback_score": feedback_score,
            "feedback_text": "",
            "has_terminal_error": has_terminal_error,
            "eval_passed": eval_passed,
        },
    }


def _tool_event(tool: str, status: str, error_code: str = "") -> Dict[str, Any]:
    return {"tool": tool, "status": status, "error_code": error_code}


# ---------------------------------------------------------------------------
# Strategy 1: positive cases
# ---------------------------------------------------------------------------

class TestBuildPositiveFromTrajectories:
    def test_includes_high_success_rate_samples(self):
        samples = [
            _bridge_sample(run_id="r1", tool_success_rate=0.9, feedback_score=1.0),
            _bridge_sample(run_id="r2", tool_success_rate=0.5, feedback_score=1.0),  # below threshold
        ]
        builder = DatasetAutoBuilder()
        result = builder.build_positive_from_trajectories(samples, min_tool_success_rate=0.75)
        ids = [s["run_id"] for s in result]
        assert "r1" in ids
        assert "r2" not in ids

    def test_excludes_terminal_errors(self):
        samples = [
            _bridge_sample(run_id="r1", tool_success_rate=1.0, has_terminal_error=True),
        ]
        builder = DatasetAutoBuilder()
        result = builder.build_positive_from_trajectories(samples)
        assert result == []

    def test_label_is_positive(self):
        samples = [_bridge_sample(run_id="r1")]
        builder = DatasetAutoBuilder()
        result = builder.build_positive_from_trajectories(samples)
        assert all(s["label"] == "positive" for s in result)

    def test_respects_max_samples(self):
        samples = [_bridge_sample(run_id=f"r{i}") for i in range(20)]
        builder = DatasetAutoBuilder()
        result = builder.build_positive_from_trajectories(samples, max_samples=5)
        assert len(result) <= 5


# ---------------------------------------------------------------------------
# Strategy 2: negative cases
# ---------------------------------------------------------------------------

class TestBuildNegativeFromFailures:
    def test_includes_terminal_error_samples(self):
        samples = [
            _bridge_sample(run_id="r1", has_terminal_error=True),
            _bridge_sample(run_id="r2", has_terminal_error=False),
        ]
        builder = DatasetAutoBuilder()
        result = builder.build_negative_from_failures(samples)
        ids = [s["run_id"] for s in result]
        assert "r1" in ids
        assert "r2" not in ids

    def test_includes_eval_failed_samples(self):
        samples = [_bridge_sample(run_id="r1", eval_passed=False, has_terminal_error=False)]
        builder = DatasetAutoBuilder()
        result = builder.build_negative_from_failures(samples)
        assert len(result) == 1
        assert result[0]["label"] == "negative"

    def test_includes_negative_feedback_score(self):
        samples = [_bridge_sample(run_id="r1", feedback_score=-1.0, has_terminal_error=False)]
        builder = DatasetAutoBuilder()
        result = builder.build_negative_from_failures(samples)
        assert len(result) == 1

    def test_excludes_clean_samples(self):
        samples = [
            _bridge_sample(run_id="r1", has_terminal_error=False, eval_passed=True, feedback_score=1.0),
        ]
        builder = DatasetAutoBuilder()
        result = builder.build_negative_from_failures(samples)
        assert result == []

    def test_respects_max_samples(self):
        samples = [_bridge_sample(run_id=f"r{i}", has_terminal_error=True) for i in range(20)]
        builder = DatasetAutoBuilder()
        result = builder.build_negative_from_failures(samples, max_samples=3)
        assert len(result) <= 3


# ---------------------------------------------------------------------------
# Strategy 3: tool-contract cases
# ---------------------------------------------------------------------------

class TestBuildToolContractCases:
    def _samples_with_events(self, events_per_sample):
        return [
            {
                "run_id": f"r{i}",
                "state": {"user_content": "test", "channel": "", "final_status": "done"},
                "action": {"assistant_output": "ok"},
                "tool_result": {"events": evts},
                "reward_proxy": {
                    "tool_success_rate": 1.0,
                    "feedback_score": 0.0,
                    "feedback_text": "",
                    "has_terminal_error": False,
                    "eval_passed": True,
                },
            }
            for i, evts in enumerate(events_per_sample)
        ]

    def test_extracts_patterns_above_min_occurrences(self):
        samples = self._samples_with_events([
            [_tool_event("exec", "error", "timeout")] for _ in range(3)
        ])
        builder = DatasetAutoBuilder()
        result = builder.build_tool_contract_cases(samples, min_occurrences=2)
        assert len(result) >= 1
        assert any("exec" in r["user_content"] for r in result)

    def test_skips_patterns_below_min_occurrences(self):
        samples = self._samples_with_events([
            [_tool_event("exec", "error")]
        ])
        builder = DatasetAutoBuilder()
        result = builder.build_tool_contract_cases(samples, min_occurrences=3)
        assert result == []

    def test_success_events_become_positive_label(self):
        samples = self._samples_with_events([
            [_tool_event("web_search", "ok")] for _ in range(3)
        ])
        builder = DatasetAutoBuilder()
        result = builder.build_tool_contract_cases(samples, min_occurrences=2)
        positive = [r for r in result if r["label"] == "positive"]
        assert len(positive) >= 1

    def test_respects_tool_filter(self):
        samples = self._samples_with_events([
            [_tool_event("exec", "error"), _tool_event("web_search", "ok")] for _ in range(3)
        ])
        builder = DatasetAutoBuilder()
        result = builder.build_tool_contract_cases(samples, tool_names=["exec"], min_occurrences=2)
        assert all("web_search" not in r["user_content"] for r in result)


# ---------------------------------------------------------------------------
# Strategy 4: recall query set
# ---------------------------------------------------------------------------

class TestBuildRecallQuerySetFromSkills:
    def test_generates_query_per_skill(self):
        skills = {
            "web_search": "Search the web for information and return relevant results.",
            "exec": "Execute shell commands and return stdout and stderr.",
        }
        builder = DatasetAutoBuilder()
        result = builder.build_recall_query_set_from_skills(skills, queries_per_skill=2)
        assert len(result) >= 2

    def test_output_format_compatible_with_recall_regression(self):
        skills = {"web_search": "Search the web for recent news and information."}
        builder = DatasetAutoBuilder()
        result = builder.build_recall_query_set_from_skills(skills, queries_per_skill=1)
        assert len(result) >= 1
        q = result[0]
        assert "id" in q
        assert "query" in q
        assert "expected_terms" in q
        assert isinstance(q["expected_terms"], list)
        assert len(q["expected_terms"]) >= 1

    def test_llm_fallback_on_parse_error(self):
        """When LLM returns invalid JSON, should fall back to keyword approach."""
        def bad_llm(_prompt: str) -> str:
            return "not valid json"

        skills = {"exec": "Execute shell commands in the local environment."}
        builder = DatasetAutoBuilder()
        result = builder.build_recall_query_set_from_skills(
            skills, queries_per_skill=1, llm_caller=bad_llm
        )
        # Should still produce results via keyword fallback
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Combined dataset
# ---------------------------------------------------------------------------

class TestBuildCombinedDataset:
    def test_combined_includes_all_strategies(self):
        pos_sample = _bridge_sample(run_id="pos1", tool_success_rate=1.0)
        neg_sample = _bridge_sample(run_id="neg1", has_terminal_error=True)
        contract_samples = [
            {
                "run_id": f"c{i}",
                "state": {"user_content": "test", "channel": "", "final_status": "done"},
                "action": {"assistant_output": "ok"},
                "tool_result": {"events": [_tool_event("exec", "error")]},
                "reward_proxy": {
                    "tool_success_rate": 0.0, "feedback_score": 0.0,
                    "feedback_text": "", "has_terminal_error": True, "eval_passed": False,
                },
            }
            for i in range(3)
        ]
        all_samples = [pos_sample, neg_sample] + contract_samples
        builder = DatasetAutoBuilder()
        combined = builder.build_combined_dataset(all_samples)
        assert combined["meta"]["positive"] >= 1
        assert combined["meta"]["negative"] >= 1
        assert combined["total"] == sum(combined["meta"].values())

    def test_respects_limits(self):
        samples = [_bridge_sample(run_id=f"r{i}") for i in range(50)]
        builder = DatasetAutoBuilder()
        combined = builder.build_combined_dataset(
            samples, positive_limit=3, negative_limit=3, contract_limit=3
        )
        assert combined["meta"]["positive"] <= 3
        assert combined["meta"]["negative"] <= 3

    def test_combined_strategy_flags(self):
        pos = _bridge_sample(run_id="p1", tool_success_rate=1.0)
        neg = _bridge_sample(run_id="n1", has_terminal_error=True)
        builder = DatasetAutoBuilder()

        pos_only = builder.build_combined_dataset(
            [pos, neg], include_positive=True, include_negative=False, include_tool_contracts=False
        )
        assert pos_only["meta"]["negative"] == 0
        assert pos_only["meta"]["positive"] >= 1
