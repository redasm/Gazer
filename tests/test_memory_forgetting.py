"""Tests for memory.forgetting -- MemoryDecay, MemoryCurator."""

import math
import pytest
from datetime import datetime, timedelta
from soul.core import MemoryEntry
from memory.forgetting import MemoryDecay, MemoryCurator


@pytest.fixture
def decay():
    return MemoryDecay()


def _make_entry(days_ago=0, importance=0.5, sentiment=0.0, content="test"):
    return MemoryEntry(
        content=content,
        sender="User",
        timestamp=datetime.now() - timedelta(days=days_ago),
        importance=importance,
        sentiment=sentiment,
    )


class TestMemoryDecay:
    def test_fresh_memory_high_retention(self, decay):
        entry = _make_entry(days_ago=0)
        retention = decay.calculate_retention(entry)
        assert retention > 0.9

    def test_old_memory_lower_retention(self, decay):
        entry = _make_entry(days_ago=60)
        retention = decay.calculate_retention(entry)
        assert retention < 0.8

    def test_very_old_memory_low_retention(self, decay):
        entry = _make_entry(days_ago=365)
        retention = decay.calculate_retention(entry)
        assert retention <= 0.3

    def test_importance_boosts_retention(self, decay):
        low = _make_entry(days_ago=30, importance=0.1)
        high = _make_entry(days_ago=30, importance=0.9)
        assert decay.calculate_retention(high) > decay.calculate_retention(low)

    def test_strong_emotion_boosts_retention(self, decay):
        neutral = _make_entry(days_ago=30, sentiment=0.0)
        emotional = _make_entry(days_ago=30, sentiment=0.9)
        assert decay.calculate_retention(emotional) > decay.calculate_retention(neutral)

    def test_repetition_boosts_retention(self, decay):
        entry = _make_entry(days_ago=30)
        r1 = decay.calculate_retention(entry, repetition_count=1)
        r5 = decay.calculate_retention(entry, repetition_count=5)
        assert r5 > r1

    def test_should_keep_important(self, decay):
        entry = _make_entry(days_ago=100, importance=0.9)
        assert decay.should_keep(entry) is True

    def test_should_keep_recent(self, decay):
        entry = _make_entry(days_ago=1)
        assert decay.should_keep(entry) is True

    def test_should_archive(self, decay):
        entry = _make_entry(days_ago=45, importance=0.3)
        # This might be in the archive zone
        retention = decay.calculate_retention(entry)
        archived = decay.should_archive(entry)
        assert isinstance(archived, bool)

    def test_filter_memories(self, decay):
        memories = [
            _make_entry(days_ago=1, importance=0.8),    # Active
            _make_entry(days_ago=30, importance=0.3),    # Possibly archived
            _make_entry(days_ago=365, importance=0.1),   # Likely forgotten
        ]
        active, archived, forgotten = decay.filter_memories(memories)
        assert len(active) + len(archived) + len(forgotten) == 3
        # Fresh important memory should be active
        assert len(active) >= 1

    def test_get_decay_info(self, decay):
        entry = _make_entry(days_ago=7, content="test decay info")
        info = decay.get_decay_info(entry)
        assert "content_preview" in info
        assert "retention" in info
        assert "status" in info
        assert info["age_days"] == pytest.approx(7, abs=0.5)


class TestMemoryCurator:
    def test_simulate_forgetting(self):
        curator = MemoryCurator()
        memories = [
            _make_entry(days_ago=1, importance=0.9),
            _make_entry(days_ago=100, importance=0.1),
            _make_entry(days_ago=365, importance=0.7),  # Old but important
        ]
        kept = curator.simulate_forgetting(memories)
        assert len(kept) >= 1  # At least the fresh important one
