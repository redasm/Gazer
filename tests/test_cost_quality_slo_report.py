from pathlib import Path
from types import SimpleNamespace

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
    def list_recent(self, limit=50):
        return [
            {"run_id": "r1", "status": "success", "turn_latency_ms": 1200},
            {"run_id": "r2", "status": "error", "turn_latency_ms": 2800},
        ][:limit]

    def get_trajectory(self, run_id):
        if run_id == "r1":
            return {"final": {"metrics": {"iterations": 1}}}
        if run_id == "r2":
            return {"final": {"metrics": {"iterations": 3}}}
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


@pytest.mark.asyncio
async def test_cost_quality_slo_build_and_export(monkeypatch, tmp_path: Path):
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
                    "max_p95_latency_ms": 4000.0,
                    "max_avg_retries_per_run": 2.0,
                    "max_downgrade_trigger_rate": 0.3,
                }
            },
            "api": {"export_allowed_dirs": [".task", ".tmp_pytest", "exports"]},
        }
    )

    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", _FakeTrajectoryStore())
    monkeypatch.setattr(
        admin_api,
        "USAGE_TRACKER",
        SimpleNamespace(summary=lambda: {"prompt_tokens": 1000, "completion_tokens": 3000, "total_tokens": 4000}),
    )
    monkeypatch.setattr(admin_api, "LLM_ROUTER", _FakeRouter())

    report_payload = await admin_api.get_cost_quality_slo(window=20)
    report = report_payload["report"]
    assert report_payload["status"] == "ok"
    assert report["metrics"]["runs"] == 2
    assert report["metrics"]["success_rate"] == 0.5
    assert report["metrics"]["avg_retries_per_run"] == 1.0
    assert report["metrics"]["estimated_cost_usd"] == 0.008
    assert report["metrics"]["router_downgrade_trigger_rate"] == 0.1333

    md_path = tmp_path / "cost_quality.md"
    md_export = await admin_api.export_cost_quality_slo(
        {"window": 20, "format": "markdown", "output_path": str(md_path)}
    )
    assert md_export["status"] == "ok"
    assert md_path.exists()
    assert "Cost & Quality SLO Report" in md_path.read_text(encoding="utf-8")

    json_path = tmp_path / "cost_quality.json"
    json_export = await admin_api.export_cost_quality_slo(
        {"window": 20, "format": "json", "output_path": str(json_path)}
    )
    assert json_export["status"] == "ok"
    assert json_path.exists()
    assert "\"metrics\"" in json_path.read_text(encoding="utf-8")
