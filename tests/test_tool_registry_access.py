from tools.base import Tool
from tools.registry_access import evaluate_tool_access_decision
from tools.registry_policy import ToolPolicy


class _DummyTool(Tool):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "dummy"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs) -> str:
        return "ok"


def test_access_decision_blocks_owner_only_without_context() -> None:
    decision = evaluate_tool_access_decision(
        name="dummy",
        tool=_DummyTool(),
        denylist=set(),
        allowlist=set(),
        policy=None,
        sender_id="",
        channel="",
        model_provider="",
        model_name="",
        provider="core",
        owner_context_available=False,
        owner_sender=False,
        owner_only=True,
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "blocked_by_owner_only_no_context"


def test_access_decision_blocks_model_selector_miss() -> None:
    decision = evaluate_tool_access_decision(
        name="dummy",
        tool=_DummyTool(),
        denylist=set(),
        allowlist=set(),
        policy=ToolPolicy(allow_model_selectors={"openai/gpt-4o-mini"}),
        sender_id="owner",
        channel="web",
        model_provider="openai",
        model_name="gpt-4o",
        provider="core",
        owner_context_available=True,
        owner_sender=True,
        owner_only=False,
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "blocked_by_policy_allow_model_selectors"
