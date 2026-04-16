"""Tests for eval.skill_evolver.SkillEvolver."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from eval.skill_evolver import SkillEvolver, SkillEvolutionProposal, ToolFailureProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bridge_sample_with_events(
    run_id: str,
    tool: str,
    status: str,
    error_code: str = "",
    user_content: str = "test input",
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "state": {"user_content": user_content, "channel": "", "final_status": "done"},
        "action": {"assistant_output": "ok"},
        "tool_result": {
            "events": [{"tool": tool, "status": status, "error_code": error_code}],
        },
        "reward_proxy": {
            "tool_success_rate": 0.0 if status not in {"ok", "success"} else 1.0,
            "feedback_score": 0.0,
            "feedback_text": "",
            "has_terminal_error": False,
            "eval_passed": True,
        },
    }


def _raw_traj_event(tool: str, status: str, error_code: str = "") -> Dict[str, Any]:
    return {
        "action": "tool_result",
        "payload": {"tool": tool, "status": status, "error_code": error_code},
    }


# ---------------------------------------------------------------------------
# ToolFailureProfile extraction
# ---------------------------------------------------------------------------

class TestAnalyzeToolFailures:
    def test_counts_failures_per_tool(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        samples = [
            _bridge_sample_with_events(f"r{i}", "exec", "error", "timeout")
            for i in range(5)
        ] + [
            _bridge_sample_with_events(f"s{i}", "web_search", "error", "rate_limit")
            for i in range(2)
        ]
        profiles = evolver.analyze_tool_failures(samples, top_n=3)
        names = [p.tool_name for p in profiles]
        assert "exec" in names
        assert "web_search" in names

    def test_orders_by_failure_count_descending(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        samples = (
            [_bridge_sample_with_events(f"r{i}", "exec", "error") for i in range(5)]
            + [_bridge_sample_with_events(f"s{i}", "web_search", "error") for i in range(2)]
        )
        profiles = evolver.analyze_tool_failures(samples, top_n=2)
        assert profiles[0].tool_name == "exec"

    def test_collects_sample_bad_inputs(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        samples = [
            _bridge_sample_with_events("r1", "exec", "error", user_content="run dangerous cmd"),
            _bridge_sample_with_events("r2", "exec", "error", user_content="another command"),
        ]
        profiles = evolver.analyze_tool_failures(samples, top_n=1)
        assert len(profiles[0].sample_bad_inputs) >= 1

    def test_limits_bad_inputs_to_five(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        samples = [
            _bridge_sample_with_events(f"r{i}", "exec", "error", user_content=f"input {i}")
            for i in range(10)
        ]
        profiles = evolver.analyze_tool_failures(samples, top_n=1)
        assert len(profiles[0].sample_bad_inputs) <= 5

    def test_skips_successful_events(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        samples = [_bridge_sample_with_events("r1", "exec", "ok")]
        profiles = evolver.analyze_tool_failures(samples, top_n=5)
        assert profiles == []

    def test_accepts_raw_trajectory_format(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        raw = {
            "run_id": "r1",
            "events": [_raw_traj_event("exec", "error", "timeout")],
        }
        profiles = evolver.analyze_tool_failures([raw], top_n=1)
        assert len(profiles) == 1
        assert profiles[0].tool_name == "exec"


# ---------------------------------------------------------------------------
# Safety check
# ---------------------------------------------------------------------------

class TestSafetyCheck:
    def test_rejects_oversized_description(self, tmp_path):
        evolver = SkillEvolver(tmp_path, max_description_chars=50)
        original = "Short description."
        proposed = "X" * 100
        result = evolver.safety_check(original, proposed)
        assert result["size_ok"] is False
        assert result["ok"] is False

    def test_rejects_low_semantic_preservation(self, tmp_path):
        evolver = SkillEvolver(tmp_path, min_semantic_preservation=0.75)
        original = "Execute shell commands safely in the environment."
        proposed = "Send email messages to recipients."  # completely different
        result = evolver.safety_check(original, proposed)
        assert result["key_terms_retained"] is False
        assert result["ok"] is False

    def test_accepts_similar_description(self, tmp_path):
        # Use explicit threshold to make the test robust regardless of token overlap
        evolver = SkillEvolver(tmp_path, min_semantic_preservation=0.6)
        original = "Execute shell commands and return stdout and stderr."
        proposed = "Execute shell commands securely and return stdout, stderr, and exit code."
        result = evolver.safety_check(original, proposed)
        assert result["size_ok"] is True
        assert result["semantic_score"] >= 0.6
        assert result["ok"] is True

    def test_semantic_score_in_valid_range(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        result = evolver.safety_check("hello world", "hello world!")
        assert 0.0 <= result["semantic_score"] <= 1.0


# ---------------------------------------------------------------------------
# Proposal generation
# ---------------------------------------------------------------------------

class TestGenerateProposals:
    def test_generates_proposals_with_heuristic(self, tmp_path):
        evolver = SkillEvolver(tmp_path, min_semantic_preservation=0.0)
        profiles = [
            ToolFailureProfile(
                tool_name="exec",
                failure_count=5,
                error_codes={"timeout": 3, "permission_denied": 2},
                sample_bad_inputs=["run dangerous cmd"],
                current_description="Execute shell commands.",
            )
        ]
        proposals = evolver.generate_proposals(profiles)
        assert len(proposals) >= 1
        assert proposals[0].tool_name == "exec"
        assert proposals[0].status == "pending"

    def test_proposal_passes_safety_check(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        profiles = [
            ToolFailureProfile(
                tool_name="exec",
                failure_count=3,
                error_codes={"timeout": 2},
                sample_bad_inputs=[],
                current_description="Execute shell commands and return output.",
            )
        ]
        proposals = evolver.generate_proposals(profiles)
        for prop in proposals:
            assert prop.safety_check["ok"] is True

    def test_rejects_proposals_failing_safety(self, tmp_path):
        # min_semantic_preservation=1.0 means proposed must be identical to original
        evolver = SkillEvolver(tmp_path, min_semantic_preservation=1.0)
        profiles = [
            ToolFailureProfile(
                tool_name="exec",
                failure_count=3,
                error_codes={"timeout": 1},
                sample_bad_inputs=[],
                current_description="Run commands.",
            )
        ]
        proposals = evolver.generate_proposals(profiles)
        # With min=1.0, heuristic adds suffix which changes the description
        # All proposals should be filtered out (or pass if suffix is short and identical)
        for prop in proposals:
            assert prop.safety_check["ok"] is True  # only passing ones are returned


# ---------------------------------------------------------------------------
# Persistence: save / list / get
# ---------------------------------------------------------------------------

class TestPersistence:
    def _make_proposal(self, proposal_id: str = "prop_001", tool: str = "exec") -> SkillEvolutionProposal:
        import time
        return SkillEvolutionProposal(
            proposal_id=proposal_id,
            tool_name=tool,
            current_description="Execute commands.",
            proposed_description="Execute shell commands safely.",
            rationale="Added safety hint.",
            safety_check={"ok": True, "size_ok": True, "semantic_score": 0.9, "key_terms_retained": True},
            status="pending",
            created_at=time.time(),
            updated_at=time.time(),
        )

    def test_save_and_list(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        prop = self._make_proposal()
        evolver.save_proposals([prop])
        items = evolver.list_proposals()
        assert len(items) == 1
        assert items[0].proposal_id == prop.proposal_id

    def test_list_filter_by_status(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        p1 = self._make_proposal("p1")
        p2 = self._make_proposal("p2")
        evolver.save_proposals([p1, p2])
        evolver.approve_proposal("p1", actor="admin")
        pending = evolver.list_proposals(status="pending")
        approved = evolver.list_proposals(status="approved")
        assert len(pending) == 1
        assert len(approved) == 1

    def test_get_by_proposal_id(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        prop = self._make_proposal("find_me")
        evolver.save_proposals([prop])
        found = evolver.get_proposal("find_me")
        assert found is not None
        assert found.proposal_id == "find_me"

    def test_get_nonexistent_returns_none(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        assert evolver.get_proposal("does_not_exist") is None


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    def _save_pending(self, evolver: SkillEvolver, proposal_id: str = "p1") -> SkillEvolutionProposal:
        import time
        prop = SkillEvolutionProposal(
            proposal_id=proposal_id,
            tool_name="exec",
            current_description="Execute commands.",
            proposed_description="Execute shell commands safely.",
            rationale="Added hint.",
            safety_check={"ok": True, "size_ok": True, "semantic_score": 0.9, "key_terms_retained": True},
            status="pending",
            created_at=time.time(),
            updated_at=time.time(),
        )
        evolver.save_proposals([prop])
        return prop

    def test_approve_changes_status(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        self._save_pending(evolver)
        updated = evolver.approve_proposal("p1", actor="admin", note="looks good")
        assert updated is not None
        assert updated.status == "approved"
        assert updated.actor == "admin"

    def test_reject_changes_status(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        self._save_pending(evolver)
        updated = evolver.reject_proposal("p1", actor="admin", note="not needed")
        assert updated is not None
        assert updated.status == "rejected"

    def test_approve_nonexistent_returns_none(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        assert evolver.approve_proposal("nope", actor="admin") is None


# ---------------------------------------------------------------------------
# Apply proposal
# ---------------------------------------------------------------------------

class TestApplyProposal:
    def test_apply_requires_approved_status(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        import time
        prop = SkillEvolutionProposal(
            proposal_id="p_pending",
            tool_name="exec",
            current_description="Execute commands.",
            proposed_description="Execute shell commands safely.",
            rationale="hint",
            safety_check={"ok": True},
            status="pending",
            created_at=time.time(),
            updated_at=time.time(),
        )
        evolver.save_proposals([prop])
        with pytest.raises(ValueError, match="approved"):
            evolver.apply_proposal("p_pending", actor="admin")

    def test_apply_writes_config_and_marks_applied(self, tmp_path):
        import time
        import unittest.mock as mock

        evolver = SkillEvolver(tmp_path)
        prop = SkillEvolutionProposal(
            proposal_id="p_approved",
            tool_name="exec",
            current_description="Execute commands.",
            proposed_description="Execute shell commands safely. Avoid timeouts.",
            rationale="added hint",
            safety_check={"ok": True},
            status="approved",
            created_at=time.time(),
            updated_at=time.time(),
        )
        evolver.save_proposals([prop])

        set_calls: list = []

        class FakeConfig:
            def set(self, path: str, value: str) -> None:
                set_calls.append((path, value))

            def save(self) -> None:
                pass

        with mock.patch("tools.admin.state.config", FakeConfig()):
            result = evolver.apply_proposal("p_approved", actor="admin")

        assert result["applied"] is True
        assert result["tool_name"] == "exec"
        assert result["description_before"] == "Execute commands."
        assert result["description_after"] == "Execute shell commands safely. Avoid timeouts."
        assert set_calls == [("skill_overrides.exec.description", "Execute shell commands safely. Avoid timeouts.")]

        applied_prop = evolver.get_proposal("p_approved")
        assert applied_prop is not None
        assert applied_prop.status == "applied"
