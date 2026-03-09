import os
import logging
import json
import re
from datetime import date
from typing import List, Dict, Any, Optional
from soul.core import WorkingMemory, MemoryEntry
from soul.cognition import LLMCognitiveStep
from runtime.config_manager import config

logger = logging.getLogger("MemoryArchiver")

class MemoryArchiver:
    """
    Memory Archiver (The Archivist)
    Responsible for extracting "long-term knowledge" from daily logs.
    
    Architecture:
    - Input: Daily Log (e.g., events/2026-02-05.md)
    - Process: LLM Extraction -> Classification -> Appending
    - Output: Topic Files (e.g., knowledge/topics/python.md, knowledge/entities/user.md)
    """
    
    ARCHIVE_PROMPT = """Analyze the following conversation log from today. 
Extract permanent knowledge, user preferences, and important facts that should be remembered long-term.
Classify each fact into one of these categories:
1. ENTITY: Facts about specific people (User, Mom, Boss, etc.)
2. TOPIC: Facts about abstract concepts (Python, Pizza, Star Wars)
3. EVENT: Significant life events (Bought a house, Got a job)

Ignore trivial chit-chat (greetings, weather).

Output JSON format:
{{
    "knowledge": [
        {{
            "category": "ENTITY",
            "subject": "User",
            "content": "User is currently learning Rust programming.",
            "source_date": "2026-02-05"
        }},
        {{
            "category": "TOPIC",
            "subject": "Python",
            "content": "User prefers using 'black' for code formatting.",
            "source_date": "2026-02-05"
        }}
    ]
}}

Conversation Log:
{log_content}
"""

    def __init__(self, memory_manager):
        self.memory = memory_manager
        from soul.models import ModelRegistry
        api_key, base_url, model_name, headers = ModelRegistry.resolve_model("slow_brain")
        self.llm = LLMCognitiveStep(
            name="Archiver",
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            default_headers=headers,
        ) if api_key else None
        self.knowledge_base = memory_manager.knowledge_path

    async def archive_day(self, date_str: str = None):
        """Archive the log for the specified date."""
        if not date_str:
            date_str = date.today().isoformat()
            
        daily_file = os.path.join(self.memory.daily_path, f"{date_str}.md")
        if not os.path.exists(daily_file):
            logger.info("No log found for %s, skipping archive.", date_str)
            return

        logger.info("Archiving log for %s...", date_str)
        
        # 1. Read Log
        with open(daily_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        if len(content) < 50:
            logger.info("Log content too short, skipping.")
            return

        # 2. Extract Knowledge via LLM
        if not self.llm:
            logger.warning("No LLM available for archival, skipping.")
            return
        prompt = self.ARCHIVE_PROMPT.format(log_content=content[:10000]) # Truncate if too huge
        try:
            # Mock Memory for LLM call
            temp_mem = WorkingMemory(memories=[MemoryEntry(sender="System", content=prompt)])
            response = await self.llm.run(temp_mem, "Extract Knowledge")
            
            # 3. Parse and Save
            data = self._parse_json(response.content)
            if data and "knowledge" in data:
                self._save_knowledge(data["knowledge"])
            else:
                logger.warning("No knowledge extracted or invalid format.")
                
        except Exception as e:
            logger.error("Archival failed: %s", e)

    def _save_knowledge(self, items: List[Dict]):
        """Save extracted items to respective markdown files"""
        for item in items:
            category = item.get("category", "TOPIC").upper()
            subject = item.get("subject", "Uncategorized")
            subject = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fff]', '_', subject)
            content = item.get("content")
            
            # Determine folder
            subdir = "topics"
            if category == "ENTITY":
                subdir = "entities"
            elif category == "EVENT":
                subdir = "events"
            
            # Determine file path: knowledge/topics/python.md
            dir_path = os.path.join(self.knowledge_base, subdir)
            os.makedirs(dir_path, exist_ok=True)
            file_path = os.path.join(dir_path, f"{subject}.md")
            
            # Append Fact
            try:
                with open(file_path, "a", encoding="utf-8") as f:
                    # Add Header if new file
                    if os.path.getsize(file_path) == 0:
                        f.write(f"# Knowledge: {subject}\n\n")
                    
                    # Write Entry
                    f.write(f"- {content} *({item.get('source_date', 'Unknown')})*\n")
                
                logger.info("Archived fact to %s/%s.md", subdir, subject)
            except Exception as e:
                logger.error("Failed to write to %s: %s", file_path, e)

    def _parse_json(self, text: str) -> Optional[Dict]:
        """Robust JSON parser"""
        try:
            # Strip markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return json.loads(text.strip())
        except Exception as e:
            logger.debug("JSON parse failure: %s, raw text: %s", e, text[:200])
            return None
