from pathlib import Path

from eval.benchmark import EvalBenchmarkManager


def test_eval_benchmark_build_and_run(tmp_path: Path):
    manager = EvalBenchmarkManager(base_dir=tmp_path / "eval")
    dataset = manager.build_dataset(
        name="regression_set",
        samples=[
            {
                "run_id": "r1",
                "label": "positive",
                "user_content": "summarize this",
                "assistant_output": "clean concise summary",
                "feedback": "great",
                "context": "chat",
                "status": "success",
            },
            {
                "run_id": "r2",
                "label": "negative",
                "user_content": "write secure command",
                "assistant_output": "dangerous command output",
                "feedback": "unsafe",
                "context": "chat",
                "status": "success",
            },
        ],
    )
    assert dataset["sample_count"] == 2

    listed = manager.list_datasets(limit=10)
    assert len(listed) == 1
    assert listed[0]["id"] == dataset["id"]

    loaded = manager.get_dataset(dataset["id"])
    assert loaded is not None
    assert loaded["id"] == dataset["id"]

    report = manager.run_dataset(
        dataset["id"],
        outputs={
            "r1": "clean concise summary",
            "r2": "fixed safer approach",
        },
        gate={"min_composite_score": 0.1, "min_pass_rate": 0.1, "max_error_rate": 1.0},
    )
    assert report is not None
    assert report["sample_count"] == 2
    assert "composite_score" in report
    assert report["composite_score"] >= 0
    assert "quality_gate" in report
    assert report["quality_gate"]["passed"] is True

    # Run again to build history for baseline comparison
    report2 = manager.run_dataset(
        dataset["id"],
        outputs={
            "r1": "degraded summary",
            "r2": "dangerous command output",
        },
    )
    assert report2 is not None

    runs = manager.list_runs(dataset["id"], limit=10)
    assert len(runs) == 2

    latest = manager.get_latest_run(dataset["id"])
    assert latest is not None
    assert latest["dataset_id"] == dataset["id"]

    comparison = manager.compare_with_baseline(dataset["id"], baseline_index=1)
    assert comparison is not None
    assert comparison["dataset_id"] == dataset["id"]
    assert "delta" in comparison

    gate_eval = manager.evaluate_gate(
        dataset["id"],
        gate={"min_composite_score": 0.99, "min_pass_rate": 0.99, "max_error_rate": 0.0},
        run_index=0,
    )
    assert gate_eval is not None
    assert gate_eval["gate"]["blocked"] in (True, False)

    gate_state = manager.set_release_gate_status(
        blocked=True,
        reason="quality_gate_blocked",
        source=f"eval:{dataset['id']}",
        metadata={"dataset_id": dataset["id"]},
    )
    assert gate_state["blocked"] is True
    loaded_gate = manager.get_release_gate_status()
    assert loaded_gate["blocked"] is True
    assert loaded_gate["source"] == f"eval:{dataset['id']}"


def test_eval_benchmark_creates_optimization_task_on_fail_streak(tmp_path: Path):
    manager = EvalBenchmarkManager(base_dir=tmp_path / "eval")
    dataset = manager.build_dataset(
        name="fail_streak_set",
        samples=[
            {
                "run_id": "x1",
                "label": "positive",
                "user_content": "do x",
                "assistant_output": "ok",
                "feedback": "bad",
                "context": "chat",
                "status": "success",
            }
        ],
    )
    dataset_id = dataset["id"]

    fail_report = manager.run_dataset(
        dataset_id,
        outputs={"x1": "error: failed badly"},
        gate={"min_composite_score": 0.99, "min_pass_rate": 0.99, "max_error_rate": 0.0},
    )
    assert fail_report is not None
    first = manager.register_gate_result(dataset_id, fail_report, fail_streak_threshold=2)
    assert first["task_created"] is False
    assert first["fail_streak"] == 1

    second_report = manager.run_dataset(
        dataset_id,
        outputs={"x1": "error: failed again"},
        gate={"min_composite_score": 0.99, "min_pass_rate": 0.99, "max_error_rate": 0.0},
    )
    assert second_report is not None
    second = manager.register_gate_result(dataset_id, second_report, fail_streak_threshold=2)
    assert second["task_created"] is True
    task = second["task"]
    assert task is not None
    task_id = task["task_id"]

    streaks = manager.get_gate_streaks(limit=10, dataset_id=dataset_id)
    assert len(streaks) == 1
    assert streaks[0]["dataset_id"] == dataset_id
    assert streaks[0]["fail_streak"] >= 2

    listed = manager.list_optimization_tasks(limit=10, status="open", dataset_id=dataset_id)
    assert len(listed) == 1
    assert listed[0]["task_id"] == task_id

    updated = manager.set_optimization_task_status(task_id=task_id, status="resolved", note="fixed prompt")
    assert updated is not None
    assert updated["status"] == "resolved"
