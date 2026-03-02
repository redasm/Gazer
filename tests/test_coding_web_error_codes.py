import asyncio
from pathlib import Path

from tools.coding import ReadFileTool
from tools.web_tools import WebFetchTool


def test_read_file_returns_stable_error_code_for_outside_workspace() -> None:
    workspace = Path.cwd()
    tool = ReadFileTool(workspace)
    result = asyncio.run(tool.execute(file_path="../outside.txt"))
    assert "CODING_PATH_OUTSIDE_WORKSPACE" in result


def test_web_fetch_returns_stable_error_code_for_invalid_url() -> None:
    tool = WebFetchTool()
    result = asyncio.run(tool.execute(url="ftp://example.com"))
    assert "WEB_URL_INVALID" in result
