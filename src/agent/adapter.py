
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Dict, List, Set

from agent.loop import AgentLoop
from bus.queue import MessageBus
from bus.events import InboundMessage, OutboundMessage
from llm.base import LLMProvider
from llm.litellm_provider import LiteLLMProvider
from llm.router import (
    RouterProvider,
    ProviderRoute,
    resolve_router_strategy_template,
)
from agent.turn_hooks import TurnHookManager
from agent.context import ContextBuilder
from skills.loader import SkillLoader
from soul.compaction import ContextPruner
from soul.persona_runtime import get_persona_runtime_manager
from soul.persona import GazerPersonality

from memory.manager import MemoryManager
from soul.core import MemoryEntry
from runtime.config_manager import config
from runtime.provider_registry import get_provider_registry
from security.owner import get_owner_manager
from soul.models import ModelRegistry

logger = logging.getLogger("GazerAdapter")

# Named constants (formerly magic numbers)
DEFAULT_CONTEXT_MAX_TOKENS = 100_000
PROCESS_MESSAGE_TIMEOUT = 60.0
FAST_BRAIN_MAX_LENGTH = 50  # Messages shorter than this may use fast_brain
FAST_BRAIN_PATTERNS = {
    "hi", "hello", "hey", "你好", "嗨", "在吗", "在不",
    "thanks", "thank you", "谢谢", "ok", "good", "好的",
    "bye", "再见", "晚安", "早安", "gn", "gm",
}
MEMORY_TURN_HEALTH_REPORT = Path("data/reports/memory_turn_health.jsonl")
TOOL_PERSIST_REPORT = Path("data/reports/tool_result_persistence.jsonl")


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
                # Backward compatibility with alternate memory managers.
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
            logger.error(f"Failed to prepare memory context: {e}")
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

        # Inject companion context (memories, relationships, emotions)
        if self._companion_context:
            parts.append(f"\n## Memory & Context\n{self._companion_context}\n")

        # Inject available skills as XML (Agent Skills standard)
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


class GazerAgent:
    """Gazer's core agent -- orchestrates LLM calls, tools, and message routing."""
    def __init__(self, workspace: Path, memory_manager: MemoryManager):
        self.workspace = workspace
        self.memory_manager = memory_manager
        self.bus = MessageBus()
        self.turn_hooks = TurnHookManager()
        
        # Resolve provider credentials from ModelRegistry (slow_brain = reasoning)
        self.router: Optional[RouterProvider] = None
        self._router_fallback_provider: Optional[LLMProvider] = None
        self._router_rollout: Dict[str, Any] = {}
        self.provider = self._init_slow_brain_provider()

        # Fast brain provider for quick responses (greetings, short acknowledgements)
        self._fast_provider: Optional[LiteLLMProvider] = None
        self._fast_model: Optional[str] = None
        self._init_fast_brain()
        
        # Initialize Context
        self.context_builder = GazerContextBuilder(workspace, memory_manager)
        if isinstance(self.provider, LiteLLMProvider):
            context_window = self.provider.get_model_context_window(self.provider.get_default_model())
            if context_window and context_window > 0:
                self.context_builder.pruner.max_tokens = context_window
                logger.info("Context pruner max_tokens set from model contextWindow=%s", context_window)
        
        # Initialize Loop (pass fast_brain for quick response routing)
        self.loop = AgentLoop(
            bus=self.bus,
            provider=self.provider,
            workspace=workspace,
            context_builder=self.context_builder,
            fast_provider=self._fast_provider,
            fast_model=self._fast_model,
            slow_provider_resolver=self._resolve_slow_provider_for_message,
            persist_turn_callback=self._persist_turn_memory,
            turn_hooks=self.turn_hooks,
        )
        self.personality = GazerPersonality(
            memory_manager=self.memory_manager,
            tool_registry=self.loop.tools,
            llm_provider=self.provider,
            usage_tracker=self.loop.usage,
        )
        self._register_turn_hooks()
        
        # Dispatch task will be started in start()
        self._dispatch_task = None

        # Track response futures: { request_id: Future }
        # Simplified: We just track by chat_id since we are single user for now
        self._response_futures: Dict[str, asyncio.Future] = {}

        # Subscribe globally to catch our own messages
        self.bus.subscribe_outbound("gazer", self._handle_outbound)

    def _register_turn_hooks(self) -> None:
        self.turn_hooks.on_before_prompt_build(self._hook_before_prompt_build)
        self.turn_hooks.on_after_tool_result(self._hook_after_tool_result)
        self.turn_hooks.on_after_turn(self._hook_after_turn)

    async def _hook_before_prompt_build(self, payload: Dict[str, Any]) -> None:
        logger.debug(
            "before_prompt_build: session=%s history=%s",
            str(payload.get("session_key", "")),
            int(payload.get("history_len", 0) or 0),
        )

    async def _hook_after_tool_result(self, payload: Dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name", "") or "").strip()
        result_payload = payload.get("result_payload", {}) if isinstance(payload.get("result_payload"), dict) else {}
        raw_result = str(payload.get("tool_result", "") or "")
        should_persist, reason = self._should_persist_tool_result(
            tool_name=tool_name,
            result_payload=result_payload,
            raw_result=raw_result,
        )
        report_item = {
            "ts": time.time(),
            "session_key": str(payload.get("session_key", "")),
            "channel": str(payload.get("channel", "")),
            "chat_id": str(payload.get("chat_id", "")),
            "run_id": str(payload.get("run_id", "")),
            "tool_name": tool_name,
            "status": str(result_payload.get("status", "")),
            "decision": "memory" if should_persist else "trajectory_only",
            "reason": reason,
        }
        if not should_persist:
            self._append_jsonl(TOOL_PERSIST_REPORT, report_item)
            return

        normalized = self._normalize_tool_result_for_memory(raw_result)
        if not normalized:
            report_item["decision"] = "trajectory_only"
            report_item["reason"] = "empty_after_normalization"
            self._append_jsonl(TOOL_PERSIST_REPORT, report_item)
            return

        metadata = {
            "tool_call": True,
            "tool_name": tool_name,
            "tool_status": str(result_payload.get("status", "")),
            "tool_error_code": str(result_payload.get("error_code", "")),
            "channel": str(payload.get("channel", "")),
            "chat_id": str(payload.get("chat_id", "")),
            "run_id": str(payload.get("run_id", "")),
        }
        await self.memory_manager.save_entry(
            MemoryEntry(
                sender="System",
                content=f"Tool Execution [{tool_name}] Result: {normalized}",
                metadata=metadata,
            )
        )
        self._append_jsonl(TOOL_PERSIST_REPORT, report_item)

    async def _hook_after_turn(self, payload: Dict[str, Any]) -> None:
        row = {
            "ts": time.time(),
            "session_key": str(payload.get("session_key", "")),
            "channel": str(payload.get("channel", "")),
            "chat_id": str(payload.get("chat_id", "")),
            "sender_id": str(payload.get("sender_id", "")),
            "run_id": str(payload.get("run_id", "")),
            "status": str(payload.get("status", "")),
            "memory_context_chars": int(payload.get("memory_context_chars", 0) or 0),
            "recall_count": int(payload.get("recall_count", 0) or 0),
            "persist_ok": payload.get("persist_ok", None),
            "tool_calls_executed": int(payload.get("tool_calls_executed", 0) or 0),
        }
        self._append_jsonl(MEMORY_TURN_HEALTH_REPORT, row)

    @staticmethod
    def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("Failed to append report file: %s", path, exc_info=True)

    @staticmethod
    def _tool_result_policy() -> Dict[str, Any]:
        raw = config.get("memory.tool_result_persistence", {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        mode = str(raw.get("mode", "allowlist") or "allowlist").strip().lower()
        if mode not in {"allowlist", "denylist"}:
            mode = "allowlist"
        allow_tools = raw.get(
            "allow_tools",
            [
                "web_search",
                "web_fetch",
                "web_report",
                "read_file",
                "grep",
                "find_files",
                "list_dir",
                "email_read",
                "email_search",
                "vision_query",
            ],
        )
        deny_tools = raw.get(
            "deny_tools",
            [
                "exec",
                "write_file",
                "edit_file",
                "node_invoke",
                "gui_task_execute",
                "git_commit",
                "git_push",
                "email_send",
                "hardware_control",
                "delegate_task",
            ],
        )
        allow = {str(item).strip().lower() for item in allow_tools if str(item).strip()}
        deny = {str(item).strip().lower() for item in deny_tools if str(item).strip()}
        try:
            min_chars = max(1, int(raw.get("min_result_chars", 16) or 16))
        except (TypeError, ValueError):
            min_chars = 16
        try:
            max_chars = max(80, int(raw.get("max_result_chars", 1200) or 1200))
        except (TypeError, ValueError):
            max_chars = 1200
        return {
            "enabled": bool(raw.get("enabled", True)),
            "mode": mode,
            "allow": allow,
            "deny": deny,
            "persist_on_error": bool(raw.get("persist_on_error", False)),
            "min_result_chars": min_chars,
            "max_result_chars": max_chars,
        }

    def _should_persist_tool_result(
        self,
        *,
        tool_name: str,
        result_payload: Dict[str, Any],
        raw_result: str,
    ) -> tuple[bool, str]:
        policy = self._tool_result_policy()
        if not policy["enabled"]:
            return False, "policy_disabled"
        tool = str(tool_name or "").strip().lower()
        if not tool:
            return False, "empty_tool_name"
        if tool in policy["deny"]:
            return False, "deny_tools"
        if policy["mode"] == "allowlist" and tool not in policy["allow"]:
            return False, "not_in_allowlist"
        if not policy["persist_on_error"] and str(result_payload.get("status", "")).strip().lower() == "error":
            return False, "status_error"
        text = str(raw_result or "").strip()
        if len(text) < int(policy["min_result_chars"]):
            return False, "below_min_chars"
        return True, "policy_match"

    def _normalize_tool_result_for_memory(self, raw_result: str) -> str:
        policy = self._tool_result_policy()
        text = str(raw_result or "").strip()
        if not text:
            return ""
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        max_chars = int(policy["max_result_chars"])
        if len(text) > max_chars:
            text = text[: max(0, max_chars - 24)].rstrip() + "\n...[result trimmed]"
        return text

    def _build_litellm_provider(
        self,
        provider_name: str,
        model: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[LiteLLMProvider]:
        provider_cfg = dict(ModelRegistry.get_provider_config(provider_name) or {})
        overrides = dict(overrides or {})
        if overrides:
            provider_cfg.update(overrides)
        if not provider_cfg:
            return None
        env_key = provider_name.upper().replace("-", "_").replace(".", "_")
        api_key = (
            provider_cfg.get("api_key")
            or provider_cfg.get("apiKey")
            or os.getenv(f"{env_key}_API_KEY")
        )
        base_url = provider_cfg.get("base_url") or provider_cfg.get("baseUrl")
        default_model = model or provider_cfg.get("default_model")
        if not default_model:
            models_cfg = provider_cfg.get("models")
            if isinstance(models_cfg, list) and models_cfg:
                first = models_cfg[0] if isinstance(models_cfg[0], dict) else {}
                default_model = first.get("id") or first.get("name")
        if not default_model:
            default_model = self._resolve_provider_agents_default_model(
                provider_name=provider_name,
                provider_cfg=provider_cfg,
            )
        api_mode = provider_cfg.get("api")
        models_cfg = provider_cfg.get("models")
        model_settings: Dict[str, Dict[str, Any]] = {}
        if isinstance(models_cfg, list) and default_model:
            for entry in models_cfg:
                if not isinstance(entry, dict):
                    continue
                entry_id = str(entry.get("id") or "").strip()
                entry_name = str(entry.get("name") or "").strip()
                entry_lookup = entry_id or entry_name
                if entry_id:
                    model_settings[entry_id] = dict(entry)
                if entry_name:
                    model_settings[entry_name] = dict(entry)
                if entry_lookup and entry_lookup == str(default_model).strip():
                    api_mode = entry.get("api") or api_mode
                    break
        headers = provider_cfg.get("headers")
        extra_headers = headers if isinstance(headers, dict) else None
        raw_auth_mode = str(provider_cfg.get("auth", "") or "").strip().lower()
        auth_mode = raw_auth_mode if raw_auth_mode in {"", "api-key", "bearer", "none"} else ""
        raw_auth_header = provider_cfg.get("authHeader")
        if raw_auth_header is None:
            raw_auth_header = provider_cfg.get("auth_header")
        if auth_mode in {"api-key", "bearer"}:
            auth_header = True
        elif auth_mode == "none":
            auth_header = False
        else:
            auth_header = bool(raw_auth_header) if isinstance(raw_auth_header, bool) else False
        raw_strict_api_mode = provider_cfg.get("strict_api_mode")
        if raw_strict_api_mode is None:
            raw_strict_api_mode = provider_cfg.get("strictApiMode")
        strict_api_mode = bool(raw_strict_api_mode) if isinstance(raw_strict_api_mode, bool) else True
        raw_reasoning_param = provider_cfg.get("reasoning_param")
        if raw_reasoning_param is None:
            raw_reasoning_param = provider_cfg.get("reasoningParam")
        reasoning_param = raw_reasoning_param if isinstance(raw_reasoning_param, bool) else None
        if provider_name == "openai" and not base_url:
            base_url = "https://api.openai.com/v1"
        if not default_model:
            return None
        return LiteLLMProvider(
            api_key=api_key,
            api_base=base_url,
            default_model=default_model,
            api_mode=str(api_mode or "").strip() or None,
            extra_headers=extra_headers,
            model_settings=model_settings,
            auth_mode=auth_mode,
            auth_header=auth_header,
            strict_api_mode=strict_api_mode,
            reasoning_param=reasoning_param,
        )

    @staticmethod
    def _resolve_provider_agents_default_model(
        *,
        provider_name: str,
        provider_cfg: Dict[str, Any],
    ) -> Optional[str]:
        agents_cfg = provider_cfg.get("agents")
        if not isinstance(agents_cfg, dict):
            return None
        defaults_cfg = agents_cfg.get("defaults")
        if not isinstance(defaults_cfg, dict):
            return None
        model_cfg = defaults_cfg.get("model")
        if isinstance(model_cfg, str):
            candidate = str(model_cfg or "").strip()
            if "/" in candidate:
                ref_provider, ref_model = candidate.split("/", 1)
                if ref_provider.strip().lower() != str(provider_name or "").strip().lower():
                    return None
                ref_model = ref_model.strip()
                return ref_model or None
            return candidate or None
        if not isinstance(model_cfg, dict):
            return None

        refs: List[str] = []
        primary_ref = str(model_cfg.get("primary", "") or "").strip()
        if primary_ref:
            refs.append(primary_ref)
        fallbacks_raw = model_cfg.get("fallbacks", [])
        if isinstance(fallbacks_raw, list):
            refs.extend(str(item or "").strip() for item in fallbacks_raw if str(item or "").strip())

        for ref in refs:
            if "/" in ref:
                ref_provider, ref_model = ref.split("/", 1)
                if ref_provider.strip().lower() != str(provider_name or "").strip().lower():
                    continue
                model_name = ref_model.strip()
                if model_name:
                    return model_name
                continue
            return ref
        return None

    @staticmethod
    def _normalize_channel_allowlist(raw: Any) -> Set[str]:
        if not isinstance(raw, list):
            return set()
        return {str(item).strip().lower() for item in raw if str(item).strip()}

    def _is_router_allowed_for_context(self, *, channel: str, sender_id: str) -> bool:
        if not self.router:
            return False
        rollout = self._router_rollout if isinstance(self._router_rollout, dict) else {}
        if not bool(rollout.get("enabled", False)):
            return True

        owner_only = bool(rollout.get("owner_only", False))
        allowed_channels = self._normalize_channel_allowlist(rollout.get("channels", []))
        if not owner_only and not allowed_channels:
            return True

        ch = str(channel or "").strip()
        sid = str(sender_id or "").strip()
        if ch and sid:
            try:
                if get_owner_manager().is_owner_sender(ch, sid):
                    return True
            except Exception:
                pass
        if allowed_channels and ch.lower() in allowed_channels:
            return True
        return False

    def _resolve_slow_provider_for_message(
        self,
        msg: InboundMessage,
        current_provider: LLMProvider,
    ) -> LLMProvider:
        if not self.router:
            return current_provider
        if self._is_router_allowed_for_context(channel=msg.channel, sender_id=msg.sender_id):
            return self.router
        return self._router_fallback_provider or current_provider

    def _init_direct_slow_brain_provider(self) -> LLMProvider:
        provider_name, model_name = ModelRegistry.resolve_model_ref("slow_brain")
        provider = None
        if provider_name:
            provider = self._build_litellm_provider(provider_name, model=model_name)
        if provider is not None:
            logger.info(
                "Initializing GazerAgent with provider=%s model=%s",
                provider_name,
                provider.get_default_model(),
            )
            return provider

        api_key, base_url, model, _headers = ModelRegistry.resolve_model("slow_brain")
        model = model or "openai/gpt-3.5-turbo"
        logger.info("Initializing GazerAgent with model: %s", model)
        return LiteLLMProvider(api_key=api_key, api_base=base_url, default_model=model)

    def _init_slow_brain_provider(self) -> LLMProvider:
        router_cfg = config.get("models.router", {}) or {}
        if isinstance(router_cfg, dict):
            template_name = str(router_cfg.get("strategy_template", "custom")).strip().lower()
            if template_name and template_name != "custom":
                try:
                    tpl = resolve_router_strategy_template(template_name)
                    merged_budget = dict(router_cfg.get("budget", {}) or {})
                    merged_budget.update(tpl.get("budget", {}))
                    merged_outlier = dict(router_cfg.get("outlier_ejection", {}) or {})
                    merged_outlier.update(tpl.get("outlier_ejection", {}))
                    router_cfg = dict(router_cfg)
                    router_cfg["strategy"] = tpl.get("strategy", router_cfg.get("strategy", "priority"))
                    router_cfg["budget"] = merged_budget
                    router_cfg["outlier_ejection"] = merged_outlier
                    logger.info("Applied router strategy template: %s", template_name)
                except ValueError:
                    logger.warning(
                        "Unknown router strategy template '%s'; fallback to custom strategy fields.",
                        template_name,
                    )
        router_enabled = bool(router_cfg.get("enabled", False))
        strategy = str(router_cfg.get("strategy", "priority")).strip().lower()
        candidates = router_cfg.get("candidates", []) or []
        target_candidates = router_cfg.get("deployment_targets", []) or []
        self._router_rollout = router_cfg.get("rollout", {}) if isinstance(router_cfg, dict) else {}

        if router_enabled and (
            (isinstance(target_candidates, list) and target_candidates)
            or (isinstance(candidates, list) and candidates)
        ):
            routes: List[ProviderRoute] = []
            deployment_profiles = config.get("models.deployment_profiles", {}) or {}
            registry = get_provider_registry()

            if isinstance(target_candidates, list) and target_candidates:
                target_map = registry.list_deployment_targets() if hasattr(registry, "list_deployment_targets") else {}
                for raw_target_id in target_candidates:
                    target_id = str(raw_target_id).strip()
                    if not target_id:
                        continue
                    target_cfg = target_map.get(target_id, {}) if isinstance(target_map, dict) else {}
                    if not isinstance(target_cfg, dict):
                        continue
                    provider_name = str(target_cfg.get("provider", "")).strip()
                    if not provider_name:
                        continue
                    provider = self._build_litellm_provider(
                        provider_name,
                        model=str(target_cfg.get("default_model", "")).strip() or None,
                        overrides=target_cfg,
                    )
                    if provider is None:
                        continue
                    profile_key = str(target_cfg.get("profile", "")).strip() or provider_name
                    profile = (
                        deployment_profiles.get(profile_key, {})
                        if isinstance(deployment_profiles, dict)
                        else {}
                    )
                    routes.append(
                        ProviderRoute(
                            name=target_id,
                            provider_name=provider_name,
                            target_type=str(target_cfg.get("type", "gateway") or "gateway"),
                            health_url=str(target_cfg.get("health_url", "") or ""),
                            enabled=bool(target_cfg.get("enabled", True)),
                            provider=provider,
                            default_model=provider.get_default_model(),
                            capacity_rpm=int(
                                target_cfg.get(
                                    "capacity_rpm",
                                    profile.get("capacity_rpm", 120),
                                )
                                or 120
                            ),
                            cost_tier=str(
                                target_cfg.get(
                                    "cost_tier",
                                    profile.get("cost_tier", "medium"),
                                )
                                or "medium"
                            ),
                            latency_target_ms=float(
                                target_cfg.get(
                                    "latency_target_ms",
                                    profile.get("latency_target_ms", 2000.0),
                                )
                                or 2000.0
                            ),
                            traffic_weight=float(target_cfg.get("traffic_weight", 1.0) or 1.0),
                        )
                    )
            else:
                for name in candidates:
                    pname = str(name).strip()
                    if not pname:
                        continue
                    provider = self._build_litellm_provider(pname)
                    if provider is None:
                        continue
                    profile = deployment_profiles.get(pname, {}) if isinstance(deployment_profiles, dict) else {}
                    routes.append(
                        ProviderRoute(
                            name=pname,
                            provider_name=pname,
                            target_type="provider",
                            provider=provider,
                            default_model=provider.get_default_model(),
                            capacity_rpm=int(profile.get("capacity_rpm", 120) or 120),
                            cost_tier=str(profile.get("cost_tier", "medium")),
                            latency_target_ms=float(profile.get("latency_target_ms", 2000.0) or 2000.0),
                            traffic_weight=float(profile.get("traffic_weight", 1.0) or 1.0),
                        )
                    )
            if routes:
                budget_cfg = router_cfg.get("budget", {}) if isinstance(router_cfg, dict) else {}
                outlier_cfg = router_cfg.get("outlier_ejection", {}) if isinstance(router_cfg, dict) else {}
                complexity_cfg = (
                    router_cfg.get("complexity_routing", {}) if isinstance(router_cfg, dict) else {}
                )
                self.router = RouterProvider(
                    routes,
                    strategy=strategy,
                    budget_policy=budget_cfg,
                    outlier_policy=outlier_cfg,
                    complexity_policy=complexity_cfg,
                )
                logger.info(
                    "Initializing GazerAgent with router strategy=%s routes=%s budget_enabled=%s",
                    strategy,
                    [route.name for route in routes],
                    bool((budget_cfg or {}).get("enabled", False)),
                )
                self._router_fallback_provider = self._init_direct_slow_brain_provider()
                if self._is_router_allowed_for_context(channel="", sender_id=""):
                    return self.router
                return self._router_fallback_provider

        return self._init_direct_slow_brain_provider()

    def _init_fast_brain(self) -> None:
        """Initialize the fast_brain provider for quick/simple responses."""
        try:
            provider_name, model_name = ModelRegistry.resolve_model_ref("fast_brain")
            if provider_name:
                provider = self._build_litellm_provider(provider_name, model=model_name)
                if provider is not None:
                    self._fast_provider = provider
                    self._fast_model = provider.get_default_model()
                    logger.info("Fast brain initialized: provider=%s model=%s", provider_name, self._fast_model)
                    return

            fb_key, fb_base, fb_model, _fb_headers = ModelRegistry.resolve_model("fast_brain")
            if fb_key and fb_model:
                self._fast_provider = LiteLLMProvider(
                    api_key=fb_key, api_base=fb_base, default_model=fb_model,
                )
                self._fast_model = fb_model
                logger.info(f"Fast brain initialized: {fb_model}")
        except Exception as e:
            logger.warning(f"Fast brain unavailable: {e}")

    async def _handle_outbound(self, msg: OutboundMessage) -> None:
        """Handle outbound messages and resolve pending futures."""
        if msg.channel == "gazer" and msg.chat_id in self._response_futures:
            future = self._response_futures[msg.chat_id]
            if not future.done():
                future.set_result(msg.content)

    async def start(self) -> None:
        """Start the agent loop in background."""
        self._dispatch_task = asyncio.create_task(self.bus.dispatch_outbound())
        await self.loop.run()
        
    def stop(self) -> None:
        """Stop the agent loop."""
        self.loop.stop()
        self.bus.stop()
        if self._dispatch_task:
            self._dispatch_task.cancel()
        
    async def process_message(self, content: str, sender: str = "User") -> str:
        """
        Primary entry point for Gazer's Brain to send a message to the agent.
        Returns the response content.
        """
        # Use a consistent chat_id for the main session
        chat_id = "main"
        
        msg = InboundMessage(
            channel="gazer",
            chat_id=chat_id,
            sender_id=sender,
            content=content
        )
        
        # Create future for response
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._response_futures[chat_id] = future
        
        try:
            await self.bus.publish_inbound(msg)
            # Wait for response with timeout
            response = await asyncio.wait_for(future, timeout=PROCESS_MESSAGE_TIMEOUT)
            return response
        except asyncio.TimeoutError:
            return "Thinking took too long..."
        finally:
            self._response_futures.pop(chat_id, None)

    async def process_multi_agent(self, goal: str, max_workers: int | None = None) -> str:
        """Execute a goal using the multi-agent collaboration system.

        Instantiates a fresh MultiAgentRuntime per invocation, keeping
        the existing single-agent path completely untouched.
        """
        from multi_agent.runtime import MultiAgentRuntime

        ma_cfg = self._get_multi_agent_config()
        if max_workers is None:
            max_workers = ma_cfg["max_workers"]
        runtime = MultiAgentRuntime(self, max_agents=max_workers)
        return await runtime.execute(goal)

    def _get_multi_agent_config(self) -> dict:
        """Read multi_agent settings from config, with safe defaults."""
        return {
            "allow_multi": bool(config.get("multi_agent.allow_multi", False)),
            "max_workers": int(config.get("multi_agent.max_workers", 5) or 5),
        }

    async def process_auto(self, content: str, sender: str = "User") -> str:
        """Unified entry point with automatic single/multi-agent routing.

        Uses ``TaskComplexityAssessor`` (fast brain, four-dimension scoring)
        to decide whether to dispatch to multi-agent execution.  The assessor
        also provides a ``worker_hint`` that caps the pool size per task.
        """
        ma_cfg = self._get_multi_agent_config()

        if ma_cfg["allow_multi"] and self._fast_provider is not None:
            try:
                from multi_agent.brain_router import DualBrainRouter
                from multi_agent.assessor import TaskComplexityAssessor

                router = DualBrainRouter(
                    slow_provider=self.provider,
                    fast_provider=self._fast_provider,
                    fast_model=self._fast_model,
                )
                assessor = TaskComplexityAssessor(
                    router=router,
                    max_workers_limit=ma_cfg["max_workers"],
                )
                result = await assessor.assess(content)
                if result.use_multi_agent:
                    workers = min(result.worker_hint, ma_cfg["max_workers"])
                    logger.info(
                        "Auto-route: multi-agent (score=%d, workers=%d)",
                        result.score, workers,
                    )
                    return await self.process_multi_agent(content, max_workers=workers)
            except Exception:
                logger.debug("TaskComplexityAssessor failed, falling back to single agent", exc_info=True)

        return await self.process_message(content, sender=sender)

    def register_tool(self, tool: "Tool") -> None:
        """Register a Tool ABC instance into the AgentLoop's ToolRegistry."""
        self.loop.tools.register(tool)

    async def stream_response(self, content: str, sender: str = "User") -> AsyncIterator[str]:
        """Send a message and stream the final text response token-by-token.

        This bypasses the bus / future mechanism and calls the provider's
        streaming endpoint directly for the *last* LLM turn (the one that
        produces text, not tool calls).  Tool-call turns still run
        non-streaming inside the agent loop.
        """
        # Build messages the same way the loop does
        session_key = f"gazer:main"
        if hasattr(self.context_builder, 'prepare_memory_context'):
            await self.context_builder.prepare_memory_context(content)

        history = self.loop._get_history(session_key)
        messages = self.loop.context.build_messages(
            history=history, current_message=content,
            channel="gazer", chat_id="main",
        )

        # Run the tool-call iterations (non-streaming)
        iteration = 0
        while iteration < self.loop.max_iterations:
            iteration += 1
            response = await self.provider.chat(
                messages=messages,
                tools=self.loop.tools.get_definitions(
                    sender_id=sender,
                    channel="gazer",
                ),
                model=self.loop.model,
            )
            if response.has_tool_calls:
                import json as _json
                tool_call_dicts = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": _json.dumps(tc.arguments)}}
                    for tc in response.tool_calls
                ]
                messages = self.loop.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                for tc in response.tool_calls:
                    result = await self.loop.tools.execute(
                        tc.name,
                        tc.arguments,
                        sender_id=sender,
                        channel="gazer",
                    )
                    messages = self.loop.context.add_tool_result(
                        messages, tc.id, tc.name, result
                    )
                continue
            else:
                # Final turn — stream it
                collected = ""
                async for chunk in self.provider.stream_chat(
                    messages=messages,
                    tools=[],  # no tools for final turn
                    model=self.loop.model,
                ):
                    collected += chunk
                    yield chunk
                # Update session history
                self.loop._update_history(session_key, "user", content)
                self.loop._update_history(session_key, "assistant", collected)
                # Persist to long-term memory
                await self._save_to_memory(content, collected, sender)
                return

        yield "\n[Reached max iterations without final response]"

    async def _save_to_memory(self, user_content: str, assistant_content: str, sender: str) -> None:
        """Persist user + assistant messages to long-term memory."""
        try:
            await self.memory_manager.save_entry(
                MemoryEntry(sender=sender, content=user_content)
            )
            await self.memory_manager.save_entry(
                MemoryEntry(sender="Gazer", content=assistant_content)
            )
        except Exception as e:
            logger.error(f"Failed to save stream response to memory: {e}")

    async def _persist_turn_memory(self, msg: InboundMessage, assistant_content: str) -> bool:
        """Persist a normal bus-driven turn into long-term memory."""
        user_content = str(msg.content or "").strip()
        assistant_text = str(assistant_content or "").strip()
        if not user_content or not assistant_text:
            return False
        try:
            metadata = {
                "channel": str(msg.channel or ""),
                "chat_id": str(msg.chat_id or ""),
                "sender_id": str(msg.sender_id or ""),
            }
            await self.memory_manager.save_entry(
                MemoryEntry(sender="user", content=user_content, metadata=metadata)
            )
            await self.memory_manager.save_entry(
                MemoryEntry(sender="Gazer", content=assistant_text, metadata=metadata)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to persist turn memory: {e}")
            return False




    def set_skill_loader(self, loader: SkillLoader) -> None:
        """Attach a SkillLoader so its metadata is injected into the system prompt."""
        self.context_builder.skill_loader = loader
