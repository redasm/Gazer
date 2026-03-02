"""Tests for MemoryManager OpenViking migration path."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from memory.manager import MemoryManager
from soul.core import MemoryEntry, WorkingMemory


class _FakeConfig:
    def __init__(self, data: dict):
        self._data = data

    def get(self, key_path: str, default=None):
        cur = self._data
        for key in key_path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return default
        return cur


def _patch_manager_runtime(monkeypatch, tmp_path: Path):
    cfg = _FakeConfig(
        {
            "memory": {
                "context_backend": {
                    "enabled": False,
                    "mode": "openviking",
                    "data_dir": str(tmp_path / "ov_data"),
                    "config_file": "",
                    "session_prefix": "gazer",
                    "default_user": "owner",
                }
            }
        }
    )
    monkeypatch.setattr("memory.manager.config", cfg)


def test_manager_uses_openviking_index_adapter(monkeypatch, tmp_path: Path):
    _patch_manager_runtime(monkeypatch, tmp_path)
    base_path = tmp_path / "memory"
    mm = MemoryManager(base_path=str(base_path))
    try:
        assert mm.index.__class__.__name__ == "OpenVikingSearchIndex"
        assert hasattr(mm.index, "fts_search")
        assert hasattr(mm.index, "hybrid_search")
        assert not (base_path / "index.db").exists()
        assert not (base_path / "vector_index.faiss").exists()
    finally:
        mm.stop()


@pytest.mark.asyncio
async def test_manager_save_and_load_recent_with_backend(monkeypatch, tmp_path: Path):
    _patch_manager_runtime(monkeypatch, tmp_path)
    mm = MemoryManager(base_path=str(tmp_path / "memory"))
    try:
        await mm.save_entry(
            MemoryEntry(
                sender="assistant",
                content="OpenViking backend message",
                timestamp=datetime.now(),
            )
        )
        recent = mm.load_recent(limit=10)
        assert len(recent.memories) >= 1
        assert recent.memories[-1].content == "OpenViking backend message"

        store_file = tmp_path / "ov_data" / "memory_events.jsonl"
        assert store_file.is_file()
        assert "OpenViking backend message" in store_file.read_text(encoding="utf-8")
        assert not (tmp_path / "memory" / "events").exists()
    finally:
        mm.stop()


@pytest.mark.asyncio
async def test_manager_companion_context_guard_options(monkeypatch, tmp_path: Path):
    _patch_manager_runtime(monkeypatch, tmp_path)
    mm = MemoryManager(base_path=str(tmp_path / "memory"))
    captured: dict = {}
    try:
        async def _fake_relevant(current_message: str, current_sentiment: float = 0.0, *, entity_limit: int = 3, semantic_limit: int = 3):
            captured["current_message"] = current_message
            captured["entity_limit"] = entity_limit
            captured["semantic_limit"] = semantic_limit
            return {
                "entity_memories": ["entity-a", "entity-b"],
                "semantic_memories": ["semantic-a"],
                "time_reminders": ["today"],
                "emotion_context": "emotion",
                "relationship_context": "relationship",
            }

        def _fake_format(
            recall_result: dict,
            *,
            max_recall_items: int = 5,
            include_relationship_context: bool = True,
            include_time_reminders: bool = True,
            include_emotion_context: bool = True,
        ) -> str:
            captured["max_recall_items"] = max_recall_items
            captured["include_relationship_context"] = include_relationship_context
            captured["include_time_reminders"] = include_time_reminders
            captured["include_emotion_context"] = include_emotion_context
            return "X" * 500

        monkeypatch.setattr(mm.recall, "get_relevant_memories", _fake_relevant)
        monkeypatch.setattr(mm.recall, "format_for_prompt", _fake_format)
        monkeypatch.setattr(mm.emotions, "get_recent_mood", lambda days=3: "mood")

        context = await mm.get_companion_context(
            "query",
            WorkingMemory(memories=[]),
            entity_limit=2,
            semantic_limit=1,
            max_recall_items=1,
            max_context_chars=180,
            include_relationship_context=False,
            include_time_reminders=True,
            include_emotion_context=False,
            include_recent_observation=False,
        )
        assert captured["entity_limit"] == 2
        assert captured["semantic_limit"] == 1
        assert captured["max_recall_items"] == 1
        assert captured["include_relationship_context"] is False
        assert captured["include_emotion_context"] is False
        assert "Recent Observation" not in context
        assert len(context) <= 180
    finally:
        mm.stop()
