"""Tests for /memory/graph OpenViking-first data sourcing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory.viking_backend import OpenVikingMemoryBackend
from tools.admin import api_facade as admin_api


class _FakeBackend:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


class _FakeMemoryManager:
    def __init__(self, base_path: Path, backend_path: Path):
        self.base_path = str(base_path)
        self.backend = _FakeBackend(backend_path)


@pytest.mark.asyncio
async def test_memory_graph_prefers_openviking_sources(monkeypatch, tmp_path: Path):
    ov_dir = tmp_path / "openviking"
    legacy_dir = tmp_path / "legacy_memory"
    ov_dir.mkdir(parents=True, exist_ok=True)
    (ov_dir / "long_term").mkdir(parents=True, exist_ok=True)
    (ov_dir / "emotions").mkdir(parents=True, exist_ok=True)
    (legacy_dir / "knowledge" / "topics").mkdir(parents=True, exist_ok=True)
    (legacy_dir / "knowledge" / "topics" / "LegacyOnly.md").write_text(
        "# Legacy\nThis should not be loaded\n",
        encoding="utf-8",
    )

    (ov_dir / "memory_events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "content": "User asked about roadmap milestones",
                        "sender": "user",
                        "timestamp": "2026-02-17T10:00:00",
                        "date": "2026-02-17",
                        "metadata": {},
                    }
                ),
                json.dumps(
                    {
                        "content": "Assistant summarized the release plan",
                        "sender": "assistant",
                        "timestamp": "2026-02-17T10:01:30",
                        "date": "2026-02-17",
                        "metadata": {},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (ov_dir / "long_term" / "profile.json").write_text(
        json.dumps({"user_identity": {"content": "User is building an AI desktop assistant."}}),
        encoding="utf-8",
    )
    (ov_dir / "RELATIONSHIPS.json").write_text(
        json.dumps(
            {
                "alice": {
                    "name": "Alice",
                    "aliases": ["Alice"],
                    "relationship": "friend",
                    "mention_count": 3,
                    "sentiment": 0.6,
                    "last_mentioned": "2026-02-17T10:00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    (ov_dir / "emotions" / "2026-02-17.json").write_text(
        json.dumps({"overall_mood": "positive", "avg_sentiment": 0.3, "message_count": 2}),
        encoding="utf-8",
    )

    fake_mm = _FakeMemoryManager(base_path=legacy_dir, backend_path=ov_dir)
    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: fake_mm)

    payload = await admin_api.get_memory_graph()
    nodes = payload.get("nodes", [])
    links = payload.get("links", [])
    groups = {str(item.get("group", "")) for item in nodes}
    names = {str(item.get("name", "")) for item in nodes}

    assert "root" in groups
    assert "daily" in groups
    assert "event" in groups
    assert "entity" in groups
    assert "emotion" in groups
    assert any(name == "2026-02-17" for name in names)
    assert any("Alice" in name for name in names)
    assert any("user_identity" in name for name in names)
    assert "LegacyOnly" not in names
    assert len(links) > 0
