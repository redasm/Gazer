"""
Gazer AI Companion - Natural Recall Triggers

Gazer naturally recalls related memories like humans, without requiring explicit user queries.

Core mechanisms:
1. Entity trigger - Auto-associate when names, places, events are mentioned
2. Semantic trigger - Associate when topics are similar
3. Time trigger - Anniversaries, periodic events
4. Emotion trigger - Select appropriate memories based on current mood
"""
import os
import json
import re
import logging
from datetime import datetime, timedelta, date
from typing import Any, List, Dict, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("GazerRecall")

# Named constants (formerly magic numbers)
ENTITY_RECALL_LIMIT = 3
HYBRID_SCORE_THRESHOLD = 0.35
MAX_RECALL_ITEMS = 5


class Milestone(BaseModel):
    """Important date/milestone."""
    name: str                          # "Mom's birthday", "Wedding anniversary"
    date: str                          # "MM-DD" or "YYYY-MM-DD"
    recurring: bool = True             # Whether it repeats yearly
    person: Optional[str] = None       # Associated person
    notes: str = ""
    remind_days_before: int = 3        # Days before to remind


class MemoryRecaller:
    """
    Natural Recall Trigger
    
    Automatically recalls relevant past memories based on current conversation context,
    allowing the AI to naturally say things like "you mentioned before..."
    """
    
    def __init__(self, 
                 memory_index,           # Search index adapter (OpenVikingSearchIndex)
                 relationship_graph,     # RelationshipGraph instance
                 emotion_tracker,        # EmotionTracker instance
                 milestones_path: Optional[str] = None):
        self.index = memory_index
        self.relationships = relationship_graph
        self.emotions = emotion_tracker
        if milestones_path is None:
            from runtime.config_manager import config as _cfg
            base_dir = str(_cfg.get("memory.context_backend.data_dir", "data/openviking") or "data/openviking")
            milestones_path = os.path.join(base_dir, "MILESTONES.md")
        self.milestones_path = milestones_path
        self.milestones: List[Milestone] = []
        
        self._load_milestones()
    
    def _load_milestones(self):
        """Load milestones/important dates."""
        json_path = self.milestones_path.replace(".md", ".json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.milestones = [Milestone(**m) for m in data]
            except Exception as e:
                logger.error("Failed to load milestones: %s", e)
    
    def _save_milestones(self):
        """Save milestones."""
        try:
            json_path = self.milestones_path.replace(".md", ".json")
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump([m.model_dump() for m in self.milestones], f, 
                         ensure_ascii=False, indent=2)
            
            # 同时生成 Markdown
            self._save_milestones_markdown()
        except Exception as e:
            logger.error("Failed to save milestones: %s", e)
    
    def _save_milestones_markdown(self):
        """Generate human-readable Markdown."""
        lines = ["# Important Dates\n\n"]
        
        # Group by month
        by_month: Dict[str, List[Milestone]] = {}
        for m in self.milestones:
            month = m.date.split("-")[0] if "-" in m.date else "00"
            if len(month) == 4:  # YYYY-MM-DD
                month = m.date.split("-")[1]
            if month not in by_month:
                by_month[month] = []
            by_month[month].append(m)
        
        for month in sorted(by_month.keys()):
            lines.append(f"## Month {month}\n")
            for m in by_month[month]:
                person_str = f" ({m.person})" if m.person else ""
                lines.append(f"- **{m.date}** - {m.name}{person_str}\n")
            lines.append("\n")
        
        with open(self.milestones_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    
    def add_milestone(self, name: str, date_str: str, 
                     person: Optional[str] = None, recurring: bool = True):
        """Add an important date."""
        milestone = Milestone(
            name=name,
            date=date_str,
            person=person,
            recurring=recurring
        )
        self.milestones.append(milestone)
        self._save_milestones()
        return milestone
    
    def _extract_entities(self, text: str) -> Dict[str, List[str]]:
        """
        Extract entities from text.
        
        Returns:
            {"people": [...], "topics": [...], "dates": [...]}
        """
        entities = {
            "people": [],
            "topics": [],
            "dates": []
        }
        
        # Extract mentioned people (match from relationship graph)
        for name in self.relationships.people.keys():
            if name in text:
                entities["people"].append(name)
        
        # Extract topics (Chinese labels for prompt/test consistency)
        topic_patterns = {
            "工作": ["工作", "上班", "项目", "work", "job", "project"],
            "健康": ["身体", "健康", "医院", "health", "doctor", "sick"],
            "感情": ["感情", "恋爱", "约会", "love", "date", "relationship"],
            "家庭": ["家人", "回家", "父母", "family", "home", "parents"],
        }
        for topic, keywords in topic_patterns.items():
            if any(kw in text for kw in keywords):
                entities["topics"].append(topic)
        
        # Extract date-related words (Chinese patterns)
        date_patterns = [
            r"(生日|纪念日|周年|过年|春节|中秋|情人节|birthday|anniversary)",
            r"(\d{1,2}月\d{1,2}[日号])",
            r"(上周|上个月|去年|前几天|那次|last week|last month|last year)",
        ]
        for pattern in date_patterns:
            matches = re.findall(pattern, text)
            entities["dates"].extend(matches)
        
        return entities
    
    async def _recall_by_entities(self, entities: Dict[str, List[str]], *, limit: int = ENTITY_RECALL_LIMIT) -> List[str]:
        """Recall related memories based on entities."""
        memories = []
        
        # Recall by person name
        for person_name in entities.get("people", []):
            person = self.relationships.get_person(person_name)
            if person and person.context_snippets:
                # Get the most recent context snippet
                recent_context = person.context_snippets[-1]
                memories.append(f"About {person_name}: {recent_context}")
        
        safe_limit = max(1, min(int(limit), 20))
        return memories[:safe_limit]
    
    async def _recall_hybrid(self, text: str, limit: int = 3) -> List[str]:
        """Hybrid search recall (semantic + keyword)."""
        try:
            results = await self.index.hybrid_search(text, limit=limit)
            memories = []
            for item in results:
                if item["score"] > HYBRID_SCORE_THRESHOLD:
                    content = item["content"]
                    sender = item["sender"]
                    memories.append(f"[Previously discussed] {sender}: {content[:100]}")
            return memories
        except Exception as e:
            logger.error("Hybrid recall failed: %s", e)
            return []
    
    def _check_time_triggers(self) -> List[str]:
        """Check time-related triggers (anniversaries, etc.)."""
        reminders = []
        today = date.today()
        today_str = today.strftime("%m-%d")
        
        for milestone in self.milestones:
            # Extract month-day
            if len(milestone.date) == 10:  # YYYY-MM-DD
                m_date = milestone.date[5:]  # MM-DD
            else:
                m_date = milestone.date
            
            # Check if approaching
            try:
                m_month, m_day = map(int, m_date.split("-"))
                try:
                    milestone_date = today.replace(month=m_month, day=m_day)
                except ValueError:
                    continue
                
                # If already passed this year, look at next year
                if milestone_date < today:
                    try:
                        milestone_date = milestone_date.replace(year=today.year + 1)
                    except ValueError:
                        continue
                
                days_until = (milestone_date - today).days
                
                if 0 <= days_until <= milestone.remind_days_before:
                    if days_until == 0:
                        reminders.append(f"今天是 {milestone.name}！")
                    else:
                        reminders.append(f"距离 {milestone.name} 还有 {days_until} 天")
            except (ValueError, TypeError):
                pass
        
        return reminders
    
    def _get_emotion_appropriate_context(self, current_sentiment: float) -> Optional[str]:
        """Select appropriate recall context based on current mood."""
        if current_sentiment < -0.3:
            # User is feeling down, provide comforting context
            return "用户当前可能需要安慰与倾听，可优先回忆积极时刻并给出鼓励。"
        elif current_sentiment > 0.5:
            # User is happy, can share the joy
            return "用户现在很开心，可以共鸣这份快乐并延续积极情绪。"
        return None
    
    async def get_relevant_memories(
        self,
        current_message: str,
        current_sentiment: float = 0.0,
        *,
        entity_limit: int = ENTITY_RECALL_LIMIT,
        semantic_limit: int = 3,
    ) -> Dict[str, Any]:
        """
        Automatically recall relevant memories based on current conversation.
        
        Returns:
            {
                "entity_memories": [...],    # Entity-associated memories
                "semantic_memories": [...],  # Semantically similar memories
                "time_reminders": [...],     # Time reminders (anniversaries)
                "emotion_context": str,      # Emotional context suggestion
                "relationship_context": str  # Relationship context
            }
        """
        # 1. Extract entities
        entities = self._extract_entities(current_message)
        
        # 2. Entity recall
        safe_entity_limit = max(1, min(int(entity_limit), 20))
        entity_memories = await self._recall_by_entities(entities, limit=safe_entity_limit)

        # 3. Semantic recall
        safe_semantic_limit = max(1, min(int(semantic_limit), 20))
        semantic_memories = await self._recall_hybrid(current_message, limit=safe_semantic_limit)
        
        # 4. Time triggers
        time_reminders = self._check_time_triggers()
        
        # 5. Emotion context
        emotion_context = self._get_emotion_appropriate_context(current_sentiment)
        
        # 6. Relationship context
        relationship_context = self.relationships.to_context()
        
        return {
            "entity_memories": entity_memories,
            "semantic_memories": semantic_memories,
            "time_reminders": time_reminders,
            "emotion_context": emotion_context,
            "relationship_context": relationship_context
        }
    
    def format_for_prompt(
        self,
        recall_result: Dict,
        *,
        max_recall_items: int = MAX_RECALL_ITEMS,
        include_relationship_context: bool = True,
        include_time_reminders: bool = True,
        include_emotion_context: bool = True,
    ) -> str:
        """
        Format recall results as text for injection into the system prompt.
        """
        sections = []
        
        # Time reminders first
        if include_time_reminders and recall_result.get("time_reminders"):
            sections.append("## Important Reminders\n" + "\n".join(recall_result["time_reminders"]))

        # Relationship context
        if include_relationship_context and recall_result.get("relationship_context"):
            sections.append("## " + recall_result["relationship_context"])

        # Related memories
        safe_max_recall_items = max(1, min(int(max_recall_items), 20))
        all_memories = (
            recall_result.get("entity_memories", []) + 
            recall_result.get("semantic_memories", [])
        )
        if all_memories:
            sections.append("## Related Past Events\n" + "\n".join(all_memories[:safe_max_recall_items]))

        # Emotion suggestions
        if include_emotion_context and recall_result.get("emotion_context"):
            sections.append("## Emotional Context\n" + recall_result["emotion_context"])
        
        return "\n\n".join(sections)
