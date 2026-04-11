from multi_agent.monitor_state import (
    apply_monitor_log_entry,
    apply_monitor_session_init,
    apply_monitor_task_comment,
    apply_monitor_task_completed,
    apply_monitor_task_created,
    apply_monitor_task_failed,
    apply_monitor_task_status,
    apply_monitor_task_tool_call,
    ensure_monitor_session,
    ensure_monitor_task,
    resolve_monitor_session_key,
)


def test_ensure_monitor_session_creates_default_session() -> None:
    sessions = {}
    session = ensure_monitor_session(sessions, "sess-1")

    assert session["session_key"] == "sess-1"
    assert session["tasks"] == {}
    assert session["logs"] == []


def test_resolve_monitor_session_key_prefers_task_index_lookup() -> None:
    session_key = resolve_monitor_session_key({"task_id": "t1"}, task_index={"t1": "sess-1"})
    assert session_key == "sess-1"


def test_ensure_monitor_task_creates_placeholder_task() -> None:
    sessions = {}
    task_index = {}
    task = ensure_monitor_task(
        sessions,
        task_index,
        {"session_key": "sess-1", "task_id": "t1", "agent_id": "worker-1"},
    )

    assert task is not None
    assert task["task_id"] == "t1"
    assert task_index["t1"] == "sess-1"


def test_apply_monitor_task_lifecycle_mutates_state() -> None:
    sessions = {}
    task_index = {}

    latest = apply_monitor_session_init(
        sessions,
        task_index,
        payload={"session_key": "sess-1", "session_label": "Session", "tasks": [], "logs": []},
        latest_session_key=None,
        max_logs_per_session=10,
    )
    assert latest == "sess-1"

    created = apply_monitor_task_created(
        sessions,
        task_index,
        payload={"session_key": "sess-1", "task_id": "t1", "title": "Task", "description": "", "agent_id": "worker-1", "depends": [], "priority": "normal"},
    )
    assert created == "sess-1"

    apply_monitor_task_status(sessions, task_index, {"task_id": "t1", "status": "running", "tool_calls": 1})
    apply_monitor_task_tool_call(sessions, task_index, {"task_id": "t1", "tool_name": "web_search", "tool_call_index": 2})
    apply_monitor_task_completed(sessions, task_index, {"task_id": "t1", "result_summary": "done", "ended_at": 1.0})

    task = sessions["sess-1"]["tasks"]["t1"]
    assert task["status"] == "completed"
    assert task["current_tool"] == "web_search"
    assert task["tool_calls"] == 2
    assert task["result_summary"] == "done"


def test_apply_monitor_task_comment_and_log_entry_keep_limits() -> None:
    sessions = {"sess-1": {"session_key": "sess-1", "session_label": "Session", "tasks": {"t1": {"task_id": "t1", "comments": []}}, "logs": []}}
    task_index = {"t1": "sess-1"}

    apply_monitor_task_comment(sessions, task_index, {"task_id": "t1", "text": "hello"}, max_comments_per_task=1)
    apply_monitor_task_comment(sessions, task_index, {"task_id": "t1", "text": "latest"}, max_comments_per_task=1)
    apply_monitor_log_entry(sessions, task_index, {"session_key": "sess-1", "message": "first"}, max_logs_per_session=1)
    apply_monitor_log_entry(sessions, task_index, {"session_key": "sess-1", "message": "second"}, max_logs_per_session=1)

    assert sessions["sess-1"]["tasks"]["t1"]["comments"][0]["text"] == "latest"
    assert sessions["sess-1"]["logs"][0]["message"] == "second"


def test_apply_monitor_task_failed_sets_error() -> None:
    sessions = {"sess-1": {"session_key": "sess-1", "session_label": "Session", "tasks": {}, "logs": []}}
    task_index = {}

    apply_monitor_task_failed(sessions, task_index, {"session_key": "sess-1", "task_id": "t2", "error": "boom"})

    assert sessions["sess-1"]["tasks"]["t2"]["status"] == "failed"
    assert sessions["sess-1"]["tasks"]["t2"]["error"] == "boom"
