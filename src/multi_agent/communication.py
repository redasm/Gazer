"""Inter-agent communication: AgentMessageBus and Blackboard.

AgentMessageBus provides point-to-point, broadcast, and request/response
messaging between agents.  It is completely independent of the existing
channel MessageBus in ``src/bus/queue.py``.

Blackboard provides shared state backed by OpenViking, with namespace-
isolated reads/writes and semantic search.  Workers write full results
here and pass lightweight references back through the task graph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from multi_agent.models import AgentMessage, MessageType, _short_uuid

logger = logging.getLogger("multi_agent.Communication")


# ------------------------------------------------------------------
# AgentMessageBus
# ------------------------------------------------------------------

_MAILBOX_MAX = 128


class AgentMessageBus:
    """Async message bus for inter-agent communication.

    Each registered agent gets an independent mailbox (bounded queue).
    Supports broadcast, point-to-point, and request/response patterns.
    """

    def __init__(self) -> None:
        self._mailboxes: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._pending_replies: dict[str, asyncio.Future[AgentMessage]] = {}

    async def register_agent(self, agent_id: str) -> None:
        if agent_id not in self._mailboxes:
            self._mailboxes[agent_id] = asyncio.Queue(maxsize=_MAILBOX_MAX)
            logger.debug("Registered agent mailbox: %s", agent_id)

    async def unregister_agent(self, agent_id: str) -> None:
        self._mailboxes.pop(agent_id, None)

    async def send(self, message: AgentMessage) -> None:
        """Non-blocking send. Drops if target mailbox is full."""
        if message.target_id is None:
            # Broadcast
            for aid, mbox in self._mailboxes.items():
                if aid == message.sender_id:
                    continue
                try:
                    mbox.put_nowait(message)
                except asyncio.QueueFull:
                    logger.debug("Mailbox full for %s, dropping broadcast", aid)
        else:
            mbox = self._mailboxes.get(message.target_id)
            if mbox is None:
                logger.warning("No mailbox for target %s", message.target_id)
                return
            try:
                mbox.put_nowait(message)
            except asyncio.QueueFull:
                logger.debug("Mailbox full for %s, dropping message", message.target_id)

        # Resolve pending ask() futures for reply messages
        if message.msg_type == MessageType.REPLY and message.reply_to:
            fut = self._pending_replies.pop(message.reply_to, None)
            if fut is not None and not fut.done():
                fut.set_result(message)

    async def receive(
        self,
        agent_id: str,
        timeout: float = 0.1,
    ) -> AgentMessage | None:
        """Non-blocking receive with TTL-based expiry."""
        mbox = self._mailboxes.get(agent_id)
        if mbox is None:
            return None
        try:
            msg = await asyncio.wait_for(mbox.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if msg.is_expired:
            return None
        return msg

    async def ask(
        self,
        sender_id: str,
        target_id: str,
        content: Any,
        timeout: float = 10.0,
    ) -> AgentMessage | None:
        """Send a request and wait for a matching reply."""
        msg = AgentMessage(
            sender_id=sender_id,
            target_id=target_id,
            msg_type=MessageType.ASK,
            content=content,
            ttl_sec=timeout,
        )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[AgentMessage] = loop.create_future()
        self._pending_replies[msg.msg_id] = fut

        await self.send(msg)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_replies.pop(msg.msg_id, None)
            return None

    def drain_all(self, agent_id: str) -> list[AgentMessage]:
        """Drain and return all non-expired messages from a mailbox."""
        mbox = self._mailboxes.get(agent_id)
        if mbox is None:
            return []
        messages: list[AgentMessage] = []
        while not mbox.empty():
            try:
                msg = mbox.get_nowait()
                if not msg.is_expired:
                    messages.append(msg)
            except asyncio.QueueEmpty:
                break
        return messages


# ------------------------------------------------------------------
# Blackboard (shared state via OpenViking)
# ------------------------------------------------------------------

class Blackboard:
    """Shared state store for the multi-agent session.

    Uses a dict-based in-memory store with optional OpenViking backend
    for semantic search.  Workers write full results here; only lightweight
    references flow through the task graph (avoiding the "telephone game").

    Namespace convention::

        viking://workspace/{session_id}/context/     <- Planner writes, all read
        viking://workspace/{session_id}/results/      <- Worker results
        viking://workspace/{session_id}/knowledge/    <- Accumulated knowledge
        viking://workspace/{session_id}/coordination/ <- Coordination info
    """

    def __init__(
        self,
        session_id: str,
        memory_manager: Any = None,
    ) -> None:
        self._session_id = session_id
        self._memory_manager = memory_manager
        self._store: dict[str, dict[str, Any]] = {
            "context": {},
            "results": {},
            "knowledge": {},
            "coordination": {},
        }
        self._lock = asyncio.Lock()

    @property
    def session_id(self) -> str:
        return self._session_id

    def _ns(self, namespace: str) -> dict[str, Any]:
        if namespace not in self._store:
            self._store[namespace] = {}
        return self._store[namespace]

    async def write(
        self,
        key: str,
        value: Any,
        agent_id: str,
        namespace: str = "results",
    ) -> str:
        """Write a value and return a reference URI."""
        async with self._lock:
            entry = {
                "value": value,
                "agent_id": agent_id,
                "timestamp": time.time(),
            }
            self._ns(namespace)[key] = entry

        # Persist to OpenViking for semantic search if available
        if self._memory_manager is not None:
            try:
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                backend = getattr(self._memory_manager, "backend", None)
                if backend is not None and hasattr(backend, "add_memory"):
                    from datetime import datetime
                    backend.add_memory(
                        content=f"[{namespace}/{key}] {text}",
                        sender=agent_id,
                        timestamp=datetime.now(),
                        metadata={
                            "session_id": self._session_id,
                            "namespace": namespace,
                            "key": key,
                        },
                    )
            except Exception:
                logger.debug("Failed to persist blackboard entry to OpenViking", exc_info=True)

        ref = f"blackboard://{self._session_id}/{namespace}/{key}"
        logger.debug("Blackboard write: %s by %s", ref, agent_id)
        return ref

    async def read(
        self,
        key: str,
        namespace: str = "results",
    ) -> Any:
        entry = self._ns(namespace).get(key)
        if entry is None:
            return None
        return entry["value"]

    async def write_context(self, key: str, value: Any) -> str:
        return await self.write(key, value, agent_id="planner", namespace="context")

    async def read_context(self, key: str) -> Any:
        return await self.read(key, namespace="context")

    async def search(
        self,
        query: str,
        namespace: str = "results",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic search via OpenViking hybrid_search, scoped to session."""
        if self._memory_manager is None:
            return self._fallback_search(query, namespace, limit)

        backend = getattr(self._memory_manager, "backend", None)
        if backend is None or not hasattr(backend, "hybrid_search"):
            return self._fallback_search(query, namespace, limit)

        try:
            results = await backend.hybrid_search(
                query=f"[{namespace}] {query}",
                limit=limit,
            )
            filtered = []
            for row in results:
                meta = row.get("metadata", {}) or {}
                if meta.get("session_id") == self._session_id:
                    if namespace == "" or meta.get("namespace") == namespace:
                        filtered.append(row)
            return filtered[:limit]
        except Exception:
            logger.debug("OpenViking search failed, falling back to local", exc_info=True)
            return self._fallback_search(query, namespace, limit)

    def _fallback_search(
        self,
        query: str,
        namespace: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Simple substring match fallback when OpenViking is unavailable."""
        ns = self._ns(namespace)
        query_lower = query.lower()
        matches: list[dict[str, Any]] = []
        for key, entry in ns.items():
            text = str(entry.get("value", ""))
            if query_lower in text.lower() or query_lower in key.lower():
                matches.append({"key": key, **entry})
            if len(matches) >= limit:
                break
        return matches

    def get_all(self, namespace: str = "results") -> dict[str, Any]:
        """Return all entries in a namespace (for aggregation)."""
        return {k: v["value"] for k, v in self._ns(namespace).items()}
