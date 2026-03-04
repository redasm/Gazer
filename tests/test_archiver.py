"""Tests for memory.archiver -- MemoryArchiver."""

import os
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from memory.archiver import MemoryArchiver


@pytest.fixture
def mock_memory_manager(tmp_dir):
    mm = MagicMock()
    mm.daily_path = str(tmp_dir / "events")
    mm.knowledge_path = str(tmp_dir / "knowledge")
    os.makedirs(mm.daily_path, exist_ok=True)
    os.makedirs(mm.knowledge_path, exist_ok=True)
    return mm


@pytest.fixture
def archiver(mock_memory_manager):
    with patch("memory.archiver.LLMCognitiveStep"):
        arc = MemoryArchiver(mock_memory_manager)
    return arc


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------

class TestParseJson:
    def test_plain_json(self, archiver):
        raw = '{"knowledge": [{"category": "TOPIC", "subject": "Python", "content": "test"}]}'
        result = archiver._parse_json(raw)
        assert result is not None
        assert result["knowledge"][0]["subject"] == "Python"

    def test_json_in_code_block(self, archiver):
        raw = '```json\n{"knowledge": [{"category": "ENTITY", "subject": "Alice", "content": "test"}]}\n```'
        result = archiver._parse_json(raw)
        assert result is not None
        assert result["knowledge"][0]["category"] == "ENTITY"

    def test_json_in_generic_code_block(self, archiver):
        raw = '```\n{"key": "value"}\n```'
        result = archiver._parse_json(raw)
        assert result == {"key": "value"}

    def test_invalid_json(self, archiver):
        result = archiver._parse_json("not json at all")
        assert result is None

    def test_empty_string(self, archiver):
        result = archiver._parse_json("")
        assert result is None


# ---------------------------------------------------------------------------
# _save_knowledge
# ---------------------------------------------------------------------------

class TestSaveKnowledge:
    def test_saves_topic_file(self, archiver, mock_memory_manager):
        items = [
            {"category": "TOPIC", "subject": "Python",
             "content": "User likes black formatter.", "source_date": "2026-02-05"}
        ]
        archiver._save_knowledge(items)
        topic_file = os.path.join(mock_memory_manager.knowledge_path, "topics", "Python.md")
        assert os.path.exists(topic_file)
        content = open(topic_file, "r", encoding="utf-8").read()
        assert "black formatter" in content
        assert "2026-02-05" in content

    def test_saves_entity_file(self, archiver, mock_memory_manager):
        items = [
            {"category": "ENTITY", "subject": "Alice",
             "content": "Allergic to peanuts.", "source_date": "2026-02-05"}
        ]
        archiver._save_knowledge(items)
        entity_file = os.path.join(mock_memory_manager.knowledge_path, "entities", "Alice.md")
        assert os.path.exists(entity_file)
        content = open(entity_file, "r", encoding="utf-8").read()
        assert "peanuts" in content

    def test_saves_event_file(self, archiver, mock_memory_manager):
        items = [
            {"category": "EVENT", "subject": "NewJob",
             "content": "Started new role.", "source_date": "2026-01-01"}
        ]
        archiver._save_knowledge(items)
        event_file = os.path.join(mock_memory_manager.knowledge_path, "events", "NewJob.md")
        assert os.path.exists(event_file)

    def test_multiple_items_same_subject(self, archiver, mock_memory_manager):
        items = [
            {"category": "TOPIC", "subject": "Rust", "content": "Fact 1."},
            {"category": "TOPIC", "subject": "Rust", "content": "Fact 2."},
        ]
        archiver._save_knowledge(items)
        topic_file = os.path.join(mock_memory_manager.knowledge_path, "topics", "Rust.md")
        content = open(topic_file, "r", encoding="utf-8").read()
        assert "Fact 1" in content
        assert "Fact 2" in content

    def test_default_category(self, archiver, mock_memory_manager):
        items = [{"subject": "X", "content": "Uncategorized fact."}]
        archiver._save_knowledge(items)
        topic_file = os.path.join(mock_memory_manager.knowledge_path, "topics", "X.md")
        assert os.path.exists(topic_file)

    def test_special_chars_in_subject(self, archiver, mock_memory_manager):
        items = [
            {"category": "TOPIC", "subject": "C++/C#",
             "content": "User knows C++.", "source_date": "2026-02-05"}
        ]
        archiver._save_knowledge(items)
        files = os.listdir(os.path.join(mock_memory_manager.knowledge_path, "topics"))
        assert len(files) == 1


# ---------------------------------------------------------------------------
# archive_day
# ---------------------------------------------------------------------------

class TestArchiveDay:
    @pytest.mark.asyncio
    async def test_skip_if_no_log(self, archiver):
        """Should return without error when daily file doesn't exist."""
        await archiver.archive_day("2099-01-01")

    @pytest.mark.asyncio
    async def test_skip_short_content(self, archiver, mock_memory_manager):
        """Should skip if log content is too short."""
        daily_file = os.path.join(mock_memory_manager.daily_path, "2026-02-05.md")
        with open(daily_file, "w") as f:
            f.write("short")
        await archiver.archive_day("2026-02-05")

    @pytest.mark.asyncio
    async def test_full_archive_flow(self, archiver, mock_memory_manager):
        """Full flow with mocked LLM."""
        daily_file = os.path.join(mock_memory_manager.daily_path, "2026-02-05.md")
        with open(daily_file, "w", encoding="utf-8") as f:
            f.write("### [10:00:00] User\n" + "A" * 100 + "\n")

        mock_result = MagicMock()
        mock_result.content = '```json\n{"knowledge": [{"category": "TOPIC", "subject": "Test", "content": "A fact."}]}\n```'
        archiver.llm = MagicMock()
        archiver.llm.run = AsyncMock(return_value=mock_result)

        await archiver.archive_day("2026-02-05")

        topic_file = os.path.join(mock_memory_manager.knowledge_path, "topics", "Test.md")
        assert os.path.exists(topic_file)
