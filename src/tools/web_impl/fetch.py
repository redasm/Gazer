"""Web tools: fetch.

Extracted from web_tools.py.
"""

class WebFetchTool(WebToolBase):
    """Fetch a URL and extract readable text content."""

    @property
    def name(self) -> str:
        return "web_fetch"


    @property
    def description(self) -> str:
        return "Fetch a URL and extract its main text content (HTML to readable text)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch (http/https)."},
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters to return. Defaults to 50000.",
                },
            },
            "required": ["url"],
        }

    @staticmethod
    def _is_private_ip(hostname: str) -> bool:
        """Check if hostname resolves to a private/loopback IP (SSRF prevention)."""
        import ipaddress
        import socket
        try:
            # Resolve hostname to IP
            addr = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in addr:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                    return True
        except (socket.gaierror, ValueError):
            return True  # If we can't resolve, block it for safety
        return False

    async def execute(self, url: str, max_chars: int = 50000, **_: Any) -> str:
        if not url.startswith(("http://", "https://")):
            return self._error("WEB_URL_INVALID", "URL must start with http:// or https://")

        # SSRF protection: block private/internal IPs
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if self._is_private_ip(hostname):
            return self._error("WEB_URL_BLOCKED_PRIVATE", "Access to private/internal network addresses is blocked.")

        cache_key = f"fetch:{url}"
        cached = _cache_get(cache_key)
        if cached:
            return cached[:max_chars]

        try:
            import httpx
        except ImportError:
            return self._error("WEB_DEPENDENCY_MISSING", "httpx is not installed. Run: pip install httpx")

        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Gazer/1.0)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:
            return self._error("WEB_FETCH_FAILED", f"Error fetching URL: {exc}")

        text = self._extract_text(html)
        _cache_set(cache_key, text)
        return text[:max_chars]

    @staticmethod
    def _extract_text(html: str) -> str:
        """Extract readable text from HTML. Try trafilatura, fallback to basic strip."""
        try:
            import trafilatura
            text = trafilatura.extract(html, include_links=True)
            if text:
                return text
        except ImportError:
            pass

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except ImportError:
            pass

        # Last resort: naive tag stripping
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text


