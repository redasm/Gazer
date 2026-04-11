from multi_agent.monitor_payloads import (
    DEFAULT_EMPTY_SESSION_LABEL,
    build_monitor_event,
    build_task_payload,
    copy_session_payload,
)


def test_build_monitor_event_copies_payload() -> None:
    payload = {"task_id": "t1", "nested": {"count": 1}}
    event = build_monitor_event("task.created", payload)
    payload["nested"]["count"] = 2

    assert event["event"] == "task.created"
    assert event["payload"]["nested"]["count"] == 1


def test_copy_session_payload_preserves_default_label() -> None:
    payload = copy_session_payload("sess-1", {"tasks": {"t1": {"task_id": "t1"}}, "logs": []})

    assert payload["session_key"] == "sess-1"
    assert payload["session_label"] == DEFAULT_EMPTY_SESSION_LABEL
    assert payload["tasks"][0]["task_id"] == "t1"


def test_build_task_payload_sets_monitor_defaults() -> None:
    task = build_task_payload(
        session_key="sess-1",
        task_id="t1",
        title="Task",
        description="Desc",
        agent_id="worker-1",
        depends=["t0"],
        priority="high",
    )

    assert task["status"] == "queued"
    assert task["comments"] == []
    assert task["depends"] == ["t0"]
