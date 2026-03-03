from __future__ import annotations

from pathlib import Path

import pytest

from agent.agents_md_lint import lint_agents_overlay
from tools.admin import api_facade as admin_api


def test_lint_agents_overlay_reports_conflicts_unknown_fields_and_invalid_tokens(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "\n".join(
            [
                "allowed-tools: web_search, web_search, bad tool",
                "deny-tools: web_search",
                "routing-hints: latency_first, bad hint",
                "allowed_toolz: node_invoke",
            ]
        ),
        encoding="utf-8",
    )

    report = lint_agents_overlay(workspace, workspace)
    assert report["status"] == "ok"
    assert report["summary"]["total"] >= 3
    assert report["summary"]["error"] >= 1
    assert report["summary"]["warning"] >= 1
    codes = {item.get("code") for item in report.get("issues", [])}
    assert "allow_deny_conflict_same_file" in codes
    assert "unknown_field" in codes
    assert "invalid_tool_token" in codes
    assert "invalid_routing_hint" in codes


@pytest.mark.asyncio
async def test_agents_md_effective_endpoint_returns_overlay_debug(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "repo"
    app_dir = workspace / "apps"
    app_dir.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "\n".join(
            [
                "skills_priority: alpha, beta",
                "allowed-tools: web_search, web_fetch",
            ]
        ),
        encoding="utf-8",
    )
    (app_dir / "AGENTS.md").write_text(
        "\n".join(
            [
                "deny-tools: web_search",
                "routing-hints: quality_first",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", workspace)

    payload = await admin_api.get_agents_md_effective("apps")
    assert payload["status"] == "ok"
    assert payload["target_dir"] == "apps"
    assert payload["skill_priority"] == ["alpha", "beta"]
    assert payload["allowed_tools"] == ["web_search", "web_fetch"]
    assert payload["deny_tools"] == ["web_search"]
    assert payload["routing_hints"] == ["quality_first"]
    assert len(payload["files"]) == 2
    assert isinstance(payload["debug"], list)
    assert any(item.get("type") == "allow_deny_conflict" for item in payload.get("conflicts", []))


@pytest.mark.asyncio
async def test_agents_md_lint_endpoint(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "\n".join(
            [
                "allowed-tools: web_search",
                "deny-tools: web_search",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", workspace)
    payload = await admin_api.run_agents_md_lint({"agents_target_dir": "."})
    assert payload["status"] == "ok"
    assert payload["summary"]["error"] >= 1
    assert any(item.get("code") == "allow_deny_conflict_same_file" for item in payload.get("issues", []))
