"""Shared persistence helpers for evaluation managers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class EvalStore:
    """Common dataset/run storage utilities for eval modules."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._datasets = self._base / "datasets"
        self._runs = self._base / "runs"
        self._datasets.mkdir(parents=True, exist_ok=True)
        self._runs.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_id(dataset_id: str) -> str:
        return str(dataset_id).replace("/", "_").replace("\\", "_")

    def _dataset_path(self, dataset_id: str) -> Path:
        return self._datasets / f"{self._safe_id(dataset_id)}.json"

    def _run_path(self, dataset_id: str) -> Path:
        return self._runs / f"{self._safe_id(dataset_id)}.jsonl"

    @staticmethod
    def _read_json(path: Path, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        default = fallback or {}
        if not path.is_file():
            return dict(default)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return dict(default)

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_jsonl(path: Path, items: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
        if not path.is_file():
            return []
        items: List[Dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    items.append(payload)
        except Exception:
            return []
        return items

    def _list_dataset_payloads(self, limit: int = 50) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        paths = sorted(
            self._datasets.glob("*.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        for path in paths[:limit]:
            payload = self._read_json(path)
            if payload:
                items.append(payload)
        return items

    def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        payload = self._read_json(self._dataset_path(dataset_id))
        return payload if payload else None
