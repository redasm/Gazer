"""Tests for soul.system_prompt -- build_agent_system_prompt."""

import os
import pytest
from soul.system_prompt import build_agent_system_prompt, _read_asset


class TestReadAsset:
    def test_reads_existing_file(self, tmp_dir, monkeypatch):
        asset_dir = tmp_dir / "assets"
        asset_dir.mkdir()
        (asset_dir / "TEST.md").write_text("hello asset", encoding="utf-8")
        monkeypatch.chdir(tmp_dir)
        result = _read_asset("TEST.md")
        assert result == "hello asset"

    def test_returns_empty_for_missing(self, tmp_dir, monkeypatch):
        monkeypatch.chdir(tmp_dir)
        result = _read_asset("NONEXISTENT.md")
        assert result == ""


class TestBuildAgentSystemPrompt:
    def test_basic_prompt(self, tmp_dir, monkeypatch):
        # Create minimal assets
        asset_dir = tmp_dir / "assets"
        asset_dir.mkdir()
        (asset_dir / "SOUL.md").write_text("I am Gazer.", encoding="utf-8")
        (asset_dir / "AGENTS.md").write_text("Rule 1: Be helpful.", encoding="utf-8")
        (asset_dir / "TOOLS.md").write_text("Use tools wisely.", encoding="utf-8")
        monkeypatch.chdir(tmp_dir)

        prompt = build_agent_system_prompt(workspace_dir=str(tmp_dir))
        assert "Identity & Mission" in prompt
        assert "I am Gazer" in prompt
        assert "Rule 1" in prompt
        assert str(tmp_dir) in prompt

    def test_includes_tools(self, tmp_dir, monkeypatch):
        (tmp_dir / "assets").mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_dir)
        prompt = build_agent_system_prompt(
            workspace_dir="/workspace",
            tool_summaries={"echo": "Echoes text", "search": "Web search"},
        )
        assert "Tooling Protocol" in prompt
        assert "`echo`" in prompt
        assert "`search`" in prompt

    def test_includes_skills(self, tmp_dir, monkeypatch):
        (tmp_dir / "assets").mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_dir)
        prompt = build_agent_system_prompt(
            workspace_dir="/workspace",
            skill_instructions="Run the deploy skill.",
        )
        assert "Available Skills" in prompt
        assert "deploy" in prompt

    def test_includes_context_files(self, tmp_dir, monkeypatch):
        (tmp_dir / "assets").mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_dir)
        prompt = build_agent_system_prompt(
            workspace_dir="/workspace",
            context_files=[{"path": "README.md", "content": "# My Project"}],
        )
        assert "README.md" in prompt
        assert "My Project" in prompt

    def test_includes_runtime_info(self, tmp_dir, monkeypatch):
        (tmp_dir / "assets").mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_dir)
        prompt = build_agent_system_prompt(
            workspace_dir="/workspace",
            runtime_info={"platform": "Windows", "channel": "telegram"},
        )
        assert "Windows" in prompt
        assert "telegram" in prompt

    def test_fallback_identity(self, tmp_dir, monkeypatch):
        (tmp_dir / "assets").mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_dir)
        # No SOUL.md -> fallback
        prompt = build_agent_system_prompt(workspace_dir="/workspace")
        assert "Gazer" in prompt

    def test_final_instructions(self, tmp_dir, monkeypatch):
        (tmp_dir / "assets").mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_dir)
        prompt = build_agent_system_prompt(workspace_dir="/workspace")
        assert "Execution Loop" in prompt
        assert "Output Contract" in prompt
        assert "Markdown" in prompt
