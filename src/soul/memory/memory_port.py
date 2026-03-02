"""Memory port abstraction — dependency inversion for memory storage.

``MentalProcess`` and ``CognitiveStep`` depend **only** on the abstract
``MemoryPort`` interface, never on concrete backends like OpenViking.

Concrete backends:
  - ``OpenVikingMemoryPort``: wraps the existing ``OpenVikingMemoryBackend``
  - ``EmotionAwareMemoryPort``: decorator that injects emotion bias (Issue-05)
  - ``InMemoryMemoryPort``: tests without any external service

References:
    - soul_architecture_reform.md Issue-05, Issue-08
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from soul.affect.affective_state import AffectiveState

logger = logging.getLogger("SoulMemoryPort")


# =====================================================================
# Abstract interface
# =====================================================================


class MemoryPort(ABC):
    """Abstract memory interface.

    ``MentalProcess`` and ``CognitiveStep`` depend only on this interface —
    they never know about OpenViking, Pinecone, or any concrete backend.
    """

    @abstractmethod
    async def query(
        self,
        query: str,
        current_affect: "AffectiveState | None" = None,
        top_k: int = 5,
        slot: str = "",
    ) -> list[str]:
        """Retrieve relevant memories.

        Args:
            query: Natural-language search query.
            current_affect: Optional current emotional state for biased retrieval.
            top_k: Maximum number of results.
            slot: Context slot filter (``"user"`` / ``"agent"`` / ``"session"``).

        Returns:
            List of memory content strings, ordered by relevance.
        """
        ...

    @abstractmethod
    async def store(self, key: str, content: dict[str, Any]) -> None:
        """Persist a memory record.

        Args:
            key: Unique key  (e.g. ``"personality:user:123:history:1709000000"``).
            content: Arbitrary JSON-serializable payload.
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a memory record by key.

        Returns:
            ``True`` if a record was deleted, ``False`` if not found.
        """
        ...


# =====================================================================
# OpenViking concrete implementation
# =====================================================================


class OpenVikingMemoryPort(MemoryPort):
    """Wraps ``memory.viking_backend.OpenVikingMemoryBackend``.

    When the project switches vector databases, only this class needs
    to change — the rest of ``soul/`` is unaffected.
    """

    # Slot → sender roles mapping for post-filtering.
    _SLOT_SENDER_MAP: dict[str, set[str]] = {
        "user": {"user", "owner", "human"},
        "agent": {"assistant", "system"},
    }

    def __init__(self, viking_backend: Any) -> None:
        """
        Args:
            viking_backend: An ``OpenVikingMemoryBackend`` instance (or any
                object that exposes compatible ``semantic_search`` /
                ``add_memory`` / ``delete_by_date`` methods).
        """
        self._backend = viking_backend

    async def query(
        self,
        query: str,
        current_affect: "AffectiveState | None" = None,
        top_k: int = 5,
        slot: str = "",
    ) -> list[str]:
        try:
            # Fetch more results when filtering by slot so we can still
            # return up to *top_k* after post-filter.
            fetch_limit = top_k * 3 if slot else top_k

            search_index = getattr(self._backend, "search_index", None)
            if search_index is not None:
                results = await search_index.semantic_search(query=query, limit=fetch_limit)
            else:
                results = await self._backend.semantic_search(query=query, limit=fetch_limit)

            # --- slot filtering (Issue-09 v1.1) ---
            allowed_senders = self._SLOT_SENDER_MAP.get(slot)
            if allowed_senders is not None:
                results = [
                    r for r in results
                    if self._sender_of(r).lower() in allowed_senders
                ]
            elif slot == "session":
                # Session slot: prefer the most recent records (already
                # ordered by relevance; no sender filter needed).
                pass  # no additional filtering

            contents = [
                r.get("content", str(r)) if isinstance(r, dict) else str(r)
                for r in results
            ]
            return contents[:top_k]
        except Exception as exc:
            logger.warning("OpenViking query failed: %s", exc)
            return []

    @staticmethod
    def _sender_of(record: Any) -> str:
        """Extract sender string from a result record (dict or tuple)."""
        if isinstance(record, dict):
            return str(record.get("sender", ""))
        if isinstance(record, (tuple, list)) and len(record) >= 2:
            return str(record[1])
        return ""

    async def store(self, key: str, content: dict[str, Any]) -> None:
        try:
            from datetime import datetime, timezone

            search_index = getattr(self._backend, "search_index", None)
            if search_index is not None:
                search_index.add_memory(
                    content=str(content),
                    sender="system",
                    timestamp=datetime.now(timezone.utc),
                    metadata={"key": key, **content},
                )
            else:
                self._backend.add_memory(
                    content=str(content),
                    sender="system",
                    timestamp=datetime.now(timezone.utc),
                    metadata={"key": key, **content},
                )
        except Exception as exc:
            logger.warning("OpenViking store failed for key=%s: %s", key, exc)

    async def delete(self, key: str) -> bool:
        try:
            self._backend.delete_by_date(key)
            return True
        except Exception as exc:
            logger.warning("OpenViking delete failed for key=%s: %s", key, exc)
            return False


# =====================================================================
# In-memory implementation for testing
# =====================================================================


class InMemoryMemoryPort(MemoryPort):
    """Simple in-memory store for unit tests — no external dependencies."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def query(
        self,
        query: str,
        current_affect: "AffectiveState | None" = None,
        top_k: int = 5,
        slot: str = "",
    ) -> list[str]:
        if slot:
            # Filter by slot metadata stored via store()
            filtered = [
                k for k, v in self._store.items()
                if v.get("slot", "") == slot
            ]
            return filtered[:top_k]
        return list(self._store.keys())[:top_k]

    async def store(self, key: str, content: dict[str, Any]) -> None:
        self._store[key] = content

    async def delete(self, key: str) -> bool:
        return bool(self._store.pop(key, None))

    # ----- test helpers -----

    def count(self) -> int:
        """Return number of stored records."""
        return len(self._store)

    def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a record by key (test inspection)."""
        return self._store.get(key)


# =====================================================================
# Emotion-aware decorator  (Issue-05)
# =====================================================================


class EmotionAwareMemoryPort(MemoryPort):
    """Decorator that injects mood-congruent bias into memory queries.

    Wraps any ``MemoryPort`` and modifies the query string based on the
    current ``AffectiveState``, implementing the **Mood-Congruent Memory
    Effect**: when valence is strongly negative the query is augmented
    with negative-sentiment keywords so the underlying vector search
    preferentially returns emotionally congruent memories.

    References:
        - soul_architecture_reform.md Issue-05
    """

    def __init__(self, delegate: MemoryPort) -> None:
        """
        Args:
            delegate: The underlying ``MemoryPort`` to wrap.
        """
        self._delegate = delegate

    # ------------------------------------------------------------------
    # MemoryPort interface
    # ------------------------------------------------------------------

    async def query(
        self,
        query: str,
        current_affect: "AffectiveState | None" = None,
        top_k: int = 5,
        slot: str = "",
    ) -> list[str]:
        bias_prompt = self._build_bias_prompt(current_affect) if current_affect else ""
        biased_query = f"{query} {bias_prompt}".strip() if bias_prompt else query
        return await self._delegate.query(
            query=biased_query,
            current_affect=current_affect,
            top_k=top_k,
            slot=slot,
        )

    async def store(self, key: str, content: dict[str, Any]) -> None:
        return await self._delegate.store(key, content)

    async def delete(self, key: str) -> bool:
        return await self._delegate.delete(key)

    # ------------------------------------------------------------------
    # Bias helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_bias_prompt(affect: "AffectiveState") -> str:
        """Return sentiment keywords to append based on current valence."""
        if affect.valence < -0.5:
            return "negative sad difficult"
        if affect.valence > 0.5:
            return "positive happy pleasant"
        return ""

    @staticmethod
    def _build_affect_filter(affect: "AffectiveState | None") -> dict[str, str] | None:
        """Build an OpenViking metadata filter for emotional polarity.

        Returns ``None`` when no strong polarity is detected so the
        caller can fall back to an unfiltered search.
        """
        if affect and abs(affect.valence) > 0.6:
            polarity = "negative" if affect.valence < 0 else "positive"
            return {"metadata.emotional_polarity": polarity}
        return None
