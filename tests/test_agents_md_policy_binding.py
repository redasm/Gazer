from pathlib import Path
from types import SimpleNamespace

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.queue import MessageBus
from llm.base import LLMResponse


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key_path: str, default=None):
        cur = self.data
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class _Provider:
    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return LLMResponse(content="ok", tool_calls=[])


def _build_loop(monkeypatch, workspace: Path, tool_policy: dict | None = None) -> AgentLoop:
    monkeypatch.setattr(config_manager, "config", _FakeConfig({"security": {"tool_groups": {}}}))
    monkeypatch.setattr(
        "agent.loop.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )
    return AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=workspace,
        tool_policy=tool_policy,
    )


def test_agents_allowed_tools_applies_to_tool_policy(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("allowed-tools: web_search, web_fetch\n", encoding="utf-8")

    loop = _build_loop(monkeypatch, workspace)
    policy = loop._resolve_tool_policy()
    assert policy.allow_names == {"web_search", "web_fetch"}
    assert policy.deny_names == set()


def test_agents_deny_tools_merges_with_base_tool_policy(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("deny-tools: node_invoke\n", encoding="utf-8")

    loop = _build_loop(monkeypatch, workspace, tool_policy={"deny_names": ["shell_command"]})
    policy = loop._resolve_tool_policy()
    assert policy.deny_names == {"shell_command", "node_invoke"}


def test_agents_allowed_tools_intersects_with_base_allowlist(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("allowed-tools: web_search\n", encoding="utf-8")

    loop = _build_loop(monkeypatch, workspace, tool_policy={"allow_names": ["web_search", "shell_command"]})
    policy = loop._resolve_tool_policy()
    assert policy.allow_names == {"web_search"}


def test_agents_child_overlay_applies_after_target_dir_switch(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "repo"
    child = workspace / "apps" / "desktop"
    child.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("allowed-tools: web_search, web_fetch\n", encoding="utf-8")
    (workspace / "apps" / "AGENTS.md").write_text("deny-tools: web_search\n", encoding="utf-8")

    loop = _build_loop(monkeypatch, workspace)
    loop.context.set_agents_target_dir(child)
    policy = loop._resolve_tool_policy()
    assert policy.allow_names == {"web_fetch"}
    assert "web_search" in policy.deny_names
