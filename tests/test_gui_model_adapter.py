import asyncio
import json

from agent.gui_model_adapter import GUIModelAdapter
from devices.models import NodeActionResult
from tools.device_tools import GuiTaskExecuteTool


def test_gui_model_adapter_passthrough_when_steps_provided():
    adapter = GUIModelAdapter()
    plan = adapter.suggest_actions(
        goal="open settings",
        steps=[{"action": "input.mouse.click", "args": {"x": 12, "y": 34}}],
        max_steps=5,
    )
    assert plan["mode"] == "passthrough"
    assert plan["used_fallback"] is True
    assert plan["steps"][0]["action"] == "input.mouse.click"


def test_gui_model_adapter_fallback_observe_only_when_no_steps():
    adapter = GUIModelAdapter()
    plan = adapter.suggest_actions(goal="inspect screen", steps=[], max_steps=5)
    assert plan["mode"] == "fallback_observe_only"
    assert plan["used_fallback"] is True
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["action"] == "screen.observe"


def test_gui_model_adapter_uses_custom_planner_output():
    adapter = GUIModelAdapter(
        planner=lambda **kwargs: {
            "steps": [
                {"action": "screen.observe", "args": {"query": kwargs.get("goal", "")}},
                {"action": "input.keyboard.type", "args": {"text": "hello"}},
            ],
            "note": "model-guided",
        },
        adapter_name="stub_model",
    )
    plan = adapter.suggest_actions(goal="search docs", steps=[], max_steps=3)
    assert plan["mode"] == "model_suggested"
    assert plan["used_fallback"] is False
    assert plan["adapter"] == "stub_model"
    assert plan["steps"][1]["action"] == "input.keyboard.type"


class _FakeRegistry:
    def __init__(self):
        self.default_target = "local-desktop"
        self.calls = []

    async def invoke(self, *, action: str, args: dict, target: str = ""):
        self.calls.append((action, dict(args), str(target)))
        if action == "screen.observe":
            return NodeActionResult(ok=True, message="observed", data={"observation": {"summary": "ok"}})
        if action.startswith("input."):
            return NodeActionResult(ok=True, message="acted")
        return NodeActionResult(ok=True, message="ok")


def test_gui_task_execute_uses_adapter_fallback_when_steps_missing():
    registry = _FakeRegistry()
    tool = GuiTaskExecuteTool(registry)  # type: ignore[arg-type]
    raw = asyncio.run(
        tool.execute(
            goal="just inspect current UI",
            steps=[],
        )
    )
    payload = json.loads(raw)
    assert payload["status"] == "completed"
    assert payload["adapter"]["mode"] in {"fallback_observe_only", "passthrough"}
    assert payload["steps_executed"] >= 1
    assert payload["steps"][0]["action"] == "screen.observe"
