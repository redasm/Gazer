"""GUI grounding contract tests for structured screen.observe output."""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from devices.adapters.local_desktop import LocalDesktopNode
from devices.registry import DeviceRegistry
from perception.capture import CaptureManager
from perception.sources.base import CaptureFrame, ContextSource
from tools.device_tools import NodeInvokeTool


class _StubScreenSource(ContextSource):
    source_type = "screen"
    source_id = "stub-local"

    async def start(self):
        return None

    async def stop(self):
        return None

    async def capture(self):
        return CaptureFrame(
            source_type=self.source_type,
            source_id=self.source_id,
            image=Image.new("RGB", (640, 360), color="black"),
            timestamp=datetime.now(),
        )


@patch("perception.capture.ModelRegistry")
@patch("perception.capture.LLMCognitiveStep")
def test_capture_manager_structured_observation_contract(mock_llm_cls, mock_registry):
    mock_registry.resolve_model.return_value = ("key", "url", "model", {})
    mock_registry.resolve_model_ref.return_value = ("provider", "model")
    mock_registry.get_provider_config.return_value = {}
    vlm_instance = MagicMock()
    vlm_instance.process_with_image = AsyncMock(return_value="Browser window. Search page is active.")
    mock_llm_cls.return_value = vlm_instance

    mm = MagicMock()
    mm.save_entry = AsyncMock()
    manager = CaptureManager(mm, capture_interval=9999)
    manager.register_source(_StubScreenSource())

    payload = asyncio.run(manager.get_latest_observation_structured(query="find active window"))
    assert payload["summary"]
    assert payload["query"] == "find active window"
    assert payload["frame"]["width"] == 640
    assert payload["frame"]["height"] == 360
    assert isinstance(payload["elements"], list) and payload["elements"]

    first = payload["elements"][0]
    assert "type" in first
    assert "text" in first
    assert "coordinates" in first
    assert {"x", "y", "width", "height"} <= set(first["coordinates"].keys())
    assert "confidence" in first


class _StructuredCaptureManager:
    def get_observe_capability(self):
        return True, ""

    async def get_latest_observation(self, query: str):
        return f"fallback summary: {query}"

    async def get_latest_observation_structured(self, query: str):
        return {
            "summary": f"summary: {query}",
            "query": query,
            "frame": {
                "source_type": "screen",
                "source_id": "local",
                "timestamp": "2026-02-18T00:00:00",
                "width": 1920,
                "height": 1080,
            },
            "elements": [
                {
                    "type": "button",
                    "text": "Search",
                    "coordinates": {"x": 120, "y": 88, "width": 160, "height": 48},
                    "confidence": 0.9,
                }
            ],
        }


def test_node_invoke_observe_returns_structured_elements():
    registry = DeviceRegistry(default_target="local-desktop")
    registry.register(LocalDesktopNode(capture_manager=_StructuredCaptureManager(), action_enabled=False))
    raw = asyncio.run(
        NodeInvokeTool(registry).execute(
            action="screen.observe",
            args={"query": "open app"},
        )
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    observation = payload["data"]["observation"]
    assert observation["summary"] == "summary: open app"
    assert observation["elements"][0]["type"] == "button"
    assert observation["elements"][0]["coordinates"]["x"] == 120
    assert float(observation["elements"][0]["confidence"]) > 0.5
