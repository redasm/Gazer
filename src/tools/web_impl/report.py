"""Web tools: report.

Extracted from web_tools.py.
"""

class WebReportTool(WebToolBase):
    """Generate a compact research report with explicit source citations."""

    def __init__(
        self,
        *,
        search_tool: Optional[WebSearchTool] = None,
        fetch_tool: Optional["WebFetchTool"] = None,
        memory_manager: Any = None,
    ) -> None:
        self._search_tool = search_tool or WebSearchTool()
        self._fetch_tool = fetch_tool or WebFetchTool()
        self._memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "web_report"


    @property
    def description(self) -> str:
        return (
            "Build a research report from web search results with source citations. "
            "Workflow: search -> fetch -> summarize -> cite."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research topic or question."},
                "scene": {
                    "type": "string",
                    "description": "Search scene: auto|general|news|reference|tech. Defaults to auto.",
                },
                "count": {
                    "type": "integer",
                    "description": "Search result count (1-10). Defaults to 6.",
                },
                "max_sources": {
                    "type": "integer",
                    "description": "Max source pages to read (1-8). Defaults to 3.",
                },
                "style": {
                    "type": "string",
                    "description": "Report style: brief|standard|detailed. Defaults to standard.",
                },
            },
            "required": ["query"],
        }

    @staticmethod
    def _normalize_style(style: str) -> str:
        s = str(style or "standard").strip().lower()
        if s not in {"brief", "standard", "detailed"}:
            return "standard"
        return s

    @classmethod
    def _max_fetch_chars_by_style(cls, style: str) -> int:
        if style == "brief":
            return 5000
        if style == "detailed":
            return 12000
        return 8000

    @classmethod
    def _max_sentences_by_style(cls, style: str) -> int:
        if style == "brief":
            return 2
        if style == "detailed":
            return 5
        return 3

    @staticmethod
    def _summarize_plain_text(text: str, *, max_sentences: int, max_chars: int = 600) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned:
            return ""
        # Prefer sentence-level clipping, fallback to char clipping.
        parts = [p.strip() for p in re.split(r"(?<=[。！？.!?])\s+", cleaned) if p.strip()]
        if parts:
            summary = " ".join(parts[:max_sentences]).strip()
        else:
            summary = cleaned
        if len(summary) > max_chars:
            summary = summary[: max_chars - 1].rstrip() + "…"
        return summary

    @staticmethod
    def _build_exec_summary(query: str, findings: List[Dict[str, str]]) -> str:
        highlights: List[str] = []
        for item in findings[:3]:
            text = str(item.get("summary", "") or "").strip()
            if not text:
                continue
            highlights.append(text)
        if not highlights:
            return f"围绕“{query}”检索到可用来源，但正文信息较少。"
        joined = " ".join(highlights)
        if len(joined) > 420:
            joined = joined[:419].rstrip() + "…"
        return joined

    async def _save_report_evidence(
        self,
        *,
        query: str,
        scene: str,
        style: str,
        findings: List[Dict[str, str]],
        report: str,
    ) -> None:
        if self._memory_manager is None:
            return
        save_entry = getattr(self._memory_manager, "save_entry", None)
        if not callable(save_entry):
            return
        try:
            from soul.core import MemoryEntry
        except Exception:
            return
        evidence = {
            "kind": "web_report",
            "query": query,
            "scene": scene,
            "style": style,
            "source_count": len(findings),
            "sources": [
                {
                    "index": int(item.get("index", 0) or 0),
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "summary": item.get("summary", ""),
                }
                for item in findings
            ],
        }
        content = (
            f"[WebReport] {query}\n"
            f"{json.dumps(evidence, ensure_ascii=False)}\n\n"
            f"{report[:3000]}"
        )
        await save_entry(
            MemoryEntry(
                sender="System",
                content=content,
                metadata=evidence,
            )
        )

    async def execute(
        self,
        query: str,
        scene: str = "auto",
        count: int = 6,
        max_sources: int = 3,
        style: str = "standard",
        **_: Any,
    ) -> str:
        query = str(query or "").strip()
        if not query:
            return self._error("WEB_REPORT_QUERY_REQUIRED", "query is required.")
        count = min(max(int(count or 6), 1), 10)
        max_sources = min(max(int(max_sources or 3), 1), 8)
        style = self._normalize_style(style)

        search_output = await self._search_tool.execute(query=query, count=count, scene=scene)
        if str(search_output).strip().lower().startswith("error ["):
            return str(search_output)

        candidates = WebSearchTool._parse_search_results(search_output, max_entries=max(count, max_sources * 2))
        if not candidates:
            return self._error(
                "WEB_REPORT_NO_SEARCH_RESULTS",
                "No parseable search results found for report generation.",
            )

        findings: List[Dict[str, str]] = []
        seen_urls: set[str] = set()
        fetch_chars = self._max_fetch_chars_by_style(style)
        max_sentences = self._max_sentences_by_style(style)

        for item in candidates:
            if len(findings) >= max_sources:
                break
            url = str(item.get("url", "") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            fetched = await self._fetch_tool.execute(url=url, max_chars=fetch_chars)
            if str(fetched).strip().lower().startswith("error ["):
                continue
            summary = self._summarize_plain_text(
                str(fetched),
                max_sentences=max_sentences,
                max_chars=700 if style == "detailed" else 500,
            )
            if not summary:
                continue
            findings.append(
                {
                    "index": len(findings) + 1,
                    "title": str(item.get("title", "") or "Untitled"),
                    "url": url,
                    "summary": summary,
                }
            )

        if not findings:
            return self._error(
                "WEB_REPORT_FETCH_EMPTY",
                "Search returned links, but none could be fetched into readable content.",
            )

        exec_summary = self._build_exec_summary(query, findings)
        lines: List[str] = [f"# Research Report: {query}", "", "## Executive Summary", exec_summary, "", "## Findings"]
        for item in findings:
            idx = int(item.get("index", 0) or 0)
            lines.append(f"{idx}. {item.get('summary', '')} [{idx}]")
        lines.append("")
        lines.append("## Sources")
        for item in findings:
            idx = int(item.get("index", 0) or 0)
            lines.append(f"[{idx}] {item.get('title', '')} - {item.get('url', '')}")

        report = "\n".join(lines).strip()
        try:
            await self._save_report_evidence(
                query=query,
                scene=str(scene or "auto"),
                style=style,
                findings=findings,
                report=report,
            )
        except Exception as exc:
            logger.warning("web_report memory save failed: %s", exc)
        return report


