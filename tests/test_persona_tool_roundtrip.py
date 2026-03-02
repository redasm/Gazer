from types import SimpleNamespace

import pytest

from soul.core import MemoryEntry, WorkingMemory
from soul.memory.working_context import WorkingContext
from soul.persona import GazerPersonality


class _StubBackend:
    """Minimal backend stub for OpenVikingMemoryPort."""
    async def semantic_search(self, query: str, limit: int = 5):
        return []

    def add_memory(self, **kwargs):
        pass

    def list_recent(self, limit: int = 50):
        return []

    def delete_by_date(self, key: str):
        pass


class _StubEmotionAnalyzer:
    async def analyze_with_llm(self, text: str):
        return ("neutral", 0.0, [])

    def analyze(self, text: str):
        return ("neutral", 0.0)

    async def analyze_message_async(self, text: str):
        return ("neutral", 0.0, [])


class _StubEmotionTracker:
    def __init__(self):
        self.analyzer = _StubEmotionAnalyzer()
        self._today_data = None

    def get_recent_mood(self, days: int = 3):
        return ""


class _StubRelationships:
    def __init__(self):
        self.people = {}

    def update_from_message(self, text: str, sentiment: float):
        pass


class _StubMemoryManager:
    def __init__(self):
        self.saved_entries: list[MemoryEntry] = []
        self.backend = _StubBackend()
        self.relationships = _StubRelationships()
        self.emotions = _StubEmotionTracker()
        self.knowledge_path = "data/test-knowledge"
        self.daily_path = "data/test-events"

    async def get_companion_context(self, _current_message: str, _working_memory=None, **kwargs) -> str:
        return "context"

    async def save_entry(self, entry: MemoryEntry) -> None:
        self.saved_entries.append(entry)

    def load_recent(self, limit: int = 50) -> WorkingMemory:
        return WorkingMemory()


class _StubToolRegistry:
    def __init__(self):
        self.calls: list[tuple[str, dict, str, str]] = []
        self._definitions = [
            {
                "type": "function",
                "function": {
                    "name": "weather_lookup",
                    "description": "Lookup weather by city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]

    def get_definitions(self):
        return self._definitions

    async def execute(self, name: str, args: dict, *, sender_id: str = "", channel: str = ""):
        self.calls.append((name, args, sender_id, channel))
        return "sunny 26C"


class _StubCognitiveStep:
    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, memory: WorkingMemory, full_prompt: str, tools=None, **kwargs) -> MemoryEntry:
        self.calls.append(
            {
                "memory_size": len(memory.memories),
                "full_prompt": full_prompt,
                "tools": tools,
            }
        )
        if len(self.calls) == 1:
            return MemoryEntry(
                sender=memory.owner,
                content="",
                metadata={"tool_calls": [{"name": "weather_lookup", "args": {"city": "Shanghai"}}]},
            )
        return MemoryEntry(sender=memory.owner, content="今天上海晴，26C。")


@pytest.mark.asyncio
async def test_persona_process_executes_tool_and_runs_followup_llm():
    memory_manager = _StubMemoryManager()
    tool_registry = _StubToolRegistry()
    personality = GazerPersonality(
        memory_manager=memory_manager,
        tool_registry=tool_registry,
    )
    step = _StubCognitiveStep()
    personality.legacy_cognitive_step = step

    context = WorkingContext(
        user_input="上海今天天气怎么样？",
        metadata=(
            ("sender_id", "user_123"),
            ("channel", "web"),
        ),
    )
    result = await personality.process(context)

    assert len(step.calls) == 2
    assert step.calls[0]["tools"] == tool_registry.get_definitions()
    assert tool_registry.calls == [("weather_lookup", {"city": "Shanghai"}, "user_123", "web")]
    assert result.get_metadata("reply") == "今天上海晴，26C。"
    assert any(
        entry.sender == "System" and "Tool Execution [weather_lookup]" in entry.content
        for entry in memory_manager.saved_entries
    )
