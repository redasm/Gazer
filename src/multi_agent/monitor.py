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

logger = logging.getLogger("multi_agent.Monitor")

_DEFAULT_EMPTY_SESSION_LABEL = "No active multi-agent session"
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
                    "session_label": _DEFAULT_EMPTY_SESSION_LABEL,
                    "tasks": [],
                    "logs": [],
                }
            session = self._sessions.get(resolved_key)
            if session is None:
                return {
                    "session_key": resolved_key,
                    "session_label": _DEFAULT_EMPTY_SESSION_LABEL,
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
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in subscribers:
            try:
                queue.put_nowait(copy.deepcopy(envelope))
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(copy.deepcopy(envelope))
                except Exception:
                    stale.append(queue)
            except Exception:
                stale.append(queue)
        if stale:
            async with self._lock:
                for queue in stale:
                    self._subscribers.discard(queue)

    def _apply_event_locked(self, envelope: dict[str, Any]) -> None:
        event = str(envelope.get("event", "")).strip()
        payload = envelope.get("payload", {})
        if not isinstance(payload, dict):
            return

        if event == "session.init":
            self._apply_session_init_locked(payload)
            return
        if event == "task.created":
            self._apply_task_created_locked(payload)
            return
        if event == "task.status":
            self._apply_task_status_locked(payload)
            return
        if event == "task.tool_call":
            self._apply_task_tool_call_locked(payload)
            return
        if event == "task.completed":
            self._apply_task_completed_locked(payload)
            return
        if event == "task.failed":
            self._apply_task_failed_locked(payload)
            return
        if event == "task.comment":
            self._apply_task_comment_locked(payload)
            return
        if event == "log.entry":
            self._apply_log_entry_locked(payload)

    def _apply_session_init_locked(self, payload: dict[str, Any]) -> None:
        session_key = str(payload.get("session_key", "")).strip()
        if not session_key:
            session_key = self._latest_session_key or ""
        session = self._ensure_session_locked(
            session_key,
            session_label=str(payload.get("session_label", "")).strip() or _DEFAULT_EMPTY_SESSION_LABEL,
        )
        session["session_label"] = str(payload.get("session_label", "")).strip() or _DEFAULT_EMPTY_SESSION_LABEL
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
                task = self._task_template(
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
                self._task_index[task_id] = session_key

        if isinstance(raw_logs, list):
            session["logs"] = [copy.deepcopy(entry) for entry in raw_logs if isinstance(entry, dict)][-_MAX_LOGS_PER_SESSION:]

        self._latest_session_key = session_key or self._latest_session_key

    def _apply_task_created_locked(self, payload: dict[str, Any]) -> None:
        session_key = str(payload.get("session_key", "")).strip()
        task_id = str(payload.get("task_id", "")).strip()
        if not session_key or not task_id:
            return
        session = self._ensure_session_locked(session_key)
        task = self._task_template(
            session_key=session_key,
            task_id=task_id,
            title=str(payload.get("title", "")).strip() or task_id,
            description=str(payload.get("description", "")).strip(),
            agent_id=str(payload.get("agent_id", "")).strip(),
            depends=payload.get("depends", []) if isinstance(payload.get("depends", []), list) else [],
            priority=str(payload.get("priority", "normal")).strip().lower() or "normal",
        )
        session["tasks"][task_id] = task
        self._task_index[task_id] = session_key
        self._latest_session_key = session_key

    def _apply_task_status_locked(self, payload: dict[str, Any]) -> None:
        task = self._ensure_task_for_update_locked(payload)
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

    def _apply_task_tool_call_locked(self, payload: dict[str, Any]) -> None:
        task = self._ensure_task_for_update_locked(payload)
        if task is None:
            return
        tool_name = str(payload.get("tool_name", "")).strip()
        if tool_name:
            task["current_tool"] = tool_name
        if payload.get("tool_call_index") is not None:
            task["tool_calls"] = max(0, int(payload.get("tool_call_index") or 0))
        if payload.get("agent_id") is not None:
            task["agent_id"] = str(payload.get("agent_id", "")).strip()

    def _apply_task_completed_locked(self, payload: dict[str, Any]) -> None:
        task = self._ensure_task_for_update_locked(payload)
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

    def _apply_task_failed_locked(self, payload: dict[str, Any]) -> None:
        task = self._ensure_task_for_update_locked(payload)
        if task is None:
            return
        task["status"] = "failed"
        task["error"] = str(payload.get("error", "")).strip()
        if "ended_at" in payload:
            task["ended_at"] = payload.get("ended_at")

    def _apply_task_comment_locked(self, payload: dict[str, Any]) -> None:
        task = self._ensure_task_for_update_locked(payload)
        if task is None:
            return
        comments = task.setdefault("comments", [])
        comments.append(copy.deepcopy(payload))
        if len(comments) > _MAX_COMMENTS_PER_TASK:
            del comments[:-_MAX_COMMENTS_PER_TASK]

    def _apply_log_entry_locked(self, payload: dict[str, Any]) -> None:
        session_key = self._resolve_payload_session_key_locked(payload)
        if not session_key:
            return
        session = self._ensure_session_locked(session_key)
        entry = copy.deepcopy(payload)
        entry.setdefault("ts", time.time())
        session["logs"].append(entry)
        if len(session["logs"]) > _MAX_LOGS_PER_SESSION:
            del session["logs"][:-_MAX_LOGS_PER_SESSION]

    def _resolve_payload_session_key_locked(self, payload: dict[str, Any]) -> str:
        session_key = str(payload.get("session_key", "")).strip()
        if session_key:
            return session_key
        task_id = str(payload.get("task_id", "")).strip()
        if task_id:
            return self._task_index.get(task_id, "")
        return ""

    def _ensure_task_for_update_locked(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        session_key = self._resolve_payload_session_key_locked(payload)
        task_id = str(payload.get("task_id", "")).strip()
        if not session_key or not task_id:
            return None
        session = self._ensure_session_locked(session_key)
        task = session["tasks"].get(task_id)
        if task is None:
            task = self._task_template(
                session_key=session_key,
                task_id=task_id,
                title=task_id,
                description="",
                agent_id=str(payload.get("agent_id", "")).strip(),
                depends=[],
                priority="normal",
            )
            session["tasks"][task_id] = task
            self._task_index[task_id] = session_key
        return task

    def _ensure_session_locked(self, session_key: str, *, session_label: str | None = None) -> dict[str, Any]:
        key = str(session_key or "").strip()
        if key not in self._sessions:
            self._sessions[key] = {
                "session_key": key,
                "session_label": session_label or _DEFAULT_EMPTY_SESSION_LABEL,
                "tasks": {},
                "logs": [],
            }
        elif session_label:
            self._sessions[key]["session_label"] = session_label
        return self._sessions[key]

    def _resolve_session_key_locked(self, session_key: str | None) -> str | None:
        key = str(session_key or "").strip()
        if key:
            return key
        return self._latest_session_key

    def _copy_session_payload_locked(self, session_key: str, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_key": session_key,
            "session_label": session.get("session_label", _DEFAULT_EMPTY_SESSION_LABEL),
            "tasks": [copy.deepcopy(task) for task in session.get("tasks", {}).values()],
            "logs": copy.deepcopy(session.get("logs", [])),
        }

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

    @staticmethod
    def _build_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event": event,
            "ts": time.time(),
            "payload": copy.deepcopy(payload),
        }


monitor_hub = MultiAgentMonitorHub()


def should_forward_monitor_events() -> bool:
    """No longer needed in single-process architecture."""
    return False
