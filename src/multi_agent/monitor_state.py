from __future__ import annotations

import copy
import time
from typing import Any

from multi_agent.monitor_payloads import DEFAULT_EMPTY_SESSION_LABEL, build_task_payload


def ensure_monitor_session(
    sessions: dict[str, dict[str, Any]],
    session_key: str,
    *,
    session_label: str | None = None,
) -> dict[str, Any]:
    key = str(session_key or "").strip()
    if key not in sessions:
        sessions[key] = {
            "session_key": key,
            "session_label": session_label or DEFAULT_EMPTY_SESSION_LABEL,
            "tasks": {},
            "logs": [],
        }
    elif session_label:
        sessions[key]["session_label"] = session_label
    return sessions[key]


def resolve_monitor_session_key(
    payload: dict[str, Any],
    *,
    task_index: dict[str, str],
) -> str:
    session_key = str(payload.get("session_key", "")).strip()
    if session_key:
        return session_key
    task_id = str(payload.get("task_id", "")).strip()
    if task_id:
        return task_index.get(task_id, "")
    return ""


def ensure_monitor_task(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    session_key = resolve_monitor_session_key(payload, task_index=task_index)
    task_id = str(payload.get("task_id", "")).strip()
    if not session_key or not task_id:
        return None
    session = ensure_monitor_session(sessions, session_key)
    task = session["tasks"].get(task_id)
    if task is None:
        task = build_task_payload(
            session_key=session_key,
            task_id=task_id,
            title=task_id,
            description="",
            agent_id=str(payload.get("agent_id", "")).strip(),
            depends=[],
            priority="normal",
        )
        session["tasks"][task_id] = task
        task_index[task_id] = session_key
    return task


def apply_monitor_session_init(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    *,
    payload: dict[str, Any],
    latest_session_key: str | None,
    max_logs_per_session: int,
) -> str | None:
    session_key = str(payload.get("session_key", "")).strip()
    if not session_key:
        session_key = latest_session_key or ""
    session = ensure_monitor_session(
        sessions,
        session_key,
        session_label=str(payload.get("session_label", "")).strip() or DEFAULT_EMPTY_SESSION_LABEL,
    )
    session["session_label"] = str(payload.get("session_label", "")).strip() or DEFAULT_EMPTY_SESSION_LABEL
    raw_tasks = payload.get("tasks", [])
    raw_logs = payload.get("logs", [])
    session["tasks"] = {}
    session["logs"] = []

    if isinstance(raw_tasks, list):
        for raw_task in raw_tasks:
            if not isinstance(raw_task, dict):
                continue
            task_id = str(raw_task.get("task_id", "")).strip()
            if not task_id:
                continue
            task = build_task_payload(
                session_key=session_key,
                task_id=task_id,
                title=str(raw_task.get("title", "")).strip() or task_id,
                description=str(raw_task.get("description", "")).strip(),
                agent_id=str(raw_task.get("agent_id", "")).strip(),
                depends=raw_task.get("depends", []) if isinstance(raw_task.get("depends", []), list) else [],
                priority=str(raw_task.get("priority", "normal")).strip().lower() or "normal",
            )
            task.update(copy.deepcopy(raw_task))
            task["session_key"] = session_key
            task.setdefault("comments", [])
            session["tasks"][task_id] = task
            task_index[task_id] = session_key

    if isinstance(raw_logs, list):
        session["logs"] = [copy.deepcopy(entry) for entry in raw_logs if isinstance(entry, dict)][-max_logs_per_session:]

    return session_key or latest_session_key


def apply_monitor_task_created(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    *,
    payload: dict[str, Any],
) -> str | None:
    session_key = str(payload.get("session_key", "")).strip()
    task_id = str(payload.get("task_id", "")).strip()
    if not session_key or not task_id:
        return None
    session = ensure_monitor_session(sessions, session_key)
    task = build_task_payload(
        session_key=session_key,
        task_id=task_id,
        title=str(payload.get("title", "")).strip() or task_id,
        description=str(payload.get("description", "")).strip(),
        agent_id=str(payload.get("agent_id", "")).strip(),
        depends=payload.get("depends", []) if isinstance(payload.get("depends", []), list) else [],
        priority=str(payload.get("priority", "normal")).strip().lower() or "normal",
    )
    session["tasks"][task_id] = task
    task_index[task_id] = session_key
    return session_key


def apply_monitor_task_status(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    payload: dict[str, Any],
) -> None:
    task = ensure_monitor_task(sessions, task_index, payload)
    if task is None:
        return
    task["status"] = str(payload.get("status", task.get("status", "queued"))).strip().lower() or task.get("status", "queued")
    if payload.get("agent_id") is not None:
        task["agent_id"] = str(payload.get("agent_id", "")).strip()
    if "current_tool" in payload:
        task["current_tool"] = payload.get("current_tool")
    if payload.get("tool_calls") is not None:
        task["tool_calls"] = max(0, int(payload.get("tool_calls") or 0))
    if "started_at" in payload:
        task["started_at"] = payload.get("started_at")
    if "ended_at" in payload:
        task["ended_at"] = payload.get("ended_at")


def apply_monitor_task_tool_call(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    payload: dict[str, Any],
) -> None:
    task = ensure_monitor_task(sessions, task_index, payload)
    if task is None:
        return
    tool_name = str(payload.get("tool_name", "")).strip()
    if tool_name:
        task["current_tool"] = tool_name
    if payload.get("tool_call_index") is not None:
        task["tool_calls"] = max(0, int(payload.get("tool_call_index") or 0))
    if payload.get("agent_id") is not None:
        task["agent_id"] = str(payload.get("agent_id", "")).strip()


def apply_monitor_task_completed(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    payload: dict[str, Any],
) -> None:
    task = ensure_monitor_task(sessions, task_index, payload)
    if task is None:
        return
    task["status"] = "completed"
    task["result_summary"] = str(payload.get("result_summary", "")).strip()
    if payload.get("tool_calls") is not None:
        task["tool_calls"] = max(0, int(payload.get("tool_calls") or 0))
    if "started_at" in payload:
        task["started_at"] = payload.get("started_at")
    if "ended_at" in payload:
        task["ended_at"] = payload.get("ended_at")
    task["error"] = ""


def apply_monitor_task_failed(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    payload: dict[str, Any],
) -> None:
    task = ensure_monitor_task(sessions, task_index, payload)
    if task is None:
        return
    task["status"] = "failed"
    task["error"] = str(payload.get("error", "")).strip()
    if "ended_at" in payload:
        task["ended_at"] = payload.get("ended_at")


def apply_monitor_task_comment(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    payload: dict[str, Any],
    *,
    max_comments_per_task: int,
) -> None:
    task = ensure_monitor_task(sessions, task_index, payload)
    if task is None:
        return
    comments = task.setdefault("comments", [])
    comments.append(copy.deepcopy(payload))
    if len(comments) > max_comments_per_task:
        del comments[:-max_comments_per_task]


def apply_monitor_log_entry(
    sessions: dict[str, dict[str, Any]],
    task_index: dict[str, str],
    payload: dict[str, Any],
    *,
    max_logs_per_session: int,
) -> None:
    session_key = resolve_monitor_session_key(payload, task_index=task_index)
    if not session_key:
        return
    session = ensure_monitor_session(sessions, session_key)
    entry = copy.deepcopy(payload)
    entry.setdefault("ts", time.time())
    session["logs"].append(entry)
    if len(session["logs"]) > max_logs_per_session:
        del session["logs"][:-max_logs_per_session]
