"""Tests for soul.consolidation -- MemoryConsolidator, NightlyConsolidator."""

import os
import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from soul.core import WorkingMemory, MemoryEntry
from soul.consolidation import MemoryConsolidator, NightlyConsolidator


# ---------------------------------------------------------------------------
# MemoryConsolidator
# ---------------------------------------------------------------------------

class TestMemoryConsolidator:
    def test_update_long_term_memory(self):
        with patch("soul.consolidation.LLMCognitiveStep"):
            mc = MemoryConsolidator(api_key=None)
        wm = WorkingMemory(memories=[
            MemoryEntry(sender="User", content="I like coffee"),
        ])
        result = mc.update_long_term_memory(wm, "User prefers coffee.")
        assert len(result.memories) == 2
        assert "Long-term Insight" in result.memories[-1].content
        assert "coffee" in result.memories[-1].content
        assert result.memories[-1].metadata.get("type") == "long_term_insight"

    def test_update_preserves_original(self):
        with patch("soul.consolidation.LLMCognitiveStep"):
            mc = MemoryConsolidator(api_key=None)
        wm = WorkingMemory(memories=[
            MemoryEntry(sender="User", content="original"),
        ])
        result = mc.update_long_term_memory(wm, "summary")
        # Original should still be there
        assert result.memories[0].content == "original"
        assert len(wm.memories) == 1  # Immutable


# ---------------------------------------------------------------------------
# NightlyConsolidator -- pure logic methods
# ---------------------------------------------------------------------------

@pytest.fixture
def consolidator(tmp_dir):
    mm = MagicMock()
    mm.daily_path = str(tmp_dir / "events")
    mm.knowledge_path = str(tmp_dir / "knowledge")
    rel = MagicMock()
    rel.people = {}
    emo = MagicMock()

    identity_path = str(tmp_dir / "IDENTITY.md")
    stories_dir = str(tmp_dir / "stories")

    with patch("soul.consolidation.LLMCognitiveStep"):
        with patch("soul.consolidation.MemoryArchiver"):
            nc = NightlyConsolidator(
                memory_manager=mm,
                relationship_graph=rel,
                emotion_tracker=emo,
                api_key=None,
                identity_path=identity_path,
                stories_dir=stories_dir,
            )
    return nc


class TestAppendToIdentity:
    def test_writes_personality(self, consolidator, tmp_dir):
        data = {
            "personality_traits": ["内向", "乐观"],
            "interests": ["编程", "音乐"],
            "occupation": "软件工程师",
            "habits": ["早起"],
        }
        consolidator._append_to_identity(data)
        content = open(consolidator.identity_path, "r", encoding="utf-8").read()
        assert "内向" in content
        assert "编程" in content
        assert "软件工程师" in content
        assert "早起" in content
        assert date.today().isoformat() in content

    def test_empty_data_no_write(self, consolidator, tmp_dir):
        consolidator._append_to_identity({})
        assert not os.path.exists(consolidator.identity_path)

    def test_partial_data(self, consolidator):
        data = {"interests": ["hiking"]}
        consolidator._append_to_identity(data)
        content = open(consolidator.identity_path, "r", encoding="utf-8").read()
        assert "hiking" in content

    def test_appends_not_overwrites(self, consolidator):
        consolidator._append_to_identity({"interests": ["first"]})
        consolidator._append_to_identity({"interests": ["second"]})
        content = open(consolidator.identity_path, "r", encoding="utf-8").read()
        assert "first" in content
        assert "second" in content


class TestSaveStory:
    def test_saves_story_file(self, consolidator):
        story = {
            "title": "获得Offer",
            "summary": "用户拿到了心仪的工作机会",
            "emotion": "开心",
            "people_involved": ["小明"],
        }
        consolidator._save_story(story)
        files = os.listdir(consolidator.stories_dir)
        assert len(files) == 1
        content = open(os.path.join(consolidator.stories_dir, files[0]), "r", encoding="utf-8").read()
        assert "获得Offer" in content
        assert "开心" in content
        assert "小明" in content

    def test_saves_minimal_story(self, consolidator):
        story = {"title": "Untitled", "summary": "Something happened."}
        consolidator._save_story(story)
        files = os.listdir(consolidator.stories_dir)
        assert len(files) == 1

    def test_multiple_stories(self, consolidator):
        consolidator._save_story({"title": "Story A", "summary": "A"})
        consolidator._save_story({"title": "Story B", "summary": "B"})
        files = os.listdir(consolidator.stories_dir)
        assert len(files) == 2


class TestConsolidateRelationships:
    def test_does_not_raise(self, consolidator):
        """_consolidate_relationships is a logging-only method."""
        consolidator._consolidate_relationships()
