"""MemorySearchManager -- formal interface for pluggable memory search backends.

Inspired by OpenClaw's MemorySearchManager interface. Defines a common
contract that all memory backends (SQLiteIndex, OpenViking, QMD, etc.)
can satisfy, enabling admin status reporting, sync progress callbacks,
and embedding availability probes.

Usage::

    from memory.interface import MemorySearchManager, MemorySearchResult

    class MyBackend(MemorySearchManager):
        async def search(self, query, *, max_results=10, ...) -> list[MemorySearchResult]:
            ...
        def status(self) -> MemoryProviderStatus:
            ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class MemorySearchResult:
    """A single memory search hit."""

    content: str
    sender: str
    timestamp: str
    score: float
    source: str = "memory"
    """Source collection: ``"memory"`` or ``"sessions"``."""
    citation: Optional[str] = None
    """Optional human-readable citation string."""


@dataclass
class MemoryEmbeddingProbeResult:
    """Result of probing embedding availability."""

    ok: bool
    error: Optional[str] = None


@dataclass
class MemoryProviderStatus:
    """Runtime snapshot of a memory backend's health and configuration."""

    backend: str
    """Backend identifier: ``"sqlite"``, ``"openviking"``, ``"qmd"``, etc."""
    provider: str
    """Embedding provider name (e.g. ``"openai"``, ``"local"``, ``"none"``)."""
    model: Optional[str] = None
    """Embedding model identifier."""

    # Storage stats
    files: int = 0
    chunks: int = 0
    dirty: bool = False

    # Capability flags
    fts_available: bool = True
    vector_available: bool = False
    vector_dims: Optional[int] = None

    # Cache stats
    cache_entries: int = 0

    # Error condition
    error: Optional[str] = None

    # Backend-specific extras
    custom: Dict[str, Any] = field(default_factory=dict)


# Sync progress callback: called with (completed, total, label)
MemorySyncProgressCallback = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MemorySearchManager(Protocol):
    """
    Common interface for memory search backends.

    All methods have a default ``...`` body (Protocol convention).
    Implementations must provide at least :meth:`search`, :meth:`status`,
    :meth:`probe_embedding_availability`, :meth:`probe_vector_availability`,
    and :meth:`close`.
    """

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        min_score: float = 0.0,
        session_key: Optional[str] = None,
    ) -> List[MemorySearchResult]:
        """Perform hybrid (FTS + vector) memory search and return ranked results."""
        ...

    def status(self) -> MemoryProviderStatus:
        """Return a snapshot of the backend's health and configuration."""
        ...

    async def probe_embedding_availability(self) -> MemoryEmbeddingProbeResult:
        """Check whether the embedding provider is reachable and usable."""
        ...

    async def probe_vector_availability(self) -> bool:
        """Check whether vector (semantic) search is currently available."""
        ...

    async def sync(
        self,
        *,
        reason: Optional[str] = None,
        force: bool = False,
        progress: Optional[MemorySyncProgressCallback] = None,
    ) -> None:
        """Synchronise in-memory state (e.g. Faiss index) to persistent storage.

        *progress* is an optional callback ``(completed, total, label) -> None``
        that implementations may call during long sync operations.
        """
        ...

    def close(self) -> None:
        """Release resources held by this backend (flush, close DB, etc.)."""
        ...
