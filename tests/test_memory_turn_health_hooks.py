from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.adapter import GazerContextBuilder
from agent.turn_hooks import TurnHookManager
from soul.core import WorkingMemory
from tools.admin import workflows as admin_api


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
    def load_recent(self, limit: int = 20) -> WorkingMemory:
        return WorkingMemory(memories=[])

    async def get_companion_context(self, _current_message: str, _working_memory: WorkingMemory, **_kwargs) -> str:
        return "ctx"

    def get_last_context_stats(self) -> dict:
        return {
            "recall_count": 4,
            "entity_count": 1,
            "semantic_count": 2,
            "time_reminder_count": 1,
            "memory_context_chars": 3,
        }


@pytest.mark.asyncio
async def test_turn_hook_manager_emits_all_events():
    mgr = TurnHookManager()
    called = []

    async def _before(payload):
        called.append(("before", payload.get("id")))

    async def _after_tool(payload):
        called.append(("tool", payload.get("id")))

    async def _after_turn(payload):
        called.append(("turn", payload.get("id")))

    mgr.on_before_prompt_build(_before)
    mgr.on_after_tool_result(_after_tool)
    mgr.on_after_turn(_after_turn)

    await mgr.emit_before_prompt_build({"id": "a"})
    await mgr.emit_after_tool_result({"id": "b"})
    await mgr.emit_after_turn({"id": "c"})

    assert called == [("before", "a"), ("tool", "b"), ("turn", "c")]


@pytest.mark.asyncio
async def test_context_builder_exposes_memory_stats(monkeypatch, tmp_path: Path):
    fake_cfg = _FakeConfig(
        {
            "personality": {
                "runtime": {
                    "memory_context_guard": {
                        "enabled": False
                    }
                }
            }
        }
    )
    monkeypatch.setattr("agent.adapter.config", fake_cfg)
    builder = GazerContextBuilder(workspace=tmp_path, memory_manager=_FakeMemoryManager())
    await builder.prepare_memory_context("hello")
    stats = builder.get_memory_context_stats()
    assert stats["memory_context_chars"] >= 1
    assert stats["recall_count"] == 4


@pytest.mark.asyncio
async def test_memory_turn_health_api(monkeypatch, tmp_path: Path):
    health_path = tmp_path / "memory_turn_health.jsonl"
    tool_path = tmp_path / "tool_result_persistence.jsonl"
    rows = [
        {"ts": 1, "memory_context_chars": 300, "recall_count": 3, "persist_ok": True, "status": "success"},
        {"ts": 2, "memory_context_chars": 100, "recall_count": 1, "persist_ok": False, "status": "success"},
    ]
    tool_rows = [
        {"tool_name": "web_search", "decision": "memory"},
        {"tool_name": "exec", "decision": "trajectory_only"},
    ]
    health_path.parent.mkdir(parents=True, exist_ok=True)
    with open(health_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(tool_path, "w", encoding="utf-8") as fh:
        for row in tool_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    monkeypatch.setattr(admin_api, "_MEMORY_TURN_HEALTH_LOG_PATH", health_path)
    monkeypatch.setattr(admin_api, "_TOOL_PERSIST_LOG_PATH", tool_path)
    monkeypatch.setattr(
        admin_api,
        "config",
        _FakeConfig({"memory": {"tool_result_persistence": {"enabled": True, "mode": "allowlist"}}}),
    )

    payload = await admin_api.get_memory_turn_health(limit=20)
    assert payload["status"] == "ok"
    assert payload["summary"]["turn_count"] == 2
    assert payload["summary"]["avg_recall_count"] == 2.0
    assert payload["tool_persistence"]["decision_counts"]["memory"] == 1
    assert payload["tool_persistence"]["decision_counts"]["trajectory_only"] == 1
