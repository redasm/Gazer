from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent.context_builder import GazerContextBuilder
from soul.core import WorkingMemory


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key_path: str, default=None):
        cur = self.data
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class _FakeMemoryManager:
    def __init__(self):
        self.load_recent_limits: list[int] = []
        self.context_kwargs: list[dict] = []

    def load_recent(self, limit: int = 20) -> WorkingMemory:
        self.load_recent_limits.append(int(limit))
        return WorkingMemory(memories=[])

    async def get_companion_context(self, current_message: str, working_memory: WorkingMemory, **kwargs) -> str:
        self.context_kwargs.append(dict(kwargs))
        return "记忆上下文-" + ("A" * 6000)


class _FakePersonaRuntime:
    def __init__(self, signal: dict):
        self._signal = signal

    def get_latest_signal(self):
        return dict(self._signal)


@pytest.mark.asyncio
async def test_context_builder_warning_signal_shrinks_injected_memory(monkeypatch, tmp_path: Path):
    fake_cfg = _FakeConfig(
        {
            "personality": {
                "runtime": {
                    "memory_context_guard": {
                        "enabled": True,
                        "trigger_levels": ["warning", "critical"],
                        "window_seconds": 1800,
                        "sources": ["persona_eval"],
                        "warning": {
                            "recent_limit": 7,
                            "entity_limit": 2,
                            "semantic_limit": 2,
                            "max_recall_items": 2,
                            "max_context_chars": 240,
                            "include_relationship_context": False,
                            "include_time_reminders": True,
                            "include_emotion_context": True,
                            "include_recent_observation": False,
                        },
                    }
                }
            }
        }
    )
    signal = {
        "level": "warning",
        "source": "persona_eval",
        "created_at": time.time(),
    }
    fake_mm = _FakeMemoryManager()
    monkeypatch.setattr("agent.context_builder.config", fake_cfg)
    monkeypatch.setattr("soul.persona_runtime.get_persona_runtime_manager", lambda: _FakePersonaRuntime(signal))

    builder = GazerContextBuilder(workspace=Path(tmp_path), memory_manager=fake_mm)
    await builder.prepare_memory_context("最近我有点焦虑")

    assert fake_mm.load_recent_limits[-1] == 7
    assert fake_mm.context_kwargs
    kwargs = fake_mm.context_kwargs[-1]
    assert kwargs["entity_limit"] == 2
    assert kwargs["semantic_limit"] == 2
    assert kwargs["max_recall_items"] == 2
    assert kwargs["max_context_chars"] == 240
    assert kwargs["include_relationship_context"] is False
    assert kwargs["include_recent_observation"] is False
    assert builder._companion_context is not None
    assert len(builder._companion_context) <= 240

