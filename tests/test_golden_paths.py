"""Golden path integration tests.

These tests validate realistic multi-step flows across tool groups instead of
isolated single-function behaviors.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from devices.adapters.local_desktop import LocalDesktopNode
from devices.registry import DeviceRegistry
from tools.browser_tool import BrowserTool
from tools.coding import EditFileTool, FindFilesTool, GrepTool, ReadFileTool, WriteFileTool
from tools.device_tools import NodeInvokeTool, NodeListTool
from tools.email_tools import EmailReadTool, EmailSendTool
from tools.media_marker import MEDIA_MARKER
from tools.registry import ToolRegistry
from tools.web_tools import WebFetchTool, WebSearchTool


class _FakeCaptureManager:
    def get_observe_capability(self):
        return True, ""

    async def get_latest_observation(self, query: str):
        return f"observed: {query}"


class _FakeEmailClient:
    async def _ensure_imap(self):
        return None

    async def find_uid_by_message_id(self, message_id: str, folder: str = "INBOX"):
        return "101" if message_id == "<m101@example.com>" else None

    async def fetch_message(self, uid: str, folder: str = "INBOX"):
        return SimpleNamespace(
            subject="Golden Subject",
            sender="alice@example.com",
            to="owner@example.com",
            cc="",
            date="2026-02-13",
            message_id="<m101@example.com>",
            body_text="Golden body",
            body_html="",
            attachments=[],
        )

    async def send_message(self, to: str, subject: str, body: str, cc: str = "", reply_to: str = ""):
        return f"Email sent successfully to {to}"


@pytest.mark.asyncio
async def test_golden_path_coding_chain(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(WriteFileTool(tmp_path))
    registry.register(ReadFileTool(tmp_path))
    registry.register(EditFileTool(tmp_path))
    registry.register(FindFilesTool(tmp_path))
    registry.register(GrepTool(tmp_path))

    result = await registry.execute(
        "write_file",
        {"path": "docs/notes.txt", "content": "hello\nworld\n"},
    )
    assert "Wrote 2 lines" in result

    result = await registry.execute(
        "edit_file",
        {"path": "docs/notes.txt", "old_text": "world", "new_text": "gazer"},
    )
    assert "replaced 1 occurrence" in result

    result = await registry.execute("read_file", {"path": "docs/notes.txt", "limit": 20})
    assert "gazer" in result

    result = await registry.execute("find_files", {"pattern": "**/*.txt", "path": "."})
    assert "docs/notes.txt" in result.replace("\\", "/")

    result = await registry.execute("grep", {"pattern": "gazer", "path": ".", "literal": True})
    assert "notes.txt" in result


@pytest.mark.asyncio
async def test_golden_path_registry_param_validation(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(WriteFileTool(tmp_path))

    # Missing required "content" should be blocked by registry param validation.
    result = await registry.execute("write_file", {"path": "a.txt"})
    assert "TOOL_PARAMS_INVALID" in result


@pytest.mark.asyncio
async def test_golden_path_web_search_cache_hit(monkeypatch):
    tool = WebSearchTool()
    registry = ToolRegistry()
    registry.register(tool)

    calls = {"n": 0}
    query = f"gazer-cache-{uuid.uuid4().hex[:8]}"

    async def _fake_duckduckgo_search(_query: str, _count: int) -> str:
        calls["n"] += 1
        return "result-line"

    monkeypatch.setattr(tool, "_duckduckgo_search", _fake_duckduckgo_search)

    result1 = await registry.execute("web_search", {"query": query, "count": 3})
    result2 = await registry.execute("web_search", {"query": query, "count": 3})
    assert result1 == "result-line"
    assert result2 == "result-line"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_golden_path_web_fetch_validation_error_code():
    registry = ToolRegistry()
    registry.register(WebFetchTool())
    result = await registry.execute("web_fetch", {"url": "ftp://example.com"})
    assert "WEB_URL_INVALID" in result


@pytest.mark.asyncio
async def test_golden_path_device_observe_and_screenshot(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        LocalDesktopNode,
        "_detect_screenshot_support",
        staticmethod(lambda: (True, "")),
    )

    device_registry = DeviceRegistry(default_target="local-desktop")
    node = LocalDesktopNode(capture_manager=_FakeCaptureManager(), action_enabled=False)
    fake_shot = tmp_path / "shot.png"
    monkeypatch.setattr(node, "_capture_screenshot_to_file", lambda: (True, str(fake_shot)))
    device_registry.register(node)

    registry = ToolRegistry()
    registry.register(NodeListTool(device_registry))
    registry.register(NodeInvokeTool(device_registry))

    listed = await registry.execute("node_list", {})
    payload = json.loads(listed)
    actions = [cap["action"] for cap in payload["nodes"][0]["capabilities"]]
    assert "screen.observe" in actions
    assert "screen.screenshot" in actions

    observed = await registry.execute(
        "node_invoke",
        {"action": "screen.observe", "args": {"query": "active app"}},
        confirmed=True,
    )
    assert "observed: active app" in observed

    shot = await registry.execute(
        "node_invoke",
        {"action": "screen.screenshot", "args": {}},
        confirmed=True,
    )
    assert MEDIA_MARKER in shot
    assert "shot.png" in shot


@pytest.mark.asyncio
async def test_golden_path_device_hidden_capability_returns_stable_code(monkeypatch):
    monkeypatch.setattr(
        LocalDesktopNode,
        "_detect_screenshot_support",
        staticmethod(lambda: (False, "unavailable")),
    )
    device_registry = DeviceRegistry(default_target="local-desktop")
    device_registry.register(LocalDesktopNode(capture_manager=_FakeCaptureManager(), action_enabled=False))

    registry = ToolRegistry()
    registry.register(NodeInvokeTool(device_registry))

    result = await registry.execute(
        "node_invoke",
        {"action": "screen.screenshot", "args": {}},
        confirmed=True,
    )
    assert "DEVICE_ACTION_UNSUPPORTED" in result


@pytest.mark.asyncio
async def test_golden_path_email_read_and_send():
    client = _FakeEmailClient()
    registry = ToolRegistry()
    registry.register(EmailReadTool(client))
    registry.register(EmailSendTool(client))

    read_result = await registry.execute(
        "email_read",
        {"message_id": "<m101@example.com>", "folder": "INBOX"},
    )
    assert "Golden Subject" in read_result
    assert "Golden body" in read_result

    send_result = await registry.execute(
        "email_send",
        {"to": "alice@example.com", "subject": "Re: Golden", "body": "ok"},
    )
    assert "Email sent successfully" in send_result


def test_golden_path_browser_unknown_action_error_code():
    tool = BrowserTool()
    result = asyncio.run(tool.execute(action="unknown"))
    assert "BROWSER_ACTION_UNKNOWN" in result
