"""Browser tool: action-based Playwright wrapper (single tool, multiple operations)."""

import logging
from typing import Any, Dict, Optional

from tools.base import Tool, ToolSafetyTier

logger = logging.getLogger("BrowserTool")


class BrowserTool(Tool):
    """Control a headless browser for web automation and scraping."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None

    @property
    def name(self) -> str:
        return "browser"

    @property
    def provider(self) -> str:
        return "browser"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.PRIVILEGED

    @property
    def description(self) -> str:
        return (
            "Control a headless browser. Actions: "
            "start (launch browser), "
            "open (navigate to URL), "
            "snapshot (get page text/accessibility tree), "
            "screenshot (capture page image), "
            "act (click/type/press on elements), "
            "close (shut down browser)."
        )

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "open", "snapshot", "screenshot", "act", "close"],
                    "description": "Browser action to perform.",
                },
                "url": {
                    "type": "string",
                    "description": "URL for 'open' action.",
                },
                "act_type": {
                    "type": "string",
                    "enum": ["click", "type", "press"],
                    "description": "Interaction type for 'act' action.",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for 'act' action.",
                },
                "text": {
                    "type": "string",
                    "description": "Text for 'type' act_type, or key for 'press'.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        dispatch = {
            "start": self._start,
            "open": self._open,
            "snapshot": self._snapshot,
            "screenshot": self._screenshot,
            "act": self._act,
            "close": self._close,
        }
        handler = dispatch.get(action)
        if not handler:
            return self._error("BROWSER_ACTION_UNKNOWN", f"unknown action '{action}'.")
        return await handler(**kwargs)

    async def _ensure_browser(self) -> Optional[str]:
        """Ensure browser is running. Returns error string if Playwright unavailable."""
        if self._page:
            return None
        return await self._start()

    async def _start(self, **_: Any) -> str:
        if self._page:
            return "Browser already running."
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return self._error(
                "BROWSER_DEPENDENCY_MISSING",
                "playwright is not installed. Run: pip install playwright && playwright install chromium",
            )

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            )
        )
        self._page = await ctx.new_page()
        return "Browser started."

    async def _open(self, url: str = "", **_: Any) -> str:
        if not url:
            return self._error("BROWSER_URL_REQUIRED", "'url' is required for 'open' action.")
        err = await self._ensure_browser()
        if err and err.startswith("Error"):
            return err

        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await self._page.title()
            return f"Navigated to: {url}\nTitle: {title}"
        except Exception as exc:
            return self._error("BROWSER_NAVIGATE_FAILED", f"navigating to {url} failed: {exc}")

    async def _snapshot(self, **_: Any) -> str:
        if not self._page:
            return self._error("BROWSER_NOT_STARTED", "browser not started. Use action='start' first.")
        try:
            title = await self._page.title()
            url = self._page.url
            # Extract visible text as a lightweight snapshot
            text = await self._page.evaluate(
                "() => document.body.innerText.substring(0, 8000)"
            )
            return f"URL: {url}\nTitle: {title}\n---\n{text}"
        except Exception as exc:
            return self._error("BROWSER_SNAPSHOT_FAILED", f"taking snapshot failed: {exc}")

    async def _screenshot(self, **_: Any) -> str:
        if not self._page:
            return self._error("BROWSER_NOT_STARTED", "browser not started.")
        try:
            import tempfile, os
            path = os.path.join(tempfile.gettempdir(), "gazer_browser_screenshot.png")
            await self._page.screenshot(path=path, full_page=False)
            return f"Screenshot saved to: {path}"
        except Exception as exc:
            return self._error("BROWSER_SCREENSHOT_FAILED", f"taking screenshot failed: {exc}")

    async def _act(self, act_type: str = "", selector: str = "", text: str = "", **_: Any) -> str:
        if not self._page:
            return self._error("BROWSER_NOT_STARTED", "browser not started.")
        if not act_type or not selector:
            return self._error(
                "BROWSER_ACT_ARGS_REQUIRED",
                "'act_type' and 'selector' are required for 'act' action.",
            )

        try:
            if act_type == "click":
                await self._page.click(selector, timeout=10000)
                return f"Clicked: {selector}"
            elif act_type == "type":
                await self._page.fill(selector, text, timeout=10000)
                return f"Typed into {selector}: {text[:50]}..."
            elif act_type == "press":
                await self._page.press(selector, text, timeout=10000)
                return f"Pressed {text} on {selector}"
            else:
                return self._error("BROWSER_ACT_TYPE_UNKNOWN", f"unknown act_type '{act_type}'.")
        except Exception as exc:
            return self._error(
                "BROWSER_ACT_FAILED",
                f"performing {act_type} on {selector} failed: {exc}",
            )

    async def _close(self, **_: Any) -> str:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        self._page = None
        return "Browser closed."
