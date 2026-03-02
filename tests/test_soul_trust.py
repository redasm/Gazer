"""Tests for soul.trust -- TrustSystem, Identity, TrustLevel."""

import os
import json
import pytest
from soul.trust import TrustSystem, TrustLevel, Identity


@pytest.fixture
def trust(tmp_dir):
    path = str(tmp_dir / "trust.json")
    return TrustSystem(persist_path=path)


class TestTrustLevel:
    def test_values(self):
        assert TrustLevel.PRIMARY == 100
        assert TrustLevel.KNOWN == 50
        assert TrustLevel.STRANGER == 0


class TestIdentity:
    def test_defaults(self):
        ident = Identity(name="Alice")
        assert ident.trust_score == 0.0
        assert ident.interaction_count == 0


class TestTrustSystem:
    def test_observe_new_stranger(self, trust):
        trust.observe("Alice")
        assert "Alice" in trust.identities
        # Initial STRANGER (0) + gradual trust increment (0.5) = 0.5
        assert trust.identities["Alice"].trust_score == 0.5
        assert trust.identities["Alice"].interaction_count == 1

    def test_observe_primary(self, trust):
        trust.observe("Owner", is_primary=True)
        assert trust.identities["Owner"].trust_score == TrustLevel.PRIMARY

    def test_observe_increments_count(self, trust):
        trust.observe("Bob")
        trust.observe("Bob")
        trust.observe("Bob")
        assert trust.identities["Bob"].interaction_count == 3

    def test_trust_gradually_increases(self, trust):
        trust.observe("Carol")
        initial = trust.identities["Carol"].trust_score
        for _ in range(20):
            trust.observe("Carol")
        assert trust.identities["Carol"].trust_score > initial

    def test_relationship_prompt_unknown(self, trust):
        prompt = trust.get_relationship_prompt("Nobody")
        assert "first encounter" in prompt

    def test_relationship_prompt_primary(self, trust):
        trust.observe("Owner", is_primary=True)
        prompt = trust.get_relationship_prompt("Owner")
        assert "primary user" in prompt

    def test_persistence(self, tmp_dir):
        path = str(tmp_dir / "trust.json")
        ts1 = TrustSystem(persist_path=path)
        ts1.observe("Alice")
        ts1.observe("Bob", is_primary=True)

        # Reload
        ts2 = TrustSystem(persist_path=path)
        assert "Alice" in ts2.identities
        assert "Bob" in ts2.identities
        assert ts2.identities["Bob"].trust_score == TrustLevel.PRIMARY
