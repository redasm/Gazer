"""Verify the perception capture pipeline: Source -> CaptureManager -> MemoryManager."""

import asyncio
import sys
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from perception.sources.base import CaptureFrame, ContextSource
from perception.sources.screen_local import LocalScreenSource
from perception.sources.screen_remote import RemoteScreenSource
from perception.sources.camera_local import LocalCameraSource
from perception.capture import CaptureManager


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _dummy_image(width: int = 64, height: int = 64) -> Image.Image:
    return Image.new("RGB", (width, height), color="blue")


class StubSource(ContextSource):
    """A trivial source that returns a fixed image."""

    source_type = "screen"
    source_id = "stub"

    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def capture(self):
        return CaptureFrame(
            source_type=self.source_type,
            source_id=self.source_id,
            image=_dummy_image(),
            timestamp=datetime.now(),
        )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------
def test_capture_frame_to_base64():
    frame = CaptureFrame(
        source_type="screen",
        source_id="local",
        image=_dummy_image(),
    )
    b64 = frame.to_base64()
    assert isinstance(b64, str)
    assert len(b64) > 0


def test_remote_source_push_and_capture():
    src = RemoteScreenSource(source_id="test-sat")
    img = _dummy_image()
    src.push_frame(img)

    frame = asyncio.run(src.capture())
    assert frame is not None
    assert frame.source_type == "screen"
    assert frame.source_id == "test-sat"
    assert frame.image.size == img.size


def test_remote_source_empty_returns_none():
    src = RemoteScreenSource(source_id="empty")
    frame = asyncio.run(src.capture())
    assert frame is None


def test_remote_source_drops_old_frames():
    src = RemoteScreenSource(source_id="overflow")
    for i in range(20):
        src.push_frame(_dummy_image(width=i + 10, height=i + 10))
    frame = asyncio.run(src.capture())
    assert frame is not None
    # Should get the newest (largest) image
    assert frame.image.size[0] > 10


@patch("perception.capture.ModelRegistry")
@patch("perception.capture.LLMCognitiveStep")
def test_capture_manager_register_and_observation(mock_llm_cls, mock_registry):
    """CaptureManager should use the VLM and return a description."""
    mock_registry.resolve_model.return_value = ("key", "url", "model", {})
    mock_registry.resolve_model_ref.return_value = ("provider", "model")
    vlm_instance = MagicMock()
    vlm_instance.process_with_image = AsyncMock(return_value="User is editing code in VS Code.")
    mock_llm_cls.return_value = vlm_instance

    mm = MagicMock()
    mm.save_entry = AsyncMock()

    mgr = CaptureManager(mm, capture_interval=9999)
    stub = StubSource()
    mgr.register_source(stub)

    assert len(mgr.sources) == 1

    result = asyncio.run(
        mgr.get_latest_observation(query="What is on screen?")
    )
    assert "VS Code" in result or "code" in result.lower()
    vlm_instance.process_with_image.assert_called_once()


@patch("perception.capture.ModelRegistry")
@patch("perception.capture.LLMCognitiveStep")
def test_capture_manager_structured_observation_contains_elements(mock_llm_cls, mock_registry):
    mock_registry.resolve_model.return_value = ("key", "url", "model", {})
    mock_registry.resolve_model_ref.return_value = ("provider", "model")
    vlm_instance = MagicMock()
    vlm_instance.process_with_image = AsyncMock(return_value="VS Code opened. User edits code.")
    mock_llm_cls.return_value = vlm_instance

    mm = MagicMock()
    mm.save_entry = AsyncMock()

    mgr = CaptureManager(mm, capture_interval=9999)
    stub = StubSource()
    mgr.register_source(stub)

    payload = asyncio.run(mgr.get_latest_observation_structured(query="Observe screen"))
    assert payload["summary"]
    assert payload["query"] == "Observe screen"
    assert payload["frame"]["source_type"] == "screen"
    assert isinstance(payload["elements"], list) and payload["elements"]
    first = payload["elements"][0]
    assert "type" in first
    assert "text" in first
    assert "coordinates" in first
    assert "confidence" in first


@patch("perception.capture.ModelRegistry")
@patch("perception.capture.LLMCognitiveStep")
def test_capture_manager_persist_on_passive_loop(mock_llm_cls, mock_registry):
    """Passive loop should persist observations to MemoryManager."""
    mock_registry.resolve_model.return_value = ("key", "url", "model", {})
    mock_registry.resolve_model_ref.return_value = ("provider", "model")
    vlm_instance = MagicMock()
    vlm_instance.process_with_image = AsyncMock(return_value="User browsing docs.")
    mock_llm_cls.return_value = vlm_instance

    mm = MagicMock()
    mm.save_entry = AsyncMock()

    mgr = CaptureManager(mm, capture_interval=9999)
    stub = StubSource()
    mgr.register_source(stub)

    # Manually invoke one cycle of the passive loop
    asyncio.run(
        mgr._capture_loop.__wrapped__(mgr) if hasattr(mgr._capture_loop, "__wrapped__") else _run_one_cycle(mgr)
    )


async def _run_one_cycle(mgr: CaptureManager):
    """Run exactly one iteration of the capture loop logic."""
    for key, src in mgr._sources.items():
        frame = await src.capture()
        if frame:
            desc = await mgr._analyze(frame)
            if desc:
                await mgr._persist(frame, desc)


@patch("perception.capture.ModelRegistry")
@patch("perception.capture.LLMCognitiveStep")
def test_persist_writes_memory_entry(mock_llm_cls, mock_registry):
    mock_registry.resolve_model.return_value = ("k", "u", "m", {})
    mock_registry.resolve_model_ref.return_value = ("provider", "model")
    vlm_instance = MagicMock()
    vlm_instance.process_with_image = AsyncMock(return_value="Terminal open.")
    mock_llm_cls.return_value = vlm_instance

    mm = MagicMock()
    mm.save_entry = AsyncMock()

    mgr = CaptureManager(mm)
    stub = StubSource()
    mgr.register_source(stub)

    asyncio.run(_run_one_cycle(mgr))
    mm.save_entry.assert_called_once()
    entry = mm.save_entry.call_args[0][0]
    assert "screen:stub" in entry.content
    assert "Terminal" in entry.content


if __name__ == "__main__":
    test_capture_frame_to_base64()
    print("PASS test_capture_frame_to_base64")

    test_remote_source_push_and_capture()
    print("PASS test_remote_source_push_and_capture")

    test_remote_source_empty_returns_none()
    print("PASS test_remote_source_empty_returns_none")

    test_remote_source_drops_old_frames()
    print("PASS test_remote_source_drops_old_frames")

    test_capture_manager_register_and_observation()
    print("PASS test_capture_manager_register_and_observation")

    test_persist_writes_memory_entry()
    print("PASS test_persist_writes_memory_entry")

    print("\nAll capture pipeline tests passed.")
