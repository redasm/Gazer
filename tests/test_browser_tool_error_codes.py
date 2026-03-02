import asyncio

from tools.browser_tool import BrowserTool


def test_browser_unknown_action_returns_error_code() -> None:
    tool = BrowserTool()
    result = asyncio.run(tool.execute(action="unknown"))
    assert "BROWSER_ACTION_UNKNOWN" in result
