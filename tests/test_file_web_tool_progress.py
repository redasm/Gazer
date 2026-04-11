from pathlib import Path
import sys
import types

import pytest

from tools.coding_impl import file_tools as file_tools_module
from tools.coding_impl.file_tools import ReadFileTool
from tools.coding_impl.native_ops import CodingToolResult
from tools.web_impl import fetch as fetch_module
from tools.web_impl import search as search_module
from tools.web_impl.fetch import WebFetchTool
from tools.web_impl.search import WebSearchTool


@pytest.mark.asyncio
async def test_read_file_tool_emits_progress(monkeypatch, tmp_path: Path):
    events = []

    async def _progress(payload):
        events.append(payload)

    async def _fake_native_read_file(path, workspace, *, offset=1, limit=500):
        assert path == "src/app.py"
        return CodingToolResult(text="[lines 1-2 of 2]\n1|a\n2|b", is_error=False)

    monkeypatch.setattr(file_tools_module, "native_read_file", _fake_native_read_file)

    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path="src/app.py", _progress_callback=_progress)

    assert result.startswith("[src/app.py]")
    assert events[0]["stage"] == "prepare"
    assert events[0]["message"] == "Reading src/app.py (offset=1, limit=500)"
    assert events[1]["stage"] == "summary"
    assert events[1]["message"] == "Read src/app.py"


@pytest.mark.asyncio
async def test_web_fetch_tool_emits_progress(monkeypatch):
    events = []

    async def _progress(payload):
        events.append(payload)

    class _Resp:
        text = "<html><body>Hello fetch</body></html>"

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            assert url == "https://example.com"
            return _Resp()

    monkeypatch.setattr(fetch_module, "_cache_get", lambda key: None)
    monkeypatch.setattr(fetch_module, "_cache_set", lambda key, value: None)
    fake_httpx = types.SimpleNamespace(AsyncClient=lambda **kwargs: _Client())
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setattr(WebFetchTool, "_is_private_ip", staticmethod(lambda hostname: False))
    monkeypatch.setattr(WebFetchTool, "_extract_text", staticmethod(lambda html: "Hello fetch"))

    tool = WebFetchTool()
    result = await tool.execute("https://example.com", _progress_callback=_progress)

    assert result == "Hello fetch"
    assert events[0]["message"] == "Fetching https://example.com"
    assert any(item["stage"] == "network" for item in events)
    assert events[-1]["message"] == "Extracted 11 chars from https://example.com"


@pytest.mark.asyncio
async def test_web_search_tool_emits_progress(monkeypatch):
    events = []

    async def _progress(payload):
        events.append(payload)

    monkeypatch.setattr(search_module, "_cache_get", lambda key: None)
    monkeypatch.setattr(search_module, "_cache_set", lambda key, value: None)
    monkeypatch.setattr(
        search_module,
        "config",
        type(
            "Cfg",
            (),
            {
                "get": staticmethod(
                    lambda key, default=None: {
                        "web.search.primary_provider": "duckduckgo",
                        "web.search.primary_only": True,
                        "web.search.providers_order": ["duckduckgo"],
                        "web.search.providers_enabled": {},
                        "web.search.relevance_gate": {"enabled": False},
                        "web.search.scenario_routing": {"enabled": False},
                    }.get(key, default)
                )
            },
        ),
    )

    async def _fake_search_with_provider(self, *, provider, query, count, brave_key, perplexity_cfg):
        assert provider == "duckduckgo"
        return "1. Example\n   https://example.com\n   snippet"

    monkeypatch.setattr(WebSearchTool, "_search_with_provider", _fake_search_with_provider)
    monkeypatch.setattr(WebSearchTool, "_record_search_observation", staticmethod(lambda payload: None))

    tool = WebSearchTool()
    result = await tool.execute("example query", _progress_callback=_progress)

    assert "https://example.com" in result
    assert events[0]["message"] == 'Searching web for "example query"'
    assert any(item["stage"] == "provider" and item["message"] == 'Trying duckduckgo for "example query"' for item in events)
    assert events[-1]["message"] == "Search finished via duckduckgo with 1 result(s)"
