"""Compatibility tests for soul flows after OpenViking memory switch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from memory.manager import MemoryManager
from soul.consolidation import NightlyConsolidator
from soul.core import MemoryEntry, WorkingMemory
from soul.memory.working_context import WorkingContext
from soul.persona import GazerPersonality


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


class _StubToolRegistry:
    def get_definitions(self, **kwargs):
        return []

    def __len__(self):
        return 0

    async def execute(self, _name: str, _args: dict):
        return {"ok": True}


class _StubCognitiveStep:
    def __init__(self):
        self.prompts: list[str] = []
        self.user_prompts: list[str] = []

    async def run(self, memory: WorkingMemory, full_prompt: str, tools=None, **kwargs) -> MemoryEntry:
        self.prompts.append(full_prompt)
        # Capture the budget-built prompt (user message content)
        if memory.memories:
            self.user_prompts.append(memory.memories[-1].content)
        return MemoryEntry(sender="Gazer", content="我记得你提到过妈妈小红。")


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
                    "commit_every_messages": 3,
                }
            }
        }
    )
    monkeypatch.setattr("memory.manager.config", cfg)


@pytest.mark.asyncio
async def test_persona_process_keeps_context_and_triggers(monkeypatch, tmp_path: Path):
    _patch_manager_runtime(monkeypatch, tmp_path)
    mm = MemoryManager(base_path=str(tmp_path / "memory"))
    try:
        await mm.save_entry(
            MemoryEntry(sender="user", content="我妈妈小红，今天很开心，工作也顺利。")
        )

        personality = GazerPersonality(
            memory_manager=mm,
            tool_registry=_StubToolRegistry(),
        )
        stub_step = _StubCognitiveStep()
        personality.legacy_cognitive_step = stub_step

        context = WorkingContext(user_input="我妈妈小红最近怎么样？")
        output = await personality.process(context)

        assert output.get_metadata("reply") == "我记得你提到过妈妈小红。"
        assert stub_step.user_prompts
        # The companion context is now rendered into the budget-managed
        # prompt (user message), not the system prompt.
        budget_prompt = stub_step.user_prompts[-1]
        # Budget prompt should contain the companion context sections
        assert "Related Past Events" in budget_prompt or "Relationship Trust" in budget_prompt

        assert mm.emotions._today_data is not None
        assert mm.emotions._today_data.message_count >= 2
        assert len(mm.relationships.people) >= 1
    finally:
        mm.stop()


@pytest.mark.asyncio
async def test_companion_context_format_stays_stable(monkeypatch, tmp_path: Path):
    _patch_manager_runtime(monkeypatch, tmp_path)
    mm = MemoryManager(base_path=str(tmp_path / "memory"))
    try:
        await mm.save_entry(
            MemoryEntry(sender="user", content="今天工作压力很大，我有点焦虑。")
        )
        context = await mm.get_companion_context(
            "我最近工作压力大",
            WorkingMemory(memories=[]),
        )
        assert "## Related Past Events" in context
        assert "## Emotional Context" in context
        assert "## Recent Observation" in context
    finally:
        mm.stop()


@pytest.mark.asyncio
async def test_nightly_consolidator_works_with_openviking_memory(monkeypatch, tmp_path: Path):
    _patch_manager_runtime(monkeypatch, tmp_path)

    class _FakeArchiver:
        def __init__(self, _memory_manager):
            self.called = False

        async def archive_day(self):
            self.called = True

    monkeypatch.setattr("soul.consolidation.MemoryArchiver", _FakeArchiver)

    mm = MemoryManager(base_path=str(tmp_path / "memory"))
    try:
        await mm.save_entry(
            MemoryEntry(sender="user", content="今天我们讨论了重构计划。")
        )
        await mm.save_entry(
            MemoryEntry(sender="assistant", content="好的，我会按计划推进 OpenViking 迁移。")
        )

        consolidator = NightlyConsolidator(
            memory_manager=mm,
            relationship_graph=mm.relationships,
            emotion_tracker=mm.emotions,
            api_key=None,
            identity_path=str(tmp_path / "IDENTITY.md"),
            stories_dir=str(tmp_path / "stories"),
        )
        consolidator._update_identity = AsyncMock()
        consolidator._extract_stories = AsyncMock()

        await consolidator.run_nightly()

        assert consolidator.archiver.called is True
        consolidator._update_identity.assert_awaited_once()
        conversation = consolidator._update_identity.await_args.args[0]
        assert "重构计划" in conversation
        consolidator._extract_stories.assert_awaited_once()
    finally:
        mm.stop()
