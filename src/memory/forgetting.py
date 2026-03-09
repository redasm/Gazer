"""
Gazer AI Companion - Memory Forgetting Curve

Simulates the Ebbinghaus forgetting curve for human-like memory:
- Older memories are more likely to be forgotten
- Emotionally strong memories persist longer
- High-importance memories resist forgetting
- Frequently mentioned memories are more stable
"""
import math
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from soul.core import MemoryEntry  # MemoryEntry is a cognitive data model, stays in soul/

logger = logging.getLogger("GazerForgetting")


class MemoryDecay:
    """
    Memory Decay Model
    
    Based on the Ebbinghaus forgetting curve, calculates memory retention rate.
    Retention is affected by:
    1. Time elapsed (core factor)
    2. Emotional intensity (strong emotions are harder to forget)
    3. Importance score (important memories persist longer)
    4. Repetition count (multiple mentions deepen memory)
    """
    
    # Forgetting curve parameters
    HALF_LIFE_DAYS = 30              # Half-life: retention drops to 50% after 30 days
    MIN_RETENTION = 0.1              # Minimum retention rate
    IMPORTANCE_BOOST = 0.4           # Importance boost coefficient
    EMOTION_BOOST = 0.3              # Emotional intensity boost coefficient
    REPETITION_BOOST = 0.1           # Boost per repetition
    
    # Retention thresholds
    KEEP_THRESHOLD = 0.3             # Memories below this can be cleaned up
    ARCHIVE_THRESHOLD = 0.5          # Memories below this but above cleanup threshold are archived
    
    def __init__(self, 
                 half_life_days: int = 30,
                 keep_threshold: float = 0.3):
        self.half_life_days = half_life_days
        self.keep_threshold = keep_threshold
    
    def calculate_retention(self, 
                           memory: MemoryEntry, 
                           now: datetime = None,
                           repetition_count: int = 1) -> float:
        """
        Calculate memory retention rate.
        
        Args:
            memory: Memory entry
            now: Current time (defaults to now)
            repetition_count: Number of times this memory has been mentioned
        
        Returns:
            Retention rate between 0.0 and 1.0
        """
        if now is None:
            now = datetime.now()
        
        # 1. Time-based Ebbinghaus forgetting curve
        days_passed = (now - memory.timestamp).total_seconds() / 86400
        base_retention = math.exp(-math.log(2) * days_passed / self.half_life_days)
        
        # 2. Importance factor (multiplicative)
        importance_factor = 1.0 + memory.importance * self.IMPORTANCE_BOOST
        
        # 3. Emotional intensity factor (absolute value - both positive and negative emotions are memorable)
        emotion_factor = 1.0 + abs(memory.sentiment) * self.EMOTION_BOOST
        
        # 4. Repetition factor (logarithmic growth to avoid excessive boost)
        repetition_factor = 1.0 + math.log1p(repetition_count - 1) * self.REPETITION_BOOST
        
        # Combined calculation (multiplicative)
        retention = base_retention * importance_factor * emotion_factor * repetition_factor
        
        # Clamp to [MIN_RETENTION, 1.0] range
        return max(self.MIN_RETENTION, min(1.0, retention))
    
    def should_keep(self, memory: MemoryEntry, now: datetime = None) -> bool:
        """Determine if a memory should be kept."""
        retention = self.calculate_retention(memory, now)
        
        # High-importance memories are always kept
        if memory.importance >= 0.8:
            return True
        
        return retention > self.keep_threshold
    
    def should_archive(self, memory: MemoryEntry, now: datetime = None) -> bool:
        """Determine if a memory should be archived (rather than kept in active memory)."""
        retention = self.calculate_retention(memory, now)
        return self.keep_threshold < retention <= self.ARCHIVE_THRESHOLD
    
    def filter_memories(self, 
                       memories: List[MemoryEntry],
                       now: datetime = None) -> Tuple[List[MemoryEntry], List[MemoryEntry], List[MemoryEntry]]:
        """
        Filter and categorize a list of memories.
        
        Returns:
            (active, archived, forgotten)
            - active: Still-active memories
            - archived: Memories to be archived
            - forgotten: Memories that can be forgotten
        """
        active = []
        archived = []
        forgotten = []
        
        for memory in memories:
            retention = self.calculate_retention(memory, now)
            
            if retention > self.ARCHIVE_THRESHOLD or memory.importance >= 0.8:
                active.append(memory)
            elif retention > self.keep_threshold:
                archived.append(memory)
            else:
                forgotten.append(memory)
        
        return active, archived, forgotten
    
    def get_decay_info(self, memory: MemoryEntry, now: datetime = None) -> dict:
        """Get memory decay status info (for debugging or visualization)."""
        if now is None:
            now = datetime.now()
        
        days_passed = (now - memory.timestamp).total_seconds() / 86400
        retention = self.calculate_retention(memory, now)
        
        return {
            "content_preview": memory.content[:50],
            "age_days": round(days_passed, 1),
            "retention": round(retention, 3),
            "importance": memory.importance,
            "emotion_strength": abs(memory.sentiment),
            "status": "active" if retention > self.ARCHIVE_THRESHOLD 
                      else "archived" if retention > self.keep_threshold 
                      else "fading"
        }


class MemoryCurator:
    """
    Memory Curator
    
    Responsible for periodically cleaning and organizing memories,
    simulating the human memory consolidation process.
    """
    
    def __init__(self, decay_model: MemoryDecay = None):
        self.decay = decay_model or MemoryDecay()
    
    def curate_daily_memories(
        self,
        index,           # Search index adapter
        memory_manager,  # MemoryManager instance
        days_to_check: int = 30,
    ) -> Dict[str, int]:
        """
        Curate memories from the past period.

        1. Retrieve memories from the past N days from the index
        2. Calculate forgetting status for each memory
        3. Mark/archive memories that are fading but still valuable
        4. Clean up low-value forgotten memories

        Returns:
            Summary dict with counts: {active, archived, forgotten}
        """
        from datetime import date, timedelta
        import os

        logger.info("Curating memories from the past %s days...", days_to_check)

        stats = {"active": 0, "archived": 0, "forgotten": 0}
        now = datetime.now()
        archive_dir = os.path.join(memory_manager.base_path, "archive")
        os.makedirs(archive_dir, exist_ok=True)

        for i in range(days_to_check):
            d = date.today() - timedelta(days=i)
            date_str = d.isoformat()
            daily_file = os.path.join(memory_manager.daily_path, f"{date_str}.md")

            if not os.path.exists(daily_file):
                continue

            try:
                with open(daily_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue

            blocks = re.findall(
                r"### \[(.*?)\] (.*?)\n(.*?)(?=\n###|\Z)", content, re.DOTALL
            )

            active_blocks: List[str] = []
            archive_blocks: List[str] = []

            for time_str, sender, text in blocks:
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    dt = datetime.combine(d, datetime.min.time())

                entry = MemoryEntry(
                    sender=sender.strip(),
                    content=text.strip(),
                    timestamp=dt,
                )
                retention = self.decay.calculate_retention(entry, now)

                original_block = f"### [{time_str}] {sender}\n{text}"

                if retention > self.decay.ARCHIVE_THRESHOLD or entry.importance >= 0.8:
                    active_blocks.append(original_block)
                    stats["active"] += 1
                elif retention > self.decay.keep_threshold:
                    archive_blocks.append(original_block)
                    stats["archived"] += 1
                else:
                    stats["forgotten"] += 1

            # Write archived blocks to archive directory
            if archive_blocks:
                archive_file = os.path.join(archive_dir, f"{date_str}.md")
                try:
                    with open(archive_file, "a", encoding="utf-8") as f:
                        for block in archive_blocks:
                            f.write(block + "\n\n")
                except OSError as e:
                    logger.error("Failed to write archive for %s: %s", date_str, e)

        logger.info(
            "Memory curation completed: %s active, %s archived, %s forgotten.",
            stats["active"], stats["archived"], stats["forgotten"],
        )
        return stats
    
    def simulate_forgetting(self, memories: List[MemoryEntry]) -> List[MemoryEntry]:
        """
        Simulate the forgetting process, returning retained memories.
        
        This method is used when building context to naturally "forget"
        unimportant memories rather than stuffing all memories into context.
        """
        active, archived, _ = self.decay.filter_memories(memories)
        
        # Return active memories + important archived memories
        important_archived = [m for m in archived if m.importance > 0.6]
        
        return active + important_archived


# Default instances
memory_decay = MemoryDecay()
memory_curator = MemoryCurator(memory_decay)
