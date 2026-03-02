"""Structured trajectory storage for agent turn replay."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("TrajectoryStore")

MAX_STRING_LEN = 2000
MAX_EVENTS_PREVIEW = 200


def _safe_run_id() -> str:
    return f"traj_{int(time.time() * 1000)}_{uuid.uuid4().hex[:10]}"


def _truncate(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "..."
    if isinstance(value, str):
        if len(value) <= MAX_STRING_LEN:
            return value
        return f"{value[:900]}\n...[{len(value) - 1800} chars omitted]...\n{value[-900:]}"
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            out[str(key)] = _truncate(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_truncate(item, depth + 1) for item in value[:200]]
    return value


class TrajectoryStore:
    """Append-only trajectory recorder and replay store."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir or (Path.home() / ".gazer" / "trajectories")
        self._base.mkdir(parents=True, exist_ok=True)

    def _path_for(self, run_id: str) -> Path:
        return self._base / f"{run_id}.jsonl"

    def _all_paths(self) -> List[Path]:
        return sorted(
            self._base.glob("traj_*.jsonl"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )

    @staticmethod
    def _line(record: Dict[str, Any]) -> str:
        return json.dumps(record, ensure_ascii=False) + "\n"

    def _append(self, run_id: str, record: Dict[str, Any]) -> None:
        path = self._path_for(run_id)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(self._line(record))
        except OSError as exc:
            logger.error("Failed to append trajectory %s: %s", run_id, exc)

    def start(
        self,
        *,
        session_key: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        user_content: str,
    ) -> str:
        run_id = _safe_run_id()
        self._append(
            run_id,
            {
                "type": "meta",
                "ts": time.time(),
                "run_id": run_id,
                "session_key": session_key,
                "channel": channel,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "user_content": _truncate(user_content),
            },
        )
        return run_id

    def add_event(self, run_id: str, *, stage: str, action: str, payload: Dict[str, Any]) -> None:
        self._append(
            run_id,
            {
                "type": "event",
                "ts": time.time(),
                "stage": stage,
                "action": action,
                "payload": _truncate(payload),
            },
        )

    def add_feedback(
        self,
        run_id: str,
        *,
        label: str,
        feedback: str,
        context: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Attach user feedback to an existing trajectory."""
        path = self._path_for(run_id)
        if not path.is_file():
            return False
        self._append(
            run_id,
            {
                "type": "feedback",
                "ts": time.time(),
                "label": str(label),
                "feedback": _truncate(feedback),
                "context": _truncate(context),
                "metadata": _truncate(metadata or {}),
            },
        )
        return True

    def finalize(
        self,
        run_id: str,
        *,
        status: str,
        final_content: str,
        usage: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._append(
            run_id,
            {
                "type": "final",
                "ts": time.time(),
                "status": status,
                "final_content": _truncate(final_content),
                "usage": _truncate(usage or {}),
                "metrics": _truncate(metrics or {}),
            },
        )

    def get_trajectory(self, run_id: str) -> Optional[Dict[str, Any]]:
        path = self._path_for(run_id)
        if not path.is_file():
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        meta: Optional[Dict[str, Any]] = None
        final: Optional[Dict[str, Any]] = None
        events: List[Dict[str, Any]] = []
        feedbacks: List[Dict[str, Any]] = []
        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec_type = rec.get("type")
            if rec_type == "meta":
                meta = rec
            elif rec_type == "event":
                events.append(rec)
            elif rec_type == "final":
                final = rec
            elif rec_type == "feedback":
                feedbacks.append(rec)

        if meta is None:
            return None
        return {
            "run_id": run_id,
            "meta": meta,
            "events": events,
            "feedback": feedbacks,
            "final": final,
            "event_count": len(events),
        }

    def list_recent(self, limit: int = 50, session_key: Optional[str] = None) -> List[Dict[str, Any]]:
        files = self._all_paths()
        items: List[Dict[str, Any]] = []
        for path in files:
            run_id = path.stem
            traj = self.get_trajectory(run_id)
            if not traj:
                continue
            meta = traj["meta"]
            if session_key and str(meta.get("session_key", "")) != session_key:
                continue
            final = traj.get("final") or {}
            events = traj.get("events") or []
            feedback = traj.get("feedback") or []
            items.append(
                {
                    "run_id": run_id,
                    "ts": meta.get("ts"),
                    "session_key": meta.get("session_key"),
                    "channel": meta.get("channel"),
                    "chat_id": meta.get("chat_id"),
                    "sender_id": meta.get("sender_id"),
                    "status": final.get("status", "running"),
                    "event_count": len(events),
                    "feedback_count": len(feedback),
                    "turn_latency_ms": ((final.get("metrics") or {}).get("turn_latency_ms")),
                    "final_preview": str(final.get("final_content", ""))[:MAX_EVENTS_PREVIEW],
                }
            )
            if len(items) >= limit:
                break
        return items

    def resolve_latest_run(self, *, session_key: Optional[str] = None, chat_id: Optional[str] = None) -> Optional[str]:
        """Find the most recent run id by session/chat selector."""
        for path in self._all_paths():
            run_id = path.stem
            payload = self.get_trajectory(run_id)
            if not payload:
                continue
            meta = payload.get("meta") or {}
            if session_key and str(meta.get("session_key", "")) != session_key:
                continue
            if chat_id and str(meta.get("chat_id", "")) != chat_id:
                continue
            return run_id
        return None

    def list_feedback_samples(self, limit: int = 100, label: Optional[str] = None) -> List[Dict[str, Any]]:
        """Build regression-eval samples from trajectories with feedback."""
        label_filter = str(label or "").strip().lower()
        samples: List[Dict[str, Any]] = []
        for path in self._all_paths():
            run_id = path.stem
            payload = self.get_trajectory(run_id)
            if not payload:
                continue
            meta = payload.get("meta") or {}
            final = payload.get("final") or {}
            feedback_items = payload.get("feedback") or []
            for fb in feedback_items:
                fb_label = str(fb.get("label", "")).strip().lower()
                if label_filter and fb_label != label_filter:
                    continue
                samples.append(
                    {
                        "run_id": run_id,
                        "timestamp": fb.get("ts"),
                        "label": fb_label,
                        "feedback": fb.get("feedback", ""),
                        "context": fb.get("context", ""),
                        "channel": meta.get("channel"),
                        "chat_id": meta.get("chat_id"),
                        "session_key": meta.get("session_key"),
                        "user_content": meta.get("user_content", ""),
                        "assistant_output": final.get("final_content", ""),
                        "status": final.get("status", "unknown"),
                    }
                )
                if len(samples) >= limit:
                    return samples
        return samples
