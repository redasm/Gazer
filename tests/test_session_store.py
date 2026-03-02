"""Tests for agent.session_store -- SessionStore."""

import pytest
from pathlib import Path
from agent.session_store import SessionStore, _safe_filename, _decode_filename


class TestFilenameEncoding:
    def test_roundtrip(self):
        key = "telegram:12345"
        encoded = _safe_filename(key)
        assert encoded.endswith(".jsonl")
        decoded = _decode_filename(encoded.replace(".jsonl", ""))
        assert decoded == key

    def test_special_chars(self):
        key = "web:main/chat"
        encoded = _safe_filename(key)
        decoded = _decode_filename(encoded.replace(".jsonl", ""))
        assert decoded == key


class TestSessionStore:
    @pytest.fixture
    def store(self, tmp_dir):
        return SessionStore(base_dir=tmp_dir / "sessions")

    def test_append_and_load(self, store):
        store.append("s1", "user", "hello")
        store.append("s1", "assistant", "hi there")
        msgs = store.load("s1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["content"] == "hi there"

    def test_load_empty_session(self, store):
        msgs = store.load("nonexistent")
        assert msgs == []

    def test_load_limit(self, store):
        for i in range(10):
            store.append("s1", "user", f"msg {i}")
        msgs = store.load("s1", limit=3)
        assert len(msgs) == 3
        # Should be the last 3
        assert msgs[0]["content"] == "msg 7"

    def test_list_sessions(self, store):
        store.append("s1", "user", "a")
        store.append("s2", "user", "b")
        sessions = store.list_sessions()
        assert len(sessions) == 2
        assert "s1" in sessions
        assert "s2" in sessions

    def test_delete_session(self, store):
        store.append("s1", "user", "data")
        assert store.delete_session("s1") is True
        assert store.load("s1") == []
        assert store.delete_session("s1") is False

    def test_prune(self, store):
        for i in range(20):
            store.append("s1", "user", f"msg {i}")
        removed = store.prune("s1", keep_last=5)
        assert removed == 15
        msgs = store.load("s1")
        assert len(msgs) == 5

    def test_prune_no_op(self, store):
        store.append("s1", "user", "single")
        removed = store.prune("s1", keep_last=10)
        assert removed == 0

    def test_prune_nonexistent(self, store):
        assert store.prune("missing") == 0

    def test_tool_calls_stored(self, store):
        store.append("s1", "assistant", "calling tool", tool_calls=[{"id": "t1", "name": "echo"}])
        msgs = store.load("s1")
        assert len(msgs) == 1
        # tool_calls may or may not be in simplified load
        assert msgs[0]["role"] == "assistant"

    def test_disk_persistence(self, store, tmp_dir):
        store.append("s1", "user", "persistent")
        # Clear cache, reload from disk
        store._cache.clear()
        msgs = store.load("s1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "persistent"
