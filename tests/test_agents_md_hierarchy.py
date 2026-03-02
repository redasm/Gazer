from pathlib import Path

from agent.agents_md import resolve_agents_overlay
from agent.context import ContextBuilder
from skills.loader import SkillLoader


def _write_skill(path: Path, name: str, desc: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_agents_overlay_inheritance_and_child_override(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "Root rules\nskills_priority: alpha, beta\n",
        encoding="utf-8",
    )
    app_dir = workspace / "apps" / "desktop"
    app_dir.mkdir(parents=True)
    (workspace / "apps" / "AGENTS.md").write_text(
        "App rules\nskills_priority: gamma\n",
        encoding="utf-8",
    )

    payload = resolve_agents_overlay(workspace, app_dir)
    files = payload["files"]
    assert len(files) == 2
    assert files[0]["path"] == "AGENTS.md"
    assert files[1]["path"] == "apps/AGENTS.md"
    assert "Root rules" in payload["combined_text"]
    assert "App rules" in payload["combined_text"]
    assert payload["skill_priority"] == ["gamma"]


def test_context_builder_uses_agents_overlay(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Root policy line\n", encoding="utf-8")
    mod_dir = workspace / "module"
    mod_dir.mkdir(parents=True)
    (mod_dir / "AGENTS.md").write_text("skills_priority: demo\nModule policy line\n", encoding="utf-8")

    builder = ContextBuilder(workspace)
    builder.set_agents_target_dir(mod_dir)
    prompt = builder.build_system_prompt()
    assert "Root policy line" in prompt
    assert "Module policy line" in prompt
    assert builder.get_skill_priority() == ["demo"]
    assert len(builder.get_agents_debug()) >= 1


def test_skill_loader_prefers_agents_skill_order(tmp_path: Path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root / "a_skill", "alpha", "A")
    _write_skill(skills_root / "b_skill", "beta", "B")
    _write_skill(skills_root / "c_skill", "gamma", "C")

    loader = SkillLoader([skills_root])
    loader.discover()
    xml = loader.format_for_prompt(preferred_order=["gamma", "beta"])
    gamma_pos = xml.find("<name>gamma</name>")
    beta_pos = xml.find("<name>beta</name>")
    alpha_pos = xml.find("<name>alpha</name>")
    assert gamma_pos != -1 and beta_pos != -1 and alpha_pos != -1
    assert gamma_pos < beta_pos < alpha_pos


def test_agents_overlay_parses_structured_tool_and_routing_fields(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "\n".join(
            [
                "allowed-tools: web_search, web_fetch",
                "deny-tools:",
                "  - node_invoke",
                "  - shell_command",
                "routing-hints: latency_first, cost_first",
            ]
        ),
        encoding="utf-8",
    )
    payload = resolve_agents_overlay(workspace, workspace)
    assert payload["allowed_tools"] == ["web_search", "web_fetch"]
    assert payload["deny_tools"] == ["node_invoke", "shell_command"]
    assert payload["routing_hints"] == ["latency_first", "cost_first"]


def test_agents_overlay_structured_fields_child_override(tmp_path: Path):
    workspace = tmp_path / "workspace"
    app_dir = workspace / "apps" / "desktop"
    app_dir.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "\n".join(
            [
                "allowed-tools: web_search, web_fetch",
                "deny-tools: node_invoke",
                "routing-hints: latency_first",
            ]
        ),
        encoding="utf-8",
    )
    (workspace / "apps" / "AGENTS.md").write_text(
        "\n".join(
            [
                "deny-tools: shell_command",
                "routing-hints:",
                "  - quality",
                "  - reliability",
            ]
        ),
        encoding="utf-8",
    )

    payload = resolve_agents_overlay(workspace, app_dir)
    assert payload["allowed_tools"] == ["web_search", "web_fetch"]
    assert payload["deny_tools"] == ["shell_command"]
    assert payload["routing_hints"] == ["quality", "reliability"]


def test_agents_overlay_reports_cross_scope_allow_deny_conflicts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    app_dir = workspace / "apps" / "desktop"
    app_dir.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "allowed-tools: web_search, web_fetch\n",
        encoding="utf-8",
    )
    (workspace / "apps" / "AGENTS.md").write_text(
        "deny-tools: web_search\n",
        encoding="utf-8",
    )

    payload = resolve_agents_overlay(workspace, app_dir)
    conflicts = payload.get("conflicts", [])
    assert conflicts
    assert any(
        item.get("type") == "allow_deny_conflict"
        and item.get("tool") == "web_search"
        and item.get("allowed_in") == "AGENTS.md"
        and item.get("denied_in") == "apps/AGENTS.md"
        for item in conflicts
    )


def test_agents_overlay_debug_tracks_field_overrides(tmp_path: Path):
    workspace = tmp_path / "workspace"
    child = workspace / "apps"
    child.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "\n".join(
            [
                "skills_priority: alpha",
                "allowed-tools: web_search",
            ]
        ),
        encoding="utf-8",
    )
    (child / "AGENTS.md").write_text(
        "\n".join(
            [
                "skills_priority: beta",
                "deny-tools: node_invoke",
            ]
        ),
        encoding="utf-8",
    )

    payload = resolve_agents_overlay(workspace, child)
    debug_rows = payload.get("debug", [])
    assert len(debug_rows) == 2
    assert debug_rows[0]["overrode_skill_priority"] is True
    assert debug_rows[1]["overrode_skill_priority"] is True
    assert debug_rows[1]["overrode_deny_tools"] is True
