import pytest

from tools.web_tools import WebSearchTool, WebReportTool, config, _cache


@pytest.mark.asyncio
async def test_web_search_prefers_ddg_html_when_no_brave_key(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_html(query: str, count: int):
        return "1. Result Title\n   https://example.com\n   snippet"

    async def _fake_instant(query: str, count: int):
        return "instant"

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "" if key == "web.search.brave_api_key" else default,
    )
    monkeypatch.setattr(tool, "_duckduckgo_html_search", _fake_html)
    monkeypatch.setattr(tool, "_duckduckgo_instant_answer", _fake_instant)

    out = await tool.execute("latest movie rating", 3)
    assert "Result Title" in out


@pytest.mark.asyncio
async def test_web_search_falls_back_to_instant_when_html_empty(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_html(query: str, count: int):
        return None

    async def _fake_instant(query: str, count: int):
        return "Summary: fallback"

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "" if key == "web.search.brave_api_key" else default,
    )
    monkeypatch.setattr(tool, "_duckduckgo_html_search", _fake_html)
    monkeypatch.setattr(tool, "_duckduckgo_instant_answer", _fake_instant)

    out = await tool.execute("bing-fallback-query", 2)
    assert "fallback" in out


@pytest.mark.asyncio
async def test_web_search_falls_back_to_bing_when_ddg_paths_empty(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_html(query: str, count: int):
        return None

    async def _fake_instant(query: str, count: int):
        return "No results found."

    async def _fake_bing(query: str, count: int):
        return "1. Bing Result\n   https://bing.example\n   latest info"

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "" if key == "web.search.brave_api_key" else default,
    )
    monkeypatch.setattr(tool, "_duckduckgo_html_search", _fake_html)
    monkeypatch.setattr(tool, "_duckduckgo_instant_answer", _fake_instant)
    monkeypatch.setattr(tool, "_bing_rss_search", _fake_bing)

    out = await tool.execute("query", 2)
    assert "Bing Result" in out


@pytest.mark.asyncio
async def test_web_search_prefers_config_brave_key_over_env(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_brave(query: str, count: int, api_key: str):
        return f"brave:{api_key}"

    async def _fake_ddg(query: str, count: int):
        return "ddg"

    monkeypatch.setenv("BRAVE_API_KEY", "env-key")
    monkeypatch.setattr(config, "get", lambda key, default=None: "config-key" if key == "web.search.brave_api_key" else default)
    monkeypatch.setattr(tool, "_brave_search", _fake_brave)
    monkeypatch.setattr(tool, "_duckduckgo_search", _fake_ddg)

    out = await tool.execute("config precedence query", 3)
    assert out == "brave:config-key"


@pytest.mark.asyncio
async def test_web_search_respects_provider_order_and_enabled(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_ddg(query: str, count: int):
        return "ddg-result"

    async def _fake_bing(query: str, count: int):
        return "bing-result"

    def _fake_get(key: str, default=None):
        if key == "web.search.brave_api_key":
            return ""
        if key == "web.search.providers_order":
            return ["duckduckgo", "bing_rss", "wikipedia", "brave"]
        if key == "web.search.providers_enabled":
            return {"duckduckgo": False, "bing_rss": True, "wikipedia": True, "brave": True}
        return default

    monkeypatch.setattr(config, "get", _fake_get)
    monkeypatch.setattr(tool, "_duckduckgo_search", _fake_ddg)
    monkeypatch.setattr(tool, "_bing_rss_search", _fake_bing)

    out = await tool.execute("provider-order-query", 3)
    assert out == "bing-result"


@pytest.mark.asyncio
async def test_web_search_scene_routing_uses_profile(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_wiki(query: str, count: int):
        return "wiki-result"

    def _fake_get(key: str, default=None):
        if key == "web.search.brave_api_key":
            return ""
        if key == "web.search.providers_order":
            return ["brave", "duckduckgo", "bing_rss", "wikipedia"]
        if key == "web.search.providers_enabled":
            return {"brave": True, "duckduckgo": True, "bing_rss": True, "wikipedia": True}
        if key == "web.search.scenario_routing":
            return {
                "enabled": True,
                "auto_detect": False,
                "profiles": {
                    "reference": ["wikipedia", "duckduckgo", "brave", "bing_rss"],
                },
            }
        return default

    monkeypatch.setattr(config, "get", _fake_get)
    monkeypatch.setattr(tool, "_wikipedia_search", _fake_wiki)

    out = await tool.execute("some query", 3, scene="reference")
    assert out == "wiki-result"


@pytest.mark.asyncio
async def test_web_search_skips_low_relevance_provider_and_uses_next(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_bing(query: str, count: int):
        return (
            "1. World Economic Forum Annual Meeting\n"
            "   https://example.com/wef\n"
            "   Global economy outlook and policy trends"
        )

    async def _fake_wiki(query: str, count: int):
        return (
            "1. List of 2024 films\n"
            "   https://en.wikipedia.org/wiki/List_of_American_films_of_2024\n"
            "   Includes notable movies and ratings references from IMDb"
        )

    def _fake_get(key: str, default=None):
        if key == "web.search.brave_api_key":
            return ""
        if key == "web.search.providers_order":
            return ["bing_rss", "wikipedia"]
        if key == "web.search.providers_enabled":
            return {"bing_rss": True, "wikipedia": True}
        if key == "web.search.scenario_routing":
            return {"enabled": False, "auto_detect": False, "profiles": {}}
        return default

    monkeypatch.setattr(config, "get", _fake_get)
    monkeypatch.setattr(tool, "_bing_rss_search", _fake_bing)
    monkeypatch.setattr(tool, "_wikipedia_search", _fake_wiki)

    out = await tool.execute("2024 高分电影 IMDb best movies", 3)
    assert "List of 2024 films" in out
    assert "World Economic Forum" not in out


@pytest.mark.asyncio
async def test_web_search_primary_provider_takes_priority(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_brave(query: str, count: int, api_key: str):
        return (
            "1. Primary provider test guide\n"
            "   https://brave.example\n"
            "   Primary provider test validation and rollout checklist"
        )

    async def _fake_ddg(query: str, count: int):
        return "1. DDG Result\n   https://ddg.example\n   fallback"

    def _fake_get(key: str, default=None):
        if key == "web.search.brave_api_key":
            return "brave-key"
        if key == "web.search.primary_provider":
            return "brave"
        if key == "web.search.providers_order":
            return ["duckduckgo", "wikipedia", "bing_rss"]
        if key == "web.search.providers_enabled":
            return {"brave": True, "duckduckgo": True, "wikipedia": True, "bing_rss": True}
        if key == "web.search.scenario_routing":
            return {"enabled": False, "auto_detect": False, "profiles": {}}
        if key == "web.search.relevance_gate":
            return {"enabled": True, "min_score": 0.1, "allow_low_relevance_fallback": True}
        return default

    monkeypatch.setattr(config, "get", _fake_get)
    monkeypatch.setattr(tool, "_brave_search", _fake_brave)
    monkeypatch.setattr(tool, "_duckduckgo_search", _fake_ddg)

    out = await tool.execute("primary provider test", 3)
    assert "Primary provider test guide" in out
    assert "DDG Result" not in out


@pytest.mark.asyncio
async def test_web_search_primary_only_disables_fallback(monkeypatch):
    tool = WebSearchTool()
    _cache.clear()

    async def _fake_brave(query: str, count: int, api_key: str):
        return "No results found."

    async def _fake_ddg(query: str, count: int):
        return "1. DDG Result\n   https://ddg.example\n   fallback"

    def _fake_get(key: str, default=None):
        if key == "web.search.brave_api_key":
            return "brave-key"
        if key == "web.search.primary_provider":
            return "brave"
        if key == "web.search.primary_only":
            return True
        if key == "web.search.providers_order":
            return ["duckduckgo", "wikipedia", "bing_rss"]
        if key == "web.search.providers_enabled":
            return {"brave": True, "duckduckgo": True, "wikipedia": True, "bing_rss": True}
        if key == "web.search.scenario_routing":
            return {"enabled": False, "auto_detect": False, "profiles": {}}
        return default

    monkeypatch.setattr(config, "get", _fake_get)
    monkeypatch.setattr(tool, "_brave_search", _fake_brave)
    monkeypatch.setattr(tool, "_duckduckgo_search", _fake_ddg)

    out = await tool.execute("primary only mode", 3)
    assert "No results from fallback search providers" in out
    assert "DDG Result" not in out


@pytest.mark.asyncio
async def test_web_report_generates_cited_report_and_saves_memory(monkeypatch):
    _cache.clear()
    saved_entries = []

    class _Memory:
        async def save_entry(self, entry):
            saved_entries.append(entry)

    tool = WebReportTool(memory_manager=_Memory())

    async def _fake_search(query: str, count: int = 5, scene: str = "auto", **_):
        return (
            "1. Source A\n"
            "   https://example.com/a\n"
            "   snippet A\n\n"
            "2. Source B\n"
            "   https://example.com/b\n"
            "   snippet B"
        )

    async def _fake_fetch(url: str, max_chars: int = 50000, **_):
        return "First fact. Second fact. Third fact. Fourth fact."

    monkeypatch.setattr(tool._search_tool, "execute", _fake_search)
    monkeypatch.setattr(tool._fetch_tool, "execute", _fake_fetch)

    out = await tool.execute("test topic", max_sources=2, style="brief")
    assert out.startswith("# Research Report: test topic")
    assert "## Sources" in out
    assert "[1]" in out and "https://example.com/a" in out
    assert "[2]" in out and "https://example.com/b" in out
    assert len(saved_entries) == 1
    assert saved_entries[0].metadata.get("kind") == "web_report"
    assert saved_entries[0].metadata.get("source_count") == 2


@pytest.mark.asyncio
async def test_web_report_returns_error_when_fetch_all_fail(monkeypatch):
    _cache.clear()
    tool = WebReportTool(memory_manager=None)

    async def _fake_search(query: str, count: int = 5, scene: str = "auto", **_):
        return "1. Source A\n   https://example.com/a\n   snippet A"

    async def _fake_fetch(url: str, max_chars: int = 50000, **_):
        return "Error [WEB_FETCH_FAILED]: failed"

    monkeypatch.setattr(tool._search_tool, "execute", _fake_search)
    monkeypatch.setattr(tool._fetch_tool, "execute", _fake_fetch)

    out = await tool.execute("test topic")
    assert "WEB_REPORT_FETCH_EMPTY" in out
