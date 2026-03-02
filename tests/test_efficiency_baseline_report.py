from pathlib import Path

import pytest

from tools.admin import workflows as admin_api


class _FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        cur = self.data
        for part in str(key_path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class _FakeTrajectoryStore:
    def __init__(self, now_ts: float):
        self.now_ts = float(now_ts)

    def list_recent(self, limit=50):
        rows = [
            {
                "run_id": "r_cur_ok",
                "status": "success",
                "turn_latency_ms": 1000,
                "ts": self.now_ts - 1000,
            },
            {
                "run_id": "r_cur_err",
                "status": "error",
                "turn_latency_ms": 2200,
                "ts": self.now_ts - 2000,
            },
            {
                "run_id": "r_prev_ok",
                "status": "success",
                "turn_latency_ms": 1300,
                "ts": self.now_ts - (8 * 86400),
            },
            {
                "run_id": "r_old",
                "status": "success",
                "turn_latency_ms": 1100,
                "ts": self.now_ts - (20 * 86400),
            },
        ]
        return rows[:limit]

    def get_trajectory(self, run_id):
        if run_id == "r_cur_ok":
            return {
                "events": [
                    {"action": "tool_result", "payload": {"status": "ok"}},
                    {
                        "action": "tool_result",
                        "payload": {"status": "error", "error_code": "WEB_FETCH_FAILED"},
                    },
                ],
                "final": {
                    "usage": {"total_tokens": 1500},
                    "metrics": {
                        "tool_rounds": 2,
                        "tool_calls_executed": 3,
                        "turn_latency_ms": 1000,
                    },
                },
            }
        if run_id == "r_cur_err":
            return {
                "events": [
                    {
                        "action": "tool_result",
                        "payload": {"status": "error", "error_code": "WEB_FETCH_FAILED"},
                    },
                    {
                        "action": "tool_result",
                        "payload": {"status": "error", "error_code": "TOOL_TIMEOUT"},
                    },
                ],
                "final": {
                    "usage": {"total_tokens": 3000},
                    "metrics": {
                        "tool_rounds": 4,
                        "tool_calls_executed": 4,
                        "turn_latency_ms": 2200,
                    },
                },
            }
        if run_id == "r_prev_ok":
            return {
                "events": [{"action": "tool_result", "payload": {"status": "ok"}}],
                "final": {
                    "usage": {"total_tokens": 1200},
                    "metrics": {
                        "tool_rounds": 1,
                        "tool_calls_executed": 1,
                        "turn_latency_ms": 1300,
                    },
                },
            }
        if run_id == "r_old":
            return {
                "events": [{"action": "tool_result", "payload": {"status": "ok"}}],
                "final": {
                    "usage": {"total_tokens": 900},
                    "metrics": {
                        "tool_rounds": 1,
                        "tool_calls_executed": 1,
                        "turn_latency_ms": 1100,
                    },
                },
            }
        return None


@pytest.mark.asyncio
async def test_efficiency_baseline_build_and_export(monkeypatch, tmp_path: Path):
    now_ts = 2_000_000.0
    fake_cfg = _FakeConfig(
        {
            "observability": {
                "efficiency_baseline_targets": {
                    "min_success_rate": 0.4,
                    "max_p95_latency_ms": 4000.0,
                    "max_avg_tokens_per_run": 5000.0,
                    "max_tool_error_rate": 0.7,
                }
            },
            "api": {"export_allowed_dirs": [".task", ".tmp_pytest", "exports"]},
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", _FakeTrajectoryStore(now_ts=now_ts))
    monkeypatch.setattr(admin_api.time, "time", lambda: now_ts)

    payload = await admin_api.get_observability_efficiency_baseline(window_days=7, limit=20)
    report = payload["report"]

    assert payload["status"] == "ok"
    assert report["current_window"]["runs"] == 2
    assert report["previous_window"]["runs"] == 1
    assert report["current_window"]["success_rate"] == 0.5
    assert report["current_window"]["avg_tokens_per_run"] == 2250.0
    assert report["current_window"]["avg_tool_rounds_per_run"] == 3.0
    assert report["current_window"]["tool_error_rate"] == 0.4286
    assert report["previous_window"]["avg_tokens_per_run"] == 1200.0
    assert report["delta"]["avg_tokens_per_run"] == 1050.0
    assert report["checks"]["success_rate_ok"] is True
    assert report["checks"]["tool_error_rate_ok"] is True
    assert report["passed"] is True
    top_codes = report["current_window"]["top_error_codes"]
    assert top_codes and top_codes[0]["code"] == "WEB_FETCH_FAILED"
    assert top_codes[0]["count"] == 2

    md_path = tmp_path / "efficiency_baseline.md"
    md_export = await admin_api.export_observability_efficiency_baseline(
        {"window_days": 7, "limit": 20, "format": "markdown", "output_path": str(md_path)}
    )
    assert md_export["status"] == "ok"
    assert md_export["format"] == "markdown"
    assert md_path.exists()
    assert "Efficiency Baseline Weekly Report" in md_path.read_text(encoding="utf-8")

    json_path = tmp_path / "efficiency_baseline.json"
    json_export = await admin_api.export_observability_efficiency_baseline(
        {"window_days": 7, "limit": 20, "format": "json", "output_path": str(json_path)}
    )
    assert json_export["status"] == "ok"
    assert json_export["format"] == "json"
    assert json_path.exists()
    assert "\"current_window\"" in json_path.read_text(encoding="utf-8")
