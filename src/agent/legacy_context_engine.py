"""LegacyContextEngine -- default context engine wrapping SessionStore + ContextPruner.

This engine is registered under the ``"legacy"`` id and is used by default
when no other engine is configured.  It preserves the existing behaviour
of the agent loop while satisfying the :class:`~agent.context_engine.ContextEngine`
protocol, making future engine swap-outs possible without touching
``AgentLoop`` business logic.

Responsibilities
----------------
* **ingest** -- delegates to :class:`~agent.session_store.SessionStore`.
* **assemble** -- applies token-budget pruning to the pre-built message list
  supplied by ``GazerContextBuilder.build_messages()``.
* **compact** -- explicitly rewrites the session JSONL to drop old messages.
* **bootstrap** -- reports whether a session already exists.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.context_engine import (
    AssembleResult,
    BootstrapResult,
    CompactResult,
    ContextEngine,
    ContextEngineInfo,
    IngestResult,
)
from agent.session_store import SessionStore
from soul.compaction import ContextPruner

logger = logging.getLogger("LegacyContextEngine")

_CHARS_PER_TOKEN: float = 4.0  # conservative rough estimate
_DEFAULT_MAX_TOKENS: int = 100_000


class LegacyContextEngine(ContextEngine):
    """
    Default context engine backed by JSONL :class:`~agent.session_store.SessionStore`
    and :class:`~soul.compaction.ContextPruner`.

    Parameters
    ----------
    session_store:
        Shared SessionStore instance.  When ``None`` a new one is created.
    max_tokens:
        Token budget used for pruning in :meth:`assemble` and :meth:`compact`.
    chars_per_token:
        Characters-per-token ratio for the rough token estimator.
    keep_last_n:
        Minimum number of non-system messages to keep during pruning.
    max_tool_output_chars:
        Soft limit applied to assistant/tool messages before history pruning.
    """

    _info = ContextEngineInfo(
        id="legacy",
        name="Legacy Context Engine",
        version="1.1",
        owns_compaction=True,
    )

    def __init__(
        self,
        session_store: Optional[SessionStore] = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        chars_per_token: float = _CHARS_PER_TOKEN,
        keep_last_n: int = 5,
        max_tool_output_chars: int = 2000,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._pruner = ContextPruner(
            max_tokens=max_tokens,
            chars_per_token=chars_per_token,
        )
        self._pruner.keep_last_n_messages = keep_last_n
        self._pruner.max_tool_output_chars = max_tool_output_chars
        self._chars_per_token = chars_per_token

    @property
    def info(self) -> ContextEngineInfo:
        return self._info

    # ------------------------------------------------------------------ required

    async def ingest(
        self,
        *,
        session_key: str,
        role: str,
        content: str,
        is_heartbeat: bool = False,
    ) -> IngestResult:
        """Append a single message to the JSONL session store."""
        self._session_store.append(session_key, role, content)
        return IngestResult(ingested=True)

    async def assemble(
        self,
        *,
        session_key: str,
        messages: List[Dict[str, Any]],
        token_budget: Optional[int] = None,
    ) -> AssembleResult:
        """
        Apply token-budget pruning to *messages* and return an
        :class:`~agent.context_engine.AssembleResult`.

        The caller is responsible for building the initial message list
        (system prompt + conversation history + current user turn) via
        ``GazerContextBuilder.build_messages()``.  This method only enforces
        the token budget.

        When *token_budget* is ``None`` the engine's ``max_tokens`` value is
        used as the ceiling.
        """
        budget = token_budget if token_budget is not None else self._pruner.max_tokens
        estimated = self._estimate_tokens(messages)

        if estimated <= budget:
            return AssembleResult(messages=messages, estimated_tokens=estimated)

        logger.info(
            "Context assembly: estimated %d tokens > budget %d; pruning session %s.",
            estimated,
            budget,
            session_key,
        )
        pruned = self._prune_messages(messages, budget)
        pruned_estimate = self._estimate_tokens(pruned)
        return AssembleResult(messages=pruned, estimated_tokens=pruned_estimate)

    # ------------------------------------------------------------------ optional

    async def bootstrap(
        self,
        *,
        session_key: str,
        session_file: Optional[str] = None,
    ) -> BootstrapResult:
        """Report whether the session already has persisted history."""
        history = self._session_store.load(session_key, limit=1000)
        if history:
            return BootstrapResult(
                bootstrapped=True,
                imported_messages=len(history),
                reason="existing_session",
            )
        return BootstrapResult(bootstrapped=True, imported_messages=0, reason="new_session")

    async def compact(
        self,
        *,
        session_key: str,
        token_budget: Optional[int] = None,
        force: bool = False,
        current_token_count: Optional[int] = None,
        custom_instructions: Optional[str] = None,
    ) -> CompactResult:
        """
        Prune the persisted session history to fit within the token budget.

        Loads the full history, estimates tokens, prunes when necessary (or
        always when *force* is ``True``), then atomically rewrites the session
        file.
        """
        history = self._session_store.load(session_key, limit=10_000)
        if not history:
            return CompactResult(
                ok=True, compacted=False, reason="empty_session", tokens_before=0
            )

        budget = token_budget if token_budget is not None else self._pruner.max_tokens
        tokens_before = current_token_count if current_token_count is not None \
            else self._estimate_tokens(history)

        if tokens_before <= budget and not force:
            return CompactResult(
                ok=True,
                compacted=False,
                reason="within_budget",
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        pruned = self._prune_messages(history, budget)
        tokens_after = self._estimate_tokens(pruned)
        dropped = len(history) - len(pruned)

        # Atomically rewrite the session file
        self._session_store.delete_session(session_key)
        for msg in pruned:
            self._session_store.append(
                session_key,
                msg.get("role", "user"),
                str(msg.get("content", "")),
                tool_calls=msg.get("tool_calls"),
            )

        logger.info(
            "Compacted session %s: tokens %d→%d, messages %d→%d (dropped %d).",
            session_key,
            tokens_before,
            tokens_after,
            len(history),
            len(pruned),
            dropped,
        )
        return CompactResult(
            ok=True,
            compacted=True,
            reason="over_budget" if not force else "forced",
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            summary=f"Dropped {dropped} messages to reclaim ~{tokens_before - tokens_after} tokens.",
        )

    async def dispose(self) -> None:
        pass  # SessionStore holds no background resources

    # ------------------------------------------------------------------ helpers

    def _estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Rough character-based token estimate for a message list."""
        total_chars = sum(len(str(msg.get("content", "") or "")) for msg in messages)
        return int(total_chars / self._chars_per_token)

    def _prune_messages(
        self, messages: List[Dict[str, Any]], budget: int
    ) -> List[Dict[str, Any]]:
        """
        Prune *messages* to fit within *budget* tokens.

        Strategy (mirrors :class:`~soul.compaction.ContextPruner`):

        1. Soft-trim oversized tool/system messages (head + tail).
        2. Drop oldest non-system, non-anchor messages until within budget.
        3. Always preserve the leading system message and the last
           ``keep_last_n`` messages.
        """
        if not messages:
            return messages

        keep_last = self._pruner.keep_last_n_messages
        max_tool_chars = self._pruner.max_tool_output_chars
        head_chars = self._pruner.head_chars
        tail_chars = self._pruner.tail_chars

        # ------------------------------------------------------------------
        # Step 1: soft-trim oversized tool / system messages
        # ------------------------------------------------------------------
        trimmed: List[Dict[str, Any]] = []
        for msg in messages:
            content = str(msg.get("content", "") or "")
            if len(content) > max_tool_chars and msg.get("role") in ("tool", "system"):
                head = content[:head_chars]
                tail = content[-tail_chars:]
                removed = len(content) - head_chars - tail_chars
                note = f"\n[legacy engine: {removed} chars trimmed]"
                trimmed.append({**msg, "content": f"{head}\n...\n{tail}{note}"})
            else:
                trimmed.append(msg)

        if self._estimate_tokens(trimmed) <= budget:
            return trimmed

        # ------------------------------------------------------------------
        # Step 2: drop oldest non-system messages
        # ------------------------------------------------------------------
        system_msgs = [m for m in trimmed if m.get("role") == "system"][:1]
        non_system = [m for m in trimmed if m not in system_msgs]

        # Anchor: last keep_last non-system messages are never dropped
        anchor = non_system[-keep_last:] if len(non_system) > keep_last else non_system
        candidates = non_system[: max(0, len(non_system) - keep_last)]

        current_tokens = self._estimate_tokens(system_msgs + non_system)
        tokens_to_drop = current_tokens - budget

        dropped_tokens = 0
        drop_indices: set[int] = set()
        for i, msg in enumerate(candidates):
            msg_tokens = int(len(str(msg.get("content", "") or "")) / self._chars_per_token)
            drop_indices.add(i)
            dropped_tokens += msg_tokens
            if dropped_tokens >= tokens_to_drop:
                break

        remaining = [m for i, m in enumerate(candidates) if i not in drop_indices]
        if drop_indices:
            # Insert a pruning tombstone where the oldest dropped message was
            remaining.append(
                {
                    "role": "system",
                    "content": (
                        f"[legacy engine: {len(drop_indices)} older messages pruned "
                        f"to fit token budget]"
                    ),
                }
            )

        return system_msgs + remaining + anchor
