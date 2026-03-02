import asyncio
import base64
from io import BytesIO

from PIL import Image

from devices.adapters.remote_satellite import RemoteSatelliteNode
from devices.models import NodeActionResult


class _FakeCaptureManager:
    def get_observe_capability(self):
        return True, ""

    async def get_latest_observation(self, query: str):
        return f"remote observed: {query}"

    async def get_latest_observation_structured(self, query: str):
        return {
            "summary": f"remote observed: {query}",
            "query": query,
            "frame": {
                "source_type": "screen",
                "source_id": "sat-01",
                "timestamp": "2026-02-18T00:00:00",
                "width": 1920,
                "height": 1080,
            },
            "elements": [
                {
                    "type": "text_block",
                    "text": "Browser",
                    "coordinates": {"x": 100, "y": 80, "width": 600, "height": 48},
                    "confidence": 0.84,
                }
            ],
        }


class _UnavailableCaptureManager(_FakeCaptureManager):
    def get_observe_capability(self):
        return False, "Remote vision unavailable in test."


class _FakeSessionManager:
    def __init__(self, result: NodeActionResult, online: bool = True):
        self._result = result
        self._online = online
        self.calls = []

    def is_online(self, node_id: str) -> bool:
        return self._online

    async def send_invoke(self, *, node_id: str, action: str, args: dict, timeout_seconds: float):
        self.calls.append((node_id, action, args, timeout_seconds))
        return self._result


def _png_b64() -> str:
    image = Image.new("RGB", (4, 4), color="red")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def test_remote_node_observe_uses_capture_pipeline() -> None:
    manager = _FakeSessionManager(NodeActionResult(ok=True, message="unused"))
    node = RemoteSatelliteNode(
        node_id="sat-01",
        session_manager=manager,
        capture_manager=_FakeCaptureManager(),
    )
    result = asyncio.run(node.invoke("screen.observe", {"query": "open windows"}))
    assert result.ok is True
    assert "open windows" in result.message
    assert "observation" in result.data
    assert result.data["observation"]["elements"][0]["type"] == "text_block"
    info = node.info().to_dict()
    assert info["metadata"]["transport_backend"] == "python"


def test_remote_node_invoke_maps_media_payload() -> None:
    manager = _FakeSessionManager(
        NodeActionResult(
            ok=True,
            message="Screenshot captured.",
            data={"media_b64": _png_b64(), "media_format": "png"},
        )
    )
    node = RemoteSatelliteNode(node_id="sat-01", session_manager=manager, capture_manager=_FakeCaptureManager())
    result = asyncio.run(node.invoke("screen.screenshot", {}))
    assert result.ok is True
    assert "media_path" in result.data


def test_remote_node_hides_observe_capability_when_capture_unavailable() -> None:
    manager = _FakeSessionManager(NodeActionResult(ok=True, message="unused"))
    node = RemoteSatelliteNode(
        node_id="sat-01",
        session_manager=manager,
        capture_manager=_UnavailableCaptureManager(),
    )
    info = node.info().to_dict()
    actions = [cap["action"] for cap in info["capabilities"]]
    assert "screen.observe" not in actions
    assert info["metadata"]["capture_available"] is False
    assert "unavailable" in info["metadata"]["capture_unavailable_reason"].lower()

    result = asyncio.run(node.invoke("screen.observe", {"query": "open windows"}))
    assert result.ok is False
    assert result.code == "DEVICE_CAPTURE_UNAVAILABLE"
    assert "unavailable" in result.message.lower()
