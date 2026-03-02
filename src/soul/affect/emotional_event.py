"""Emotional events with half-life exponential decay and memory consolidation.

Models individual emotional events that decay over time following an
exponential curve.  ``AffectiveStateManager`` aggregates active events to
compute the current composite emotional state.

When events decay below a threshold, they can be consolidated into long-term
memory via an optional ``MemoryPort`` callback.

References:
    - soul_architecture_reform.md Issue-04 (including v1.1 revision)
    - Jill Bolte Taylor: emotional chemical storm ~90 seconds
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from soul.affect.affective_state import AffectiveState

if TYPE_CHECKING:
    from soul.memory.memory_port import MemoryPort

logger = logging.getLogger("SoulEmotionalEvent")


@dataclass
class EmotionalEvent:
    """A single emotional stimulus with exponential decay.

    Args:
        trigger: Human-readable description of what caused the event.
        affect_delta: Direction of the emotional shift.
        timestamp: When the event occurred (epoch seconds).
        half_life_seconds: Time for intensity to drop to 50%.
    """

    trigger: str
    affect_delta: AffectiveState
    timestamp: float = field(default_factory=time.time)
    half_life_seconds: float = 300.0

    def current_intensity(self, now: float | None = None) -> float:
        """Current remaining intensity via exponential decay.

        At ``t = half_life_seconds`` the return value is approximately 0.5.
        """
        elapsed = (now or time.time()) - self.timestamp
        if elapsed < 0:
            return 1.0
        return math.exp(-elapsed * math.log(2) / self.half_life_seconds)


class AffectiveStateManager:
    """Manages emotional events and computes the current composite affect.

    The composite affect is the personality-determined baseline plus the
    weighted sum of all active (non-expired) emotional events.
    """

    HALF_LIFE_MAP: dict[str, float] = {
        "user_praise": 180.0,
        "user_criticism": 600.0,
        "task_success": 120.0,
        "task_failure": 480.0,
        "default": 300.0,
    }

    def __init__(
        self,
        baseline: AffectiveState,
        memory_port: "MemoryPort | None" = None,
    ) -> None:
        """
        Args:
            baseline: Personality-determined emotional baseline
                (from ``PersonalityVector.to_affect_baseline()``).
            memory_port: Optional ``MemoryPort`` for memory consolidation
                of expired events.
        """
        self._baseline = baseline
        self._events: list[EmotionalEvent] = []
        self._memory_port = memory_port
        self._pending_consolidations: list[dict] = []

    @property
    def baseline(self) -> AffectiveState:
        return self._baseline

    def update_baseline(self, new_baseline: AffectiveState) -> None:
        """Replace the personality-determined emotional baseline."""
        self._baseline = new_baseline

    def add_event(self, event: EmotionalEvent) -> None:
        """Register a new emotional event and prune expired ones."""
        self._events.append(event)
        self._prune_expired()

    def current_affect(self) -> AffectiveState:
        """Compute current composite emotional state.

        Starts from the baseline and drifts toward each active event's
        direction, weighted by its remaining intensity.
        """
        now = time.time()
        affect = self._baseline
        for event in self._events:
            intensity = event.current_intensity(now)
            if intensity > 0.01:
                affect = affect.transition_toward(event.affect_delta, intensity)
        return affect

    def get_history(self, n: int = 10) -> list[AffectiveState]:
        """Return the affect directions from the most recent *n* events.

        Used by ``ProactiveInferenceEngine`` for trend analysis.
        """
        return [e.affect_delta for e in self._events[-n:]]

    def active_event_count(self) -> int:
        """Number of currently active (non-expired) events."""
        return len(self._events)

    def _prune_expired(
        self,
        threshold: float = 0.01,
    ) -> None:
        """Remove events whose intensity has dropped below *threshold*.

        Expired events are collected into ``_pending_consolidations`` for
        later async persistence via ``flush_consolidations()``.  This
        keeps the synchronous ``add_event()`` path free of async calls.
        """
        now = time.time()
        expired = [e for e in self._events if e.current_intensity(now) <= threshold]
        active = [e for e in self._events if e.current_intensity(now) > threshold]

        if expired and self._memory_port is not None:
            self._pending_consolidations.append({
                "type": "emotional_consolidation",
                "timestamp": now,
                "events": [
                    {
                        "trigger": e.trigger,
                        "valence": e.affect_delta.valence,
                        "arousal": e.affect_delta.arousal,
                        "dominance": e.affect_delta.dominance,
                        "half_life": e.half_life_seconds,
                    }
                    for e in expired
                ],
            })

        self._events = active

    async def flush_consolidations(self) -> None:
        """Persist any pending emotional consolidation payloads.

        Must be called from an async context (e.g. at the end of
        ``GazerPersonality.process()``).
        """
        if not self._pending_consolidations or self._memory_port is None:
            return
        pending = self._pending_consolidations[:]
        self._pending_consolidations.clear()
        for payload in pending:
            try:
                await self._memory_port.store(
                    key=f"emotional_consolidation:{int(payload['timestamp'])}",
                    content=payload,
                )
            except Exception as exc:
                logger.warning("Memory consolidation failed (non-fatal): %s", exc)
