"""Tests for soul.core -- MemoryEntry, WorkingMemory, MentalState."""

import pytest
from datetime import datetime, timezone
from soul.core import (
    MemoryEntry,
    WorkingMemory,
    CognitiveStep,
    MentalState,
)


class TestMemoryEntry:
    def test_defaults(self):
        entry = MemoryEntry(content="hello", sender="User")
        assert entry.content == "hello"
        assert entry.sender == "User"
        assert entry.emotion is None
        assert entry.sentiment == 0.0
        assert entry.importance == 0.5
        assert entry.people == []
        assert entry.topics == []
        assert isinstance(entry.timestamp, datetime)

    def test_custom_fields(self):
        entry = MemoryEntry(
            content="I love Python", sender="User",
            emotion="happy", sentiment=0.8,
            people=["Alice"], topics=["编程"],
            importance=0.9,
        )
        assert entry.emotion == "happy"
        assert entry.sentiment == 0.8
        assert entry.people == ["Alice"]
        assert entry.importance == 0.9


class TestWorkingMemory:
    def test_empty(self):
        wm = WorkingMemory()
        assert len(wm.memories) == 0
        assert wm.owner == "Gazer"

    def test_append_returns_new_instance(self):
        wm = WorkingMemory()
        entry = MemoryEntry(content="hi", sender="User")
        wm2 = wm.append(entry)
        assert len(wm.memories) == 0  # original unchanged (immutable)
        assert len(wm2.memories) == 1
        assert wm2.memories[0].content == "hi"

    def test_immutability(self):
        wm = WorkingMemory()
        with pytest.raises(Exception):  # Frozen model
            wm.owner = "Other"

    def test_memories_container_is_not_mutable_in_place(self):
        wm = WorkingMemory().append(MemoryEntry(content="hello", sender="User"))
        with pytest.raises(Exception):
            wm.memories.append(MemoryEntry(content="should fail", sender="User"))

    def test_get_context_string(self):
        entry = MemoryEntry(content="hello world", sender="User")
        wm = WorkingMemory().append(entry)
        ctx = wm.get_context_string()
        assert "User: hello world" in ctx

    def test_chained_append(self):
        wm = WorkingMemory()
        wm = wm.append(MemoryEntry(content="a", sender="User"))
        wm = wm.append(MemoryEntry(content="b", sender="Assistant"))
        wm = wm.append(MemoryEntry(content="c", sender="User"))
        assert len(wm.memories) == 3


class TestMentalState:
    def test_creation(self):
        state = MentalState(name="idle", description="Default state")
        assert state.name == "idle"
        assert state.meta_data == {}



class TestCognitiveStep:
    @pytest.mark.asyncio
    async def test_abstract_run(self):
        step = CognitiveStep("test")
        with pytest.raises(NotImplementedError):
            await step.run(WorkingMemory())
