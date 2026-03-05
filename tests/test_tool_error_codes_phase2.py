import asyncio

from tools.base import Tool
from tools.cron_tool import CronTool
from tools.hardware import HardwareControlTool
from tools.registry import ToolRegistry
from tools.system_tools import ImageAnalyzeTool


class _DummyScheduler:
    def list_jobs(self):
        return []


class _DummyBody:
    def set_actuator(self, *_args, **_kwargs):
        return None

    def set_leds(self, *_args, **_kwargs):
        return None


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    @property
    def owner_only(self):
        return False

    @property
    def provider(self):
        return "core"

    async def execute(self, **kwargs):
        return "ok"


def test_cron_unknown_action_returns_error_code() -> None:
    tool = CronTool(_DummyScheduler())
    result = asyncio.run(tool.execute(action="unknown"))
    assert "CRON_ACTION_UNKNOWN" in result


def test_system_image_analyze_missing_file_returns_error_code() -> None:
    tool = ImageAnalyzeTool()
    result = asyncio.run(tool.execute(image_path="not_exists.png"))
    assert "SYSTEM_IMAGE_NOT_FOUND" in result


def test_hardware_control_missing_args_returns_error_code() -> None:
    tool = HardwareControlTool(_DummyBody())
    result = asyncio.run(tool.execute(action="move_head"))
    assert "HARDWARE_MOVE_HEAD_ARGS_REQUIRED" in result


def test_registry_not_found_returns_error_code() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    result = asyncio.run(registry.execute("missing", {}))
    assert "TOOL_NOT_FOUND" in result
