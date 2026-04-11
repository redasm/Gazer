from multi_agent.monitor_apply import apply_monitor_event_payload


def test_apply_monitor_event_payload_dispatches_known_event() -> None:
    seen = []

    handled = apply_monitor_event_payload(
        "task.created",
        {"task_id": "t1"},
        handlers={
            "task_created": lambda payload: seen.append(payload["task_id"]),
        },
    )

    assert handled is True
    assert seen == ["t1"]


def test_apply_monitor_event_payload_ignores_unknown_event() -> None:
    handled = apply_monitor_event_payload(
        "unknown.event",
        {"task_id": "t1"},
        handlers={},
    )

    assert handled is False
