from pathlib import Path

import pytest

from tools.coding_impl import exec_tool as exec_tool_module
from tools.coding_impl import search_tools as search_tools_module
from tools.coding_impl.exec_tool import ExecTool
from tools.coding_impl.native_ops import CodingToolResult
from tools.coding_impl.search_tools import FindFilesTool


@pytest.mark.asyncio
async def test_exec_tool_emits_progress(monkeypatch, tmp_path: Path):
    events = []

    async def _progress(payload):
        events.append(payload)

    async def _fake_native_exec(command, cwd, *, timeout=120.0, progress_callback=None):
        assert command == "echo hello"
        assert progress_callback is not None
        await progress_callback({"stage": "launch", "message": f"Started command in {cwd}"})
        await progress_callback({"stage": "stdout", "message": "[stdout] hello", "stream": "stdout", "line_count": 1})
        return CodingToolResult(text="[exit code: 0]\nhello", is_error=False)

    monkeypatch.setattr(exec_tool_module, "native_exec", _fake_native_exec)

    tool = ExecTool(workspace=tmp_path)
    result = await tool.execute("echo hello", _progress_callback=_progress)

    assert result == "[exit code: 0]\nhello"
    assert events[0]["stage"] == "prepare"
    assert "Preparing exec in" in events[0]["message"]
    assert any(item["stage"] == "launch" for item in events)
    assert any(item["stage"] == "stdout" and item["message"] == "[stdout] hello" for item in events)


@pytest.mark.asyncio
async def test_find_files_tool_emits_progress(monkeypatch, tmp_path: Path):
    events = []

    async def _progress(payload):
        events.append(payload)

    async def _fake_native_find(pattern, workspace, *, search_dir=".", progress_callback=None):
        assert pattern == "*.py"
        assert progress_callback is not None
        await progress_callback({"stage": "scan", "message": f"Searching {search_dir} for {pattern}"})
        await progress_callback({"stage": "summary", "message": "Found 2 file(s)"})
        return CodingToolResult(text="Found 2 file(s)\na.py\nb.py", is_error=False)

    monkeypatch.setattr(search_tools_module, "native_find", _fake_native_find)

    tool = FindFilesTool(workspace=tmp_path)
    result = await tool.execute("*.py", _progress_callback=_progress)

    assert result == "Found 2 file(s)\na.py\nb.py"
    assert events[0]["stage"] == "prepare"
    assert events[0]["message"] == "Finding files in . matching *.py"
    assert any(item["stage"] == "scan" and item["message"] == "Searching . for *.py" for item in events)
    assert any(item["stage"] == "summary" and item["message"] == "Found 2 file(s)" for item in events)
