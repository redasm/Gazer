"""Tests for tools.canvas -- CanvasState + Canvas tools."""

import json
import pytest
from tools.canvas import (
    CanvasState, CanvasPanel, ALLOWED_CONTENT_TYPES,
    A2UIApplyTool, CanvasSnapshotTool, CanvasResetTool,
)


@pytest.fixture
def canvas():
    return CanvasState(max_panels=3, max_content_size=1000)


class TestCanvasState:
    @pytest.mark.asyncio
    async def test_push_creates_panel(self, canvas):
        panel = await canvas.push("p1", "markdown", "# Hello")
        assert panel.id == "p1"
        assert panel.content_type == "markdown"
        assert panel.content == "# Hello"
        assert canvas.version == 1

    @pytest.mark.asyncio
    async def test_push_update_existing(self, canvas):
        await canvas.push("p1", "markdown", "v1")
        await canvas.push("p1", "text", "v2")
        assert len(canvas.panels) == 1
        assert canvas.panels[0].content == "v2"
        assert canvas.panels[0].content_type == "text"
        assert canvas.version == 2

    @pytest.mark.asyncio
    async def test_eviction_when_full(self, canvas):
        await canvas.push("a", "text", "1")
        await canvas.push("b", "text", "2")
        await canvas.push("c", "text", "3")
        # 4th should evict "a"
        await canvas.push("d", "text", "4")
        ids = [p.id for p in canvas.panels]
        assert "a" not in ids
        assert len(ids) == 3

    @pytest.mark.asyncio
    async def test_invalid_content_type(self, canvas):
        with pytest.raises(ValueError, match="Invalid content_type"):
            await canvas.push("p1", "invalid_type", "data")

    @pytest.mark.asyncio
    async def test_content_truncation(self, canvas):
        long_content = "x" * 2000
        panel = await canvas.push("p1", "text", long_content)
        assert len(panel.content) == 1000

    @pytest.mark.asyncio
    async def test_reset_all(self, canvas):
        await canvas.push("a", "text", "1")
        await canvas.push("b", "text", "2")
        removed = await canvas.reset()
        assert removed == 2
        assert len(canvas.panels) == 0

    @pytest.mark.asyncio
    async def test_reset_single(self, canvas):
        await canvas.push("a", "text", "1")
        await canvas.push("b", "text", "2")
        removed = await canvas.reset("a")
        assert removed == 1
        assert len(canvas.panels) == 1

    @pytest.mark.asyncio
    async def test_to_dict(self, canvas):
        await canvas.push("p1", "markdown", "hi")
        d = canvas.to_dict()
        assert "version" in d
        assert len(d["panels"]) == 1

    @pytest.mark.asyncio
    async def test_on_change_callback(self):
        calls = []

        async def on_change(state, extra=None):
            calls.append((state.version, extra))

        canvas = CanvasState(on_change=on_change)
        await canvas.push("p", "text", "data")
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_apply_a2ui_surface_update(self, canvas):
        summary = await canvas.apply_a2ui_messages([
            {"beginRendering": {"surfaceId": "main", "catalog": "standard"}},
            {
                "surfaceUpdate": {
                    "surfaceId": "main",
                    "root": "root",
                    "components": {
                        "root": {
                            "category": "column",
                            "properties": {"children": ["title"]},
                        },
                        "title": {
                            "category": "text",
                            "properties": {"text": "Hello A2UI"},
                        },
                    },
                },
            },
        ])
        assert summary["message_count"] == 2
        assert summary["surface_ids"] == ["main"]

        panel = canvas.get_panel("a2ui:main")
        assert panel is not None
        assert panel.content_type == "a2ui"
        payload = json.loads(panel.content)
        assert payload["surfaceId"] == "main"
        assert payload["root"] == "root"
        assert "root" in payload["components"]

    @pytest.mark.asyncio
    async def test_apply_a2ui_data_model_update(self, canvas):
        await canvas.apply_a2ui_messages([
            {"beginRendering": {"surfaceId": "main", "catalog": "standard"}},
            {
                "dataModelUpdate": {
                    "surfaceId": "main",
                    "path": "$",
                    "contents": [
                        {"key": "name", "valueString": "Ada"},
                        {"key": "count", "valueNumber": 3},
                    ],
                },
            },
        ])
        surface = canvas.get_surface("main")
        assert surface is not None
        assert surface.data_model["name"] == "Ada"
        assert surface.data_model["count"] == 3

    @pytest.mark.asyncio
    async def test_apply_a2ui_delete_surface(self, canvas):
        await canvas.apply_a2ui_messages([
            {"beginRendering": {"surfaceId": "main", "catalog": "standard"}},
            {
                "surfaceUpdate": {
                    "surfaceId": "main",
                    "root": "root",
                    "components": {"root": {"category": "text", "properties": {"text": "x"}}},
                },
            },
        ])
        assert canvas.get_panel("a2ui:main") is not None
        await canvas.apply_a2ui_messages([{"deleteSurface": {"surfaceId": "main"}}])
        assert canvas.get_surface("main") is None
        assert canvas.get_panel("a2ui:main") is None


class TestCanvasTools:
    @pytest.mark.asyncio
    async def test_canvas_snapshot_empty(self, canvas):
        tool = CanvasSnapshotTool(canvas)
        result = await tool.execute()
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_canvas_snapshot_with_data(self, canvas):
        await canvas.push("p1", "text", "hello")
        tool = CanvasSnapshotTool(canvas)
        result = await tool.execute()
        assert "p1" in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_canvas_reset_tool(self, canvas):
        await canvas.push("p1", "text", "data")
        tool = CanvasResetTool(canvas)
        result = await tool.execute()
        assert "cleared" in result.lower()
        assert len(canvas.panels) == 0

    @pytest.mark.asyncio
    async def test_a2ui_apply_tool(self, canvas):
        tool = A2UIApplyTool(canvas)
        result = await tool.execute(
            messages=[
                {"beginRendering": {"surfaceId": "main", "catalog": "standard"}},
                {
                    "surfaceUpdate": {
                        "surfaceId": "main",
                        "root": "root",
                        "components": {"root": {"category": "text", "properties": {"text": "Hello"}}},
                    },
                },
            ]
        )
        assert "A2UI applied" in result
        assert canvas.get_panel("a2ui:main") is not None
