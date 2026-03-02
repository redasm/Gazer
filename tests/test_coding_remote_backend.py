"""Tests for coding tools with injected operations.

Verifies that the native implementation reads actual files, and that
GitStatusTool uses injected ShellOperations.
"""

from pathlib import Path

import pytest

from tools.base import FileOperations, ShellOperations
from tools.coding import GitStatusTool, ReadFileTool, WriteFileTool


class _DummyFileOps(FileOperations):
    async def read_file(self, path: str) -> str:
        return "a\nb\nc\n"

    async def file_exists(self, path: str) -> bool:
        return True


class _DummyShellOps(ShellOperations):
    async def exec(self, command: str, cwd: str, *, timeout: int = 30) -> tuple:
        if "git status --porcelain" in command:
            return 0, " M src/main.py\n?? new.txt\n", ""
        return 0, "", ""


@pytest.mark.asyncio
async def test_read_file_tool_reads_actual_file(tmp_path):
    (Path(tmp_path) / "remote.txt").write_text("real-a\nreal-b\n", encoding="utf-8")
    tool = ReadFileTool(Path(tmp_path), file_ops=_DummyFileOps())
    out = await tool.execute("remote.txt", offset=2, limit=2)
    assert "real-b" in out


@pytest.mark.asyncio
async def test_write_file_tool_supports_file_path_alias(tmp_path):
    tool = WriteFileTool(Path(tmp_path))
    out = await tool.execute(file_path="alias-write.txt", content="hello\n")
    assert "Wrote 1 lines to alias-write.txt." in out
    assert (Path(tmp_path) / "alias-write.txt").read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_git_status_tool_uses_injected_shell_ops(tmp_path):
    tool = GitStatusTool(Path(tmp_path), shell_ops=_DummyShellOps())
    out = await tool.execute()
    assert "[M] src/main.py" in out
    assert "[??] new.txt" in out
