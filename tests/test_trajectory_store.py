from pathlib import Path

from agent.trajectory import TrajectoryStore


def test_trajectory_store_roundtrip(tmp_path: Path):
    store = TrajectoryStore(base_dir=tmp_path / "trajectories")
    run_id = store.start(
        session_key="web:web-main",
        channel="web",
        chat_id="web-main",
        sender_id="u1",
        user_content="hello",
    )
    store.add_event(
        run_id,
        stage="think",
        action="llm_request",
        payload={"iteration": 1, "tool_count": 3},
    )
    store.finalize(
        run_id,
        status="success",
        final_content="done",
        usage={"total_tokens": 5},
        metrics={"iterations": 1},
    )

    payload = store.get_trajectory(run_id)
    assert payload is not None
    assert payload["run_id"] == run_id
    assert payload["meta"]["session_key"] == "web:web-main"
    assert payload["event_count"] == 1
    assert payload["final"]["status"] == "success"

    recent = store.list_recent(limit=5)
    assert len(recent) == 1
    assert recent[0]["run_id"] == run_id


def test_trajectory_store_feedback_samples(tmp_path: Path):
    store = TrajectoryStore(base_dir=tmp_path / "trajectories")
    run_id = store.start(
        session_key="web:web-main",
        channel="web",
        chat_id="web-main",
        sender_id="u1",
        user_content="summarize this",
    )
    store.add_event(run_id, stage="think", action="llm_request", payload={"iteration": 1})
    store.finalize(run_id, status="success", final_content="summary ok")
    attached = store.add_feedback(
        run_id,
        label="negative",
        feedback="summary missed details",
        context="review_panel",
        metadata={"source": "manual"},
    )
    assert attached is True

    payload = store.get_trajectory(run_id)
    assert payload is not None
    assert len(payload["feedback"]) == 1
    assert payload["feedback"][0]["label"] == "negative"

    samples = store.list_feedback_samples(limit=10, label="negative")
    assert len(samples) == 1
    assert samples[0]["run_id"] == run_id
    assert samples[0]["assistant_output"] == "summary ok"
