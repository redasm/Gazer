from __future__ import annotations

from runtime.rust_gate import (
    get_current_tool_access_context,
    is_rust_allowed_for_context,
    push_tool_access_context,
)


class _Cfg:
    def __init__(self, values):
        self._values = dict(values)

    def get(self, key_path: str, default=None):
        return self._values.get(key_path, default)


class _OwnerMgr:
    def __init__(self, is_owner: bool = False):
        self._is_owner = bool(is_owner)

    def is_owner_sender(self, channel: str, sender_id: str) -> bool:
        return self._is_owner


def test_rollout_disabled_allows_rust_without_context() -> None:
    cfg = _Cfg({"runtime.rust_sidecar.rollout": {"enabled": False}})
    assert is_rust_allowed_for_context(cfg) is True


def test_rollout_owner_only_requires_owner(monkeypatch) -> None:
    cfg = _Cfg(
        {
            "runtime.rust_sidecar.rollout": {
                "enabled": True,
                "owner_only": True,
                "channels": [],
            }
        }
    )
    monkeypatch.setattr("runtime.rust_gate.get_owner_manager", lambda: _OwnerMgr(is_owner=False))
    assert is_rust_allowed_for_context(cfg, channel="web", sender_id="u1") is False

    monkeypatch.setattr("runtime.rust_gate.get_owner_manager", lambda: _OwnerMgr(is_owner=True))
    assert is_rust_allowed_for_context(cfg, channel="web", sender_id="owner") is True


def test_rollout_channel_allowlist(monkeypatch) -> None:
    cfg = _Cfg(
        {
            "runtime.rust_sidecar.rollout": {
                "enabled": True,
                "owner_only": False,
                "channels": ["feishu", "web"],
            }
        }
    )
    monkeypatch.setattr("runtime.rust_gate.get_owner_manager", lambda: _OwnerMgr(is_owner=False))
    assert is_rust_allowed_for_context(cfg, channel="web", sender_id="u1") is True
    assert is_rust_allowed_for_context(cfg, channel="discord", sender_id="u1") is False


def test_rollout_missing_context_denies_when_restricted() -> None:
    cfg = _Cfg(
        {
            "runtime.rust_sidecar.rollout": {
                "enabled": True,
                "owner_only": True,
                "channels": [],
            }
        }
    )
    assert is_rust_allowed_for_context(cfg, channel="", sender_id="") is False


def test_push_tool_access_context_roundtrip() -> None:
    assert get_current_tool_access_context() == {}
    with push_tool_access_context(channel="web", sender_id="u1"):
        payload = get_current_tool_access_context()
        assert payload["channel"] == "web"
        assert payload["sender_id"] == "u1"
    assert get_current_tool_access_context() == {}

