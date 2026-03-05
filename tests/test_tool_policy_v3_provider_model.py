"""Tool policy V3 coverage for provider/model constrained access."""

from __future__ import annotations

import pytest

from tools.base import Tool
from tools.registry import ToolPolicy, ToolRegistry, normalize_tool_policy


class _DummyTool(Tool):
    def __init__(self, name: str, owner_only: bool = False, provider: str = "web"):
        self._name = name
        self._owner_only = owner_only
        self._provider = provider

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def owner_only(self) -> bool:
        return self._owner_only

    @property
    def provider(self) -> str:
        return self._provider

    async def execute(self, **kwargs) -> str:
        return "ok"


@pytest.fixture
def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_DummyTool("web_search", provider="web"))
    return registry


def test_normalize_tool_policy_v3_model_fields() -> None:
    policy = normalize_tool_policy(
        {
            "allow_model_providers": ["OpenAI"],
            "deny_model_providers": ["Anthropic"],
            "allow_model_names": ["gpt-4o-mini"],
            "deny_model_names": ["gpt-4o"],
            "allow_model_selectors": ["openai/gpt-4o-mini", "openai/*"],
            "deny_model_selectors": ["anthropic/*"],
        }
    )
    assert policy.allow_model_providers == {"openai"}
    assert policy.deny_model_providers == {"anthropic"}
    assert policy.allow_model_names == {"gpt-4o-mini"}
    assert policy.deny_model_names == {"gpt-4o"}
    assert "openai/gpt-4o-mini" in policy.allow_model_selectors


def test_evaluate_tool_access_blocks_on_model_selector_miss(_registry: ToolRegistry) -> None:
    policy = ToolPolicy(allow_model_selectors={"openai/gpt-4o-mini"})
    blocked = _registry.evaluate_tool_access(
        "web_search",
        policy=policy,
        model_provider="openai",
        model_name="gpt-4o",
    )
    allowed = _registry.evaluate_tool_access(
        "web_search",
        policy=policy,
        model_provider="openai",
        model_name="gpt-4o-mini",
    )

    assert blocked["allowed"] is False
    assert blocked["reason"] == "blocked_by_policy_allow_model_selectors"
    assert any(item["rule"] == "policy_allow_model_selectors" for item in blocked["rule_chain"])

    assert allowed["allowed"] is True
    assert allowed["reason"] == "allowed"


def test_get_definitions_respects_model_provider_constraints(_registry: ToolRegistry) -> None:
    policy = ToolPolicy(allow_model_providers={"openai"})
    denied_defs = _registry.get_definitions(
        policy=policy,
        model_provider="anthropic",
        model_name="claude-3-5-sonnet",
    )
    allowed_defs = _registry.get_definitions(
        policy=policy,
        model_provider="openai",
        model_name="gpt-4o-mini",
    )

    assert denied_defs == []
    assert [item["function"]["name"] for item in allowed_defs] == ["web_search"]

