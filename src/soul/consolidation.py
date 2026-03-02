"""
Memory consolidation -- simulates nightly memory integration.

1. Extract important information from daily conversations
2. Update user identity (IDENTITY.md)
3. Update relationship graph
4. Extract meaningful stories
5. Save emotion snapshots
"""

import os
import re
import logging
import json
from datetime import date
from typing import Dict, Optional

from soul.core import WorkingMemory, MemoryEntry
from soul.cognition import LLMCognitiveStep
from memory.archiver import MemoryArchiver
from llm.base import LLMProvider

logger = logging.getLogger("GazerConsolidation")


class MemoryConsolidator:
    """Basic memory consolidator: summarizes short-term interactions into long-term facts."""

    _CONSOLIDATION_SYSTEM_PROMPT = (
        "You are Gazer's Memory Consolidator. Your task is to extract key facts, "
        "user preferences, and relationship changes from the following dialogue history. "
        "Provide a concise summary of what Gazer should remember about the user for the long term."
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        llm_provider: Optional[LLMProvider] = None,
        llm_model: Optional[str] = None,
    ):
        self._llm_provider = llm_provider
        self._llm_model = llm_model

        from soul.models import ModelRegistry
        api_key_resolved, base_url, model_name, headers = ModelRegistry.resolve_model("slow_brain")
        self.api_key = api_key or api_key_resolved or ""
        if not llm_model:
            self._llm_model = model_name
        self.llm = (
            LLMCognitiveStep(
                name="Consolidator",
                model=model_name,
                api_key=self.api_key,
                base_url=base_url,
                default_headers=headers,
            )
            if self.api_key and not llm_provider
            else None
        )

    async def summarize_interactions(self, memory: WorkingMemory) -> str:
        consolidation_prompt = self._CONSOLIDATION_SYSTEM_PROMPT

        # Prefer injected LLMProvider
        if self._llm_provider is not None:
            messages = [{"role": "system", "content": consolidation_prompt}]
            for m in memory.memories:
                role = "assistant" if m.sender == (memory.owner or "Gazer") else "user"
                messages.append({"role": role, "content": m.content})
            try:
                resp = await self._llm_provider.chat(messages, model=self._llm_model)
                return resp.content or ""
            except Exception as e:
                logger.error("LLM provider consolidation failed: %s", e)
                return "[Consolidation Error]"

        # Legacy fallback
        if not self.llm:
            return "Unable to consolidate memory: No LLM configured."
        summary_entry = await self.llm.run(memory, consolidation_prompt)
        return summary_entry.content

    def update_long_term_memory(
        self, current_memory: WorkingMemory, summary: str
    ) -> WorkingMemory:
        insight_entry = MemoryEntry(
            sender="System",
            content=f"Long-term Insight: {summary}",
            metadata={"type": "long_term_insight"},
        )
        return current_memory.append(insight_entry)


class NightlyConsolidator(MemoryConsolidator):
    """Enhanced nightly consolidator with knowledge archiving.

    Extends :class:`MemoryConsolidator` -- inherits ``summarize_interactions``
    and ``update_long_term_memory`` to avoid code duplication.
    """

    IDENTITY_EXTRACTION_PROMPT = """

1. 性格特征（内向/外向、乐观/悲观等）
2. 兴趣爱好
3. 工作/学习情况
4. 生活习惯
5. 重要的人生经历或故事

对话内容：
{conversation}

请以 JSON 格式输出，只包含今天对话中明确提到的信息：
{{
    "personality_traits": [],
    "interests": [],
    "occupation": null,
    "habits": [],
    "stories": [],
    "important_dates": []
}}
"""

    STORY_EXTRACTION_PROMPT = """从以下对话中，找出用户分享的有意义的故事或经历。
只提取用户主动分享的、有情感价值的故事，不要包括日常琐事。

对话内容：
{conversation}

如果有值得记住的故事，以 JSON 格式输出：
{{
    "stories": [
        {{
            "title": "故事标题",
            "summary": "简短摘要",
            "emotion": "相关情感",
            "people_involved": ["涉及的人"]
        }}
    ]
}}

如果没有值得记住的故事，返回空列表。
"""

    def __init__(
        self,
        memory_manager,
        relationship_graph,
        emotion_tracker,
        api_key: Optional[str] = None,
        identity_path: Optional[str] = None,
        stories_dir: Optional[str] = None,
        llm_provider: Optional[LLMProvider] = None,
        llm_model: Optional[str] = None,
    ):
        super().__init__(api_key=api_key, llm_provider=llm_provider, llm_model=llm_model)
        if identity_path is None or stories_dir is None:
            from runtime.config_manager import config as _cfg
            base_dir = str(_cfg.get("memory.context_backend.data_dir", "data/openviking") or "data/openviking")
            if identity_path is None:
                identity_path = os.path.join(base_dir, "IDENTITY.md")
            if stories_dir is None:
                stories_dir = os.path.join(base_dir, "stories")
        self.memory = memory_manager
        self.relationships = relationship_graph
        self.emotions = emotion_tracker
        self.identity_path = str(identity_path)
        self.stories_dir = str(stories_dir)
        self.archiver = MemoryArchiver(memory_manager)
        os.makedirs(self.stories_dir, exist_ok=True)

    async def run_nightly(self):
        logger.info("Starting nightly memory consolidation...")

        await self.archiver.archive_day()

        today_memory = self.memory.load_recent(limit=100)
        if not today_memory.memories:
            logger.info("No conversations today, skipping.")
            return

        conversation_text = today_memory.get_context_string()
        await self._update_identity(conversation_text)
        self._consolidate_relationships()
        await self._extract_stories(conversation_text)

        logger.info("Nightly consolidation completed.")

    # summarize_interactions() and update_long_term_memory() are inherited
    # from MemoryConsolidator — no need to duplicate.

    async def _update_identity(self, conversation: str):
        if not self.llm:
            return
        try:
            prompt = self.IDENTITY_EXTRACTION_PROMPT.format(
                conversation=conversation[:3000]
            )
            temp_memory = WorkingMemory(
                memories=[MemoryEntry(sender="System", content=prompt)]
            )
            result = await self.llm.run(temp_memory, "Extract user identity information.")

            content = result.content
            # Extract JSON from potential markdown code fences
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if match:
                content = match.group(1).strip()

            identity_data = json.loads(content)
            self._append_to_identity(identity_data)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse identity extraction: {e}")
        except Exception as e:
            logger.error(f"Failed to update identity: {e}")

    def _append_to_identity(self, data: Dict):
        new_info = []
        if data.get("personality_traits"):
            new_info.append(f"- Personality: {', '.join(data['personality_traits'])}")
        if data.get("interests"):
            new_info.append(f"- Interests: {', '.join(data['interests'])}")
        if data.get("occupation"):
            new_info.append(f"- Occupation: {data['occupation']}")
        if data.get("habits"):
            new_info.append(f"- Habits: {', '.join(data['habits'])}")

        if not new_info:
            return

        try:
            os.makedirs(os.path.dirname(self.identity_path), exist_ok=True)
            with open(self.identity_path, "a", encoding="utf-8") as f:
                f.write(f"\n## {date.today().isoformat()} Update\n")
                f.write("\n".join(new_info) + "\n")
            logger.info(f"Updated identity with {len(new_info)} new items.")
        except Exception as e:
            logger.error(f"Failed to write identity file: {e}")

    def _consolidate_relationships(self):
        logger.info(
            f"Relationship graph has {len(self.relationships.people)} people."
        )

    async def _extract_stories(self, conversation: str):
        if not self.llm:
            return
        try:
            prompt = self.STORY_EXTRACTION_PROMPT.format(
                conversation=conversation[:3000]
            )
            temp_memory = WorkingMemory(
                memories=[MemoryEntry(sender="System", content=prompt)]
            )
            result = await self.llm.run(temp_memory, "Extract meaningful stories.")

            content = result.content
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if match:
                content = match.group(1).strip()

            data = json.loads(content)
            for story in data.get("stories", []):
                self._save_story(story)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse story extraction: {e}")
        except Exception as e:
            logger.error(f"Failed to extract stories: {e}")

    def _save_story(self, story: Dict):
        try:
            title = story.get("title", "untitled")
            date_str = date.today().isoformat()
            slug = title.replace(" ", "-")[:30]
            filename = f"{date_str}-{slug}.md"
            filepath = os.path.join(self.stories_dir, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n")
                f.write(f"*Recorded on {date_str}*\n\n")
                f.write(f"## Summary\n{story.get('summary', '')}\n\n")
                if story.get("emotion"):
                    f.write(f"## Emotion\n{story['emotion']}\n\n")
                if story.get("people_involved"):
                    f.write(
                        "## People Involved\n" + ", ".join(story["people_involved"]) + "\n"
                    )
            logger.info(f"Saved story: {title}")
        except Exception as e:
            logger.error(f"Failed to save story: {e}")


# Default instance (requires dependencies, instantiated by Persona)
consolidator = None
