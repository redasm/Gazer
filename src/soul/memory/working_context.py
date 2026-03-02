"""Immutable three-slot working context snapshot.

Replaces the flat ``WorkingMemory`` with entity-boundary isolation:

- ``user_context``: user preferences, behavioral history, user profile
  (sourced from OpenViking user layer)
- ``agent_context``: AI personality description, current goals, self-awareness
  (sourced from ``PersonalityVector``)
- ``session_context``: current session short-term memory, recent conversation
  summaries (session-scoped)

Every field is immutable.  State transitions consume a snapshot and produce
a new one via ``with_update()``.

References:
    - soul_architecture_reform.md Issue-02 (v1.1 revision)
    - Context Engineering 2.0, §5.3.2 Context Isolation
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from soul.affect.affective_state import AffectiveState


@dataclass(frozen=True)
class WorkingContext:
    """Immutable context snapshot with three entity-boundary slots.

    This is the primary data carrier flowing through the cognitive pipeline.
    Each processing step receives a ``WorkingContext`` and returns a new one —
    never mutates in place.
    """

    # ── Entity-boundary slots ──────────────────────────────────────────
    user_context: tuple[str, ...] = ()
    """User preferences, history, profile (from OpenViking user layer)."""

    agent_context: tuple[str, ...] = ()
    """AI personality description, goals, self-awareness (from PersonalityVector)."""

    session_context: tuple[str, ...] = ()
    """Current session short-term memory, recent dialogue summaries."""

    # ── State & control ────────────────────────────────────────────────
    affect: AffectiveState = field(default_factory=AffectiveState)
    """Current emotional state (VAD continuous vector)."""

    user_input: str = ""
    """The latest user message text."""

    turn_count: int = 0
    """Number of dialogue turns in the current session."""

    session_id: str = ""
    """Unique identifier for the current session."""

    metadata: tuple[tuple[str, Any], ...] = ()
    """Arbitrary key-value metadata (immutable tuple of pairs)."""

    # ── Immutable update ───────────────────────────────────────────────

    def with_update(self, **kwargs: Any) -> WorkingContext:
        """Return a new snapshot with selected fields replaced.

        The original instance is never modified.

        Example::

            new_ctx = ctx.with_update(
                turn_count=ctx.turn_count + 1,
                user_input="hello",
            )
        """
        current = {f.name: getattr(self, f.name) for f in fields(self)}
        current.update(kwargs)
        return WorkingContext(**current)

    # ── Backward compatibility ─────────────────────────────────────────

    def all_memories(self) -> tuple[str, ...]:
        """Aggregate view of all three slots.

        Provides backward compatibility for code that previously consumed
        the flat ``WorkingMemory.memories`` field.
        """
        return self.user_context + self.agent_context + self.session_context

    # ── Helpers ─────────────────────────────────────────────────────────

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Look up a value from the metadata tuple-of-pairs."""
        for k, v in self.metadata:
            if k == key:
                return v
        return default

    def set_metadata(self, key: str, value: Any) -> "WorkingContext":
        """Return a new snapshot with the given metadata key set/replaced."""
        new_meta = tuple((k, v) for k, v in self.metadata if k != key)
        new_meta = new_meta + ((key, value),)
        return self.with_update(metadata=new_meta)
