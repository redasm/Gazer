from __future__ import annotations

import copy
import time
from typing import Any


DEFAULT_EMPTY_SESSION_LABEL = "No active multi-agent session"


def build_monitor_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event,
        "ts": time.time(),
        "payload": copy.deepcopy(payload),
    }


def build_task_payload(
    *,
    session_key: str,
    task_id: str,
    title: str,
    description: str,
    agent_id: str,
    depends: list[str],
    priority: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": title,
        "description": description,
        "agent_id": agent_id,
        "depends": list(depends),
        "session_key": session_key,
        "priority": priority,
        "status": "queued",
        "current_tool": None,
        "tool_calls": 0,
        "result_summary": None,
        "started_at": None,
        "ended_at": None,
        "error": "",
        "comments": [],
    }


def copy_session_payload(session_key: str, session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": session_key,
        "session_label": session.get("session_label", DEFAULT_EMPTY_SESSION_LABEL),
        "tasks": [copy.deepcopy(task) for task in session.get("tasks", {}).values()],
        "logs": copy.deepcopy(session.get("logs", [])),
    }
