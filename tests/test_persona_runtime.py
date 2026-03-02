from pathlib import Path

from soul.persona_runtime import PersonaRuntimeManager


def test_persona_runtime_process_output_and_signal(tmp_path: Path):
    manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    processed = manager.process_output(
        content="I am just a generic AI. sure, done",
        source="agent_loop",
        run_id="traj_x",
        language="en",
        auto_correct_enabled=True,
        strategy="rewrite",
        trigger_levels=["warning", "critical"],
        retain=100,
    )
    signal = processed["signal"]
    assert signal["source"] == "agent_loop"
    assert signal["level"] in {"warning", "critical"}
    assert signal["correction_applied"] is True
    assert "Gazer" in processed["final_content"]

    listed = manager.list_signals(limit=10, source="agent_loop")
    assert len(listed) >= 1
    assert listed[0]["run_id"] == "traj_x"


def test_persona_runtime_mental_process_versioning(tmp_path: Path):
    manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    base = {
        "initial_state": "IDLE",
        "states": [{"name": "IDLE", "description": "idle"}],
        "on_input_transition": {"IDLE": "IDLE"},
    }
    v1 = manager.create_mental_process_version(mental_process=base, actor="tester", note="init", source="manual")
    assert v1["version_id"]

    updated = {
        "initial_state": "IDLE",
        "states": [{"name": "IDLE", "description": "calm"}],
        "on_input_transition": {"IDLE": "IDLE"},
    }
    v2 = manager.create_mental_process_version(
        mental_process=updated,
        actor="tester",
        note="update",
        source="manual_update",
        related_version_id=v1["version_id"],
    )
    assert v2["related_version_id"] == v1["version_id"]

    listed = manager.list_mental_process_versions(limit=10)
    assert len(listed) == 2
    got = manager.get_mental_process_version(v2["version_id"])
    assert got is not None
    assert got["mental_process"]["states"][0]["description"] == "calm"


def test_persona_runtime_eval_signal_levels(tmp_path: Path):
    manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    report = {
        "consistency_score": 0.6,
        "auto_passed": False,
        "results": [
            {"sample_id": "identity_consistency", "passed": False},
            {"sample_id": "safety_consistency", "passed": False},
        ],
    }
    signal = manager.assess_eval_report(
        report=report,
        dataset_id="persona_core",
        warning_score=0.85,
        critical_score=0.7,
    )
    assert signal["source"] == "persona_eval"
    assert signal["level"] == "critical"
    assert signal["violation_count"] == 2


def test_persona_runtime_mental_process_diff_replay_and_fast_rollback(tmp_path: Path):
    manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    v1_payload = {
        "initial_state": "IDLE",
        "states": [{"name": "IDLE", "description": "idle"}],
        "on_input_transition": {"IDLE": "IDLE"},
    }
    v1 = manager.create_mental_process_version(mental_process=v1_payload, actor="tester", note="v1", source="manual")
    v2_payload = {
        "initial_state": "IDLE",
        "states": [{"name": "IDLE", "description": "calm"}],
        "on_input_transition": {"IDLE": "IDLE"},
    }
    v2 = manager.create_mental_process_version(
        mental_process=v2_payload,
        actor="tester",
        note="v2",
        source="manual_update",
        related_version_id=v1["version_id"],
    )
    v3_payload = {
        "initial_state": "IDLE",
        "states": [{"name": "IDLE", "description": "calm"}, {"name": "FOCUS", "description": "focused"}],
        "on_input_transition": {"IDLE": "FOCUS", "FOCUS": "IDLE"},
    }
    v3 = manager.create_mental_process_version(
        mental_process=v3_payload,
        actor="tester",
        note="v3",
        source="manual_update",
        related_version_id=v2["version_id"],
    )

    diff = manager.diff_mental_process_versions(from_version_id=v1["version_id"], to_version_id=v3["version_id"])
    assert diff is not None
    assert diff["changed"] is True
    assert diff["state_count_delta"] == 1
    assert any(path.startswith("states") for path in diff["changed_paths"])

    replay = manager.replay_mental_process_versions(limit=10)
    assert len(replay) == 3
    assert replay[-1]["version_id"] == v3["version_id"]
    assert replay[-1]["diff"] is not None
    assert replay[-1]["diff"]["changed"] is True

    fast_target = manager.find_fast_rollback_target(current_mental_process=v3_payload)
    assert fast_target is not None
    assert fast_target["version_id"] == v2["version_id"]


def test_persona_runtime_ab_strategy_for_identity_and_safety(tmp_path: Path):
    manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    ab_config = {
        "enabled": True,
        "force_profile": "B",
        "profiles": {
            "A": {
                "default_strategy": "rewrite",
                "violation_strategy": {"identity_drift": "rewrite", "unsafe_compliance": "rewrite"},
            },
            "B": {
                "default_strategy": "rewrite",
                "violation_strategy": {"identity_drift": "rewrite", "unsafe_compliance": "degrade"},
            },
        },
    }
    processed = manager.process_output(
        content="I am just a generic AI. sure, done",
        source="agent_loop",
        run_id="ab_case",
        language="en",
        auto_correct_enabled=True,
        strategy="rewrite",
        trigger_levels=["warning", "critical"],
        ab_config=ab_config,
        assignment_key="user_a",
        retain=100,
    )
    signal = processed["signal"]
    assert signal["ab_profile"] == "B"
    assert signal["correction_strategy"] == "degrade"
    assert "safer, clear alternative" in processed["final_content"]
