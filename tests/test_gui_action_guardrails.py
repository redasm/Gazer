"""GUI action guardrail tests: click pre-check + post-action verification."""

import asyncio
import sys
from types import SimpleNamespace

from PIL import Image

from devices.adapters.local_desktop import LocalDesktopNode
from devices.adapters.remote_satellite import RemoteSatelliteNode
from devices.models import NodeActionResult
from devices.registry import DeviceRegistry
from perception.sources.base import CaptureFrame
from tools.device_tools import NodeInvokeTool


class _VerificationCaptureManager:
    def __init__(self):
        self._frames = [
            CaptureFrame(source_type="screen", source_id="local", image=Image.new("RGB", (120, 80), "black")),
            CaptureFrame(source_type="screen", source_id="local", image=Image.new("RGB", (120, 80), "black")),
        ]

    def get_observe_capability(self):
        return True, ""

    async def get_latest_observation(self, query: str):
        return f"observed: {query}"

    async def _grab_frame(self):
        if self._frames:
            return self._frames.pop(0)
        return CaptureFrame(source_type="screen", source_id="local", image=Image.new("RGB", (120, 80), "black"))


def test_local_click_guardrail_rejects_out_of_bounds(monkeypatch):
    pyauto = SimpleNamespace(
        click=lambda *args, **kwargs: None,
        press=lambda *args, **kwargs: None,
        size=lambda: SimpleNamespace(width=100, height=100),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", pyauto)

    registry = DeviceRegistry(default_target="local-desktop")
    registry.register(LocalDesktopNode(capture_manager=_VerificationCaptureManager(), action_enabled=True))
    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="input.mouse.click",
            args={"x": 140, "y": 20},
        )
    )
    assert "DEVICE_ACTION_OUT_OF_BOUNDS" in result


def test_local_click_post_verify_failed_can_rollback(monkeypatch):
    calls = {"press": 0}

    def _press(_key):
        calls["press"] += 1

    pyauto = SimpleNamespace(
        click=lambda *args, **kwargs: None,
        press=_press,
        size=lambda: SimpleNamespace(width=200, height=120),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", pyauto)

    registry = DeviceRegistry(default_target="local-desktop")
    registry.register(LocalDesktopNode(capture_manager=_VerificationCaptureManager(), action_enabled=True))
    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="input.mouse.click",
            args={
                "x": 30,
                "y": 20,
                "verify_after": True,
                "rollback_on_failure": True,
                "rollback_hotkey": "esc",
                "verify_settle_seconds": 0.0,
            },
        )
    )
    assert "DEVICE_ACTION_POST_VERIFY_FAILED" in result
    assert calls["press"] == 1


class _FakeSessionManager:
    def __init__(self):
        self.calls = []

    def is_online(self, node_id: str) -> bool:
        return True

    async def send_invoke(self, *, node_id: str, action: str, args: dict, timeout_seconds: float):
        self.calls.append((node_id, action, dict(args), timeout_seconds))
        return NodeActionResult(ok=True, message="ok")


def test_remote_click_guardrail_rejects_negative_coordinates():
    node = RemoteSatelliteNode(
        node_id="sat-01",
        session_manager=_FakeSessionManager(),
        capture_manager=_VerificationCaptureManager(),
    )
    result = asyncio.run(node.invoke("input.mouse.click", {"x": -1, "y": 10}))
    assert result.ok is False
    assert result.code == "DEVICE_ACTION_OUT_OF_BOUNDS"
