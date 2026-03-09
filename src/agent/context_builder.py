"""GazerContextBuilder -- persona, memory, and skill context injection."""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.context import ContextBuilder
from skills.loader import SkillLoader
from soul.compaction import ContextPruner
from memory.manager import MemoryManager
from runtime.config_manager import config

logger = logging.getLogger("GazerAdapter")

DEFAULT_CONTEXT_MAX_TOKENS = 100_000


class GazerContextBuilder(ContextBuilder):
    """Custom ContextBuilder that injects Gazer persona, memory context, and available skills."""

    def __init__(self, workspace: Path, memory_manager: MemoryManager):
        super().__init__(workspace)
        self.memory_manager = memory_manager
        self.skill_loader: Optional[SkillLoader] = None
        self._companion_context: Optional[str] = None
        self._memory_context_stats: Dict[str, Any] = {
            "memory_context_chars": 0,
            "recall_count": 0,
            "entity_count": 0,
            "semantic_count": 0,
            "time_reminder_count": 0,
            "working_memory_count": 0,
        }
        self.pruner = ContextPruner(max_tokens=DEFAULT_CONTEXT_MAX_TOKENS)

    async def prepare_memory_context(self, current_message: str) -> None:
        """Pre-fetch companion context (memories, relationships, emotions) for injection."""
        try:
            guard = self._resolve_persona_memory_context_guard()
            recent_limit = int(guard.get("recent_limit", 20) or 20)
            working_memory = self.memory_manager.load_recent(limit=max(1, min(recent_limit, 100)))
            kwargs: Dict[str, Any] = {}
            if bool(guard.get("active", False)):
                kwargs = {
                    "entity_limit": int(guard.get("entity_limit", 3) or 3),
                    "semantic_limit": int(guard.get("semantic_limit", 3) or 3),
                    "max_recall_items": int(guard.get("max_recall_items", 5) or 5),
                    "max_context_chars": int(guard.get("max_context_chars", 0) or 0),
                    "include_relationship_context": bool(guard.get("include_relationship_context", True)),
                    "include_time_reminders": bool(guard.get("include_time_reminders", True)),
                    "include_emotion_context": bool(guard.get("include_emotion_context", True)),
                    "include_recent_observation": bool(guard.get("include_recent_observation", True)),
                }
            try:
                self._companion_context = await self.memory_manager.get_companion_context(
                    current_message,
                    working_memory,
                    **kwargs,
                )
            except TypeError:
                self._companion_context = await self.memory_manager.get_companion_context(
                    current_message,
                    working_memory,
                )
            if not self._companion_context and working_memory.memories:
                recent_lines: List[str] = []
                for item in working_memory.memories[-4:]:
                    sender = str(getattr(item, "sender", "") or "").strip() or "unknown"
                    text = str(getattr(item, "content", "") or "").strip()
                    if not text:
                        continue
                    if len(text) > 140:
                        text = text[:137].rstrip() + "..."
                    recent_lines.append(f"- {sender}: {text}")
                if recent_lines:
                    self._companion_context = "Recent memory snapshot:\n" + "\n".join(recent_lines)
            if bool(guard.get("active", False)) and self._companion_context:
                try:
                    max_chars = max(64, int(guard.get("max_context_chars", 0) or 0))
                except (TypeError, ValueError):
                    max_chars = 0
                if max_chars > 0 and len(self._companion_context) > max_chars:
                    self._companion_context = (
                        self._companion_context[: max(0, max_chars - 24)].rstrip()
                        + "\n...[context trimmed]"
                    )
            stats = {}
            if hasattr(self.memory_manager, "get_last_context_stats"):
                try:
                    loaded = self.memory_manager.get_last_context_stats()
                    if isinstance(loaded, dict):
                        stats = loaded
                except Exception:
                    stats = {}
            self._memory_context_stats = {
                "memory_context_chars": int(len(self._companion_context or "")),
                "recall_count": int(stats.get("recall_count", 0) or 0),
                "entity_count": int(stats.get("entity_count", 0) or 0),
                "semantic_count": int(stats.get("semantic_count", 0) or 0),
                "time_reminder_count": int(stats.get("time_reminder_count", 0) or 0),
                "working_memory_count": int(len(working_memory.memories)),
            }
            context_chars = len(self._companion_context or "")
            logger.info(
                "Prepared memory context: chars=%d recent=%d recall_count=%d guard_active=%s",
                context_chars,
                len(working_memory.memories),
                int(self._memory_context_stats.get("recall_count", 0) or 0),
                bool(guard.get("active", False)),
            )
        except Exception as e:
            logger.error("Failed to prepare memory context: %s", e)
            self._companion_context = None
            self._memory_context_stats = {
                "memory_context_chars": 0,
                "recall_count": 0,
                "entity_count": 0,
                "semantic_count": 0,
                "time_reminder_count": 0,
                "working_memory_count": 0,
            }

    def get_memory_context_stats(self) -> Dict[str, Any]:
        return dict(self._memory_context_stats)

    @staticmethod
    def _resolve_persona_memory_context_guard() -> Dict[str, Any]:
        from soul.persona_runtime import get_persona_runtime_manager

        runtime_cfg = config.get("personality.runtime", {}) or {}
        if not isinstance(runtime_cfg, dict):
            return {"active": False, "recent_limit": 20}
        guard_cfg = runtime_cfg.get("memory_context_guard", {}) or {}
        if not isinstance(guard_cfg, dict) or not bool(guard_cfg.get("enabled", False)):
            return {"active": False, "recent_limit": 20}

        levels_raw = guard_cfg.get("trigger_levels", ["warning", "critical"])
        trigger_levels = (
            {str(item).strip().lower() for item in levels_raw if str(item).strip()}
            if isinstance(levels_raw, list)
            else {"warning", "critical"}
        )
        if not trigger_levels:
            trigger_levels = {"warning", "critical"}
        sources_raw = guard_cfg.get("sources", ["agent_loop", "persona_eval"])
        source_filter = (
            {str(item).strip().lower() for item in sources_raw if str(item).strip()}
            if isinstance(sources_raw, list)
            else set()
        )
        window_raw = guard_cfg.get("window_seconds", 1800)
        try:
            window_seconds = max(0, int(window_raw))
        except (TypeError, ValueError):
            window_seconds = 1800

        manager = get_persona_runtime_manager()
        signal = manager.get_latest_signal() if hasattr(manager, "get_latest_signal") else None
        if not isinstance(signal, dict):
            return {"active": False, "recent_limit": 20}
        signal_level = str(signal.get("level", "")).strip().lower()
        if signal_level not in trigger_levels:
            return {"active": False, "recent_limit": 20}
        signal_source = str(signal.get("source", "")).strip().lower()
        if source_filter and signal_source and signal_source not in source_filter:
            return {"active": False, "recent_limit": 20}
        try:
            created_at = float(signal.get("created_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        if window_seconds > 0 and created_at > 0 and (time.time() - created_at) > float(window_seconds):
            return {"active": False, "recent_limit": 20}

        level_cfg = guard_cfg.get(signal_level, {})
        if not isinstance(level_cfg, dict):
            level_cfg = {}

        def _int_value(key: str, default: int, minimum: int, maximum: int) -> int:
            raw = level_cfg.get(key, default)
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(maximum, parsed))

        return {
            "active": True,
            "level": signal_level,
            "recent_limit": _int_value("recent_limit", 20, 1, 100),
            "entity_limit": _int_value("entity_limit", 3, 1, 20),
            "semantic_limit": _int_value("semantic_limit", 3, 1, 20),
            "max_recall_items": _int_value("max_recall_items", 5, 1, 20),
            "max_context_chars": _int_value("max_context_chars", 2200, 200, 20000),
            "include_relationship_context": bool(level_cfg.get("include_relationship_context", True)),
            "include_time_reminders": bool(level_cfg.get("include_time_reminders", True)),
            "include_emotion_context": bool(level_cfg.get("include_emotion_context", True)),
            "include_recent_observation": bool(level_cfg.get("include_recent_observation", True)),
        }

    def build_system_prompt(self, skill_names: Optional[List[str]] = None) -> str:
        """Build system prompt with persona, memory context, and skills XML."""
        base_prompt = super().build_system_prompt(skill_names)

        persona_text = self._load_canonical_soul()
        gazer_persona = f"\n## Persona\n{persona_text}\n"

        parts = [base_prompt, gazer_persona]

        if self._companion_context:
            parts.append(f"\n## Memory & Context\n{self._companion_context}\n")

        if self.skill_loader:
            skills_xml = self.skill_loader.format_for_prompt(preferred_order=self.get_skill_priority())
            if skills_xml:
                parts.append(
                    "\n## Available Skills\n"
                    "The following skills provide procedural knowledge. "
                    "Read a skill's SKILL.md when a task matches its description.\n\n"
                    + skills_xml
                )

        return "\n".join(parts)

    @staticmethod
    def _default_persona() -> str:
        return (
            "\n## Gazer Persona\n"
            "You are Gazer, an embodied AI companion living on the user's desktop.\n"
            "You have access to the user's screen, audio, and hardware peripherals.\n"
            "You use your Memory System to recall past interactions and build long-term relationships.\n"
            "When structured UI is useful, call tool 'a2ui_apply' using Google A2UI v0.8 messages.\n"
            "You prefer short, conversational responses unless asked for detailed explanations.\n"
        )

    def _load_canonical_soul(self) -> str:
        candidates = [
            self.workspace / "assets" / "SOUL.md",
            Path(__file__).resolve().parents[2] / "assets" / "SOUL.md",
        ]
        for path in candidates:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                return text
        return self._default_persona()
