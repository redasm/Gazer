"""Regression tests for OpenViking-first default storage paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory.manager import MemoryManager
from security.pairing import PairingManager
from soul.evolution import FEEDBACK_PATH, HISTORY_PATH
from soul.trust import TrustSystem
from soul.core import MemoryEntry


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
                    "commit_every_messages": 8,
                }
            }
        }
    )
    monkeypatch.setattr("memory.manager.config", cfg)


@pytest.mark.asyncio
async def test_memory_manager_defaults_to_openviking_storage(monkeypatch, tmp_path: Path):
    _patch_manager_runtime(monkeypatch, tmp_path)
    mm = MemoryManager()
    try:
        assert Path(mm.base_path) == (tmp_path / "ov_data").resolve()
        assert mm.watcher is None

        await mm.save_entry(MemoryEntry(sender="assistant", content="openviking only write path"))
        assert (tmp_path / "ov_data" / "memory_events.jsonl").is_file()
        assert not (tmp_path / "ov_data" / "events").exists()
    finally:
        mm.stop()


def test_pairing_and_trust_default_to_openviking_data_dir():
    pairing = PairingManager()
    trust = TrustSystem()
    assert str(pairing.persist_path).replace("\\", "/").endswith("data/openviking/pairing.json")
    assert str(trust.persist_path).replace("\\", "/").endswith("data/openviking/trust.json")


def test_evolution_default_paths_point_to_openviking():
    assert str(FEEDBACK_PATH).replace("\\", "/").endswith("data/openviking/feedback.json")
    assert str(HISTORY_PATH).replace("\\", "/").endswith("data/openviking/evolution_history.jsonl")
