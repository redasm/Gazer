import asyncio
import json
from types import SimpleNamespace

from PIL import Image

from devices.adapters.local_desktop import LocalDesktopNode
from devices.registry import DeviceRegistry
from runtime.rust_sidecar import RustSidecarError
from tools.device_tools import NodeDescribeTool, NodeInvokeTool, NodeListTool


class _FakeCaptureManager:
    def get_observe_capability(self):
        return True, ""

    async def get_latest_observation(self, query: str):
        return f"observed: {query}"

    async def get_latest_observation_structured(self, query: str):
        return {
            "summary": f"observed: {query}",
            "query": query,
            "frame": {
                "source_type": "screen",
                "source_id": "local",
                "timestamp": "2026-02-18T00:00:00",
                "width": 1280,
                "height": 720,
            },
            "elements": [
                {
                    "type": "text_block",
                    "text": "VS Code",
                    "coordinates": {"x": 10, "y": 12, "width": 500, "height": 42},
                    "confidence": 0.88,
                }
            ],
        }

    async def _grab_frame(self):
        image = Image.new("RGB", (16, 16), color="blue")
        return SimpleNamespace(image=image)


class _UnavailableCaptureManager(_FakeCaptureManager):
    def get_observe_capability(self):
        return False, "Vision model unavailable in test."


def test_node_list_and_describe_tools() -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    registry.register(
        LocalDesktopNode(
            capture_manager=_FakeCaptureManager(),
            action_enabled=False,
        )
    )

    list_result = asyncio.run(NodeListTool(registry).execute())
    payload = json.loads(list_result)
    assert payload["default_target"] == "local-desktop"
    assert len(payload["nodes"]) == 1
    assert payload["nodes"][0]["node_id"] == "local-desktop"
    assert any(cap["action"] == "screen.observe" for cap in payload["nodes"][0]["capabilities"])

    describe_result = asyncio.run(NodeDescribeTool(registry).execute())
    detail = json.loads(describe_result)
    assert detail["node_id"] == "local-desktop"
    assert detail["kind"] == "desktop.local"
    assert detail["metadata"]["backend"] == "python"


def test_node_invoke_observe_success() -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    registry.register(LocalDesktopNode(capture_manager=_FakeCaptureManager()))

    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.observe",
            args={"query": "what is open"},
        )
    )
    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["data"]["observation"]["summary"] == "observed: what is open"
    first = payload["data"]["observation"]["elements"][0]
    assert first["type"] == "text_block"
    assert first["coordinates"]["x"] == 10
    assert float(first["confidence"]) > 0.5


def test_node_invoke_rejects_unsupported_action() -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    registry.register(
        LocalDesktopNode(
            capture_manager=_FakeCaptureManager(),
            action_enabled=False,
        )
    )

    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="input.mouse.click",
            args={"x": 10, "y": 20},
        )
    )
    assert "DEVICE_ACTION_UNSUPPORTED" in result
    assert "not supported" in result.lower()


def test_node_invoke_screenshot_uses_node_native_capture(monkeypatch) -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    node = LocalDesktopNode(capture_manager=_FakeCaptureManager(), action_enabled=False)

    monkeypatch.setattr(
        node,
        "_capture_screenshot_to_file",
        lambda: (True, "C:/tmp/screenshot_test.png"),
    )
    registry.register(node)

    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.screenshot",
            args={},
        )
    )
    assert "Screenshot captured." in result
    assert "screenshot_test.png" in result


def test_node_screenshot_capability_hidden_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        LocalDesktopNode,
        "_detect_screenshot_support",
        staticmethod(lambda: (False, "Screenshot unavailable in test.")),
    )
    registry = DeviceRegistry(default_target="local-desktop")
    node = LocalDesktopNode(capture_manager=_FakeCaptureManager(), action_enabled=False)
    registry.register(node)

    list_result = asyncio.run(NodeListTool(registry).execute())
    payload = json.loads(list_result)
    actions = [cap["action"] for cap in payload["nodes"][0]["capabilities"]]
    assert "screen.screenshot" not in actions

    detail_result = asyncio.run(NodeDescribeTool(registry).execute())
    detail = json.loads(detail_result)
    assert detail["metadata"]["screenshot_available"] is False
    assert "unavailable" in detail["metadata"]["screenshot_unavailable_reason"].lower()

    invoke_result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.screenshot",
            args={},
        )
    )
    assert "DEVICE_ACTION_UNSUPPORTED" in invoke_result


def test_node_observe_capability_hidden_when_capture_pipeline_unavailable() -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    node = LocalDesktopNode(capture_manager=_UnavailableCaptureManager(), action_enabled=False)
    registry.register(node)

    list_result = asyncio.run(NodeListTool(registry).execute())
    payload = json.loads(list_result)
    actions = [cap["action"] for cap in payload["nodes"][0]["capabilities"]]
    assert "screen.observe" not in actions

    detail_result = asyncio.run(NodeDescribeTool(registry).execute())
    detail = json.loads(detail_result)
    assert detail["metadata"]["capture_available"] is False
    assert "unavailable" in detail["metadata"]["capture_unavailable_reason"].lower()

    invoke_result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.observe",
            args={"query": "what is open"},
        )
    )
    assert "DEVICE_ACTION_UNSUPPORTED" in invoke_result
    assert "not supported" in invoke_result.lower()


def test_node_invoke_rejects_invalid_args_with_code() -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    registry.register(LocalDesktopNode(capture_manager=_FakeCaptureManager(), action_enabled=False))

    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.observe",
            args="not-an-object",
        )
    )
    assert "DEVICE_ARGS_INVALID" in result


class _FakeRustClient:
    def __init__(self) -> None:
        self.calls = []
        self._error: Exception | None = None

    def set_error(self, error: Exception) -> None:
        self._error = error

    async def rpc(self, *, method: str, params=None, trace_id: str = ""):
        self.calls.append((method, dict(params or {}), trace_id))
        if self._error is not None:
            raise self._error
        if method == "desktop.screen.screenshot":
            return {"message": "Screenshot captured (rust).", "media_path": "C:/tmp/rust_shot.png"}
        if method == "desktop.input.mouse.click":
            return {"message": "Clicked via rust."}
        if method == "desktop.input.keyboard.type":
            return {"message": "Typed via rust."}
        if method == "desktop.input.keyboard.hotkey":
            return {"message": "Hotkey via rust."}
        return {}


def test_node_invoke_rust_backend_screenshot_and_inputs() -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    rust_client = _FakeRustClient()
    node = LocalDesktopNode(
        capture_manager=_FakeCaptureManager(),
        action_enabled=True,
        backend="rust",
        rust_client=rust_client,  # type: ignore[arg-type]
    )
    registry.register(node)

    describe_result = asyncio.run(NodeDescribeTool(registry).execute())
    detail = json.loads(describe_result)
    assert detail["metadata"]["backend"] == "rust"

    screenshot_result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.screenshot",
            args={},
        )
    )
    assert "Screenshot captured (rust)." in screenshot_result
    assert "rust_shot.png" in screenshot_result

    click_result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="input.mouse.click",
            args={"x": 1, "y": 2},
        )
    )
    assert "Clicked via rust." in click_result

    type_result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="input.keyboard.type",
            args={"text": "hello"},
        )
    )
    assert "Typed via rust." in type_result

    hotkey_result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="input.keyboard.hotkey",
            args={"keys": ["ctrl", "c"]},
        )
    )
    assert "Hotkey via rust." in hotkey_result

    methods = [item[0] for item in rust_client.calls]
    assert methods == [
        "desktop.screen.screenshot",
        "desktop.input.mouse.click",
        "desktop.input.keyboard.type",
        "desktop.input.keyboard.hotkey",
    ]


def test_node_invoke_rust_backend_maps_sidecar_errors() -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    rust_client = _FakeRustClient()
    rust_client.set_error(
        RustSidecarError(
            code="NOT_SUPPORTED",
            message="unsupported in sidecar",
            trace_id="trc_rust_x",
        )
    )
    node = LocalDesktopNode(
        capture_manager=_FakeCaptureManager(),
        action_enabled=True,
        backend="rust",
        rust_client=rust_client,  # type: ignore[arg-type]
    )
    registry.register(node)

    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="input.mouse.click",
            args={"x": 1, "y": 2},
        )
    )
    assert "DEVICE_ACTION_UNSUPPORTED" in result
    assert "trace_id=trc_rust_x" in result


def test_node_invoke_rust_backend_rollout_gate_falls_back_to_python(monkeypatch) -> None:
    registry = DeviceRegistry(default_target="local-desktop")
    rust_client = _FakeRustClient()
    node = LocalDesktopNode(
        capture_manager=_FakeCaptureManager(),
        action_enabled=False,
        backend="rust",
        rust_client=rust_client,  # type: ignore[arg-type]
    )
    monkeypatch.setattr("devices.adapters.local_desktop.is_rust_allowed_for_current_context", lambda: False)
    monkeypatch.setattr(node, "_capture_screenshot_to_file", lambda: (True, "C:/tmp/python_gate_shot.png"))
    registry.register(node)

    result = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.screenshot",
            args={},
        )
    )
    assert "python_gate_shot.png" in result
    assert rust_client.calls == []
