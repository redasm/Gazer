from pathlib import Path

from eval.training_bridge import TrainingBridgeManager


def _trajectory_payload(run_id: str, *, tool_status: str, feedback_label: str, passed: bool):
    return {
        "run_id": run_id,
        "meta": {
            "session_key": "web-main",
            "channel": "web",
            "chat_id": "chat-main",
            "sender_id": "u1",
            "user_content": f"task {run_id}",
        },
        "events": [
            {
                "ts": 100.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": f"tc_{run_id}",
                    "args_hash": "abcd",
                    "args_preview": '{"url":"https://example.com"}',
                },
            },
            {
                "ts": 101.0,
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": f"tc_{run_id}",
                    "status": tool_status,
                    "error_code": "" if tool_status == "ok" else "WEB_FETCH_FAILED",
                    "result_preview": "ok" if tool_status == "ok" else "failed",
                },
            },
        ],
        "feedback": [
            {
                "label": feedback_label,
                "feedback": "good" if feedback_label == "positive" else "unsafe tool wrong",
            }
        ],
        "final": {
            "status": "done" if passed else "llm_error",
            "final_content": "done" if passed else "failed",
            "metrics": {"turn_latency_ms": 1234.0},
        },
    }


def test_training_bridge_export_deterministic(tmp_path: Path):
    manager = TrainingBridgeManager(base_dir=tmp_path / "eval")
    traj_a = _trajectory_payload("traj_a", tool_status="error", feedback_label="negative", passed=False)
    traj_b = _trajectory_payload("traj_b", tool_status="ok", feedback_label="positive", passed=True)
    eval_map = {
        "traj_a": {"run_id": "traj_a", "passed": False, "score": 0.2, "consistency_score": 0.41},
        "traj_b": {"run_id": "traj_b", "passed": True, "score": 0.9, "consistency_score": 0.88},
    }

    first = manager.create_export(
        dataset_id="ds_bridge",
        trajectories=[traj_b, traj_a],
        source="test",
        eval_by_run=eval_map,
        release_gate={"blocked": False, "source": "eval:ds_bridge"},
    )
    second = manager.create_export(
        dataset_id="ds_bridge",
        trajectories=[traj_a, traj_b],
        source="test",
        eval_by_run=eval_map,
        release_gate={"blocked": False, "source": "eval:ds_bridge"},
    )

    assert first["sample_count"] == 2
    assert first["fingerprint"] == second["fingerprint"]
    assert first["summary"]["offline_policy_eval"]["trajectory_success_rate"] is not None
    assert first["summary"]["offline_policy_eval"]["persona_consistency_score_avg"] is not None
    assert "WEB_FETCH_FAILED" in first["summary"]["offline_policy_eval"]["tool_failure_types"]

    compare = manager.compare_with_baseline("ds_bridge", baseline_index=1)
    assert compare is not None
    assert compare["sample_delta"] == 0
    assert compare["fingerprint_changed"] is False

    inputs = manager.to_training_inputs(first["export_id"])
    assert inputs is not None
    assert len(inputs["trajectory_samples"]) == 2
    assert len(inputs["eval_samples"]) == 2
    assert inputs["trajectory_samples"][0]["events"][0]["action"] == "tool_result"


def test_training_bridge_compare_exports(tmp_path: Path):
    manager = TrainingBridgeManager(base_dir=tmp_path / "eval")
    traj_a = _trajectory_payload("traj_a", tool_status="error", feedback_label="negative", passed=False)
    traj_b = _trajectory_payload("traj_b", tool_status="ok", feedback_label="positive", passed=True)

    baseline = manager.create_export(
        dataset_id="ds_compare",
        trajectories=[traj_a],
        source="test",
        eval_by_run={"traj_a": {"run_id": "traj_a", "passed": False, "score": 0.1}},
    )
    candidate = manager.create_export(
        dataset_id="ds_compare",
        trajectories=[traj_a, traj_b],
        source="test",
        eval_by_run={
            "traj_a": {"run_id": "traj_a", "passed": False, "score": 0.1},
            "traj_b": {"run_id": "traj_b", "passed": True, "score": 0.95},
        },
    )

    compare = manager.compare_exports(
        candidate_export_id=candidate["export_id"],
        baseline_export_id=baseline["export_id"],
    )
    assert compare is not None
    assert compare["sample_delta"] == 1
    assert "traj_b" in compare["added_runs"]
    assert compare["reward_proxy_delta"]["avg_tool_success_rate"] is not None
    assert "WEB_FETCH_FAILED" in compare["reward_proxy_delta"]["tool_failure_types"]
    assert compare["reward_proxy_delta"]["trajectory_success_rate"] is not None
