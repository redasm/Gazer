
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
from skills.loader import SkillLoader
from soul.persona import GazerPersonality

from memory.manager import MemoryManager
from soul.core import MemoryEntry
from runtime.config_manager import config
from runtime.paths import resolve_runtime_path
from runtime.provider_registry import get_provider_registry
from security.owner import get_owner_manager
from soul.models import ModelRegistry
from multi_agent.models import MultiAgentExecutionContext

from agent.context_builder import GazerContextBuilder
from agent.multi_agent import _MultiAgentWorkerBudget, MultiAgentMixin

logger = logging.getLogger("GazerAdapter")

PROCESS_MESSAGE_TIMEOUT = 60.0
FAST_BRAIN_MAX_LENGTH = 50  # Messages shorter than this may use fast_brain
FAST_BRAIN_PATTERNS = {
    "hi", "hello", "hey", "你好", "嗨", "在吗", "在不",
    "thanks", "thank you", "谢谢", "ok", "good", "好的",
    "bye", "再见", "晚安", "早安", "gn", "gm",
}
MEMORY_TURN_HEALTH_REPORT = Path("data/reports/memory_turn_health.jsonl")
TOOL_PERSIST_REPORT = Path("data/reports/tool_result_persistence.jsonl")



class GazerAgent(MultiAgentMixin):
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
            auto_route_turn_callback=self._maybe_auto_route_inbound_message,
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
        self._multi_agent_worker_budget: _MultiAgentWorkerBudget | None = None

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
        self._inject_persona_enrichment(payload)

    def _inject_persona_enrichment(self, payload: Dict[str, Any]) -> None:
        """Build live persona state from GazerPersonality and inject into context builder."""
        try:
            parts = []

            # OCEAN personality traits
            parts.append(self.personality.personality.to_prompt())

            # Current affective state
            affect = self.personality.affect_manager.current_affect()
            parts.append(
                f"当前情绪：{affect.to_label()}"
                f"（valence={affect.valence:.2f}, arousal={affect.arousal:.2f}）"
            )

            # Mental state
            parts.append(f"认知状态：{self.personality.current_state.description}")

            # Drives & goals
            motivation = self.personality._build_motivation_context()
            if motivation:
                parts.append(motivation)

            # Trust context for current sender
            sender_id = str(payload.get("sender_id", "") or "").strip()
            if sender_id:
                trust_prompt = self.personality.trust_system.get_relationship_prompt(sender_id)
                parts.append(f"用户关系：{trust_prompt}")

            enrichment = "\n".join(parts)
            self.context_builder.set_persona_enrichment(enrichment)
        except Exception:
            logger.debug("Failed to inject persona enrichment", exc_info=True)

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
        resolved_path = resolve_runtime_path(path, config_manager=config)
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("Failed to append report file: %s", resolved_path, exc_info=True)

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
                logger.info("Fast brain initialized: %s", fb_model)
        except Exception as e:
            logger.warning("Fast brain unavailable: %s", e)

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
            logger.error("Failed to save stream response to memory: %s", e)

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
            self._feed_personality_after_turn(msg, user_content, assistant_text)
            return True
        except Exception as e:
            logger.error("Failed to persist turn memory: %s", e)
            return False

    def _feed_personality_after_turn(
        self, msg: InboundMessage, user_content: str, assistant_text: str,
    ) -> None:
        """Feed turn results back into GazerPersonality state machine."""
        try:
            # Mental state transition (IDLE → INTERACTING on input)
            p = self.personality
            next_name = p._on_input_transition.get(p.current_state.name.upper())
            if next_name and next_name in p._states:
                p.transition_to(p._states[next_name])

            # Goal progress tracking
            p._update_goal_progress(user_content, assistant_text)

            # Trust observation
            sender_id = str(msg.sender_id or "").strip()
            if sender_id:
                from security.owner import get_owner_manager
                is_owner = False
                try:
                    is_owner = get_owner_manager().is_owner_sender(
                        str(msg.channel or ""), sender_id,
                    )
                except Exception:
                    pass
                p.trust_system.observe(sender_id, is_primary=is_owner)
        except Exception:
            logger.debug("Failed to feed personality after turn", exc_info=True)




    def set_skill_loader(self, loader: SkillLoader) -> None:
        """Attach a SkillLoader so its metadata is injected into the system prompt."""
        self.context_builder.skill_loader = loader
