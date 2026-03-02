"""Tests for OpenViking session commit and memory extraction flow."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from memory.viking_backend import OpenVikingMemoryBackend


class _FakeOpenVikingClient:
    def __init__(self):
        self.create_calls = 0
        self.messages = []
        self.commits = []
        self.closed = False

    def create_session(self):
        self.create_calls += 1
        return {"result": {"session_id": "sess_test_1"}}

    def add_message(self, session_id: str, role: str, content: str):
        self.messages.append({"session_id": session_id, "role": role, "content": content})
        return {"status": "ok"}

    def commit_session(self, session_id: str):
        self.commits.append(session_id)
        return {"status": "ok"}

    def close(self):
        self.closed = True


def _read_jsonl(path: Path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_session_commit_by_message_threshold(tmp_path: Path):
    fake = _FakeOpenVikingClient()
    backend = OpenVikingMemoryBackend(
        data_dir=tmp_path / "ov_data",
        enable_client=True,
        client=fake,
        commit_every_messages=2,
    )

    backend.add_memory(
        content="hello",
        sender="user",
        timestamp=datetime.now(),
        metadata={},
        from_reindex=False,
    )
    backend.add_memory(
        content="world",
        sender="Gazer",
        timestamp=datetime.now(),
        metadata={},
        from_reindex=False,
    )

    assert fake.create_calls == 1
    assert len(fake.messages) == 2
    assert fake.messages[0]["role"] == "user"
    assert fake.messages[1]["role"] == "assistant"
    assert fake.commits == ["sess_test_1"]


def test_memory_extraction_decisions_create_skip_merge(tmp_path: Path):
    backend = OpenVikingMemoryBackend(
        data_dir=tmp_path / "ov_data",
        enable_client=False,
        commit_every_messages=10,
    )

    ts = datetime.now()
    backend.add_memory(
        content="I prefer coffee in the morning.",
        sender="user",
        timestamp=ts,
        metadata={"memory_key": "drink_pref"},
        from_reindex=False,
    )
    backend.add_memory(
        content="I prefer coffee in the morning.",
        sender="user",
        timestamp=ts,
        metadata={"memory_key": "drink_pref"},
        from_reindex=False,
    )
    backend.add_memory(
        content="I prefer tea at night.",
        sender="user",
        timestamp=ts,
        metadata={"memory_key": "drink_pref"},
        from_reindex=False,
    )

    decisions = _read_jsonl(tmp_path / "ov_data" / "extraction_decisions.jsonl")
    actions = [item["decision"] for item in decisions if item.get("kind") == "memory_extraction"]
    assert "CREATE" in actions
    assert "SKIP" in actions
    assert "MERGE" in actions

    pref_store = tmp_path / "ov_data" / "long_term" / "preferences.json"
    assert pref_store.is_file()
    data = json.loads(pref_store.read_text(encoding="utf-8"))
    assert "drink_pref" in data
    assert "coffee" in data["drink_pref"]["content"]
    assert "tea" in data["drink_pref"]["content"]


def test_tool_event_is_classified_as_case(tmp_path: Path):
    backend = OpenVikingMemoryBackend(
        data_dir=tmp_path / "ov_data",
        enable_client=False,
    )
    backend.add_memory(
        content="Tool Execution [web_search] Result: ok",
        sender="System",
        timestamp=datetime.now(),
        metadata={"tool_name": "web_search", "tool_call": True},
        from_reindex=False,
    )

    case_store = tmp_path / "ov_data" / "long_term" / "cases.json"
    assert case_store.is_file()
    store_data = json.loads(case_store.read_text(encoding="utf-8"))
    assert len(store_data) == 1

    decisions = _read_jsonl(tmp_path / "ov_data" / "extraction_decisions.jsonl")
    latest = [row for row in decisions if row.get("kind") == "memory_extraction"][-1]
    assert latest["category"] == "cases"
    assert latest["decision"] == "CREATE"
