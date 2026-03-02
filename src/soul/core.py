"""Legacy soul core — kept for backward compatibility.

``MemoryEntry`` now lives in ``soul.memory.memory_entry`` and is
re-exported here so existing imports continue to work.

``MentalProcess``, ``PassthroughMentalProcess``, and
``PassthroughCognitiveStep`` have been removed — they were unused
after the Soul Architecture Reform.
"""

from typing import List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

# Canonical location — re-exported for backward compatibility.
from soul.memory.memory_entry import MemoryEntry  # noqa: F401


class WorkingMemory(BaseModel):
    """Immutable Working Memory.

    .. deprecated::
        Prefer ``soul.memory.working_context.WorkingContext`` for new code.
    """
    model_config = ConfigDict(frozen=True)

    memories: tuple[MemoryEntry, ...] = Field(default_factory=tuple)
    owner: str = "Gazer"

    def append(self, entry: MemoryEntry) -> 'WorkingMemory':
        """Return a new instance with the appended entry (preserves immutability)."""
        return WorkingMemory(
            memories=self.memories + (entry,),
            owner=self.owner
        )

    def to_working_context(self) -> Any:
        """Convert to the new Phase 1 WorkingContext (backward compatibility)."""
        from soul.memory.working_context import WorkingContext
        from soul.affect.affective_state import AffectiveState

        latest_content = self.memories[-1].content if self.memories else ""
        session_ctx = tuple(m.content for m in self.memories)

        return WorkingContext(
            user_context=(),
            agent_context=(),
            session_context=session_ctx,
            affect=AffectiveState(),
            user_input=latest_content,
            turn_count=len(self.memories)
        )

    def get_context_string(self) -> str:
        """Convert to an LLM-readable context string."""
        lines = []
        for m in self.memories:
            time_str = m.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"[{time_str}] {m.sender}: {m.content}")
        return "\n".join(lines)


class CognitiveStep:
    """Legacy cognitive step base class.

    Used by ``LLMCognitiveStep`` in ``soul.cognition``.  New code should
    prefer the ABC in ``soul.cognitive.cognitive_step``.
    """
    def __init__(self, name: str):
        self.name = name

    async def run(self, memory: WorkingMemory, *args, **kwargs) -> Any:
        raise NotImplementedError("Subclasses must implement run()")


class MentalState(BaseModel):
    """A single state in the mental state machine."""
    name: str
    description: str
    meta_data: Dict[str, Any] = Field(default_factory=dict)
