from pathlib import Path

from eval.trainer import LightningLiteTrainer, TrainingJobManager


def test_training_job_manager_create_and_run(tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    created = manager.create_job(
        dataset_id="ds1",
        trajectory_samples=[
            {
                "run_id": "r1",
                "assistant_output": "error: timeout",
                "feedback": "unsafe tool wrong",
            }
        ],
        eval_samples=[{"run_id": "r1", "passed": False}],
        source="test",
    )
    assert created["status"] == "pending"
    job_id = created["job_id"]

    loaded = manager.get_job(job_id)
    assert loaded is not None
    assert loaded["job_id"] == job_id

    result = manager.run_job(job_id)
    assert result is not None
    assert result["status"] == "completed"
    output = result["output"]
    assert "prompt_patch" in output
    assert "policy_patch" in output
    assert "router_patch" in output
    assert "training_summary" in output

    listed = manager.list_jobs(limit=10, status="completed")
    assert len(listed) == 1
    assert listed[0]["job_id"] == job_id

    release = manager.create_release(
        job_id=job_id,
        actor="tester",
        note="publish test",
        before={"personality.system_prompt": "before"},
        after={"personality.system_prompt": "after"},
        dry_run=False,
        strategy_package={"version": "training_strategy_package_v1", "components": {"prompt": {"changed": True}}},
    )
    assert release["status"] == "published"
    release_id = release["release_id"]

    releases = manager.list_releases(limit=10)
    assert len(releases) == 1
    assert releases[0]["release_id"] == release_id

    fetched = manager.get_release(release_id)
    assert fetched is not None
    assert fetched["job_id"] == job_id
    assert fetched["strategy_package"]["version"] == "training_strategy_package_v1"

    rolled = manager.mark_release_rolled_back(release_id=release_id, actor="tester", note="rollback")
    assert rolled is not None
    assert rolled["status"] == "rolled_back"


def test_training_release_pending_approval_and_approve(tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    release = manager.create_release(
        job_id="job_1",
        actor="tester",
        note="needs approval",
        before={"personality.system_prompt": "before"},
        after={"personality.system_prompt": "after"},
        dry_run=False,
        rollout={"mode": "canary", "percent": 10},
        status_override="pending_approval",
        approval={"required": True, "state": "pending", "approved": False},
    )
    assert release["status"] == "pending_approval"
    assert release["approval"]["required"] is True
    assert release["approval"]["approved"] is False

    approved = manager.mark_release_approved(
        release_id=release["release_id"],
        actor="owner",
        note="approved for canary",
        status="canary",
    )
    assert approved is not None
    assert approved["status"] == "canary"
    assert approved["approval"]["approved"] is True
    assert approved["approval"]["approved_by"] == "owner"


def test_lightning_trainer_uses_trajectory_tool_failure_signals():
    trainer = LightningLiteTrainer()
    patch = trainer.generate_patch(
        trajectory_samples=[
            {
                "run_id": "r_tool",
                "assistant_output": "Error [TOOL_NOT_PERMITTED]: blocked",
                "feedback": "tool wrong",
                "events": [
                    {
                        "action": "tool_result",
                        "payload": {
                            "tool": "node_invoke",
                            "status": "error",
                            "error_code": "TOOL_NOT_PERMITTED",
                            "result_preview": "Error [TOOL_NOT_PERMITTED]: blocked",
                        },
                    }
                ],
            }
        ],
        eval_samples=[],
    )
    summary = patch["training_summary"]
    assert summary["tool_failure_count"]["node_invoke"] == 1
    assert summary["tool_error_code_count"]["tool_not_permitted"] == 1
    rules = patch["prompt_patch"]["rules"]
    assert any("policy denies a tool" in rule.lower() for rule in rules)
    assert patch["router_patch"]["strategy"] in {"priority", "latency", "cost"}


def test_bootstrap_pipeline_gate_blocked(tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    result = manager.run_bootstrap_pipeline(
        dataset_id="ds_bootstrap",
        change_set={
            "title": "Improve routing prompt",
            "summary": "Adjust fallback strategy",
            "files": ["src/agent/loop.py"],
        },
        trajectory_samples=[{"run_id": "r1", "assistant_output": "ok", "feedback": ""}],
        eval_samples=[{"run_id": "e1", "passed": False}],
        gate={"min_pass_rate": 1.0, "max_fail_count": 0},
        dry_run=False,
    )
    assert result["status"] == "gate_blocked"
    assert result["gate"]["passed"] is False
    assert result["release_id"] == ""
    runs = manager.list_bootstrap_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["pipeline_id"] == result["pipeline_id"]


def test_bootstrap_pipeline_canary_auto_rollback(tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    result = manager.run_bootstrap_pipeline(
        dataset_id="ds_bootstrap",
        change_set={
            "title": "Enable safer retries",
            "summary": "Update tool retry hints",
            "files": ["src/tools/registry.py"],
        },
        trajectory_samples=[{"run_id": "r2", "assistant_output": "ok", "feedback": ""}],
        eval_samples=[{"run_id": "e2", "passed": True}],
        gate={"min_pass_rate": 0.5, "max_fail_count": 1, "auto_rollback_on_canary_fail": True},
        rollout={"mode": "canary", "percent": 10},
        canary_health={"passed": False, "reason": "high_error_rate"},
        dry_run=False,
        actor="tester",
    )
    assert result["status"] == "rolled_back"
    assert result["release_id"]
    assert result["rollback_release_id"] == result["release_id"]
    release = manager.get_release(result["release_id"])
    assert release is not None
    assert release["status"] == "rolled_back"
