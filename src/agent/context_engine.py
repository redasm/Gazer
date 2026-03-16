"""ContextEngine -- pluggable context management protocol.

Inspired by OpenClaw's ContextEngine interface. Provides a formal contract
for assembling and compacting LLM context within a session, with optional
lifecycle hooks for bootstrapping, post-turn processing, and sub-agent
lifecycle management.

Usage::

    class MyEngine(ContextEngine):
        @property
        def info(self) -> ContextEngineInfo:
            return ContextEngineInfo(id="my_engine", name="My Engine")

        async def ingest(self, *, session_key, role, content, **kw) -> IngestResult:
            ...  # persist to your store
            return IngestResult(ingested=True)

        async def assemble(self, *, session_key, messages, token_budget=None) -> AssembleResult:
            ...  # prune / augment messages
            return AssembleResult(messages=messages, estimated_tokens=0)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ContextEngine")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class AssembleResult:
    """Result of assembling model context under a token budget."""

    messages: List[Dict[str, Any]]
    """Ordered messages ready for the LLM call."""
    estimated_tokens: int = 0
    """Estimated total tokens consumed by the assembled messages."""
    system_prompt_addition: Optional[str] = None
    """Optional text to prepend to the system prompt (engine-provided)."""


@dataclass
class CompactResult:
    """Result of a compaction operation."""

    ok: bool
    compacted: bool
    reason: Optional[str] = None
    tokens_before: int = 0
    tokens_after: Optional[int] = None
    summary: Optional[str] = None
    first_kept_entry_id: Optional[str] = None


@dataclass
class IngestResult:
    """Result of ingesting a single message."""

    ingested: bool


@dataclass
class IngestBatchResult:
    """Result of ingesting a completed turn batch."""

    ingested_count: int


@dataclass
class BootstrapResult:
    """Result of bootstrapping an engine session."""

    bootstrapped: bool
    imported_messages: int = 0
    reason: Optional[str] = None


@dataclass
class SubagentSpawnPreparation:
    """Rollback handle returned by :meth:`ContextEngine.prepare_subagent_spawn`."""

    rollback: Callable[[], Any]


@dataclass
class ContextEngineInfo:
    """Identity metadata for a context engine implementation."""

    id: str
    name: str
    version: Optional[str] = None
    owns_compaction: bool = False
    """When True, the engine manages its own compaction lifecycle."""


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class ContextEngine:
    """
    Pluggable contract for LLM context management.

    Subclasses **must** implement :meth:`ingest` and :meth:`assemble`.
    All other methods provide safe no-op defaults so partial implementations
    remain valid.

    Session identity
    ----------------
    Every method receives a ``session_key`` string (usually
    ``"<channel>:<chat_id>"``) that scopes data to a single conversation.

    Integration with AgentLoop
    --------------------------
    The loop calls :meth:`assemble` after building the raw message list via
    ``GazerContextBuilder.build_messages()``.  The engine may prune, add a
    summary tombstone, or otherwise reshape the list before returning it.
    The loop calls :meth:`ingest` after each turn to persist new messages.
    :meth:`compact` can be called explicitly (e.g. from an admin command)
    to reclaim tokens when a session grows very long.
    """

    @property
    def info(self) -> ContextEngineInfo:
        raise NotImplementedError("Subclasses must implement info")

    # ------------------------------------------------------------------ required

    async def ingest(
        self,
        *,
        session_key: str,
        role: str,
        content: str,
        is_heartbeat: bool = False,
    ) -> IngestResult:
        """Persist a single turn message into the engine's store."""
        raise NotImplementedError("Subclasses must implement ingest")

    async def assemble(
        self,
        *,
        session_key: str,
        messages: List[Dict[str, Any]],
        token_budget: Optional[int] = None,
    ) -> AssembleResult:
        """
        Assemble model context under an optional token budget.

        ``messages`` is the pre-built list supplied by the caller (system
        prompt + conversation history + current user turn).  The engine may
        prune, summarise, or augment the list before returning the final
        ``AssembleResult``.
        """
        raise NotImplementedError("Subclasses must implement assemble")

    # ------------------------------------------------------------------ optional

    async def bootstrap(
        self,
        *,
        session_key: str,
        session_file: Optional[str] = None,
    ) -> BootstrapResult:
        """Initialize engine state for a new or resumed session."""
        return BootstrapResult(bootstrapped=False, reason="not_implemented")

    async def ingest_batch(
        self,
        *,
        session_key: str,
        messages: List[Dict[str, Any]],
        is_heartbeat: bool = False,
    ) -> IngestBatchResult:
        """Ingest a completed turn batch as a single unit.

        Default implementation delegates to :meth:`ingest` for each message.
        """
        count = 0
        for msg in messages:
            result = await self.ingest(
                session_key=session_key,
                role=msg.get("role", "user"),
                content=str(msg.get("content", "")),
                is_heartbeat=is_heartbeat,
            )
            if result.ingested:
                count += 1
        return IngestBatchResult(ingested_count=count)

    async def after_turn(
        self,
        *,
        session_key: str,
        messages: List[Dict[str, Any]],
        pre_prompt_message_count: int,
        auto_compaction_summary: Optional[str] = None,
        is_heartbeat: bool = False,
        token_budget: Optional[int] = None,
    ) -> None:
        """Execute optional post-turn work (persist context, trigger compaction).

        Called by AgentLoop after the LLM response has been sent.
        Engines that set ``owns_compaction=True`` should trigger proactive
        compaction here when the token count approaches the budget.
        """

    async def compact(
        self,
        *,
        session_key: str,
        token_budget: Optional[int] = None,
        force: bool = False,
        current_token_count: Optional[int] = None,
        custom_instructions: Optional[str] = None,
    ) -> CompactResult:
        """Compact context to reclaim token budget.

        Returns a :class:`CompactResult` describing what was done.
        The default is a no-op that reports ``compacted=False``.
        """
        return CompactResult(ok=True, compacted=False, reason="not_implemented")

    async def prepare_subagent_spawn(
        self,
        *,
        parent_session_key: str,
        child_session_key: str,
        ttl_ms: Optional[int] = None,
    ) -> Optional[SubagentSpawnPreparation]:
        """Prepare engine state before a sub-agent run starts.

        Returns a :class:`SubagentSpawnPreparation` with a rollback handle,
        or ``None`` when no preparation is needed.
        """
        return None

    async def on_subagent_ended(
        self,
        *,
        child_session_key: str,
        reason: str,
    ) -> None:
        """Notify the engine that a sub-agent lifecycle ended.

        ``reason`` is one of ``"deleted"``, ``"completed"``, ``"swept"``,
        or ``"released"``.
        """

    async def dispose(self) -> None:
        """Release any resources (DB connections, threads, etc.) held by the engine."""
