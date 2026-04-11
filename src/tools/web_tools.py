"""Compatibility facade for web tools.

The implementation lives under :mod:`tools.web_impl`. Keep this module as the
stable import surface for one compatibility cycle.
"""

from tools.web_impl import (
    CACHE_TTL,
    MAX_CACHE_SIZE,
    WebFetchTool,
    WebReportTool,
    WebSearchTool,
    WebToolBase,
    _cache,
    _cache_get,
    _cache_set,
    config,
    logger,
)

__all__ = [
    "CACHE_TTL",
    "MAX_CACHE_SIZE",
    "WebFetchTool",
    "WebReportTool",
    "WebSearchTool",
    "WebToolBase",
    "_cache",
    "_cache_get",
    "_cache_set",
    "config",
    "logger",
]
