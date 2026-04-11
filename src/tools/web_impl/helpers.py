"""Shared helpers for web tools."""

from __future__ import annotations

import logging
import time
from typing import Optional

from runtime.config_manager import config
from runtime.paths import resolve_runtime_path
from tools.base import Tool

logger = logging.getLogger("WebTools")

_cache: dict[str, tuple[float, str]] = {}
CACHE_TTL = 900  # 15 minutes
MAX_CACHE_SIZE = 500


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
    if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
        oldest_key = min(_cache, key=lambda item: _cache[item][0])
        del _cache[oldest_key]
    _cache[key] = (time.time(), value)


def resolve_web_report_path(path: str) -> str:
    return str(resolve_runtime_path(path, config_manager=config))
