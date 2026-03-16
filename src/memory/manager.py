"""Unified memory management on top of the OpenViking backend."""

import os
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from memory.interface import MemoryEmbeddingProbeResult, MemoryProviderStatus, MemorySearchResult

from soul.core import WorkingMemory, MemoryEntry
from memory.relationships import RelationshipGraph
from memory.emotions import EmotionTracker
from memory.recall import MemoryRecaller
from memory.forgetting import MemoryDecay
from memory.openviking_bootstrap import load_openviking_settings
from memory.viking_backend import OpenVikingMemoryBackend
from runtime.config_manager import config

logger = logging.getLogger("GazerMemory")


class MemoryManager:
    """
    Gazer memory manager.

    Coordinates:
    1. OpenViking-backed memory/search adapter (primary persistence)
    2. Relationship graph
    3. Emotion tracking
    4. Natural recall triggers
    """

    def __init__(
        self,
        base_path: Optional[str] = None,
        embedding_provider=None,
    ):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._write_lock = asyncio.Lock()

        backend_cfg = load_openviking_settings(config)
        backend_root = Path(backend_cfg.data_dir).resolve()
        storage_root = Path(base_path).resolve() if base_path else backend_root
        self.base_path = str(storage_root)
        self.daily_path = str(storage_root / "events")
        self.knowledge_path = str(storage_root / "knowledge")
        self.long_term_file = str(storage_root / "long_term.md")

        self.backend = OpenVikingMemoryBackend(
            data_dir=backend_root,
            session_prefix=backend_cfg.session_prefix,
            default_user=backend_cfg.default_user,
            config_file=backend_cfg.config_file,
            commit_every_messages=backend_cfg.commit_every_messages,
            enable_client=bool(backend_cfg.enabled and backend_cfg.mode == "openviking"),
        )
        self.index = self.backend.index
        self.relationships = RelationshipGraph(str(storage_root / "RELATIONSHIPS.md"))
        self.emotions = EmotionTracker(str(storage_root / "emotions"))
        self.recall = MemoryRecaller(
            self.index,
            self.relationships,
            self.emotions,
            milestones_path=str(storage_root / "MILESTONES.md"),
        )
        self.decay = MemoryDecay()
        self._last_context_stats = {
            "recall_count": 0,
            "entity_count": 0,
            "semantic_count": 0,
            "time_reminder_count": 0,
            "memory_context_chars": 0,
        }

        os.makedirs(self.base_path, exist_ok=True)
        self.watcher = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the main event loop reference for cross-thread scheduling."""
        self._loop = loop

    def stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
        self.index.close()

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass

    async def save_entry(self, entry: MemoryEntry) -> None:
        """Persist a single memory entry with multi-dimensional analysis."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        async with self._write_lock:
            try:
                if entry.sender.lower() == "user":
                    emotion, sentiment, topics = await self.emotions.analyze_message_async(
                        entry.content
                    )
                    entry.emotion = emotion
                    entry.sentiment = sentiment
                    entry.topics = topics
                    self.relationships.update_from_message(entry.content, sentiment)

                payload = {
                    "emotion": entry.emotion,
                    "sentiment": entry.sentiment,
                    "importance": entry.importance,
                }
                if isinstance(entry.metadata, dict):
                    payload.update(entry.metadata)

                self.backend.add_memory(
                    content=entry.content,
                    sender=entry.sender,
                    timestamp=entry.timestamp,
                    metadata=payload,
                    from_reindex=False,
                )
            except Exception as e:
                logger.error("Failed to process memory entry: %s", e)

    # ------------------------------------------------------------------
    # Context for LLM
    # ------------------------------------------------------------------
    async def get_companion_context(
        self,
        current_message: str,
        working_memory: WorkingMemory,
        *,
        entity_limit: int = 3,
        semantic_limit: int = 3,
        max_recall_items: int = 5,
        max_context_chars: Optional[int] = None,
        include_relationship_context: bool = True,
        include_time_reminders: bool = True,
        include_emotion_context: bool = True,
        include_recent_observation: bool = True,
    ) -> str:
        user_sentiment = (
            self.emotions._today_data.avg_sentiment
            if self.emotions._today_data
            else 0.0
        )
        recall_result = await self.recall.get_relevant_memories(
            current_message,
            user_sentiment,
            entity_limit=max(1, min(int(entity_limit), 20)),
            semantic_limit=max(1, min(int(semantic_limit), 20)),
        )
        context_str = self.recall.format_for_prompt(
            recall_result,
            max_recall_items=max(1, min(int(max_recall_items), 20)),
            include_relationship_context=bool(include_relationship_context),
            include_time_reminders=bool(include_time_reminders),
            include_emotion_context=bool(include_emotion_context),
        )

        mood_trend = self.emotions.get_recent_mood(days=3) if include_recent_observation else None
        if mood_trend:
            context_str += f"\n\n## Recent Observation\n{mood_trend}"
        if max_context_chars is not None:
            try:
                cap = max(64, int(max_context_chars))
            except (TypeError, ValueError):
                cap = 0
            if cap > 0 and len(context_str) > cap:
                context_str = context_str[: max(0, cap - 24)].rstrip() + "\n...[context trimmed]"
        self._last_context_stats = {
            "recall_count": int(
                len(recall_result.get("entity_memories", []))
                + len(recall_result.get("semantic_memories", []))
                + len(recall_result.get("time_reminders", []))
            ),
            "entity_count": int(len(recall_result.get("entity_memories", []))),
            "semantic_count": int(len(recall_result.get("semantic_memories", []))),
            "time_reminder_count": int(len(recall_result.get("time_reminders", []))),
            "memory_context_chars": int(len(context_str or "")),
        }
        return context_str

    def get_last_context_stats(self) -> dict:
        return dict(self._last_context_stats)

    # ------------------------------------------------------------------
    # MemorySearchManager interface
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        min_score: float = 0.0,
        session_key: Optional[str] = None,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
        candidate_multiplier: int = 4,
        enable_mmr: bool = False,
        mmr_lambda: float = 0.7,
        enable_temporal_decay: bool = False,
        temporal_decay_half_life_days: float = 30.0,
    ) -> List[MemorySearchResult]:
        """Delegating hybrid search with optional MMR and temporal-decay via the SQLiteIndex."""
        return await self.index.search(
            query,
            max_results=max_results,
            min_score=min_score,
            session_key=session_key,
            vector_weight=vector_weight,
            text_weight=text_weight,
            candidate_multiplier=candidate_multiplier,
            enable_mmr=enable_mmr,
            mmr_lambda=mmr_lambda,
            enable_temporal_decay=enable_temporal_decay,
            temporal_decay_half_life_days=temporal_decay_half_life_days,
        )

    def status(self) -> MemoryProviderStatus:
        """Return a unified status snapshot from the SQLiteIndex."""
        try:
            return self.index.status()
        except Exception as exc:
            logger.warning("MemoryManager.status() failed: %s", exc)
            return MemoryProviderStatus(
                backend="sqlite",
                provider="unknown",
                error=str(exc),
            )

    async def probe_embedding_availability(self) -> MemoryEmbeddingProbeResult:
        """Check whether the embedding provider attached to the index is functional."""
        return await self.index.probe_embedding_availability()

    async def probe_vector_availability(self) -> bool:
        """Check whether semantic (vector) search is available."""
        return await self.index.probe_vector_availability()

    async def sync(
        self,
        *,
        reason: Optional[str] = None,
        force: bool = False,
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        """Flush the Faiss index to disk and (optionally) synchronise the backend."""
        await self.index.sync(reason=reason, force=force, progress=progress)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def load_recent(self, limit: int = 50) -> WorkingMemory:
        rows = self.backend.list_recent(limit=limit)
        memories: List[MemoryEntry] = []
        for item in rows:
            raw_ts = str(item.get("timestamp", "")).strip()
            try:
                ts = datetime.fromisoformat(raw_ts) if raw_ts else datetime.now()
            except ValueError:
                ts = datetime.now()
            memories.append(
                MemoryEntry(
                    sender=str(item.get("sender", "")).strip(),
                    content=str(item.get("content", "")).strip(),
                    timestamp=ts,
                    metadata=item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {},
                )
            )
        return WorkingMemory(memories=memories[-max(1, int(limit)) :])
