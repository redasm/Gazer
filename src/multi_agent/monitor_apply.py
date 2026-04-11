from __future__ import annotations

from collections.abc import Callable
from typing import Any


EVENT_HANDLER_MAP: dict[str, str] = {
    "session.init": "session_init",
    "task.created": "task_created",
    "task.status": "task_status",
    "task.tool_call": "task_tool_call",
    "task.completed": "task_completed",
    "task.failed": "task_failed",
    "task.comment": "task_comment",
    "log.entry": "log_entry",
}


def apply_monitor_event_payload(
    event: str,
    payload: dict[str, Any],
    *,
    handlers: dict[str, Callable[[dict[str, Any]], None]],
) -> bool:
    """Dispatch a monitor payload to the matching local state handler."""
    handler_key = EVENT_HANDLER_MAP.get(str(event or "").strip(), "")
    handler = handlers.get(handler_key)
    if handler is None:
        return False
    handler(payload)
    return True
