from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from memory.quality_eval import build_memory_quality_report
from tools.admin import workflows as admin_api


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat()


def test_build_memory_quality_report_includes_core_metrics(tmp_path: Path):
    backend_dir = tmp_path / "openviking"
    (backend_dir / "long_term").mkdir(parents=True, exist_ok=True)
    now = time.time()

    current_event_recent = _iso(now - 3600)
    current_event_old = _iso(now - (3 * 86400))
    previous_event = _iso(now - (8 * 86400))

    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [
            {
                "timestamp": current_event_recent,
                "sender": "user",
                "content": "User prefers dark theme and compact layout",
            },
            {
                "timestamp": current_event_old,
                "sender": "assistant",
                "content": "Assistant prepared weekly report email draft",
            },
            {
                "timestamp": previous_event,
                "sender": "user",
                "content": "User prefers light theme in old session",
            },
        ],
    )
    _write_jsonl(
        backend_dir / "extraction_decisions.jsonl",
        [
            {
                "timestamp": _iso(now - 3500),
                "kind": "memory_extraction",
                "category": "preferences",
                "key": "theme",
                "decision": "CREATE",
                "source_timestamp": current_event_recent,
            },
            {
                "timestamp": _iso(now - 3400),
                "kind": "memory_extraction",
                "category": "preferences",
                "key": "theme",
                "decision": "UPDATE",
                "source_timestamp": current_event_recent,
            },
            {
                "timestamp": _iso(now - 3300),
                "kind": "memory_extraction",
                "category": "events",
                "key": "weekly_report_email",
                "decision": "CREATE",
                "source_timestamp": current_event_old,
            },
            {
                "timestamp": _iso(now - (8 * 86400) + 60),
                "kind": "memory_extraction",
                "category": "preferences",
                "key": "theme",
                "decision": "CREATE",
                "source_timestamp": previous_event,
            },
        ],
    )
    (backend_dir / "long_term" / "preferences.json").write_text(
        json.dumps({"theme": {"content": "User prefers dark theme and compact layout"}}),
        encoding="utf-8",
    )
    (backend_dir / "long_term" / "events.json").write_text(
        json.dumps({"weekly_report_email": {"content": "Prepared weekly report email draft"}}),
        encoding="utf-8",
    )

    report = build_memory_quality_report(
        backend_dir=backend_dir,
        window_days=7,
        stale_days=1,
        include_samples=True,
        sample_limit=5,
    )

    assert report["status"] == "ok"
    assert report["counts"]["long_term_total"] == 2
    assert report["current_window"]["counts"]["events"] == 2
    assert report["current_window"]["counts"]["decisions"] == 3
    assert report["current_window"]["metrics"]["relevance"]["yield_rate"] > 0.0
    assert report["current_window"]["metrics"]["relevance"]["yield_rate_capped"] <= 1.0
    assert report["current_window"]["metrics"]["relevance"]["decision_acceptance_rate"] > 0.0
    assert report["current_window"]["metrics"]["relevance"]["event_binding_rate"] >= 0.0
    assert report["current_window"]["metrics"]["timeliness"]["stale_ratio"] > 0.0
    assert report["current_window"]["metrics"]["conflict"]["conflict_rate"] > 0.0
    assert report["current_window"]["scores"]["quality_level"] in {"healthy", "warning", "critical"}
    assert report["trend"]["direction"] in {"improving", "worse", "stable"}
    assert report["trend"]["interpretation"] in {"normal", "limited_baseline"}
    assert "samples" in report["current_window"]


class _FakeMemoryManagerWithBackend:
    def __init__(self, data_dir: Path):
        self.backend = type("_Backend", (), {"data_dir": Path(data_dir)})()


@pytest.mark.asyncio
async def test_memory_quality_report_endpoint_includes_persona_drift(
    monkeypatch,
    tmp_path: Path,
):
    backend_dir = tmp_path / "ov_api"
    (backend_dir / "long_term").mkdir(parents=True, exist_ok=True)
    now = time.time()
    event_ts = _iso(now - 1800)

    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [{"timestamp": event_ts, "sender": "user", "content": "Remember project alpha milestones"}],
    )
    _write_jsonl(
        backend_dir / "extraction_decisions.jsonl",
        [
            {
                "timestamp": _iso(now - 1700),
                "kind": "memory_extraction",
                "category": "events",
                "key": "project_alpha",
                "decision": "CREATE",
                "source_timestamp": event_ts,
            }
        ],
    )
    (backend_dir / "long_term" / "events.json").write_text(
        json.dumps({"project_alpha": {"content": "project alpha milestones"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManagerWithBackend(backend_dir))
    monkeypatch.setattr(
        admin_api,
        "_build_persona_memory_joint_drift_report",
        lambda window_days, source: {
            "status": "ok",
            "memory": {"drift": {"score": 0.32, "level": "warning"}},
            "joint": {"risk_level": "warning"},
        },
    )

    payload = await admin_api.get_memory_quality_report(
        window_days=7,
        stale_days=7,
        include_samples=True,
        sample_limit=3,
        include_persona_drift=True,
        source="persona_eval",
    )

    assert payload["status"] == "ok"
    assert payload["persona_drift"]["joint_risk_level"] == "warning"
    assert payload["persona_drift"]["memory_drift"]["level"] == "warning"
    assert payload["current_window"]["scores"]["joint_risk_level"] == "warning"


@pytest.mark.asyncio
async def test_memory_quality_report_export_markdown_and_json(monkeypatch, tmp_path: Path):
    backend_dir = tmp_path / "ov_export"
    (backend_dir / "long_term").mkdir(parents=True, exist_ok=True)
    now = time.time()
    event_ts = _iso(now - 1200)

    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [{"timestamp": event_ts, "sender": "user", "content": "Remember release checklist for sprint 18"}],
    )
    _write_jsonl(
        backend_dir / "extraction_decisions.jsonl",
        [
            {
                "timestamp": _iso(now - 1100),
                "kind": "memory_extraction",
                "category": "events",
                "key": "sprint_18_release",
                "decision": "CREATE",
                "source_timestamp": event_ts,
            }
        ],
    )
    (backend_dir / "long_term" / "events.json").write_text(
        json.dumps({"sprint_18_release": {"content": "release checklist sprint 18"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManagerWithBackend(backend_dir))
    monkeypatch.setattr(
        admin_api,
        "_build_persona_memory_joint_drift_report",
        lambda window_days, source: {
            "status": "ok",
            "memory": {"drift": {"score": 0.18, "level": "healthy"}},
            "joint": {"risk_level": "healthy"},
        },
    )

    md_path = tmp_path / "memory_quality_report.md"
    md_payload = await admin_api.export_memory_quality_report(
        {
            "window_days": 7,
            "stale_days": 7,
            "format": "markdown",
            "output_path": str(md_path),
            "include_persona_drift": True,
        }
    )
    assert md_payload["status"] == "ok"
    assert md_payload["format"] == "markdown"
    assert md_path.is_file()
    assert "# Memory Quality Report" in md_path.read_text(encoding="utf-8")

    json_path = tmp_path / "memory_quality_report.json"
    json_payload = await admin_api.export_memory_quality_report(
        {
            "window_days": 7,
            "stale_days": 7,
            "format": "json",
            "output_path": str(json_path),
            "include_persona_drift": True,
        }
    )
    assert json_payload["status"] == "ok"
    assert json_payload["format"] == "json"
    assert json_path.is_file()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["status"] == "ok"
    assert parsed["current_window"]["scores"]["joint_risk_level"] == "healthy"
