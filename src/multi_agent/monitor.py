"""In-memory monitor hub for multi-agent task sessions.

Keeps a snapshot of the latest multi-agent session state and broadcasts
event envelopes to local websocket subscribers.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from typing import Any

from multi_agent.monitor_apply import apply_monitor_event_payload
from multi_agent.monitor_broadcast import fan_out_monitor_event
from multi_agent.monitor_payloads import (
    DEFAULT_EMPTY_SESSION_LABEL,
    build_monitor_event,
    build_task_payload,
    copy_session_payload,
)
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

logger = logging.getLogger("multi_agent.Monitor")
_MAX_LOGS_PER_SESSION = 200
_MAX_COMMENTS_PER_TASK = 100


class MultiAgentMonitorHub:
    """State hub for the multi-agent monitor websocket and comments API."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._task_index: dict[str, str] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._latest_session_key: str | None = None

    async def reset(self) -> None:
        """Clear all monitor state while keeping subscriber registrations."""
        async with self._lock:
            self._sessions.clear()
            self._task_index.clear()
            self._latest_session_key = None

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def begin_session(
        self,
        session_key: str,
        session_label: str,
        *,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        envelope = self._build_event(
            "session.init",
            {
                "session_key": str(session_key or "").strip(),
                "session_label": str(session_label or "").strip(),
                "tasks": [],
                "logs": [],
            },
        )
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return envelope

    async def build_session_init_payload(self, session_key: str | None = None) -> dict[str, Any]:
        async with self._lock:
            resolved_key = self._resolve_session_key_locked(session_key)
            if not resolved_key:
                return {
                    "session_key": "",
                    "session_label": DEFAULT_EMPTY_SESSION_LABEL,
                    "tasks": [],
                    "logs": [],
                }
            session = self._sessions.get(resolved_key)
            if session is None:
                return {
                    "session_key": resolved_key,
                    "session_label": DEFAULT_EMPTY_SESSION_LABEL,
                    "tasks": [],
                    "logs": [],
                }
            return self._copy_session_payload_locked(resolved_key, session)

    async def build_session_init_event(self, session_key: str | None = None) -> dict[str, Any]:
        return self._build_event("session.init", await self.build_session_init_payload(session_key))

    async def task_created(
        self,
        *,
        session_key: str,
        task_id: str,
        title: str,
        description: str,
        agent_id: str,
        depends: list[str],
        priority: str,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        envelope = self._build_event(
            "task.created",
            {
                "session_key": str(session_key or "").strip(),
                "task_id": str(task_id or "").strip(),
                "title": str(title or "").strip(),
                "description": str(description or "").strip(),
                "agent_id": str(agent_id or "").strip(),
                "depends": [str(item).strip() for item in depends if str(item).strip()],
                "priority": str(priority or "normal").strip().lower() or "normal",
            },
        )
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return envelope

    async def task_status(
        self,
        *,
        task_id: str,
        status: str,
        session_key: str | None = None,
        agent_id: str | None = None,
        current_tool: str | None = None,
        tool_calls: int | None = None,
        started_at: float | None = None,
        ended_at: float | None = None,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        envelope = self._build_event(
            "task.status",
            {
                "session_key": str(session_key or "").strip(),
                "task_id": str(task_id or "").strip(),
                "status": str(status or "queued").strip().lower() or "queued",
                "agent_id": str(agent_id or "").strip(),
                "current_tool": current_tool,
                "tool_calls": tool_calls,
                "started_at": started_at,
                "ended_at": ended_at,
            },
        )
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return envelope

    async def task_tool_call(
        self,
        *,
        task_id: str,
        agent_id: str,
        tool_name: str,
        tool_call_index: int,
        session_key: str | None = None,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        envelope = self._build_event(
            "task.tool_call",
            {
                "session_key": str(session_key or "").strip(),
                "task_id": str(task_id or "").strip(),
                "agent_id": str(agent_id or "").strip(),
                "tool_name": str(tool_name or "").strip(),
                "tool_call_index": max(0, int(tool_call_index)),
            },
        )
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return envelope

    async def task_completed(
        self,
        *,
        task_id: str,
        result_summary: str,
        session_key: str | None = None,
        tool_calls: int | None = None,
        started_at: float | None = None,
        ended_at: float | None = None,
        tokens_used: int = 0,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        envelope = self._build_event(
            "task.completed",
            {
                "session_key": str(session_key or "").strip(),
                "task_id": str(task_id or "").strip(),
                "result_summary": str(result_summary or "").strip(),
                "tool_calls": tool_calls,
                "started_at": started_at,
                "ended_at": ended_at,
                "tokens_used": max(0, int(tokens_used or 0)),
            },
        )
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return envelope

    async def task_failed(
        self,
        *,
        task_id: str,
        error: str,
        session_key: str | None = None,
        ended_at: float | None = None,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        envelope = self._build_event(
            "task.failed",
            {
                "session_key": str(session_key or "").strip(),
                "task_id": str(task_id or "").strip(),
                "error": str(error or "").strip(),
                "ended_at": ended_at,
            },
        )
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return envelope

    async def log_entry(
        self,
        *,
        session_key: str,
        agent_id: str,
        type: str,
        message: str,
        task_id: str | None = None,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        envelope = self._build_event(
            "log.entry",
            {
                "session_key": str(session_key or "").strip(),
                "task_id": str(task_id or "").strip(),
                "agent_id": str(agent_id or "").strip(),
                "type": str(type or "system").strip().lower() or "system",
                "message": str(message or "").strip(),
            },
        )
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return envelope

    async def add_comment(
        self,
        task_id: str,
        *,
        text: str,
        author: str,
        forward_ipc: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            task_key = str(task_id or "").strip()
            session_key = self._task_index.get(task_key)
            if not session_key:
                raise KeyError(f"Unknown task: {task_key}")
            comment = {
                "comment_id": f"cmt_{uuid.uuid4().hex[:10]}",
                "task_id": task_key,
                "session_key": session_key,
                "text": str(text or "").strip(),
                "author": str(author or "User").strip() or "User",
                "ts": time.time(),
            }
        envelope = self._build_event("task.comment", comment)
        await self._publish_event(envelope, forward_ipc=forward_ipc)
        return copy.deepcopy(comment)

    async def apply_remote_event(self, envelope: dict[str, Any]) -> None:
        """Mirror an event received from the Brain process via IPC."""
        await self._publish_event(envelope, forward_ipc=False, already_applied=False)

    async def _publish_event(
        self,
        envelope: dict[str, Any],
        *,
        forward_ipc: bool = False,
        already_applied: bool = False,
    ) -> None:
        if not already_applied:
            async with self._lock:
                self._apply_event_locked(envelope)
                subscribers = list(self._subscribers)
        else:
            async with self._lock:
                subscribers = list(self._subscribers)
        await self._broadcast(subscribers, envelope)

    async def _broadcast(
        self,
        subscribers: list[asyncio.Queue[dict[str, Any]]],
        envelope: dict[str, Any],
    ) -> None:
        stale = fan_out_monitor_event(subscribers, envelope)
        if stale:
            async with self._lock:
                for queue in stale:
                    self._subscribers.discard(queue)

    def _apply_event_locked(self, envelope: dict[str, Any]) -> None:
        event = str(envelope.get("event", "")).strip()
        payload = envelope.get("payload", {})
        if not isinstance(payload, dict):
            return
        apply_monitor_event_payload(
            event,
            payload,
            handlers={
                "session_init": self._apply_session_init_locked,
                "task_created": self._apply_task_created_locked,
                "task_status": self._apply_task_status_locked,
                "task_tool_call": self._apply_task_tool_call_locked,
                "task_completed": self._apply_task_completed_locked,
                "task_failed": self._apply_task_failed_locked,
                "task_comment": self._apply_task_comment_locked,
                "log_entry": self._apply_log_entry_locked,
            },
        )

    def _apply_session_init_locked(self, payload: dict[str, Any]) -> None:
        self._latest_session_key = apply_monitor_session_init(
            self._sessions,
            self._task_index,
            payload=payload,
            latest_session_key=self._latest_session_key,
            max_logs_per_session=_MAX_LOGS_PER_SESSION,
        )

    def _apply_task_created_locked(self, payload: dict[str, Any]) -> None:
        self._latest_session_key = apply_monitor_task_created(self._sessions, self._task_index, payload=payload) or self._latest_session_key

    def _apply_task_status_locked(self, payload: dict[str, Any]) -> None:
        apply_monitor_task_status(self._sessions, self._task_index, payload)

    def _apply_task_tool_call_locked(self, payload: dict[str, Any]) -> None:
        apply_monitor_task_tool_call(self._sessions, self._task_index, payload)

    def _apply_task_completed_locked(self, payload: dict[str, Any]) -> None:
        apply_monitor_task_completed(self._sessions, self._task_index, payload)

    def _apply_task_failed_locked(self, payload: dict[str, Any]) -> None:
        apply_monitor_task_failed(self._sessions, self._task_index, payload)

    def _apply_task_comment_locked(self, payload: dict[str, Any]) -> None:
        apply_monitor_task_comment(
            self._sessions,
            self._task_index,
            payload,
            max_comments_per_task=_MAX_COMMENTS_PER_TASK,
        )

    def _apply_log_entry_locked(self, payload: dict[str, Any]) -> None:
        apply_monitor_log_entry(
            self._sessions,
            self._task_index,
            payload,
            max_logs_per_session=_MAX_LOGS_PER_SESSION,
        )

    def _resolve_payload_session_key_locked(self, payload: dict[str, Any]) -> str:
        return resolve_monitor_session_key(payload, task_index=self._task_index)

    def _ensure_task_for_update_locked(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return ensure_monitor_task(self._sessions, self._task_index, payload)

    def _ensure_session_locked(self, session_key: str, *, session_label: str | None = None) -> dict[str, Any]:
        return ensure_monitor_session(self._sessions, session_key, session_label=session_label)

    def _resolve_session_key_locked(self, session_key: str | None) -> str | None:
        key = str(session_key or "").strip()
        if key:
            return key
        return self._latest_session_key

    def _copy_session_payload_locked(self, session_key: str, session: dict[str, Any]) -> dict[str, Any]:
        return copy_session_payload(session_key, session)

    def _task_template(
        self,
        *,
        session_key: str,
        task_id: str,
        title: str,
        description: str,
        agent_id: str,
        depends: list[str],
        priority: str,
    ) -> dict[str, Any]:
        return build_task_payload(
            session_key=session_key,
            task_id=task_id,
            title=title,
            description=description,
            agent_id=agent_id,
            depends=depends,
            priority=priority,
        )

    @staticmethod
    def _build_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
        return build_monitor_event(event, payload)


monitor_hub = MultiAgentMonitorHub()


def should_forward_monitor_events() -> bool:
    """No longer needed in single-process architecture."""
    return False
