"""Tests for memory system integration.

Individual memory subsystems (emotions, relationships, forgetting, recall)
have dedicated test files.  This module tests cross-cutting concerns and
the MemoryManager coordination layer via lightweight unit tests.
"""

import os
import pytest
from datetime import datetime
from soul.core import WorkingMemory, MemoryEntry


class TestWorkingMemoryIntegration:
    """Cross-module tests using WorkingMemory with MemoryEntry."""

    def test_build_conversation(self):
        wm = WorkingMemory()
        wm = wm.append(MemoryEntry(sender="User", content="你好"))
        wm = wm.append(MemoryEntry(sender="Gazer", content="你好！有什么可以帮你的？"))
        assert len(wm.memories) == 2
        ctx = wm.get_context_string()
        assert "你好" in ctx

    def test_empty_memory_context(self):
        wm = WorkingMemory()
        assert wm.get_context_string() == ""

    def test_memory_entry_timestamps(self):
        e1 = MemoryEntry(sender="User", content="first")
        e2 = MemoryEntry(sender="User", content="second")
        assert e2.timestamp >= e1.timestamp

    def test_memory_entry_defaults(self):
        entry = MemoryEntry(sender="User", content="test")
        assert entry.importance == 0.5
        assert entry.emotion is None
        assert entry.sentiment == 0.0
        assert entry.topics == []
        assert entry.metadata == {}

    def test_memory_entry_custom_metadata(self):
        entry = MemoryEntry(
            sender="System",
            content="tool result",
            metadata={"tool_calls": [{"name": "echo"}]},
            importance=0.8,
        )
        assert entry.metadata["tool_calls"][0]["name"] == "echo"
        assert entry.importance == 0.8

    def test_working_memory_chain(self):
        """Test building a multi-turn conversation."""
        wm = WorkingMemory()
        turns = [
            ("User", "我叫小明"),
            ("Gazer", "你好小明！"),
            ("User", "今天心情不错"),
            ("Gazer", "很高兴听到！"),
        ]
        for sender, content in turns:
            wm = wm.append(MemoryEntry(sender=sender, content=content))
        assert len(wm.memories) == 4
        assert wm.memories[0].sender == "User"
        assert wm.memories[-1].content == "很高兴听到！"

    def test_working_memory_owner(self):
        wm = WorkingMemory(owner="CustomBot")
        assert wm.owner == "CustomBot"
        wm2 = wm.append(MemoryEntry(sender="User", content="hi"))
        assert wm2.owner == "CustomBot"
