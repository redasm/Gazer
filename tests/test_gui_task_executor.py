import asyncio
import json

from devices.models import NodeActionResult
from tools.device_tools import GuiTaskExecuteTool


class _FakeRegistry:
    def __init__(self):
        self.default_target = "local-desktop"
        self.calls = []
        self._click_calls = 0

    async def invoke(self, *, action: str, args: dict, target: str = ""):
        self.calls.append((action, dict(args), str(target)))
        if action == "screen.observe":
            return NodeActionResult(ok=True, message="observed", data={"observation": {"summary": "ok"}})
        if action == "input.mouse.click":
            self._click_calls += 1
            if self._click_calls == 1:
                return NodeActionResult(ok=False, code="DEVICE_ACTION_POST_VERIFY_FAILED", message="no change")
            return NodeActionResult(ok=True, message="clicked")
        if action == "input.keyboard.type":
            return NodeActionResult(ok=True, message="typed")
        if action == "screen.screenshot":
            return NodeActionResult(ok=True, message="shot")
        return NodeActionResult(ok=True, message="ok")


def test_gui_task_execute_switches_to_conservative_on_failure():
    registry = _FakeRegistry()
    tool = GuiTaskExecuteTool(registry)  # type: ignore[arg-type]
    raw = asyncio.run(
        tool.execute(
            goal="test gui flow",
            steps=[
                {"action": "input.mouse.click", "args": {"x": 10, "y": 20}},
                {"action": "input.keyboard.type", "args": {"text": "hello"}},
                {"action": "screen.screenshot", "args": {}},
            ],
        )
    )
    payload = json.loads(raw)
    assert payload["conservative_mode"] is True
    assert payload["steps"][0]["status"] == "failed"
    assert payload["steps"][1]["status"] == "skipped_conservative"
    assert payload["steps"][2]["status"] == "ok"
    assert payload["benchmark_hook"]["schema"] == "gui-simple-benchmark-hook.v1"
    assert payload["benchmark_hook"]["failed_step_total"] >= 1


def test_gui_task_execute_can_start_in_conservative_mode():
    registry = _FakeRegistry()
    tool = GuiTaskExecuteTool(registry)  # type: ignore[arg-type]
    raw = asyncio.run(
        tool.execute(
            goal="safe mode",
            steps=[{"action": "input.mouse.click", "args": {"x": 1, "y": 2}}],
            conservative_mode=True,
        )
    )
    payload = json.loads(raw)
    assert payload["conservative_mode"] is True
    assert payload["steps"][0]["status"] == "skipped_conservative"
    assert payload["benchmark_hook"]["schema"] == "gui-simple-benchmark-hook.v1"
