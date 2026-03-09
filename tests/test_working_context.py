"""Tests for soul.memory.working_context — Issue-02 acceptance criteria.

Verifies:
  - ``WorkingContext`` is frozen — field assignment raises FrozenInstanceError
  - ``with_update()`` returns a new snapshot, original unchanged (idempotency)
  - Three-slot isolation: user_context / agent_context / session_context
"""

import pytest

from soul.affect.affective_state import AffectiveState
from soul.memory.working_context import WorkingContext


class TestFrozenBehavior:
    def test_frozen_assignment_raises(self) -> None:
        ctx = WorkingContext()
        with pytest.raises(AttributeError):
            ctx.user_input = "hello"  # type: ignore[misc]

    def test_frozen_slot_assignment_raises(self) -> None:
        ctx = WorkingContext()
        with pytest.raises(AttributeError):
            ctx.user_context = ("override",)  # type: ignore[misc]


class TestWithUpdate:
    def test_returns_new_instance(self) -> None:
        ctx = WorkingContext(user_input="first")
        updated = ctx.with_update(user_input="second")
        assert updated is not ctx
        assert ctx.user_input == "first"
        assert updated.user_input == "second"

    def test_other_fields_preserved(self) -> None:
        ctx = WorkingContext(
            user_context=("pref1",),
            agent_context=("persona",),
            session_context=("recent_msg",),
            turn_count=5,
            session_id="sess-01",
        )
        updated = ctx.with_update(turn_count=6)
        assert updated.user_context == ("pref1",)
        assert updated.agent_context == ("persona",)
        assert updated.session_context == ("recent_msg",)
        assert updated.turn_count == 6
        assert updated.session_id == "sess-01"

    def test_idempotency(self) -> None:
        """Same context passed twice to with_update should produce equal results."""
        ctx = WorkingContext(user_input="hello", turn_count=1)
        r1 = ctx.with_update(turn_count=2)
        r2 = ctx.with_update(turn_count=2)
        assert r1 == r2


class TestThreeSlotIsolation:
    def test_slots_independent(self) -> None:
        ctx = WorkingContext(
            user_context=("user_pref",),
            agent_context=("ai_persona",),
            session_context=("short_term",),
        )
        assert "user_pref" in ctx.user_context
        assert "ai_persona" in ctx.agent_context
        assert "short_term" in ctx.session_context

    def test_slots_immutable(self) -> None:
        ctx = WorkingContext(user_context=("a", "b"))
        with pytest.raises(AttributeError):
            ctx.user_context.append("c")  # type: ignore[attr-defined]


class TestMetadata:
    def test_get_metadata(self) -> None:
        ctx = WorkingContext(metadata=(("key1", "val1"), ("key2", 42)))
        assert ctx.get_metadata("key1") == "val1"
        assert ctx.get_metadata("key2") == 42
        assert ctx.get_metadata("missing", "default") == "default"

    def test_set_metadata(self) -> None:
        ctx = WorkingContext()
        updated = ctx.set_metadata("foo", "bar")
        assert updated.get_metadata("foo") == "bar"
        assert ctx.get_metadata("foo") is None  # original unchanged


class TestAffectIntegration:
    def test_affect_field(self) -> None:
        affect = AffectiveState(valence=0.5, arousal=0.3)
        ctx = WorkingContext(affect=affect)
        assert ctx.affect.valence == 0.5
        assert ctx.affect.to_label() == "开心"

    def test_affect_update(self) -> None:
        ctx = WorkingContext()
        new_affect = AffectiveState(valence=-0.8, arousal=0.6)
        updated = ctx.with_update(affect=new_affect)
        assert updated.affect.valence == -0.8
        assert ctx.affect.valence == 0.0  # original unchanged
