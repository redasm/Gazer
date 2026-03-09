"""Canonical MemoryEntry model.

This is the single source of truth for the ``MemoryEntry`` Pydantic model.
``soul.core`` re-exports it as the canonical public import path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    """Single memory entry (AI companion enhanced).

    Includes emotion tags, mentioned people, topic tags for companion-style memory.
    """

    content: str
    sender: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Emotion tags
    emotion: Optional[str] = None  # happy/sad/anxious/calm/excited/neutral
    sentiment: float = 0.0  # -1.0 (negative) ~ 1.0 (positive)

    # Entity extraction
    people: List[str] = Field(default_factory=list)  # Mentioned people
    topics: List[str] = Field(default_factory=list)  # Topic tags

    # Importance score (affects forgetting curve)
    importance: float = 0.5  # 0.0 ~ 1.0, higher = more important

    metadata: Dict[str, Any] = Field(default_factory=dict)
