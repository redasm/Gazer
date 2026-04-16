from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from memory.recall_regression import build_memory_recall_regression_report
from tools.admin import api_facade as admin_api


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


class _FakeConfig:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in str(path).split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


class _FakeMemoryManager:
    def __init__(self, data_dir: Path):
        self.backend = type("_Backend", (), {"data_dir": Path(data_dir)})()


class _FakeEvalBenchmarkManager:
    def __init__(self) -> None:
        self._gate = {"blocked": False, "reason": "", "source": "", "updated_at": 0.0, "metadata": {}}
        self.set_calls: list[dict] = []

    def get_release_gate_status(self) -> Dict[str, Any]:
        return dict(self._gate)

    def set_release_gate_status(
        self,
        *,
        blocked: bool,
        reason: str,
        source: str,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "blocked": bool(blocked),
            "reason": str(reason or ""),
            "source": str(source or ""),
            "updated_at": time.time(),
            "metadata": metadata or {},
        }
        self._gate = dict(payload)
        self.set_calls.append(payload)
        return payload


def test_build_memory_recall_regression_report_with_trend_and_alerts(tmp_path: Path):
    backend_dir = tmp_path / "openviking"
    (backend_dir / "long_term").mkdir(parents=True, exist_ok=True)
    now = time.time()

    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [
            {
                "timestamp": _iso(now - 120),
                "sender": "user",
                "content": "Need release checklist for sprint 42 deployment",
            },
            {
                "timestamp": _iso(now - 240),
                "sender": "assistant",
                "content": "Remember Alice prefers concise weekly summary emails",
            },
        ],
    )
    _write_json(
        backend_dir / "long_term" / "preferences.json",
        {"alice_pref": {"content": "Alice likes concise weekly summary emails"}},
    )
    _write_json(
        backend_dir / "recall_query_set.json",
        [
            {
                "id": "q_release",
                "query": "sprint release checklist",
                "expected_terms": ["release", "checklist"],
                "expected_category": "events",
            },
            {
                "id": "q_pref",
                "query": "alice summary preference",
                "expected_terms": ["alice", "summary"],
                "expected_category": "preferences",
            },
            {
                "id": "q_missing",
                "query": "budget approval workflow",
                "expected_terms": ["budget"],
                "expected_category": "events",
            },
        ],
    )
    _write_jsonl(
        backend_dir / "recall_regression_runs.jsonl",
        [
            {
                "timestamp": now - 3600,
                "query_total": 3,
                "matched_queries": 3,
                "precision_hits": 3,
                "level": "healthy",
                "metrics": {"recall_proxy": 1.0, "precision_proxy": 1.0, "quality_score": 1.0},
            }
        ],
    )

    report = build_memory_recall_regression_report(
        backend_dir=backend_dir,
        include_samples=True,
        sample_limit=5,
        persist=False,
    )

    assert report["status"] == "ok"
    assert report["current_window"]["query_total"] == 3
    assert report["current_window"]["matched_queries"] >= 2
    assert report["trend"]["direction"] in {"worse", "stable", "improving"}
    assert report["trend"]["quality_score_delta"] < 0
    assert len(report["alerts"]) >= 1
    assert report["gate"]["level"] in {"warning", "critical"}
    assert len(report["samples"]["failed_queries"]) >= 1


@pytest.mark.asyncio
async def test_memory_recall_regression_endpoint_warn_mode_does_not_block_gate(
    monkeypatch,
    tmp_path: Path,
):
    backend_dir = tmp_path / "ov_warn"
    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [
            {
                "timestamp": _iso(time.time() - 60),
                "sender": "user",
                "content": "release checklist for sprint 43",
            }
        ],
    )
    _write_json(
        backend_dir / "recall_query_set.json",
        [
            {
                "id": "q1",
                "query": "release checklist",
                "expected_terms": ["release", "checklist"],
                "expected_category": "events",
            },
            {
                "id": "q2",
                "query": "nonexistent memory topic",
                "expected_terms": ["nonexistent"],
                "expected_category": "events",
            },
        ],
    )

    fake_cfg = _FakeConfig(
        {
            "memory": {
                "recall_regression": {
                    "enabled": True,
                    "window_days": 7,
                    "query_set_path": str(backend_dir / "recall_query_set.json"),
                    "top_k": 5,
                    "min_match_score": 0.18,
                    "thresholds": {
                        "min_precision_proxy": 0.95,
                        "min_recall_proxy": 0.95,
                        "warning_drop": 0.05,
                        "critical_drop": 0.12,
                    },
                    "gate": {"link_release_gate": True, "mode": "warn", "source": "memory_recall_regression"},
                }
            }
        }
    )
    fake_eval = _FakeEvalBenchmarkManager()

    monkeypatch.setattr("tools.admin.memory.config", fake_cfg)
    monkeypatch.setattr("tools.admin.memory._get_memory_manager", lambda: _FakeMemoryManager(backend_dir))
    monkeypatch.setattr("tools.admin.memory._get_eval_bm", lambda: fake_eval)

    payload = await admin_api.get_memory_recall_regression(
        window_days=7,
        include_samples=False,
        sample_limit=10,
        persist=False,
        apply_gate=True,
    )

    assert payload["status"] == "ok"
    linkage = payload["release_gate_linkage"]
    assert linkage["mode"] == "warn"
    assert linkage["alert_only"] is True
    assert linkage["changed_gate"] is False
    assert len(fake_eval.set_calls) == 0


@pytest.mark.asyncio
async def test_memory_recall_regression_endpoint_block_mode_updates_release_gate(
    monkeypatch,
    tmp_path: Path,
):
    backend_dir = tmp_path / "ov_block"
    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [{"timestamp": _iso(time.time() - 60), "sender": "user", "content": "short message"}],
    )
    _write_json(
        backend_dir / "recall_query_set.json",
        [
            {
                "id": "q_fail",
                "query": "budget approval workflow",
                "expected_terms": ["budget", "approval"],
                "expected_category": "events",
            }
        ],
    )

    fake_cfg = _FakeConfig(
        {
            "memory": {
                "recall_regression": {
                    "enabled": True,
                    "window_days": 7,
                    "query_set_path": str(backend_dir / "recall_query_set.json"),
                    "top_k": 5,
                    "min_match_score": 0.18,
                    "thresholds": {
                        "min_precision_proxy": 0.9,
                        "min_recall_proxy": 0.9,
                        "warning_drop": 0.05,
                        "critical_drop": 0.12,
                    },
                    "gate": {
                        "link_release_gate": True,
                        "mode": "block",
                        "source": "memory_recall_regression",
                        "reason_critical": "memory_recall_regression_critical",
                    },
                }
            }
        }
    )
    fake_eval = _FakeEvalBenchmarkManager()

    monkeypatch.setattr("tools.admin.memory.config", fake_cfg)
    monkeypatch.setattr("tools.admin.memory._get_memory_manager", lambda: _FakeMemoryManager(backend_dir))
    monkeypatch.setattr("tools.admin.memory._get_eval_bm", lambda: fake_eval)

    payload = await admin_api.get_memory_recall_regression(
        window_days=7,
        include_samples=False,
        sample_limit=10,
        persist=False,
        apply_gate=True,
    )

    linkage = payload["release_gate_linkage"]
    assert linkage["mode"] == "block"
    assert linkage["changed_gate"] is True
    assert fake_eval.set_calls[-1]["blocked"] is True


@pytest.mark.asyncio
async def test_memory_recall_regression_export_markdown(monkeypatch, tmp_path: Path):
    backend_dir = tmp_path / "ov_export"
    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [{"timestamp": _iso(time.time() - 30), "sender": "user", "content": "release checklist draft"}],
    )
    _write_json(
        backend_dir / "recall_query_set.json",
        [{"id": "q1", "query": "release checklist", "expected_terms": ["release"], "expected_category": "events"}],
    )

    fake_cfg = _FakeConfig(
        {
            "memory": {
                "recall_regression": {
                    "enabled": True,
                    "window_days": 7,
                    "query_set_path": str(backend_dir / "recall_query_set.json"),
                    "top_k": 5,
                    "min_match_score": 0.18,
                    "thresholds": {
                        "min_precision_proxy": 0.45,
                        "min_recall_proxy": 0.45,
                        "warning_drop": 0.05,
                        "critical_drop": 0.12,
                    },
                    "gate": {"link_release_gate": True, "mode": "warn"},
                }
            }
        }
    )
    fake_eval = _FakeEvalBenchmarkManager()
    monkeypatch.setattr("tools.admin.memory.config", fake_cfg)
    monkeypatch.setattr("tools.admin.memory._get_memory_manager", lambda: _FakeMemoryManager(backend_dir))
    monkeypatch.setattr("tools.admin.memory._get_eval_bm", lambda: fake_eval)

    output_path = tmp_path / "memory_recall_regression.md"
    payload = await admin_api.export_memory_recall_regression(
        {
            "format": "markdown",
            "window_days": 7,
            "persist": False,
            "apply_gate": True,
            "output_path": str(output_path),
        }
    )

    assert payload["status"] == "ok"
    assert payload["format"] == "markdown"
    assert output_path.is_file()
    assert "# Memory Recall Regression Report" in output_path.read_text(encoding="utf-8")
