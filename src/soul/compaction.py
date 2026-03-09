import logging
import json
from typing import List, Optional, Dict, Any
from soul.core import WorkingMemory, MemoryEntry

logger = logging.getLogger("ContextCompaction")

class ContextPruner:
    """Manages and compresses WorkingMemory context to stay within LLM token limits.

    Inspired by Pi Agent's Context Pruning and Compaction Safeguard.
    """

    def __init__(self, max_tokens: int = 12000, chars_per_token: float = 4.0):
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        # Retention policy
        self.keep_system_prompt = True
        self.keep_last_n_messages = 5
        self.max_tool_output_chars = 2000  # Max chars per tool output (soft trim)
        self.head_chars = 500
        self.tail_chars = 500

    def estimate_tokens(self, text: str) -> int:
        """Rough token count estimate."""
        if not text:
            return 0
        return int(len(text) / self.chars_per_token)

    def estimate_memory_tokens(self, memory: WorkingMemory) -> int:
        """Estimate total token count for a WorkingMemory instance."""
        total = 0
        for m in memory.memories:
            total += self.estimate_tokens(m.content)
            # Add rough overhead estimate for metadata
            if m.metadata:
                total += self.estimate_tokens(json.dumps(m.metadata))
        return total

    def prune(self, memory: WorkingMemory) -> Optional[WorkingMemory]:
        """
        Return a pruned copy of *memory* if it exceeds the token budget,
        or ``None`` if no pruning was needed.
        """
        current_tokens = self.estimate_memory_tokens(memory)
        if current_tokens <= self.max_tokens:
            return None

        logger.info("Context pruning triggered. Current est. tokens: %s, Max: %s", current_tokens, self.max_tokens)

        # 1. Soft-trim oversized tool outputs
        new_memories, _trimmed = self._soft_trim_tool_outputs(memory.memories)
        
        # Re-estimate after trimming
        temp_memory = WorkingMemory(memories=new_memories, owner=memory.owner)
        current_tokens = self.estimate_memory_tokens(temp_memory)
        if current_tokens <= self.max_tokens:
            return temp_memory

        # 2. History compaction -- drop oldest messages
        if len(new_memories) > self.keep_last_n_messages * 2:
            new_memories = self._compact_history(new_memories, current_tokens)
        
        return WorkingMemory(memories=new_memories, owner=memory.owner)

    def _soft_trim_tool_outputs(self, memories: List[MemoryEntry]) -> tuple:
        """Return (new_memories, changed) with oversized tool outputs trimmed.

        Creates new MemoryEntry instances instead of mutating in place.
        """
        changed = False
        new_memories: List[MemoryEntry] = []
        for entry in memories:
            if len(entry.content) > self.max_tool_output_chars:
                if entry.sender == "System" or "Tool Execution" in entry.content:
                    original_len = len(entry.content)
                    head = entry.content[:self.head_chars]
                    tail = entry.content[-self.tail_chars:]
                    
                    removed_count = original_len - self.head_chars - self.tail_chars
                    
                    note = f"\n\n[Gazer Pruner: Output trimmed. Kept first {self.head_chars} and last {self.tail_chars} chars. {removed_count} chars omitted.]"
                    trimmed_content = f"{head}\n...\n{tail}{note}"
                    new_memories.append(entry.model_copy(update={"content": trimmed_content}))
                    changed = True
                    logger.info("Trimmed large tool output from %s to %s chars.", original_len, len(trimmed_content))
                    continue
            new_memories.append(entry)
        return new_memories, changed

    def _compact_history(self, memories: List[MemoryEntry], current_tokens: int) -> List[MemoryEntry]:
        """Return a compacted copy of *memories* by dropping old entries."""
        processed_memory = list(memories)
        
        start_index = 1
        end_index = len(processed_memory) - self.keep_last_n_messages
        
        if end_index <= start_index:
            return processed_memory  # nothing to drop
            
        target_reduction = current_tokens - self.max_tokens
        
        reduced = 0
        indices_to_remove = []
        
        for i in range(start_index, end_index):
            msg_tokens = self.estimate_tokens(processed_memory[i].content)
            indices_to_remove.append(i)
            reduced += msg_tokens
            if reduced >= target_reduction:
                break
                
        if not indices_to_remove:
            return processed_memory
            
        new_memories = [m for i, m in enumerate(processed_memory) if i not in indices_to_remove]
        
        insert_pos = indices_to_remove[0]
        summary_entry = MemoryEntry(
            sender="System",
            content=f"[Gazer Pruner: {len(indices_to_remove)} older messages were consolidated/forgotten to save memory context.]",
            importance=0.1
        )
        new_memories.insert(insert_pos, summary_entry)
        
        logger.info("Compacted history: Removed %s messages.", len(indices_to_remove))
        return new_memories
