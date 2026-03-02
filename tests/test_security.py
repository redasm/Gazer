"""Tests for security -- OwnerManager + PairingManager."""

import os
import json
import time
import pytest
from types import SimpleNamespace
from unittest.mock import patch
from security.owner import OwnerManager
from security.pairing import PairingManager, _generate_code, _CODE_TTL_SECONDS


class TestOwnerManager:
    @pytest.fixture
    def owner(self, tmp_dir):
        path = str(tmp_dir / "owner.json")
        return OwnerManager(owner_file=path)

    def test_auto_generates_token(self, owner):
        assert owner.admin_token != ""
        assert len(owner.admin_token) > 20

    def test_validate_session(self, owner):
        token = owner.admin_token
        assert owner.validate_session(token) is True
        assert owner.validate_session("wrong") is False
        assert owner.validate_session("") is False

    def test_create_and_revoke_session_token(self, owner):
        session_token = owner.create_session(ttl_seconds=300, metadata={"source": "test"})
        assert session_token.startswith("sess_")
        assert owner.validate_session(session_token, allow_admin_token=False) is True
        assert owner.revoke_session(session_token) is True
        assert owner.validate_session(session_token, allow_admin_token=False) is False

    def test_persistence(self, tmp_dir):
        path = str(tmp_dir / "owner.json")
        o1 = OwnerManager(owner_file=path)
        token = o1.admin_token

        o2 = OwnerManager(owner_file=path)
        assert o2.admin_token == token

    def test_is_owner_sender(self, owner):
        with patch.object(type(owner), "channel_ids", new_callable=lambda: property(lambda self: {"telegram": "123"})):
            assert owner.is_owner_sender("telegram", "123") is True
            assert owner.is_owner_sender("telegram", "456") is False
            assert owner.is_owner_sender("discord", "123") is False

    def test_session_cleanup_respects_max_session_records(self, tmp_dir, monkeypatch):
        path = str(tmp_dir / "owner.json")
        fake_cfg = SimpleNamespace(
            get=lambda key, default=None: {
                "api.session_max_records": 3,
            }.get(key, default)
        )
        monkeypatch.setattr("security.owner.config", fake_cfg)

        now = {"ts": 1000.0}
        monkeypatch.setattr("security.owner.time.time", lambda: now["ts"])
        owner = OwnerManager(owner_file=path)

        tokens = []
        for _ in range(5):
            now["ts"] += 1.0
            tokens.append(owner.create_session(ttl_seconds=600, metadata={"source": "test"}))

        sessions = owner._data.get("sessions", {})
        assert isinstance(sessions, dict)
        assert len(sessions) == 3
        assert tokens[0] not in sessions
        assert tokens[1] not in sessions
        assert tokens[2] in sessions
        assert tokens[3] in sessions
        assert tokens[4] in sessions


class TestPairingManager:
    @pytest.fixture
    def pairing(self, tmp_dir):
        path = str(tmp_dir / "pairing.json")
        return PairingManager(persist_path=path)

    def test_generate_code(self):
        code = _generate_code()
        assert len(code) == 8
        # No confusable chars
        for c in code:
            assert c not in "01OIl"

    def test_challenge_creates_code(self, pairing):
        code = pairing.challenge("telegram", "user1")
        assert len(code) == 8
        assert not pairing.is_approved("telegram", "user1")

    def test_same_challenge_returns_same_code(self, pairing):
        c1 = pairing.challenge("telegram", "user1")
        c2 = pairing.challenge("telegram", "user1")
        assert c1 == c2

    def test_approve(self, pairing):
        code = pairing.challenge("telegram", "user1")
        req = pairing.approve(code)
        assert req is not None
        assert req.sender_id == "user1"
        assert pairing.is_approved("telegram", "user1") is True

    def test_approve_invalid_code(self, pairing):
        assert pairing.approve("INVALID1") is None

    def test_reject(self, pairing):
        code = pairing.challenge("telegram", "user1")
        req = pairing.reject(code)
        assert req is not None
        assert not pairing.is_approved("telegram", "user1")

    def test_revoke(self, pairing):
        code = pairing.challenge("telegram", "user1")
        pairing.approve(code)
        assert pairing.is_approved("telegram", "user1") is True
        pairing.revoke("telegram", "user1")
        assert pairing.is_approved("telegram", "user1") is False

    def test_add_approved(self, pairing):
        pairing.add_approved("discord", "admin1")
        assert pairing.is_approved("discord", "admin1") is True

    def test_list_pending(self, pairing):
        pairing.challenge("tg", "u1")
        pairing.challenge("tg", "u2")
        pending = pairing.list_pending()
        assert len(pending) == 2

    def test_list_approved(self, pairing):
        pairing.add_approved("tg", "u1")
        pairing.add_approved("tg", "u2")
        approved = pairing.list_approved()
        assert "tg" in approved
        assert len(approved["tg"]) == 2

    def test_persistence(self, tmp_dir):
        path = str(tmp_dir / "pair.json")
        p1 = PairingManager(persist_path=path)
        p1.add_approved("tg", "owner1")

        p2 = PairingManager(persist_path=path)
        assert p2.is_approved("tg", "owner1") is True

    def test_list_approved_reloads_cross_process_updates(self, tmp_dir):
        path = str(tmp_dir / "pair_cross_process.json")
        p1 = PairingManager(persist_path=path)
        assert p1.list_approved() == {}

        p2 = PairingManager(persist_path=path)
        p2.add_approved("tg", "owner2")

        approved = p1.list_approved()
        assert approved == {"tg": ["owner2"]}
