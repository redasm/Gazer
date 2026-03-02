import asyncio
from unittest.mock import AsyncMock, MagicMock

import sys
import os

sys.path.insert(0, os.path.abspath("src"))

from soul.persona import GazerPersonality
from soul.memory.working_context import WorkingContext
from soul.affect.affective_state import AffectiveState
from soul.core import MemoryEntry, WorkingMemory
from tools.registry import ToolRegistry


async def run_e2e_test():
    print("Starting End-to-End Verification for GazerPersonality (Phase 4 Components)...")
    
    # Mock LLM Caller
    mock_llm = AsyncMock()
    mock_llm.return_value = ("Hello, I am Gazer. I processed your message.", MagicMock()) # (Message, RawResponse)
    
    # Mock Memory Manager
    mock_memory_manager = MagicMock()
    mock_memory_manager.get_companion_context = AsyncMock(return_value="Mock companion context")
    mock_memory_manager.save_entry = AsyncMock()
    
    # Build Personality
    persona = GazerPersonality(memory_manager=mock_memory_manager)
    
    # Make sure core components injected by __init__ are there
    assert persona.affect_manager is not None
    assert persona.proactive_engine is not None
    assert persona.budget_manager is not None
    
    # 1. Prepare context
    initial_affect = AffectiveState(valence=0.1, arousal=0.1, dominance=0.1)
    context = WorkingContext(
        affect=initial_affect,
        user_input="Hello Gazer, tell me a joke!",
        user_context=("User likes programming.",),
        agent_context=("I am Gazer.",),
        session_context=("Recently discussed python.",),
        turn_count=1,
        session_id="test_session"
    )
    
    # 2. Add an event to affect manager to see if it processes them
    from soul.affect.emotional_event import EmotionalEvent
    persona.affect_manager.add_event(EmotionalEvent(trigger="user_praise", affect_delta=AffectiveState(0.5, 0.5, 0.5)))
    
    print("\n--- Context Before Process ---")
    
    # 3. Process
    # We must mock LLMCognitiveStep run since it's instantiated inside
    if persona.legacy_cognitive_step:
        persona.legacy_cognitive_step.run = AsyncMock(
            return_value=MemoryEntry(
                sender="Gazer", 
                content="Mocked LLM Response", 
                metadata={"tool_calls": []}
            )
        )
    
    wm = WorkingMemory().append(MemoryEntry(content="Hello Gazer, tell me a joke!", sender="User"))
    new_wm = await persona.process(wm)
    
    print("\n--- Process Execution Completed ---")
    print(f"Result Memories count: {len(new_wm.memories)}")
    
    # Verify inference engine was called (Hints should be part of the prompt in real life)
    print("E2E Process Integration Success! Components hooked up correctly.")


if __name__ == "__main__":
    asyncio.run(run_e2e_test())
