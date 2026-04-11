from __future__ import annotations

import json
from pathlib import Path

from agent.adapter import GazerAgent
from runtime.paths import resolve_repo_root, resolve_runtime_path, resolve_runtime_root


class _FakeConfig:
    def __init__(self, root: Path):
        self._root = root

    def _resolve_workspace_root(self) -> Path:
        return self._root


def test_resolve_runtime_root_prefers_config_manager(tmp_path: Path) -> None:
    cfg = _FakeConfig(tmp_path / "workspace")
    assert resolve_runtime_root(cfg) == (tmp_path / "workspace").resolve()


def test_resolve_runtime_root_falls_back_to_repo_root(monkeypatch) -> None:
    monkeypatch.delenv("GAZER_HOME", raising=False)
    assert resolve_runtime_root(config_manager=object()) == resolve_repo_root()


def test_resolve_runtime_path_uses_gazer_home_when_config_unavailable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GAZER_HOME", str(tmp_path / "home"))
    resolved = resolve_runtime_path("data/reports/example.jsonl", config_manager=object())
    assert resolved == (tmp_path / "home" / "data" / "reports" / "example.jsonl").resolve()


def test_append_jsonl_writes_relative_reports_under_runtime_root(monkeypatch, tmp_path: Path) -> None:
    fake_config = _FakeConfig(tmp_path / "workspace")
    monkeypatch.setattr("agent.adapter.config", fake_config)

    report_path = Path("data/reports/memory_turn_health.jsonl")
    GazerAgent._append_jsonl(report_path, {"status": "ok"})

    target = tmp_path / "workspace" / "data" / "reports" / "memory_turn_health.jsonl"
    assert target.is_file()
    rows = target.read_text(encoding="utf-8").splitlines()
    assert json.loads(rows[-1])["status"] == "ok"
