"""Persistent task-run store with lightweight checkpoint state machine.

Extracted from ``tools.admin._shared`` to give ``TaskExecutionStore`` its
own module under ``runtime/`` where business logic naturally belongs.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class TaskExecutionStore:
    """Persistent task-run store with lightweight checkpoint state machine."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir or (Path.home() / ".gazer" / "task_runs")
        self._base.mkdir(parents=True, exist_ok=True)
        self._path = self._base / "task_runs.jsonl"

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self._path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict):
                    out.append(rec)
        except Exception:
            return []
        return out

    def _write_all(self, items: List[Dict[str, Any]]) -> None:
        with open(self._path, "w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    def create(
        self,
        *,
        kind: str,
        run_id: str,
        session_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        task_id = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = time.time()
        rec = {
            "task_id": task_id,
            "kind": str(kind),
            "run_id": str(run_id),
            "session_id": str(session_id),
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "payload": payload or {},
            "checkpoints": [],
            "output": {},
        }
        items = self._read_all()
        items.append(rec)
        self._write_all(items)
        return rec

    def update_status(
        self,
        task_id: str,
        *,
        status: str,
        output: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        items = self._read_all()
        updated: Optional[Dict[str, Any]] = None
        for item in items:
            if str(item.get("task_id", "")) != str(task_id):
                continue
            item["status"] = str(status)
            item["updated_at"] = time.time()
            if output is not None:
                item["output"] = output
            updated = item
            break
        if updated is None:
            return None
        self._write_all(items)
        return updated

    def add_checkpoint(
        self,
        task_id: str,
        *,
        stage: str,
        status: str,
        note: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        items = self._read_all()
        updated: Optional[Dict[str, Any]] = None
        for item in items:
            if str(item.get("task_id", "")) != str(task_id):
                continue
            checkpoints = list(item.get("checkpoints", []) or [])
            checkpoints.append(
                {
                    "ts": time.time(),
                    "stage": str(stage),
                    "status": str(status),
                    "note": str(note),
                    "metadata": metadata or {},
                }
            )
            item["checkpoints"] = checkpoints
            item["updated_at"] = time.time()
            updated = item
            break
        if updated is None:
            return None
        self._write_all(items)
        return updated

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        for item in reversed(self._read_all()):
            if str(item.get("task_id", "")) == str(task_id):
                return item
        return None

    def list(self, *, limit: int = 50, status: Optional[str] = None, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        items = list(reversed(self._read_all()))
        out: List[Dict[str, Any]] = []
        status_filter = str(status or "").strip().lower()
        kind_filter = str(kind or "").strip().lower()
        for item in items:
            if status_filter and str(item.get("status", "")).strip().lower() != status_filter:
                continue
            if kind_filter and str(item.get("kind", "")).strip().lower() != kind_filter:
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out
