from __future__ import annotations

import pytest

from eval.gui_simple_benchmark import GuiSimpleBenchmarkRunner
from tools.admin import api_facade as admin_api


@pytest.mark.asyncio
async def test_gui_simple_benchmark_runner_reports_step_failures():
    async def _invoker(action: str, args: dict, target: str):
        if action == "input.keyboard.hotkey" and args.get("keys") == ["enter"]:
            return {"ok": False, "code": "DEVICE_ACTION_POST_VERIFY_FAILED", "message": "no change"}
        return {"ok": True, "code": "", "message": "ok"}

    runner = GuiSimpleBenchmarkRunner(invoker=_invoker)
    report = await runner.run(target="local-desktop")

    assert report["version"] == "gui-simple-benchmark.v1"
    assert report["total_cases"] >= 4
    assert report["failed_cases"] == 1
    assert report["steps"][-1]["status"] == "failed"
    assert report["failure_reasons"][0]["code"] == "DEVICE_ACTION_POST_VERIFY_FAILED"
    assert len(report["replay_dataset"]) == report["total_cases"]


class _FakeToolRegistry:
    def __init__(self) -> None:
        self.calls = []

    def get(self, name: str):
        if name == "node_invoke":
            return object()
        return None

    async def execute(self, name: str, params: dict, **kwargs):
        self.calls.append((name, dict(params), dict(kwargs)))
        action = str(params.get("action", "")).strip()
        if action == "input.keyboard.hotkey" and params.get("args", {}).get("keys") == ["enter"]:
            return "Error [DEVICE_ACTION_POST_VERIFY_FAILED]: no change"
        return "ok"


@pytest.mark.asyncio
async def test_gui_simple_benchmark_observability_endpoints(monkeypatch):
    admin_api._gui_simple_benchmark_history.clear()
    fake_registry = _FakeToolRegistry()
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", fake_registry)

    run_payload = await admin_api.run_gui_simple_benchmark(
        {"target": "local-desktop", "stop_on_failure": False}
    )
    assert run_payload["status"] == "ok"
    report = run_payload["report"]
    assert report["failed_cases"] == 1
    assert report["total_cases"] >= 4

    obs = await admin_api.get_observability_gui_simple_benchmark(window=20)
    assert obs["status"] == "ok"
    benchmark = obs["benchmark"]
    assert benchmark["total_runs"] >= 1
    assert benchmark["latest"]["run_id"] == report["run_id"]
    assert any(item["code"] == "DEVICE_ACTION_POST_VERIFY_FAILED" for item in benchmark["failure_reasons"])

