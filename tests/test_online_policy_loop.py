from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from eval.benchmark import EvalBenchmarkManager
from eval.online_policy_loop import OnlinePolicyLoopManager
from eval.training_bridge import TrainingBridgeManager
from eval.trainer import TrainingJobManager
from tools.admin import api_facade as admin_api


def _trajectory(run_id: str, *, ok: bool = True) -> Dict[str, Any]:
    status = "ok" if ok else "error"
    final_status = "done" if ok else "llm_error"
    label = "positive" if ok else "negative"
    feedback = "good answer" if ok else "unsafe output"
    error_code = "" if ok else "WEB_FETCH_FAILED"
    return {
        "run_id": run_id,
        "meta": {
            "session_key": "web-main",
            "channel": "web",
            "chat_id": "chat-main",
            "user_content": "task",
        },
        "events": [
            {
                "ts": 10.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {"tool": "web_fetch", "tool_call_id": f"{run_id}_call", "args_hash": run_id},
            },
            {
                "ts": 11.0,
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": f"{run_id}_call",
                    "status": status,
                    "error_code": error_code,
                    "result_preview": status,
                },
            },
        ],
        "feedback": [{"label": label, "feedback": feedback}],
        "final": {"status": final_status, "final_content": status, "metrics": {"turn_latency_ms": 900.0}},
    }


class _FakeConfig:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in str(path).split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set_many(self, patch: Dict[str, Any]) -> None:
        for path, value in patch.items():
            keys = str(path).split(".")
            node = self.data
            for key in keys[:-1]:
                current = node.get(key)
                if not isinstance(current, dict):
                    current = {}
                    node[key] = current
                node = current
            node[keys[-1]] = value

    def save(self) -> None:
        return None


def test_online_policy_loop_manager_review_gate_and_publish(tmp_path: Path) -> None:
    base_dir = tmp_path / "eval"
    bridge = TrainingBridgeManager(base_dir=base_dir)
    trainer = TrainingJobManager(base_dir=base_dir)
    eval_manager = EvalBenchmarkManager(base_dir=base_dir)
    manager = OnlinePolicyLoopManager(base_dir=base_dir)

    export = bridge.create_export(
        dataset_id="ds_online",
        trajectories=[_trajectory("run_online_ok", ok=True)],
        source="test",
        eval_by_run={"run_online_ok": {"run_id": "run_online_ok", "passed": True, "score": 0.9}},
    )
    candidate = manager.create_candidate_from_bridge(
        bridge_manager=bridge,
        training_manager=trainer,
        eval_manager=eval_manager,
        dataset_id="ds_online",
        export_id=str(export.get("export_id", "")),
        source="test_online",
    )
    candidate_id = str(candidate.get("candidate_id", ""))
    assert candidate.get("status") == "pending_review"

    checked = manager.run_gate_check(candidate_id=candidate_id, gate_status=eval_manager.get_release_gate_status())
    assert checked["gate_check"]["passed"] is True

    reviewed = manager.review_candidate(
        candidate_id=candidate_id,
        approved=True,
        reviewer="owner",
        note="looks good",
    )
    assert reviewed["status"] == "approved"

    published = manager.mark_published(
        candidate_id=candidate_id,
        actor="owner",
        note="dry run publish",
        release_id="rel_test",
        dry_run=True,
    )
    assert published["status"] == "dry_run"
    assert published["publish"]["release_id"] == "rel_test"

    listed = manager.list_candidates(limit=10, dataset_id="ds_online")
    assert len(listed) == 1
    assert listed[0]["candidate_id"] == candidate_id


@pytest.mark.asyncio
async def test_online_policy_loop_admin_api_enforces_gate_before_publish(monkeypatch, tmp_path: Path):
    base_dir = tmp_path / "eval"
    bridge = TrainingBridgeManager(base_dir=base_dir)
    trainer = TrainingJobManager(base_dir=base_dir)
    eval_manager = EvalBenchmarkManager(base_dir=base_dir)
    loop_manager = OnlinePolicyLoopManager(base_dir=base_dir)

    export = bridge.create_export(
        dataset_id="ds_online_api",
        trajectories=[_trajectory("run_online_api_ok", ok=True)],
        source="test",
        eval_by_run={"run_online_api_ok": {"run_id": "run_online_api_ok", "passed": True, "score": 0.93}},
    )
    export_id = str(export.get("export_id", ""))

    fake_cfg = _FakeConfig(
        {
            "personality": {"system_prompt": "You are Gazer."},
            "security": {"tool_denylist": [], "tool_max_tier": "standard"},
            "models": {
                "router": {
                    "strategy": "priority",
                    "strategy_template": "balanced",
                    "budget": {},
                    "outlier_ejection": {},
                }
            },
            "trainer": {
                "online_policy_loop": {
                    "enabled": True,
                    "require_review": True,
                    "gate": {
                        "require_release_gate_open": True,
                        "min_eval_pass_rate": 0.5,
                        "min_trajectory_success_rate": 0.5,
                        "max_terminal_error_rate": 0.8,
                    },
                }
            },
        }
    )

    monkeypatch.setattr(admin_api, "_get_training_bridge_manager", lambda: bridge)
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: trainer)
    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: eval_manager)
    monkeypatch.setattr(admin_api, "_get_online_policy_loop_manager", lambda: loop_manager)
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    created = await admin_api.create_online_policy_candidate(
        {"dataset_id": "ds_online_api", "export_id": export_id, "auto_gate_check": True}
    )
    assert created["status"] == "ok"
    candidate_id = created["candidate"]["candidate_id"]

    reviewed = await admin_api.review_online_policy_candidate(
        candidate_id,
        {"approved": True, "reviewer": "owner", "note": "approved"},
    )
    assert reviewed["status"] == "ok"
    assert reviewed["candidate"]["review"]["approved"] is True

    eval_manager.set_release_gate_status(
        blocked=True,
        reason="quality_gate_blocked",
        source="unit_test",
        metadata={},
    )
    with pytest.raises(HTTPException, match="gate check failed"):
        await admin_api.publish_online_policy_candidate(candidate_id, {"dry_run": True, "actor": "owner"})

    eval_manager.set_release_gate_status(
        blocked=False,
        reason="quality_gate_passed",
        source="unit_test",
        metadata={},
    )
    published = await admin_api.publish_online_policy_candidate(candidate_id, {"dry_run": True, "actor": "owner"})
    assert published["status"] == "ok"
    assert published["candidate"]["publish"]["state"] == "dry_run"


@pytest.mark.asyncio
async def test_online_policy_offpolicy_eval_endpoint(monkeypatch, tmp_path: Path):
    base_dir = tmp_path / "eval"
    bridge = TrainingBridgeManager(base_dir=base_dir)
    trainer = TrainingJobManager(base_dir=base_dir)
    eval_manager = EvalBenchmarkManager(base_dir=base_dir)
    loop_manager = OnlinePolicyLoopManager(base_dir=base_dir)

    baseline_export = bridge.create_export(
        dataset_id="ds_online_offpolicy",
        trajectories=[_trajectory("run_online_off_base", ok=False)],
        source="test",
        eval_by_run={"run_online_off_base": {"run_id": "run_online_off_base", "passed": False, "score": 0.15}},
    )
    candidate_export = bridge.create_export(
        dataset_id="ds_online_offpolicy",
        trajectories=[_trajectory("run_online_off_candidate", ok=True)],
        source="test",
        eval_by_run={"run_online_off_candidate": {"run_id": "run_online_off_candidate", "passed": True, "score": 0.92}},
    )
    _ = baseline_export

    fake_cfg = _FakeConfig(
        {
            "trainer": {
                "online_policy_loop": {
                    "enabled": True,
                    "require_review": True,
                    "gate": {
                        "require_release_gate_open": True,
                        "min_eval_pass_rate": 0.5,
                        "min_trajectory_success_rate": 0.5,
                        "max_terminal_error_rate": 0.8,
                    },
                    "offpolicy": {
                        "enabled": True,
                        "auto_run_on_create": False,
                        "baseline_index": 1,
                        "bootstrap_rounds": 80,
                        "min_reward_threshold": 0.6,
                        "min_samples_for_confidence": 5,
                    },
                }
            },
            "models": {"router": {"strategy": "priority", "budget": {}, "outlier_ejection": {}}},
        }
    )
    monkeypatch.setattr(admin_api, "_get_training_bridge_manager", lambda: bridge)
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: trainer)
    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: eval_manager)
    monkeypatch.setattr(admin_api, "_get_online_policy_loop_manager", lambda: loop_manager)
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    created = await admin_api.create_online_policy_candidate(
        {
            "dataset_id": "ds_online_offpolicy",
            "export_id": str(candidate_export.get("export_id", "")),
            "auto_gate_check": False,
            "auto_offpolicy_eval": False,
        }
    )
    candidate_id = str(created["candidate"]["candidate_id"])

    evaluated = await admin_api.run_online_policy_offpolicy_eval(
        candidate_id,
        {"offpolicy": {"bootstrap_rounds": 80, "baseline_index": 1, "min_samples_for_confidence": 5}},
    )
    assert evaluated["status"] == "ok"
    offpolicy = evaluated["candidate"]["offpolicy_eval"]
    assert offpolicy["state"] == "evaluated"
    assert offpolicy["reward_estimate"]["candidate_expected_reward"] is not None
    assert offpolicy["risk_interval"]["confidence_level"] is not None

    stored = loop_manager.get_candidate(candidate_id)
    assert isinstance(stored, dict)
    assert (stored.get("offpolicy_eval", {}) or {}).get("state") == "evaluated"
