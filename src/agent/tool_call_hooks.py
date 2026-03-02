"""Tool-call governance hooks for AgentLoop."""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple


class ToolCallHookManager:
    """Session-scoped before/after hook manager for tool governance."""

    def __init__(self) -> None:
        # session_key -> deque[(timestamp, fingerprint)]
        self._recent_calls: Dict[str, Deque[Tuple[float, str]]] = {}
        self._stats: Dict[str, Any] = {
            "before_calls": 0,
            "after_calls": 0,
            "blocked_loop_calls": 0,
            "last_blocked": {},
        }

    @staticmethod
    def _serialize_params(params: Any) -> str:
        try:
            return json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(params)

    @classmethod
    def _fingerprint(cls, *, tool_name: str, params: Any) -> str:
        raw = f"{tool_name.strip().lower()}::{cls._serialize_params(params)}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]

    @staticmethod
    def _settings() -> Dict[str, Any]:
        from runtime.config_manager import config as _cfg

        raw = _cfg.get("security.tool_call_hooks", {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "enabled": bool(raw.get("enabled", True)),
            "loop_detection_enabled": bool(raw.get("loop_detection_enabled", True)),
            "loop_max_repeats": max(1, int(raw.get("loop_max_repeats", 3) or 3)),
            "loop_window_seconds": max(1.0, float(raw.get("loop_window_seconds", 90.0) or 90.0)),
            "session_max_events": max(16, int(raw.get("session_max_events", 256) or 256)),
        }

    def before_tool_call(
        self,
        *,
        session_key: str,
        tool_name: str,
        params: Any,
    ) -> Optional[Dict[str, str]]:
        """Run before-call checks.

        Returns a block payload when rejected, otherwise None.
        """

        cfg = self._settings()
        self._stats["before_calls"] = int(self._stats.get("before_calls", 0)) + 1
        if not cfg["enabled"] or not cfg["loop_detection_enabled"]:
            return None

        key = str(session_key or "").strip()
        if not key:
            return None

        now = time.time()
        fingerprint = self._fingerprint(tool_name=tool_name, params=params)
        window_seconds = float(cfg["loop_window_seconds"])
        max_repeats = int(cfg["loop_max_repeats"])
        max_events = int(cfg["session_max_events"])
        self._prune_stale_sessions(now=now, window_seconds=window_seconds)

        events = self._recent_calls.get(key)
        if events is None:
            events = deque(maxlen=max_events)
            self._recent_calls[key] = events

        while events and (now - float(events[0][0])) > window_seconds:
            events.popleft()

        repeats = sum(1 for _ts, fp in events if fp == fingerprint)
        if repeats >= max_repeats:
            self._stats["blocked_loop_calls"] = int(self._stats.get("blocked_loop_calls", 0)) + 1
            self._stats["last_blocked"] = {
                "session_key": key,
                "tool_name": str(tool_name or ""),
                "repeats": int(repeats),
                "window_seconds": float(window_seconds),
                "blocked_at": now,
            }
            return {
                "code": "TOOL_LOOP_BLOCKED",
                "message": (
                    f"Detected repeated identical tool call '{tool_name}' "
                    f"({repeats + 1} times within {int(window_seconds)}s); blocked to prevent loop."
                ),
            }

        events.append((now, fingerprint))
        return None

    def _prune_stale_sessions(self, *, now: float, window_seconds: float) -> None:
        stale_sessions = []
        for session_key, events in list(self._recent_calls.items()):
            while events and (now - float(events[0][0])) > window_seconds:
                events.popleft()
            if not events:
                stale_sessions.append(session_key)
        for session_key in stale_sessions:
            self._recent_calls.pop(session_key, None)

    def after_tool_call(
        self,
        *,
        _session_key: str,
        _tool_name: str,
        _result: str,
    ) -> None:
        self._stats["after_calls"] = int(self._stats.get("after_calls", 0)) + 1

    def get_status(self) -> Dict[str, Any]:
        return {
            "before_calls": int(self._stats.get("before_calls", 0)),
            "after_calls": int(self._stats.get("after_calls", 0)),
            "blocked_loop_calls": int(self._stats.get("blocked_loop_calls", 0)),
            "last_blocked": dict(self._stats.get("last_blocked", {}) or {}),
            "active_sessions": int(len(self._recent_calls)),
        }
