"""Tests for memory.recall -- MemoryRecaller."""

import os
import json
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import date
from memory.recall import MemoryRecaller, Milestone


@pytest.fixture
def recaller(tmp_dir):
    mock_index = MagicMock()
    mock_index.hybrid_search = AsyncMock(return_value=[])
    mock_relationships = MagicMock()
    mock_relationships.people = {}
    mock_relationships.to_context.return_value = ""
    mock_emotions = MagicMock()
    milestones_path = str(tmp_dir / "MILESTONES.md")
    return MemoryRecaller(mock_index, mock_relationships, mock_emotions, milestones_path)


class TestMilestone:
    def test_defaults(self):
        m = Milestone(name="生日", date="03-15")
        assert m.recurring is True
        assert m.remind_days_before == 3


class TestMemoryRecaller:
    def test_extract_entities_empty(self, recaller):
        entities = recaller._extract_entities("hello world")
        assert entities["people"] == []
        assert entities["topics"] == []

    def test_extract_entities_known_person(self, recaller):
        recaller.relationships.people = {"小红": MagicMock()}
        entities = recaller._extract_entities("今天和小红聊天了")
        assert "小红" in entities["people"]

    def test_extract_entities_topics(self, recaller):
        entities = recaller._extract_entities("工作好忙")
        assert "工作" in entities["topics"]

    def test_extract_entities_dates(self, recaller):
        entities = recaller._extract_entities("下周是我的生日")
        assert len(entities["dates"]) > 0

    def test_add_milestone(self, recaller, tmp_dir):
        m = recaller.add_milestone("妈妈生日", "03-15", person="妈妈")
        assert m.name == "妈妈生日"
        assert len(recaller.milestones) == 1
        # Check persistence
        json_path = str(tmp_dir / "MILESTONES.json")
        assert os.path.exists(json_path)

    def test_time_triggers_today(self, recaller):
        today_str = date.today().strftime("%m-%d")
        recaller.milestones = [Milestone(name="今日特别", date=today_str)]
        reminders = recaller._check_time_triggers()
        assert any("今天" in r for r in reminders)

    def test_time_triggers_none(self, recaller):
        recaller.milestones = []
        reminders = recaller._check_time_triggers()
        assert reminders == []

    def test_emotion_context_sad(self, recaller):
        ctx = recaller._get_emotion_appropriate_context(-0.5)
        assert "安慰" in ctx or "倾听" in ctx

    def test_emotion_context_happy(self, recaller):
        ctx = recaller._get_emotion_appropriate_context(0.7)
        assert "快乐" in ctx or "开心" in ctx

    def test_emotion_context_neutral(self, recaller):
        ctx = recaller._get_emotion_appropriate_context(0.0)
        assert ctx is None

    @pytest.mark.asyncio
    async def test_get_relevant_memories(self, recaller):
        result = await recaller.get_relevant_memories("你好", current_sentiment=0.0)
        assert "entity_memories" in result
        assert "semantic_memories" in result
        assert "time_reminders" in result

    def test_format_for_prompt_empty(self, recaller):
        result = recaller.format_for_prompt({
            "entity_memories": [],
            "semantic_memories": [],
            "time_reminders": [],
            "emotion_context": None,
            "relationship_context": "",
        })
        assert result == ""

    def test_format_for_prompt_with_data(self, recaller):
        result = recaller.format_for_prompt({
            "entity_memories": ["关于小红: 她上周来过"],
            "semantic_memories": [],
            "time_reminders": ["还有3天就是妈妈生日了"],
            "emotion_context": "用户心情很好",
            "relationship_context": "小红是朋友",
        })
        assert "妈妈生日" in result
        assert "小红" in result
