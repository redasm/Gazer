"""Web tools: search.

Extracted from web_tools.py.
"""

from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote, quote_plus

from .helpers import (
    WebToolBase,
    _cache_get,
    _cache_set,
    config,
    logger,
    resolve_web_report_path,
)

class WebSearchTool(WebToolBase):
    """Search the web using Brave Search API or DuckDuckGo fallback."""

    @property
    def name(self) -> str:
        return "web_search"


    @property
    def description(self) -> str:
        return "Search the web for information. Returns titles, URLs, and snippets."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "count": {
                    "type": "integer",
                    "description": "Number of results (1-10). Defaults to 5.",
                },
                "scene": {
                    "type": "string",
                    "description": "Search scene: auto|general|news|reference|tech. Defaults to auto.",
                },
            },
            "required": ["query"],
        }

    @staticmethod
    def _normalize_scene(scene: str) -> str:
        normalized = str(scene or "auto").strip().lower()
        if normalized not in {"auto", "general", "news", "reference", "tech"}:
            return "auto"
        return normalized

    @staticmethod
    def _detect_scene_from_query(query: str) -> str:
        text = str(query or "").lower()
        news_words = {"today", "latest", "breaking", "news", "headline", "recent"}
        reference_words = {"what is", "who is", "wiki", "wikipedia", "definition", "meaning"}
        tech_words = {"python", "javascript", "error", "stack", "api", "docs", "github", "framework"}
        if any(word in text for word in news_words):
            return "news"
        if any(word in text for word in tech_words):
            return "tech"
        if any(word in text for word in reference_words):
            return "reference"
        return "general"

    @staticmethod
    def _normalize_query(query: str) -> str:
        text = str(query or "")
        text = re.sub(r"[\\/|]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _query_tokens(query: str) -> tuple[List[str], List[str]]:
        text = str(query or "").lower()
        word_tokens = re.findall(r"[a-z0-9][a-z0-9._-]*", text)
        cjk_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        raw_tokens: List[str] = []
        for token in word_tokens + cjk_tokens:
            t = token.strip()
            if not t:
                continue
            if t not in raw_tokens:
                raw_tokens.append(t)

        generic_words = {
            "best",
            "top",
            "latest",
            "recent",
            "new",
            "high",
            "rating",
            "ratings",
            "the",
            "a",
            "an",
            "of",
            "and",
        }
        strong_tokens = [
            t
            for t in raw_tokens
            if t not in generic_words and not re.fullmatch(r"\d{1,4}", t)
        ]
        return raw_tokens, strong_tokens

    @classmethod
    def _relevance_score(cls, query: str, result_text: str) -> float:
        entries = cls._parse_search_results(result_text, max_entries=6)
        if not entries:
            # Non-standard output (e.g., direct summary) cannot be scored safely.
            return 1.0

        query_tokens, strong_tokens = cls._query_tokens(query)
        if not query_tokens:
            return 1.0

        combined_text = " ".join(
            f"{item.get('title', '')} {item.get('url', '')} {item.get('snippet', '')}"
            for item in entries
        ).lower()

        matched_tokens = {t for t in query_tokens if t in combined_text}
        matched_strong = {t for t in strong_tokens if t in combined_text}

        coverage = len(matched_tokens) / max(len(query_tokens), 1)
        if strong_tokens:
            strong_coverage = len(matched_strong) / max(len(strong_tokens), 1)
            return min(max((0.6 * strong_coverage) + (0.4 * coverage), 0.0), 1.0)
        return min(max(coverage, 0.0), 1.0)

    @classmethod
    def _is_result_relevant(cls, query: str, result_text: str, min_score: float = 0.25) -> bool:
        return cls._relevance_score(query, result_text) >= float(min_score)

    @staticmethod
    def _normalize_primary_provider(raw_provider: Any) -> str:
        provider = str(raw_provider or "").strip().lower()
        allowed = {"brave", "perplexity", "duckduckgo", "wikipedia", "bing_rss"}
        return provider if provider in allowed else ""

    @staticmethod
    def _normalize_relevance_gate(raw_gate: Any) -> Dict[str, Any]:
        gate = raw_gate if isinstance(raw_gate, dict) else {}
        enabled = bool(gate.get("enabled", True))
        allow_low_relevance_fallback = bool(gate.get("allow_low_relevance_fallback", True))
        try:
            min_score = float(gate.get("min_score", 0.25))
        except Exception:
            min_score = 0.25
        min_score = min(max(min_score, 0.0), 1.0)
        return {
            "enabled": enabled,
            "min_score": min_score,
            "allow_low_relevance_fallback": allow_low_relevance_fallback,
        }

    @staticmethod
    def _record_search_observation(payload: Dict[str, Any]) -> None:
        event = dict(payload or {})
        event["event"] = "web_search_observation"
        event["ts"] = int(time.time())
        logger.info("web_search_observation %s", json.dumps(event, ensure_ascii=False))

        report_file = str(
            config.get("web.search.report_file", "data/reports/web_search_observations.jsonl") or ""
        ).strip()
        if not report_file:
            return
        report_file = resolve_web_report_path(report_file)
        try:
            report_dir = os.path.dirname(report_file)
            if report_dir:
                os.makedirs(report_dir, exist_ok=True)
            with open(report_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("web_search observation report write failed: %s", exc)

    async def _search_with_provider(
        self,
        *,
        provider: str,
        query: str,
        count: int,
        brave_key: str,
        perplexity_cfg: Dict[str, str],
    ) -> Optional[str]:
        if provider == "brave":
            if not brave_key:
                return None
            return await self._brave_search(query, count, brave_key)
        if provider == "perplexity":
            key = str(perplexity_cfg.get("api_key", "") or "").strip()
            if not key:
                return None
            return await self._perplexity_search(
                query=query,
                count=count,
                api_key=key,
                base_url=str(perplexity_cfg.get("base_url", "") or "").strip(),
                model=str(perplexity_cfg.get("model", "") or "").strip(),
            )
        if provider == "duckduckgo":
            return await self._duckduckgo_search(query, count)
        if provider == "bing_rss":
            return await self._bing_rss_search(query, count)
        if provider == "wikipedia":
            return await self._wikipedia_search(query, count)
        return None

    async def execute(self, query: str, count: int = 5, scene: str = "auto", **_: Any) -> str:
        started_at = time.time()
        count = min(max(count, 1), 10)
        normalized_query = self._normalize_query(query)

        brave_key = str(config.get("web.search.brave_api_key", "") or "").strip()
        if not brave_key:
            brave_key = str(os.environ.get("BRAVE_API_KEY", "") or "").strip()
        perplexity_cfg = {
            "api_key": str(config.get("web.search.perplexity_api_key", "") or "").strip()
            or str(os.environ.get("PERPLEXITY_API_KEY", "") or "").strip(),
            "base_url": str(config.get("web.search.perplexity_base_url", "https://api.perplexity.ai") or "").strip(),
            "model": str(config.get("web.search.perplexity_model", "sonar") or "").strip(),
        }
        primary_provider = self._normalize_primary_provider(
            config.get("web.search.primary_provider", "brave")
        )
        primary_only = bool(config.get("web.search.primary_only", False))
        relevance_gate = self._normalize_relevance_gate(config.get("web.search.relevance_gate", {}))
        base_order = self._resolve_provider_order(
            config.get("web.search.providers_order", []),
            default_order=["brave", "duckduckgo", "wikipedia", "bing_rss"],
        )
        providers_order = list(base_order)
        providers_enabled = config.get("web.search.providers_enabled", {})
        if not isinstance(providers_enabled, dict):
            providers_enabled = {}
        route_cfg = config.get("web.search.scenario_routing", {}) or {}
        if not isinstance(route_cfg, dict):
            route_cfg = {}
        scene_name = self._normalize_scene(scene)
        if bool(route_cfg.get("enabled", True)):
            if scene_name == "auto" and bool(route_cfg.get("auto_detect", True)):
                scene_name = self._detect_scene_from_query(query)
            profiles = route_cfg.get("profiles", {}) or {}
            if isinstance(profiles, dict):
                profile_order = profiles.get(scene_name, [])
                providers_order = self._resolve_provider_order(profile_order, default_order=base_order)

        if primary_provider:
            providers_order = [primary_provider] + [p for p in providers_order if p != primary_provider]
        if primary_only and providers_order:
            providers_order = [providers_order[0]]
        first_provider = providers_order[0] if providers_order else ""

        cache_key = f"search:{normalized_query}:{count}:{scene_name}"
        cached = _cache_get(cache_key)
        if cached:
            cached_count = len(self._parse_search_results(cached, max_entries=count))
            self._record_search_observation(
                {
                    "query": normalized_query,
                    "scene": scene_name,
                    "provider_used": "cache",
                    "providers_attempted": [],
                    "fallback_used": False,
                    "fallback_reason": "cache_hit",
                    "relevance_score": 1.0,
                    "result_count": cached_count,
                    "duration_ms": int((time.time() - started_at) * 1000),
                    "primary_provider": first_provider,
                    "primary_only": primary_only,
                    "cache_hit": True,
                }
            )
            return cached

        result = None
        last_error: Optional[str] = None
        low_relevance_candidate: Optional[str] = None
        low_relevance_provider = ""
        low_relevance_score = 0.0
        selected_relevance_score = 0.0
        provider_used = ""
        providers_attempted: List[str] = []
        fallback_reason = ""
        used_low_relevance_fallback = False
        for index, provider in enumerate(providers_order):
            if not self._provider_enabled(provider, providers_enabled):
                if index == 0 and not fallback_reason:
                    fallback_reason = "primary_disabled"
                continue
            providers_attempted.append(provider)
            candidate = await self._search_with_provider(
                provider=provider,
                query=normalized_query,
                count=count,
                brave_key=brave_key,
                perplexity_cfg=perplexity_cfg,
            )
            if candidate is None:
                if index == 0 and not fallback_reason:
                    fallback_reason = "primary_unavailable"
                continue

            if self._is_empty_or_error_result(candidate):
                if isinstance(candidate, str) and candidate.lower().startswith("error ["):
                    last_error = candidate
                if index == 0 and not fallback_reason:
                    fallback_reason = "primary_error_or_empty"
                continue
            relevance_score = self._relevance_score(normalized_query, candidate)
            if relevance_gate["enabled"] and relevance_score < relevance_gate["min_score"]:
                logger.info(
                    "web_search provider '%s' returned low-relevance results (score=%.3f, threshold=%.3f); trying next provider",
                    provider,
                    relevance_score,
                    relevance_gate["min_score"],
                )
                if low_relevance_candidate is None:
                    low_relevance_candidate = candidate
                    low_relevance_provider = provider
                    low_relevance_score = relevance_score
                if index == 0 and not fallback_reason:
                    fallback_reason = "primary_low_relevance"
                continue
            result = candidate
            provider_used = provider
            selected_relevance_score = relevance_score
            if index > 0 and not fallback_reason:
                fallback_reason = "primary_fallback"
            break

        if not result:
            if low_relevance_candidate and relevance_gate["allow_low_relevance_fallback"]:
                result = (
                    low_relevance_candidate
                    + "\n\n[Warning] Search results may be low relevance for this query. "
                    "Try adding stronger keywords (domain/source/time window) and retry."
                )
                used_low_relevance_fallback = True
                provider_used = low_relevance_provider or provider_used
                selected_relevance_score = low_relevance_score
                if not fallback_reason and first_provider:
                    fallback_reason = "primary_low_relevance"
            elif low_relevance_candidate:
                result = (
                    "Error [WEB_SEARCH_LOW_RELEVANCE]: Search providers returned low-relevance results. "
                    "Please refine query with stronger keywords (domain/source/time window)."
                )
                provider_used = low_relevance_provider or provider_used
                selected_relevance_score = low_relevance_score
                if not fallback_reason and first_provider:
                    fallback_reason = "primary_low_relevance"
            else:
                result = last_error or (
                    "No results from fallback search providers. "
                    "You can set BRAVE_API_KEY / PERPLEXITY_API_KEY for higher recall."
                )
                if not fallback_reason and first_provider:
                    fallback_reason = "primary_no_result"

        # Avoid caching low-relevance fallback output to reduce repeated stale noise.
        if not used_low_relevance_fallback:
            _cache_set(cache_key, result)

        result_count = len(self._parse_search_results(result, max_entries=count))
        fallback_used = bool(fallback_reason and fallback_reason != "none")
        self._record_search_observation(
            {
                "query": normalized_query,
                "scene": scene_name,
                "provider_used": provider_used or "none",
                "providers_attempted": providers_attempted,
                "fallback_used": fallback_used,
                "fallback_reason": fallback_reason or "none",
                "relevance_score": round(float(selected_relevance_score), 4),
                "result_count": result_count,
                "duration_ms": int((time.time() - started_at) * 1000),
                "primary_provider": first_provider,
                "primary_only": primary_only,
                "cache_hit": False,
            }
        )
        return result

    @staticmethod
    def _resolve_provider_order(raw_order: Any, default_order: list[str]) -> list[str]:
        if not isinstance(raw_order, list):
            return list(default_order)
        allowed = {"brave", "perplexity", "duckduckgo", "bing_rss", "wikipedia"}
        cleaned: list[str] = []
        for item in raw_order:
            name = str(item or "").strip().lower()
            if not name or name not in allowed or name in cleaned:
                continue
            cleaned.append(name)
        for provider in default_order:
            if provider not in cleaned:
                cleaned.append(provider)
        return cleaned

    @staticmethod
    def _provider_enabled(provider: str, enabled_map: Dict[str, Any]) -> bool:
        if provider not in enabled_map:
            return True
        return bool(enabled_map.get(provider))

    async def _brave_search(self, query: str, count: int, api_key: str) -> str:
        try:
            import httpx
        except ImportError:
            return self._error("WEB_DEPENDENCY_MISSING", "httpx is not installed. Run: pip install httpx")

        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
        params = {"q": query, "count": count}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return self._error("WEB_SEARCH_BRAVE_FAILED", f"Brave search failed: {exc}")

        results = data.get("web", {}).get("results", [])
        if not results:
            return "No results found."

        lines = []
        for i, r in enumerate(results[:count], 1):
            title = r.get("title", "")
            link = r.get("url", "")
            snippet = r.get("description", "")
            lines.append(f"{i}. {title}\n   {link}\n   {snippet}")
        return "\n\n".join(lines)

    async def _perplexity_search(
        self,
        *,
        query: str,
        count: int,
        api_key: str,
        base_url: str,
        model: str,
    ) -> str:
        try:
            import httpx
        except ImportError:
            return self._error("WEB_DEPENDENCY_MISSING", "httpx is not installed. Run: pip install httpx")

        url = f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model or "sonar",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a web search assistant. Return concise web search results. "
                        "Each line should include title, URL, and snippet."
                    ),
                },
                {"role": "user", "content": f"Query: {query}\nReturn top {count} results."},
            ],
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return self._error("WEB_SEARCH_PERPLEXITY_FAILED", f"Perplexity search failed: {exc}")

        choices = data.get("choices", [])
        if not choices:
            return "No results found."
        content = str((choices[0].get("message", {}) or {}).get("content", "") or "").strip()
        citations = data.get("citations", [])
        urls: List[str] = []
        if isinstance(citations, list):
            for item in citations:
                url = str(item or "").strip()
                if url and url not in urls:
                    urls.append(url)
                if len(urls) >= count:
                    break
        if not urls:
            for match in re.findall(r"https?://[^\s)\]>]+", content):
                url = str(match or "").strip().rstrip(".,;")
                if url and url not in urls:
                    urls.append(url)
                if len(urls) >= count:
                    break
        if not content and not urls:
            return "No results found."

        snippet = re.sub(r"\s+", " ", content).strip()
        if len(snippet) > 320:
            snippet = snippet[:319].rstrip() + "…"
        if not snippet:
            snippet = "Perplexity search result."

        lines: List[str] = []
        if urls:
            for idx, url in enumerate(urls[:count], 1):
                lines.append(f"{idx}. Perplexity Result {idx}\n   {url}\n   {snippet}")
            return "\n\n".join(lines)
        return f"1. Perplexity Result\n   https://www.perplexity.ai\n   {snippet}"

    @staticmethod
    def _is_empty_or_error_result(result: Optional[str]) -> bool:
        text = str(result or "").strip()
        if not text:
            return True
        lowered = text.lower()
        return (
            lowered.startswith("error [")
            or "no results from fallback search providers" in lowered
            or lowered == "no results found."
        )

    @staticmethod
    def _parse_search_results(result_text: str, max_entries: int = 8) -> List[Dict[str, str]]:
        """Parse normalized search output into structured entries.

        Expected per-entry format:
          1. Title
             https://url
             snippet...
        """
        text = str(result_text or "").strip()
        if not text or text.lower().startswith("error ["):
            return []

        entries: List[Dict[str, str]] = []
        chunks = re.split(r"\n\s*\n+", text)
        for chunk in chunks:
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            if not lines:
                continue
            m = re.match(r"^\d+\.\s+(.*)$", lines[0])
            if not m:
                continue
            title = m.group(1).strip()
            url = ""
            snippet_parts: List[str] = []
            for line in lines[1:]:
                if not url and re.match(r"^https?://", line):
                    url = line
                else:
                    snippet_parts.append(line)
            if not url:
                continue
            entries.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": " ".join(snippet_parts).strip(),
                }
            )
            if len(entries) >= max_entries:
                break
        return entries

    async def _duckduckgo_search(self, query: str, count: int) -> str:
        """Fallback search without API key.

        Strategy:
        1) DuckDuckGo HTML results page (better recall for normal web queries)
        2) DuckDuckGo instant-answer API (useful for direct facts)
        """
        html_result = await self._duckduckgo_html_search(query, count)
        if html_result:
            return html_result
        return await self._duckduckgo_instant_answer(query, count)

    async def _duckduckgo_html_search(self, query: str, count: int) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        url = "https://duckduckgo.com/html/"
        params = {"q": query}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:
            logger.debug("DuckDuckGo HTML search failed: %s", exc)
            return None

        lines = []
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".result")
            for card in cards:
                if len(lines) >= count:
                    break
                a = card.select_one("a.result__a")
                if not a:
                    continue
                title = a.get_text(" ", strip=True)
                href = a.get("href", "").strip()
                snippet_el = card.select_one(".result__snippet")
                snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
                if title and href:
                    lines.append(f"{len(lines)+1}. {title}\n   {href}\n   {snippet}")
        except Exception as exc:
            logger.debug("DuckDuckGo HTML parse failed: %s", exc)
            return None

        if not lines:
            return None
        return "\n\n".join(lines)

    async def _duckduckgo_instant_answer(self, query: str, count: int) -> str:
        try:
            import httpx
        except ImportError:
            return self._error("WEB_DEPENDENCY_MISSING", "httpx is not installed. Run: pip install httpx")

        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return self._error("WEB_SEARCH_DDG_FAILED", f"DuckDuckGo search failed: {exc}")

        lines = []
        abstract = data.get("Abstract")
        if abstract:
            lines.append(f"Summary: {abstract}\nSource: {data.get('AbstractURL', '')}")

        for topic in data.get("RelatedTopics", [])[:count]:
            if isinstance(topic, dict) and "Text" in topic:
                lines.append(f"- {topic['Text']}\n  {topic.get('FirstURL', '')}")

        if not lines:
            return "No results found."
        return "\n\n".join(lines)

    async def _bing_rss_search(self, query: str, count: int) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        url = f"https://www.bing.com/search?format=rss&q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                xml_text = resp.text
            root = ET.fromstring(xml_text)
        except Exception as exc:
            logger.debug("Bing RSS search failed: %s", exc)
            return None

        lines = []
        for item in root.findall(".//item"):
            if len(lines) >= count:
                break
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if title and link:
                lines.append(f"{len(lines)+1}. {title}\n   {link}\n   {desc}")
        if not lines:
            return None
        return "\n\n".join(lines)

    async def _wikipedia_search(self, query: str, count: int) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": min(max(count, 1), 10),
            "format": "json",
            "utf8": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug("Wikipedia search failed: %s", exc)
            return None

        lines = []
        for item in (data.get("query", {}) or {}).get("search", [])[:count]:
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).replace("<span class=\"searchmatch\">", "").replace("</span>", "")
            if not title:
                continue
            page_url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            lines.append(f"{len(lines)+1}. {title}\n   {page_url}\n   {snippet}")
        if not lines:
            return None
        return "\n\n".join(lines)

