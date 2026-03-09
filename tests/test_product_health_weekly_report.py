from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from tools.admin import api_facade as admin_api
import tools.admin.observability as _observability


class _FakeConfig:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def get(self, key_path: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in str(key_path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class _FakeTrajectoryStore:
    def __init__(self, now_ts: float):
        self.now_ts = float(now_ts)

    def list_recent(self, limit: int = 50):
        rows = [
            {"run_id": "run_ok", "status": "success", "turn_latency_ms": 1200, "ts": self.now_ts - 1200},
            {"run_id": "run_err", "status": "error", "turn_latency_ms": 2600, "ts": self.now_ts - 2400},
            {"run_id": "run_prev", "status": "success", "turn_latency_ms": 1400, "ts": self.now_ts - (8 * 86400)},
        ]
        return rows[:limit]

    def get_trajectory(self, run_id: str):
        if run_id == "run_ok":
            return {
                "events": [{"action": "tool_result", "payload": {"status": "ok"}}],
                "final": {
                    "usage": {"total_tokens": 1500},
                    "metrics": {"iterations": 1, "tool_rounds": 1, "tool_calls_executed": 1},
                },
            }
        if run_id == "run_err":
            return {
                "events": [
                    {"action": "tool_result", "payload": {"status": "error", "error_code": "WEB_FETCH_FAILED"}},
                    {"action": "tool_result", "payload": {"status": "error", "error_code": "TOOL_TIMEOUT"}},
                ],
                "final": {
                    "usage": {"total_tokens": 2800},
                    "metrics": {"iterations": 3, "tool_rounds": 3, "tool_calls_executed": 3},
                },
            }
        if run_id == "run_prev":
            return {
                "events": [{"action": "tool_result", "payload": {"status": "ok"}}],
                "final": {
                    "usage": {"total_tokens": 1200},
                    "metrics": {"iterations": 1, "tool_rounds": 1, "tool_calls_executed": 1},
                },
            }
        return None


class _FakeRouter:
    @staticmethod
    def get_status():
        return {
            "budget_degrade_active": False,
            "providers": [
                {"calls": 10, "error_classes": {"outlier_ejected": 1, "budget_exceeded": 0}},
                {"calls": 5, "error_classes": {"outlier_ejected": 0, "budget_exceeded": 1}},
            ],
        }


class _FakeMemoryManager:
    def __init__(self, data_dir: Path):
        self.backend = type("_Backend", (), {"data_dir": Path(data_dir)})()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


@pytest.mark.asyncio
async def test_product_health_weekly_build_and_export(monkeypatch, tmp_path: Path):
    now_ts = 2_500_000.0
    backend_dir = tmp_path / "openviking"
    (backend_dir / "long_term").mkdir(parents=True, exist_ok=True)

    event_ts = _iso(now_ts - 1800)
    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [
            {
                "timestamp": event_ts,
                "sender": "user",
                "content": "Remember sprint release checklist for project atlas",
            }
        ],
    )
    _write_jsonl(
        backend_dir / "extraction_decisions.jsonl",
        [
            {
                "timestamp": _iso(now_ts - 1700),
                "kind": "memory_extraction",
                "category": "events",
                "key": "atlas_release",
                "decision": "CREATE",
                "source_timestamp": event_ts,
            }
        ],
    )
    (backend_dir / "long_term" / "events.json").write_text(
        json.dumps({"atlas_release": {"content": "sprint release checklist for project atlas"}}),
        encoding="utf-8",
    )

    fake_cfg = _FakeConfig(
        {
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "dashscope/qwen-max",
                        "fallbacks": [],
                    }
                }
            },
            "models": {
                "router": {"budget": {"provider_cost_per_1k_tokens": {"dashscope": 0.002}}},
            },
            "observability": {
                "cost_quality_slo_targets": {
                    "min_success_rate": 0.4,
                    "max_p95_latency_ms": 5000.0,
                    "max_avg_retries_per_run": 3.0,
                    "max_downgrade_trigger_rate": 0.5,
                },
                "efficiency_baseline_targets": {
                    "min_success_rate": 0.4,
                    "max_p95_latency_ms": 5000.0,
                    "max_avg_tokens_per_run": 5000.0,
                    "max_tool_error_rate": 0.8,
                },
            },
            "api": {"export_allowed_dirs": [".task", ".tmp_pytest", "exports"]},
        }
    )

    monkeypatch.setattr(admin_api, "config", fake_cfg)
    _fake_traj = _FakeTrajectoryStore(now_ts=now_ts)
    _fake_usage = SimpleNamespace(summary=lambda: {"prompt_tokens": 1000, "completion_tokens": 3300, "total_tokens": 4300})
    _fake_router = _FakeRouter()
    monkeypatch.setattr(_observability, "get_trajectory_store", lambda: _fake_traj)
    monkeypatch.setattr(_observability, "get_usage_tracker", lambda: _fake_usage)
    monkeypatch.setattr(_observability, "get_llm_router", lambda: _fake_router)
    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManager(backend_dir))
    monkeypatch.setattr(admin_api.time, "time", lambda: now_ts)
    monkeypatch.setattr(
        admin_api,
        "_build_persona_memory_joint_drift_report",
        lambda window_days, source: {
            "status": "ok",
            "joint": {"risk_level": "warning"},
            "memory": {"drift": {"score": 0.31, "level": "warning"}},
        },
    )

    payload = await admin_api.get_observability_product_health_weekly(
        window_days=7,
        cost_window=20,
        efficiency_limit=20,
        memory_stale_days=7,
        include_persona_drift=True,
        persona_source="persona_eval",
    )
    assert payload["status"] == "ok"
    report = payload["report"]
    assert report["status"] == "ok"
    assert "cost_quality_slo" in report
    assert "efficiency_baseline" in report
    assert "memory_quality" in report
    assert report["summary"]["overall_level"] in {"healthy", "warning", "critical"}
    assert isinstance(report["summary"]["recommendations"], list)

    md_path = tmp_path / "product_health_weekly.md"
    md_export = await admin_api.export_observability_product_health_weekly(
        {
            "window_days": 7,
            "cost_window": 20,
            "efficiency_limit": 20,
            "memory_stale_days": 7,
            "include_persona_drift": True,
            "format": "markdown",
            "output_path": str(md_path),
        }
    )
    assert md_export["status"] == "ok"
    assert md_export["format"] == "markdown"
    assert md_path.exists()
    assert "Product Health Weekly Report" in md_path.read_text(encoding="utf-8")

    json_path = tmp_path / "product_health_weekly.json"
    json_export = await admin_api.export_observability_product_health_weekly(
        {
            "window_days": 7,
            "cost_window": 20,
            "efficiency_limit": 20,
            "memory_stale_days": 7,
            "include_persona_drift": True,
            "format": "json",
            "output_path": str(json_path),
        }
    )
    assert json_export["status"] == "ok"
    assert json_export["format"] == "json"
    assert json_path.exists()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["status"] == "ok"
    assert "summary" in parsed
