from pathlib import Path

from eval.persona_consistency import PersonaConsistencyManager


def test_persona_consistency_dataset_and_run(tmp_path: Path):
    manager = PersonaConsistencyManager(base_dir=tmp_path / "persona")
    dataset = manager.build_dataset(name="default", system_prompt="You are Gazer.")
    dataset_id = dataset["id"]
    assert len(dataset["samples"]) >= 3

    loaded = manager.get_dataset(dataset_id)
    assert loaded is not None
    assert loaded["id"] == dataset_id

    report = manager.run_dataset(
        dataset_id,
        outputs={
            "tone_warm": "Good morning, I'm Gazer.",
            "identity_consistency": "I am Gazer, your AI companion.",
            "safety_consistency": "I can't do that unsafe request, here's a safer option.",
        },
    )
    assert report is not None
    assert report["sample_count"] >= 3
    assert report["consistency_score"] >= 0.8
    assert report["auto_passed"] is True

    runs = manager.list_runs(dataset_id, limit=10)
    assert len(runs) == 1
    assert runs[0]["dataset_id"] == dataset_id

    latest = manager.get_latest_run(dataset_id)
    assert latest is not None
    assert latest["dataset_id"] == dataset_id

    auto_outputs = manager.generate_outputs(dataset_id, system_prompt="You are Gazer.")
    assert auto_outputs is not None
    assert "tone_warm" in auto_outputs
