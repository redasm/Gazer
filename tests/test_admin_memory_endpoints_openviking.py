"""Tests for OpenViking-adapted admin memory search endpoints."""

from __future__ import annotations

import pytest

from tools.admin import api_facade as admin_api


class _FakeIndex:
    def fts_search(self, query: str, limit: int = 10):
        return [("full quick content", "user", "2026-02-17T10:00:00", 0.88)][:limit]

    async def hybrid_search(self, query: str, limit: int = 10):
        return [
            {
                "content": "This is a detailed memory content used by deep mode.",
                "sender": "assistant",
                "timestamp": "2026-02-17T10:05:00",
                "score": 0.91,
            }
        ][:limit]


class _FakeMemoryManager:
    def __init__(self):
        self.index = _FakeIndex()


@pytest.mark.asyncio
async def test_search_memory_quick_mode(monkeypatch):
    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManager())
    payload = await admin_api.search_memory("quick", limit=5, mode="quick")
    assert payload["query"] == "quick"
    assert payload["mode"] == "quick"
    assert payload["count"] == 1
    assert payload["results"][0][0] == "full quick content"


@pytest.mark.asyncio
async def test_search_memory_deep_mode_layered_payload(monkeypatch):
    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManager())
    payload = await admin_api.search_memory("deep", limit=5, mode="deep")
    assert payload["query"] == "deep"
    assert payload["mode"] == "deep"
    assert payload["count"] == 1
    result = payload["results"][0]
    assert result["preview"] == "This is a detailed memory content used by deep mode."[:120]
    assert result["detail"] == "This is a detailed memory content used by deep mode."
    assert result["sender"] == "assistant"
