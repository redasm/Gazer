"""Web tools: helpers.

Extracted from web_tools.py.
"""

class WebToolBase(Tool):
    @property
    def provider(self) -> str:
        return "web"

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"


def _cache_get(key: str) -> Optional[str]:
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: str) -> None:
    # Evict oldest entries if cache is full
    if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
        oldest_key = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest_key]
    _cache[key] = (time.time(), value)


