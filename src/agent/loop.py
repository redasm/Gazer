"""Agent loop: the core processing engine."""

import asyncio
import collections
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, List, Dict

from bus.events import InboundMessage, OutboundMessage, TypingEvent
from bus.queue import MessageBus
from llm.base import LLMProvider, LLMResponse, ToolCallRequest
from llm.prompt_cache import PromptSegmentCache
from agent.channel_command_registry import (
    ChannelCommandRegistry,
    parse_channel_command,
)
from agent.turn_hooks import TurnHookManager
from agent.persona_tool_policy import evaluate_persona_tool_policy_linkage
from agent.tool_policy_pipeline import (
    apply_tool_policy_pipeline_steps,
    merge_tool_policy_constraints,
)
from agent.tool_call_hooks import ToolCallHookManager
from agent.context import ContextBuilder
from tools.base import CancellationToken, ToolSafetyTier
from tools.batching import ToolBatchPlan, ToolBatchPlanner, ToolBatchingTracker
from tools.planner import ToolPlanner, ToolPlannerPlan
from tools.media_marker import MEDIA_MARKER
from tools.registry import ToolRegistry, ToolPolicy, normalize_tool_policy
from agent.session_store import SessionStore
from agent.trajectory import TrajectoryStore
from eval.benchmark import EvalBenchmarkManager
from runtime.resilience import RetryBudget, classify_error_message
from runtime.rate_limiter import RateLimiter
from security.owner import get_owner_manager
from agent.loop_mixins import (
    ChannelCommandsMixin,
    ToolExecutionMixin,
    ToolPolicyMixin,
    LLMInteractionMixin,
    PlanningMixin,
    ToolResultUtilsMixin,
)

from soul.models import ModelRegistry
from soul.persona_runtime import get_persona_runtime_manager

logger = logging.getLogger("AgentLoop")

# Agent loop constants -- canonical home is agent.constants
from agent.constants import (
    DEFAULT_MAX_ITERATIONS, HISTORY_CACHE_LIMIT, MAX_CONTEXT_OVERFLOW_RETRIES,
    DEFAULT_LLM_MAX_RETRIES, DEFAULT_LLM_RETRY_BACKOFF_SECONDS,
    CHARS_PER_TOKEN_ESTIMATE, FAST_BRAIN_MAX_LENGTH,
    DEFAULT_TOOL_CALL_TIMEOUT_SECONDS, DEFAULT_TOOL_RETRY_MAX,
    DEFAULT_TOOL_RETRY_BACKOFF_SECONDS, DEFAULT_MAX_TOOL_CALLS_PER_TURN,
    DEFAULT_MAX_PARALLEL_TOOL_CALLS, DEFAULT_TOOL_BATCH_MAX_SIZE,
    DEFAULT_PARALLEL_TOOL_LANE_LIMITS, DEFAULT_RETRY_BUDGET_TOTAL,
    DEFAULT_TURN_TIMEOUT_SECONDS, _LANG_DEFAULT, _CJK_RE,
    FAST_BRAIN_PATTERNS, CONFIRM_TOKENS, CANCEL_TOKENS,
    TRUSTED_LOCAL_COMMAND_CHANNELS, _TIER_MAP, _REPLAN_ERROR_HINTS,
)

from agent.constants import _LANG_MESSAGES


class UsageTracker:
    """Accumulates LLM token usage across the session.

    Tracks totals, per-model breakdown, daily buckets, and latency.
    """

    def __init__(self) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.request_count: int = 0
        self._start_ts: float = time.time()
        # Per-model breakdown: {model_name: {prompt, completion, total, requests, cost, latencies}}
        self._by_model: Dict[str, Dict[str, Any]] = {}
        # Daily buckets: {"YYYY-MM-DD": {input, output, cache, requests, cost}}
        self._daily: Dict[str, Dict[str, Any]] = {}
        # Latency tracking
        self._latencies: collections.deque = collections.deque(maxlen=1000)
        # Today's date for fast comparison
        self._today: str = time.strftime("%Y-%m-%d")
        # Today-only counters
        self._today_input: int = 0
        self._today_output: int = 0
        self._today_requests: int = 0
        self._today_cost: float = 0.0

    def _ensure_today(self) -> str:
        """Roll over today counters if date changed."""
        now = time.strftime("%Y-%m-%d")
        if now != self._today:
            self._today = now
            self._today_input = 0
            self._today_output = 0
            self._today_requests = 0
            self._today_cost = 0.0
        return now

    def add(self, usage: dict, *, model: str = "", latency_ms: float = 0.0, cost_usd: float = 0.0) -> None:
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        total = int(usage.get("total_tokens", 0) or 0)
        cache = int(usage.get("cache_read_tokens", 0) or usage.get("cached_tokens", 0) or 0)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.request_count += 1

        # Today
        today = self._ensure_today()
        self._today_input += prompt
        self._today_output += completion
        self._today_requests += 1
        self._today_cost += cost_usd

        # Daily bucket
        bucket = self._daily.setdefault(today, {
            "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0,
            "requests": 0, "cost_usd": 0.0,
        })
        bucket["input_tokens"] += prompt
        bucket["output_tokens"] += completion
        bucket["cache_tokens"] += cache
        bucket["requests"] += 1
        bucket["cost_usd"] += cost_usd

        # Per-model
        if model:
            m = self._by_model.setdefault(model, {
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "requests": 0, "cost_usd": 0.0, "latencies": collections.deque(maxlen=1000),
            })
            m["prompt_tokens"] += prompt
            m["completion_tokens"] += completion
            m["total_tokens"] += total
            m["requests"] += 1
            m["cost_usd"] += cost_usd
            if latency_ms > 0:
                m["latencies"].append(latency_ms)

        # Latency
        if latency_ms > 0:
            self._latencies.append(latency_ms)

    def summary(self) -> dict:
        self._ensure_today()
        avg_latency = (
            sum(self._latencies) / len(self._latencies)
            if self._latencies else 0.0
        )
        # Build per-model summary (strip latency arrays)
        by_model = {}
        for name, data in self._by_model.items():
            lats = data.get("latencies", [])
            by_model[name] = {
                "prompt_tokens": data["prompt_tokens"],
                "completion_tokens": data["completion_tokens"],
                "total_tokens": data["total_tokens"],
                "requests": data["requests"],
                "cost_usd": round(data["cost_usd"], 6),
                "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else 0.0,
            }
        # Build daily trend
        daily = []
        for date in sorted(self._daily.keys()):
            b = self._daily[date]
            daily.append({
                "date": date,
                "input_tokens": b["input_tokens"],
                "output_tokens": b["output_tokens"],
                "cache_tokens": b["cache_tokens"],
                "requests": b["requests"],
                "cost_usd": round(b["cost_usd"], 6),
            })
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "requests": self.request_count,
            "avg_latency_ms": round(avg_latency, 2),
            "today_input_tokens": self._today_input,
            "today_output_tokens": self._today_output,
            "today_total_tokens": self._today_input + self._today_output,
            "today_requests": self._today_requests,
            "today_cost_usd": round(self._today_cost, 6),
            "by_model": by_model,
            "daily": daily,
        }


class AgentLoop(
    ChannelCommandsMixin,
    ToolExecutionMixin,
    ToolPolicyMixin,
    LLMInteractionMixin,
    PlanningMixin,
    ToolResultUtilsMixin,
):
    """
    The agent loop is the core processing engine.
    
    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Optionally generates a plan for complex tasks
    4. Calls the LLM
    5. Executes tool calls
    6. Sends responses back
    """
    
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: Optional[str] = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        context_builder: Optional[ContextBuilder] = None,
        fast_provider: Optional[LLMProvider] = None,
        fast_model: Optional[str] = None,
        tool_policy: Optional[Dict[str, Any]] = None,
        slow_provider_resolver: Optional[Callable[[InboundMessage, LLMProvider], LLMProvider]] = None,
        persist_turn_callback: Optional[Callable[[InboundMessage, str], Any]] = None,
        turn_hooks: Optional[TurnHookManager] = None,
        soul_turn_callback: Optional[Callable[[InboundMessage], Any]] = None,
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self._base_model = model
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self._slow_provider_resolver = slow_provider_resolver
        self._persist_turn_callback = persist_turn_callback
        self._soul_turn_callback = soul_turn_callback
        self._turn_hooks = turn_hooks
        self._active_provider_override: Optional[LLMProvider] = None
        self._active_model_override: Optional[str] = None
        
        # Fast brain for simple conversational responses (greetings, acknowledgements)
        self._fast_provider = fast_provider
        self._fast_model = fast_model
        
        self.context = context_builder or ContextBuilder(workspace)
        self.tools = ToolRegistry()
        self._tool_policy_raw = dict(tool_policy or {})
        
        # Persistent session store (single source of truth for history)
        self.session_store = SessionStore()
        self.trajectory_store = TrajectoryStore()
        self._eval_benchmark_manager = EvalBenchmarkManager()

        # Token usage tracking
        self.usage = UsageTracker()

        # Rate limiter: configurable via config, defaults to 20 req / 60s per sender
        from runtime.config_manager import config as _cfg
        self._rate_limiter = RateLimiter(
            max_requests=_cfg.get("security.rate_limit_requests", 20),
            window_seconds=_cfg.get("security.rate_limit_window", 60.0),
        )
        prompt_cache_cfg = _cfg.get("models.prompt_cache", {}) or {}
        if not isinstance(prompt_cache_cfg, dict):
            prompt_cache_cfg = {}
        self.prompt_cache = PromptSegmentCache(
            enabled=bool(prompt_cache_cfg.get("enabled", False)),
            ttl_seconds=int(prompt_cache_cfg.get("ttl_seconds", 300) or 300),
            max_items=int(prompt_cache_cfg.get("max_items", 512) or 512),
            segment_policy=str(prompt_cache_cfg.get("segment_policy", "stable_prefix") or "stable_prefix"),
            chars_per_token=CHARS_PER_TOKEN_ESTIMATE,
            scope_fields=prompt_cache_cfg.get("scope_fields"),
            sanitize_sensitive=bool(prompt_cache_cfg.get("sanitize_sensitive", True)),
        )
        tool_batch_cfg = _cfg.get("security.tool_batching", {}) or {}
        if not isinstance(tool_batch_cfg, dict):
            tool_batch_cfg = {}
        self.tool_batch_planner = ToolBatchPlanner(
            enabled=bool(tool_batch_cfg.get("enabled", True)),
            max_batch_size=int(
                tool_batch_cfg.get("max_batch_size", DEFAULT_TOOL_BATCH_MAX_SIZE)
                or DEFAULT_TOOL_BATCH_MAX_SIZE
            ),
            dedupe_enabled=bool(tool_batch_cfg.get("dedupe_enabled", False)),
        )
        planner_cfg = _cfg.get("security.tool_planner", {}) or {}
        if not isinstance(planner_cfg, dict):
            planner_cfg = {}
        self.tool_planner = ToolPlanner(
            enabled=bool(planner_cfg.get("enabled", True)),
            dependency_keys=planner_cfg.get("dependency_keys"),
            compact_results=bool(planner_cfg.get("compact_results", True)),
            max_result_chars=int(planner_cfg.get("max_result_chars", 2400) or 2400),
            error_max_result_chars=int(
                planner_cfg.get("error_max_result_chars", 4000) or 4000
            ),
            head_chars=int(planner_cfg.get("head_chars", 900) or 900),
            tail_chars=int(planner_cfg.get("tail_chars", 700) or 700),
        )
        self.tool_batching_tracker = ToolBatchingTracker()
        self.channel_command_registry = ChannelCommandRegistry()
        self._register_channel_command_handlers()

        self._running = False
        # Active cancellation token for the current request (if any)
        self._cancel_token: Optional[CancellationToken] = None
        # session_key -> {"name": tool_name, "params": dict}
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        # Per-turn prompt-cache scope (session/user/channel isolation)
        self._prompt_cache_scope: Dict[str, Any] = {}
        # Latest persona-driven tool-policy linkage snapshot (for observability/admin).
        self._persona_tool_policy_linkage_status: Dict[str, Any] = {
            "enabled": False,
            "active": False,
            "reason": "not_evaluated",
            "signal": {"level": "", "source": "", "created_at": None},
            "config": {},
            "policy_overlay": {
                "allow_names": [],
                "deny_names": [],
                "allow_providers": [],
                "deny_providers": [],
                "allow_model_providers": [],
                "deny_model_providers": [],
                "allow_model_names": [],
                "deny_model_names": [],
                "allow_model_selectors": [],
                "deny_model_selectors": [],
            },
        }
        self._tool_policy_pipeline_status: Dict[str, Any] = {
            "reason": "not_evaluated",
            "steps": [],
            "base_counts": {},
            "final_counts": {},
            "evaluated_at": None,
        }
        self._tool_policy_model_provider: str = ""
        self._tool_policy_model_name: str = ""
        self._tool_call_hooks = ToolCallHookManager()

    async def run(self) -> None:  # noqa: C901
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                try:
                    msg = await asyncio.wait_for(
                        self.bus.consume_inbound(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Rate limiting check
                rate_key = f"{msg.channel}:{msg.sender_id}"
                if not self._rate_limiter.allow(rate_key):
                    logger.warning(f"Rate limited: {rate_key}")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="You're sending messages too quickly. Please wait a moment."
                    ))
                    continue

                # Process it
                turn_timeout = self._get_turn_timeout_seconds()
                try:
                    response = await asyncio.wait_for(
                        self._process_message(msg),
                        timeout=turn_timeout,
                    )
                    if response:
                        await self.bus.publish_outbound(response)
                except asyncio.TimeoutError:
                    reply_language = self._detect_user_language(msg.content)
                    logger.error(
                        "Message processing timed out after %.1fs: channel=%s chat_id=%s sender=%s",
                        turn_timeout,
                        msg.channel,
                        msg.chat_id,
                        msg.sender_id,
                    )
                    await self.bus.publish_typing(
                        TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False),
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=self._msg(reply_language, "timeout", seconds=int(turn_timeout)),
                        )
                    )
                except Exception as e:
                    reply_language = self._detect_user_language(msg.content)
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=self._msg(reply_language, "runtime_error", error=str(e)),
                    ))
            except Exception as e:
                logger.error(f"Critical error in agent loop: {e}", exc_info=True)
                await asyncio.sleep(1)
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    def _get_history(self, session_key: str) -> List[Dict[str, Any]]:
        """Load session history from the persistent SessionStore."""
        return self.session_store.load(session_key, limit=HISTORY_CACHE_LIMIT)

    def _update_history(self, session_key: str, role: str, content: str):
        """Persist a message to the session store."""
        self.session_store.append(session_key, role, content)

    async def _persist_turn_memory(self, msg: InboundMessage, assistant_content: str) -> Optional[bool]:
        callback = self._persist_turn_callback
        if callback is None:
            return None
        content = str(assistant_content or "").strip()
        if not content:
            return False
        try:
            result = callback(msg, content)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, bool):
                return result
            return True
        except Exception:
            logger.warning("Failed to persist turn memory", exc_info=True)
            return False

    async def _emit_after_turn_hook(self, payload: Dict[str, Any]) -> None:
        if not self._turn_hooks:
            return
        await self._turn_hooks.emit_after_turn(payload)

    def cancel_current(self) -> None:
        """Cancel the currently-running request (if any)."""
        if self._cancel_token:
            self._cancel_token.cancel()
            logger.info("Current request cancelled by user.")

    def reset_session(self, session_key: str) -> None:
        """Clear conversation history for a session (supports /new, /reset)."""
        self.session_store.delete_session(session_key)
        logger.info(f"Session reset: {session_key}")

    async def _process_message(self, msg: InboundMessage) -> Optional[OutboundMessage]:
        """
        Process a single inbound message.
        """
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}")
        self._cancel_token = CancellationToken()
        turn_started = asyncio.get_running_loop().time()
        reply_language = self._detect_user_language(msg.content)
        
        session_key = msg.session_key
        trajectory_id = self.trajectory_store.start(
            session_key=session_key,
            channel=msg.channel,
            chat_id=msg.chat_id,
            sender_id=msg.sender_id,
            user_content=msg.content,
        )
        if msg.metadata:
            self.trajectory_store.add_event(
                trajectory_id,
                stage="observe",
                action="inbound_metadata",
                payload={
                    "keys": sorted([str(k) for k in msg.metadata.keys()])[:20],
                    "metadata": msg.metadata,
                },
            )
        trajectory_status = "success"
        error_message = ""
        recent_tool_failures: List[Dict[str, Any]] = []
        tool_failure_counts: Dict[str, int] = {}
        abort_after_repeats = 2  # same tool+args_hash+error_code repeats
        self._active_provider_override = None
        self._active_model_override = None
        self._tool_policy_model_provider = ""
        self._tool_policy_model_name = ""
        if self._slow_provider_resolver:
            try:
                resolved = self._slow_provider_resolver(msg, self.provider)
                if resolved is not None:
                    self._active_provider_override = resolved
                    if self._base_model is None:
                        self._active_model_override = resolved.get_default_model()
            except Exception as exc:
                logger.warning("Slow provider resolver failed; using default provider: %s", exc)

        # Handle session reset trigger from channels
        if msg.metadata.get("_reset_session"):
            self.reset_session(session_key)
            self._pending_confirmations.pop(session_key, None)
            logger.info(f"Session reset via channel trigger: {session_key}")

        # --- Typing indicator: start ---
        await self.bus.publish_typing(TypingEvent(
            channel=msg.channel, chat_id=msg.chat_id, is_typing=True,
        ))

        pending_confirmation = self._pending_confirmations.get(session_key)
        if pending_confirmation:
            pending_name = str(pending_confirmation.get("name") or "").strip()
            pending_params = pending_confirmation.get("params")
            if not pending_name or not isinstance(pending_params, dict):
                self._pending_confirmations.pop(session_key, None)
            else:
                decision = self._parse_confirmation_decision(msg.content)
                if decision is None:
                    reminder = self._build_pending_confirmation_prompt(pending_name)
                    await self.bus.publish_typing(
                        TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False)
                    )
                    self._update_history(session_key, "user", msg.content)
                    self._update_history(session_key, "assistant", reminder)
                    await self._emit_after_turn_hook(
                        {
                            "session_key": session_key,
                            "channel": msg.channel,
                            "chat_id": msg.chat_id,
                            "sender_id": msg.sender_id,
                            "status": "pending_confirmation",
                            "memory_context_chars": 0,
                            "recall_count": 0,
                            "persist_ok": None,
                        }
                    )
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=reminder,
                    )
                self._pending_confirmations.pop(session_key, None)
                if decision is False:
                    cancelled = f"已取消 `{pending_name}` 操作。"
                    await self.bus.publish_typing(
                        TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False)
                    )
                    self._update_history(session_key, "user", msg.content)
                    self._update_history(session_key, "assistant", cancelled)
                    await self._emit_after_turn_hook(
                        {
                            "session_key": session_key,
                            "channel": msg.channel,
                            "chat_id": msg.chat_id,
                            "sender_id": msg.sender_id,
                            "status": "cancelled_confirmation",
                            "memory_context_chars": 0,
                            "recall_count": 0,
                            "persist_ok": None,
                        }
                    )
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=cancelled,
                    )

                tool_max_tier = self._resolve_tool_max_tier(msg)
                if self._release_gate_limits_tier(sender_id=msg.sender_id, channel=msg.channel):
                    tool_max_tier = ToolSafetyTier.SAFE
                tool_policy = self._resolve_tool_policy()
                retry_budget = self._build_retry_budget()
                confirmed_result = await self._execute_single_tool_call(
                    ToolCallRequest(
                        id="confirmed_tool_call",
                        name=pending_name,
                        arguments=pending_params,
                    ),
                    max_tier=tool_max_tier,
                    policy=tool_policy,
                    retry_budget=retry_budget,
                    sender_id=msg.sender_id,
                    channel=msg.channel,
                    session_key=session_key,
                    confirmed=True,
                )
                pending_media = self._extract_media(confirmed_result)
                confirmed_text = self._strip_media_markers(confirmed_result) or f"已执行 `{pending_name}`。"
                await self.bus.publish_typing(
                    TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False)
                )
                self._update_history(session_key, "user", msg.content)
                self._update_history(session_key, "assistant", confirmed_text)
                await self._emit_after_turn_hook(
                    {
                        "session_key": session_key,
                        "channel": msg.channel,
                        "chat_id": msg.chat_id,
                        "sender_id": msg.sender_id,
                        "status": "confirmed_tool_call",
                        "memory_context_chars": 0,
                        "recall_count": 0,
                        "persist_ok": None,
                    }
                )
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=confirmed_text,
                    media=pending_media,
                )

        parsed_command = self.channel_command_registry.parse(msg.content)
        if parsed_command is not None:
            command_name, command_args = parsed_command
            command_reply = self._execute_channel_command(
                command=command_name,
                args=command_args,
                msg=msg,
            )
            await self.bus.publish_typing(
                TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False)
            )
            self._update_history(session_key, "user", msg.content)
            self._update_history(session_key, "assistant", command_reply)
            await self._emit_after_turn_hook(
                {
                    "session_key": session_key,
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "sender_id": msg.sender_id,
                    "status": "channel_command",
                    "memory_context_chars": 0,
                    "recall_count": 0,
                    "persist_ok": None,
                }
            )
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=command_reply,
            )

        if self._soul_turn_callback is not None:
            try:
                soul_result = self._soul_turn_callback(msg)
                if asyncio.iscoroutine(soul_result):
                    soul_result = await soul_result
                final_content = str(soul_result or "").strip()
                if not final_content:
                    final_content = self._msg(reply_language, "iteration_limit_fallback")

                persona_signal: Optional[Dict[str, Any]] = None
                try:
                    final_content, persona_signal = self._apply_persona_runtime_guard(
                        content=final_content,
                        reply_language=reply_language,
                        run_id=trajectory_id,
                        channel=msg.channel,
                    )
                except Exception as guard_exc:
                    logger.warning("Persona runtime guard failed: %s", guard_exc)
                if persona_signal is not None:
                    self.trajectory_store.add_event(
                        trajectory_id,
                        stage="persona",
                        action="runtime_guard",
                        payload={
                            "level": str(persona_signal.get("level", "")),
                            "violation_count": int(persona_signal.get("violation_count", 0) or 0),
                            "violations": list(persona_signal.get("violations", [])),
                            "correction_applied": bool(persona_signal.get("correction_applied", False)),
                            "correction_strategy": str(persona_signal.get("correction_strategy", "")),
                            "drift_score": persona_signal.get("drift_score", 0.0),
                        },
                    )

                self.trajectory_store.add_event(
                    trajectory_id,
                    stage="persona",
                    action="soul_turn",
                    payload={"callback_enabled": True},
                )
                self.trajectory_store.finalize(
                    trajectory_id,
                    status="soul_turn",
                    final_content=final_content,
                    usage=self.usage.summary(),
                    metrics={
                        "iterations": 1,
                        "overflow_retries": 0,
                        "retry_budget_remaining": 0,
                        "tool_rounds": 0,
                        "tool_calls_requested": 0,
                        "tool_calls_executed": 0,
                        "tool_deduped_calls": 0,
                        "parallel_rounds": 0,
                        "batch_groups": 0,
                        "tokens_this_turn": 0,
                        "turn_latency_ms": round((asyncio.get_running_loop().time() - turn_started) * 1000.0, 2),
                    },
                )
                self._active_provider_override = None
                self._active_model_override = None
                self._tool_policy_model_provider = ""
                self._tool_policy_model_name = ""
                self._prompt_cache_scope = {}

                await self.bus.publish_typing(
                    TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False)
                )
                self._update_history(session_key, "user", msg.content)
                self._update_history(session_key, "assistant", final_content)
                await self._emit_after_turn_hook(
                    {
                        "session_key": session_key,
                        "channel": msg.channel,
                        "chat_id": msg.chat_id,
                        "sender_id": msg.sender_id,
                        "run_id": trajectory_id,
                        "status": "soul_turn",
                        "memory_context_chars": 0,
                        "recall_count": 0,
                        "persist_ok": None,
                        "tool_calls_executed": 0,
                    }
                )
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=final_content,
                )
            except Exception as exc:
                self.trajectory_store.finalize(
                    trajectory_id,
                    status="error",
                    final_content=str(exc),
                    usage=self.usage.summary(),
                    metrics={
                        "iterations": 0,
                        "overflow_retries": 0,
                        "retry_budget_remaining": 0,
                        "tool_rounds": 0,
                        "tool_calls_requested": 0,
                        "tool_calls_executed": 0,
                        "tool_deduped_calls": 0,
                        "parallel_rounds": 0,
                        "batch_groups": 0,
                        "tokens_this_turn": 0,
                        "turn_latency_ms": round((asyncio.get_running_loop().time() - turn_started) * 1000.0, 2),
                    },
                )
                self._active_provider_override = None
                self._active_model_override = None
                self._tool_policy_model_provider = ""
                self._tool_policy_model_name = ""
                self._prompt_cache_scope = {}
                await self.bus.publish_typing(
                    TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False)
                )
                logger.error("Soul callback failed: %s", exc, exc_info=True)
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=self._msg(reply_language, "runtime_error", error=str(exc)),
                )
        
        # --- Fast brain shortcut for simple messages ---
        fast_response = await self._try_fast_brain(msg)
        if fast_response is not None:
            await self.bus.publish_typing(TypingEvent(
                channel=msg.channel, chat_id=msg.chat_id, is_typing=False,
            ))
            self._update_history(session_key, "user", msg.content)
            self._update_history(session_key, "assistant", fast_response)
            persist_ok = await self._persist_turn_memory(msg, fast_response)
            await self._emit_after_turn_hook(
                {
                    "session_key": session_key,
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "sender_id": msg.sender_id,
                    "status": "fast_brain",
                    "memory_context_chars": 0,
                    "recall_count": 0,
                    "persist_ok": persist_ok,
                }
            )
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=fast_response,
            )

        self._prompt_cache_scope = {
            "session_key": session_key,
            "channel": msg.channel,
            "sender_id": msg.sender_id,
        }
        if self._turn_hooks:
            await self._turn_hooks.emit_before_prompt_build(
                {
                    "session_key": session_key,
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "sender_id": msg.sender_id,
                    "history_len": len(self._get_history(session_key)),
                    "current_message": msg.content,
                }
            )

        # Pre-fetch memory context if the context builder supports it
        if hasattr(self.context, 'prepare_memory_context'):
            await self.context.prepare_memory_context(msg.content)

        # Build initial messages
        history = self._get_history(session_key) 
        
        messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        memory_context_chars = 0
        recall_count = 0
        if hasattr(self.context, "get_memory_context_stats"):
            try:
                stats = self.context.get_memory_context_stats()
                if isinstance(stats, dict):
                    memory_context_chars = int(stats.get("memory_context_chars", 0) or 0)
                    recall_count = int(stats.get("recall_count", 0) or 0)
            except Exception:
                logger.debug("Failed to load memory context stats", exc_info=True)
        try:
            system_content = str(messages[0].get("content", "")) if messages else ""
            logger.info(
                "Prompt prepared: history=%d system_chars=%d has_memory_context=%s memory_context_chars=%d recall_count=%d",
                len(history),
                len(system_content),
                "## Memory & Context" in system_content,
                memory_context_chars,
                recall_count,
            )
        except Exception:
            logger.debug("Failed to log prompt diagnostics", exc_info=True)
        messages.append({"role": "system", "content": self._msg(reply_language, "language_rule")})
        metadata_note = self._build_inbound_metadata_note(msg.metadata)
        if metadata_note:
            messages.append({"role": "system", "content": metadata_note})
        
        # Plan-then-Execute: if the task looks complex, ask LLM to produce a plan first
        if self._should_plan(msg.content, history_len=len(history)):
            plan = await self._generate_plan(
                messages,
                retry_budget=self._build_retry_budget(),
            )
            if plan:
                messages = self.context.add_assistant_message(messages, f"## Plan\n{plan}", None)
                logger.info("Plan generated for complex task.")

        # Agent loop
        iteration = 0
        final_content = None
        overflow_retries = 0
        pending_media: list[str] = []  # media paths from tool results
        tool_max_tier = self._resolve_tool_max_tier(msg)
        if self._release_gate_limits_tier(sender_id=msg.sender_id, channel=msg.channel):
            tool_max_tier = ToolSafetyTier.SAFE
        tool_policy = self._resolve_tool_policy()
        retry_budget = self._build_retry_budget()
        max_tool_calls_per_turn, max_parallel_tool_calls = self._get_tool_governance_limits()
        tool_calls_executed = 0
        turn_total_tokens = 0
        turn_tool_rounds = 0
        turn_parallel_rounds = 0
        turn_tool_calls_requested = 0
        turn_tool_calls_executed = 0
        turn_tool_deduped = 0
        turn_batch_groups = 0
        
        try:
            while iteration < self.max_iterations:
                iteration += 1

                # Call LLM
                model_provider, model_name = self._current_tool_policy_model_context()
                tool_defs = self.tools.get_definitions(
                    max_tier=tool_max_tier,
                    policy=tool_policy,
                    sender_id=msg.sender_id,
                    channel=msg.channel,
                    model_provider=model_provider,
                    model_name=model_name,
                )
                self.trajectory_store.add_event(
                    trajectory_id,
                    stage="think",
                    action="llm_request",
                    payload={"iteration": iteration, "tool_count": len(tool_defs)},
                )
                if iteration == 1:
                    tool_names = [t["function"]["name"] for t in tool_defs]
                    logger.info(
                        "Passing %d tools to LLM (max_tier=%s policy=%s): %s",
                        len(tool_defs),
                        tool_max_tier.value,
                        bool(
                            tool_policy
                            and (
                                tool_policy.allow_names
                                or tool_policy.allow_providers
                                or tool_policy.deny_names
                                or tool_policy.deny_providers
                            )
                        ),
                        tool_names,
                    )
                response = await self._call_llm_with_retries(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                    call_name="Agent LLM call",
                    retry_budget=retry_budget,
                )
                self.trajectory_store.add_event(
                    trajectory_id,
                    stage="think",
                    action="llm_response",
                    payload={
                        "iteration": iteration,
                        "error": response.error,
                        "finish_reason": response.finish_reason,
                        "has_tool_calls": response.has_tool_calls,
                        "request_id": response.request_id,
                        "model": response.model,
                    },
                )

                # Log LLM call details with structured metadata
                if response.request_id:
                    logger.info(
                        "Agent LLM call [iter=%d]: model=%s request_id=%s tokens=%s finish=%s",
                        iteration,
                        response.model,
                        response.request_id,
                        response.usage.get("total_tokens", "?"),
                        response.finish_reason,
                        extra={
                            "request_id": response.request_id,
                            "model": response.model,
                            "tokens": response.usage,
                        },
                    )

                # Track token usage
                if response.usage:
                    self.usage.add(response.usage, model=response.model or "")
                    turn_total_tokens += int(response.usage.get("total_tokens", 0) or 0)

                # --- Context overflow auto-compaction ---
                if self._is_context_overflow(response) and overflow_retries < MAX_CONTEXT_OVERFLOW_RETRIES:
                    overflow_retries += 1
                    logger.warning(
                        "Context overflow detected (retry %d/%d). Compacting...",
                        overflow_retries,
                        MAX_CONTEXT_OVERFLOW_RETRIES,
                    )
                    messages = self._compact_messages(messages)
                    self.trajectory_store.add_event(
                        trajectory_id,
                        stage="think",
                        action="context_compacted",
                        payload={"iteration": iteration, "overflow_retries": overflow_retries},
                    )
                    continue  # retry with compacted context

                if response.error and not response.has_tool_calls:
                    logger.warning("LLM call returned error: %s", response.content or "unknown_error")
                    final_content = self._msg(
                        reply_language,
                        "llm_error",
                        detail=(response.content or "unknown error"),
                    )
                    trajectory_status = "llm_error"
                    break

                # Handle tool calls
                logger.info(
                    "LLM response: has_tool_calls=%s, finish=%s, content_len=%d",
                    response.has_tool_calls,
                    response.finish_reason,
                    len(response.content or ""),
                )
                if response.has_tool_calls:
                    confirmation_prompt: Optional[str] = None
                    # Add assistant message with tool calls
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),  # Must be JSON string
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages = self.context.add_assistant_message(messages, response.content, tool_call_dicts)

                    # Execute tools (parallel when multiple independent calls)
                    tool_calls = response.tool_calls
                    requested_calls = len(tool_calls)
                    planner_plan: Optional[ToolPlannerPlan] = None
                    batch_plan: Optional[ToolBatchPlan] = None
                    effective_calls = requested_calls
                    if requested_calls > 1:
                        planner_plan = self._plan_tool_calls(
                            tool_calls,
                            max_parallel_calls=max_parallel_tool_calls,
                        )
                        batch_plan = planner_plan.batch_plan
                        effective_calls = max(0, int(batch_plan.unique_calls))
                        if planner_plan.used_dependency_scheduler:
                            self.trajectory_store.add_event(
                                trajectory_id,
                                stage="plan",
                                action="tool_planner",
                                payload={
                                    "dependency_edges": int(planner_plan.dependency_edges),
                                    "dependency_levels": int(len(planner_plan.dependency_levels)),
                                    "cycle_detected": bool(planner_plan.cycle_detected),
                                },
                            )
                    remaining_calls = max(0, max_tool_calls_per_turn - tool_calls_executed)
                    if remaining_calls <= 0 or effective_calls > remaining_calls:
                        final_content = self._build_tool_call_limit_message(
                            lang=reply_language,
                            limit=max_tool_calls_per_turn,
                            executed=tool_calls_executed,
                            requested=effective_calls,
                        )
                        trajectory_status = "tool_error"
                        self.trajectory_store.add_event(
                            trajectory_id,
                            stage="act",
                            action="tool_limit_blocked",
                            payload={
                                "limit": max_tool_calls_per_turn,
                                "executed": tool_calls_executed,
                                "requested": requested_calls,
                                "effective_requested": effective_calls,
                            },
                        )
                        break
                    if len(tool_calls) > 1:
                        for tc in tool_calls:
                            call_payload = self._build_tool_call_payload(tc)
                            self.trajectory_store.add_event(
                                trajectory_id,
                                stage="act",
                                action="tool_call",
                                payload=call_payload,
                            )
                            await self._emit_tool_call_stream_event(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                event_type="call",
                                payload=call_payload,
                            )
                        results, executed_plan = await self._execute_tool_calls_with_batching(
                            tool_calls,
                            max_tier=tool_max_tier,
                            policy=tool_policy,
                            retry_budget=retry_budget,
                            sender_id=msg.sender_id,
                            channel=msg.channel,
                            session_key=session_key,
                            max_parallel_calls=max_parallel_tool_calls,
                            plan=batch_plan,
                        )
                        tool_calls_executed += max(0, int(executed_plan.unique_calls))
                        turn_tool_rounds += max(0, int(executed_plan.actual_rounds))
                        turn_parallel_rounds += max(0, int(executed_plan.parallel_rounds))
                        turn_tool_calls_requested += max(0, int(executed_plan.requested_calls))
                        turn_tool_calls_executed += max(0, int(executed_plan.unique_calls))
                        turn_tool_deduped += max(0, int(executed_plan.deduped_calls))
                        turn_batch_groups += max(0, int(executed_plan.batch_groups))
                        for tc, result in zip(tool_calls, results):
                            result_payload = self._build_tool_result_payload(tc, str(result))
                            self.trajectory_store.add_event(
                                trajectory_id,
                                stage="act",
                                action="tool_result",
                                payload=result_payload,
                            )
                            await self._emit_tool_call_stream_event(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                event_type="result",
                                payload=result_payload,
                            )
                            if self._turn_hooks:
                                await self._turn_hooks.emit_after_tool_result(
                                    {
                                        "session_key": session_key,
                                        "channel": msg.channel,
                                        "chat_id": msg.chat_id,
                                        "sender_id": msg.sender_id,
                                        "run_id": trajectory_id,
                                        "tool_name": tc.name,
                                        "tool_call_id": tc.id,
                                        "tool_arguments": getattr(tc, "arguments", {}),
                                        "tool_result": str(result),
                                        "result_payload": result_payload,
                                    }
                                )
                            if result_payload.get("status") == "error":
                                fp = self._tool_failure_fingerprint(tc, str(result))
                                tool_failure_counts[fp] = tool_failure_counts.get(fp, 0) + 1
                                recent_tool_failures.append(result_payload)
                                if len(recent_tool_failures) > 6:
                                    recent_tool_failures = recent_tool_failures[-6:]
                                code = str(result_payload.get("error_code", "") or "")
                                if (
                                    tool_failure_counts[fp] >= abort_after_repeats
                                    and code
                                    and self._should_abort_on_repeat(code)
                                ):
                                    final_content = self._build_tool_repeat_abort_message(
                                        lang=reply_language, failures=recent_tool_failures
                                    )
                                    trajectory_status = "tool_error"
                                    break
                            replan_hint = self._build_replan_hint(tool_name=tc.name, tool_result=str(result))
                            if replan_hint:
                                self.trajectory_store.add_event(
                                    trajectory_id,
                                    stage="plan",
                                    action="replan_hint",
                                    payload={"tool": tc.name, "hint": replan_hint[:240]},
                                )
                                messages.append({"role": "system", "content": replan_hint})
                            if self._is_confirmation_required_result(result):
                                try:
                                    pending_params = self._normalize_tool_arguments(
                                        getattr(tc, "arguments", {})
                                    )
                                except ValueError:
                                    pending_params = {}
                                self._pending_confirmations[session_key] = {
                                    "name": tc.name,
                                    "params": pending_params,
                                }
                                confirmation_prompt = self._build_pending_confirmation_prompt(tc.name)
                                logger.info("Queued pending confirmation for tool: %s", tc.name)
                                break
                            pending_media.extend(self._extract_media(result))
                            compacted = self._compact_tool_result_for_context(
                                tool_name=tc.name,
                                result=result,
                            )
                            messages = self.context.add_tool_result(messages, tc.id, tc.name, compacted)
                        if final_content is not None:
                            break
                    else:
                        if tool_calls:
                            turn_tool_rounds += 1
                            turn_batch_groups += 1
                            turn_tool_calls_requested += len(tool_calls)
                        for tool_call in tool_calls:
                            if self._cancel_token and self._cancel_token.is_cancelled:
                                logger.info("Skipping remaining tool calls (cancelled).")
                                break
                            if tool_calls_executed >= max_tool_calls_per_turn:
                                final_content = self._build_tool_call_limit_message(
                                    lang=reply_language,
                                    limit=max_tool_calls_per_turn,
                                    executed=tool_calls_executed,
                                    requested=1,
                                )
                                trajectory_status = "tool_error"
                                self.trajectory_store.add_event(
                                    trajectory_id,
                                    stage="act",
                                    action="tool_limit_blocked",
                                    payload={
                                        "limit": max_tool_calls_per_turn,
                                        "executed": tool_calls_executed,
                                        "requested": 1,
                                    },
                                )
                                break
                            logger.info("Executing tool: %s", tool_call.name)
                            call_payload = self._build_tool_call_payload(tool_call)
                            self.trajectory_store.add_event(
                                trajectory_id,
                                stage="act",
                                action="tool_call",
                                payload=call_payload,
                            )
                            await self._emit_tool_call_stream_event(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                event_type="call",
                                payload=call_payload,
                            )
                            result = await self._execute_single_tool_call(
                                tool_call,
                                max_tier=tool_max_tier,
                                policy=tool_policy,
                                retry_budget=retry_budget,
                                sender_id=msg.sender_id,
                                channel=msg.channel,
                                session_key=session_key,
                            )
                            tool_calls_executed += 1
                            turn_tool_calls_executed += 1
                            result_payload = self._build_tool_result_payload(tool_call, str(result))
                            self.trajectory_store.add_event(
                                trajectory_id,
                                stage="act",
                                action="tool_result",
                                payload=result_payload,
                            )
                            await self._emit_tool_call_stream_event(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                event_type="result",
                                payload=result_payload,
                            )
                            if self._turn_hooks:
                                await self._turn_hooks.emit_after_tool_result(
                                    {
                                        "session_key": session_key,
                                        "channel": msg.channel,
                                        "chat_id": msg.chat_id,
                                        "sender_id": msg.sender_id,
                                        "run_id": trajectory_id,
                                        "tool_name": tool_call.name,
                                        "tool_call_id": tool_call.id,
                                        "tool_arguments": getattr(tool_call, "arguments", {}),
                                        "tool_result": str(result),
                                        "result_payload": result_payload,
                                    }
                                )
                            if result_payload.get("status") == "error":
                                fp = self._tool_failure_fingerprint(tool_call, str(result))
                                tool_failure_counts[fp] = tool_failure_counts.get(fp, 0) + 1
                                recent_tool_failures.append(result_payload)
                                if len(recent_tool_failures) > 6:
                                    recent_tool_failures = recent_tool_failures[-6:]
                                code = str(result_payload.get("error_code", "") or "")
                                if (
                                    tool_failure_counts[fp] >= abort_after_repeats
                                    and code
                                    and self._should_abort_on_repeat(code)
                                ):
                                    final_content = self._build_tool_repeat_abort_message(
                                        lang=reply_language, failures=recent_tool_failures
                                    )
                                    trajectory_status = "tool_error"
                                    break
                            extracted = self._extract_media(result)
                            if extracted:
                                logger.info("Extracted media from %s: %s", tool_call.name, extracted)
                            pending_media.extend(extracted)
                            if self._is_confirmation_required_result(result):
                                try:
                                    pending_params = self._normalize_tool_arguments(
                                        getattr(tool_call, "arguments", {})
                                    )
                                except ValueError:
                                    pending_params = {}
                                self._pending_confirmations[session_key] = {
                                    "name": tool_call.name,
                                    "params": pending_params,
                                }
                                confirmation_prompt = self._build_pending_confirmation_prompt(
                                    tool_call.name
                                )
                                logger.info("Queued pending confirmation for tool: %s", tool_call.name)
                                break
                            compacted = self._compact_tool_result_for_context(
                                tool_name=tool_call.name,
                                result=result,
                            )
                            messages = self.context.add_tool_result(
                                messages, tool_call.id, tool_call.name, compacted
                            )
                            replan_hint = self._build_replan_hint(
                                tool_name=tool_call.name,
                                tool_result=str(result),
                            )
                            if replan_hint:
                                self.trajectory_store.add_event(
                                    trajectory_id,
                                    stage="plan",
                                    action="replan_hint",
                                    payload={"tool": tool_call.name, "hint": replan_hint[:240]},
                                )
                                messages.append({"role": "system", "content": replan_hint})
                        if final_content is not None:
                            break
                    if confirmation_prompt:
                        final_content = confirmation_prompt
                        break
                else:
                    # No tool calls — but check if LLM is faking an action
                    content = response.content or ""
                    if self._is_fake_tool_call(content) and iteration < 3:
                        # LLM claimed to do something without calling tools - retry with correction
                        logger.warning(
                            "Detected fake tool call in response, retrying (iter=%d)",
                            iteration,
                        )
                        messages = self.context.add_assistant_message(messages, content, None)
                        messages.append(
                            {
                                "role": "user",
                                "content": self._msg(reply_language, "fake_tool_retry"),
                            }
                        )
                        continue

                    final_content = content
                    if final_content:
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=final_content,
                                is_partial=True,
                            )
                        )
                    break
            if final_content is None:
                logger.warning(
                    "Agent reached max iterations (%d); generating finalization response.",
                    self.max_iterations,
                )
                final_content = await self._build_iteration_limit_final_response(
                    messages=messages,
                    retry_budget=retry_budget,
                    reply_language=reply_language,
                    recent_tool_failures=recent_tool_failures,
                )
                trajectory_status = "max_iterations"
        except Exception as exc:
            trajectory_status = "error"
            error_message = str(exc)
            raise
        finally:
            persona_signal: Optional[Dict[str, Any]] = None
            if final_content:
                try:
                    final_content, persona_signal = self._apply_persona_runtime_guard(
                        content=final_content,
                        reply_language=reply_language,
                        run_id=trajectory_id,
                        channel=msg.channel,
                    )
                except Exception as guard_exc:
                    logger.warning("Persona runtime guard failed: %s", guard_exc)
            if persona_signal is not None:
                self.trajectory_store.add_event(
                    trajectory_id,
                    stage="persona",
                    action="runtime_guard",
                    payload={
                        "level": str(persona_signal.get("level", "")),
                        "violation_count": int(persona_signal.get("violation_count", 0) or 0),
                        "violations": list(persona_signal.get("violations", [])),
                        "correction_applied": bool(persona_signal.get("correction_applied", False)),
                        "correction_strategy": str(persona_signal.get("correction_strategy", "")),
                        "drift_score": persona_signal.get("drift_score", 0.0),
                    },
                )
            if final_content is None and trajectory_status == "success":
                trajectory_status = "incomplete"
            self.tool_batching_tracker.record_turn(
                total_tokens=turn_total_tokens,
                tool_rounds=turn_tool_rounds,
                parallel_rounds=turn_parallel_rounds,
                tool_calls_requested=turn_tool_calls_requested,
                tool_calls_executed=turn_tool_calls_executed,
                deduped_calls=turn_tool_deduped,
                batch_groups=turn_batch_groups,
            )
            self.trajectory_store.finalize(
                trajectory_id,
                status=trajectory_status,
                final_content=final_content or error_message or "",
                usage=self.usage.summary(),
                metrics={
                    "iterations": iteration,
                    "overflow_retries": overflow_retries,
                    "retry_budget_remaining": retry_budget.remaining,
                    "tool_rounds": turn_tool_rounds,
                    "tool_calls_requested": turn_tool_calls_requested,
                    "tool_calls_executed": turn_tool_calls_executed,
                    "tool_deduped_calls": turn_tool_deduped,
                    "parallel_rounds": turn_parallel_rounds,
                    "batch_groups": turn_batch_groups,
                    "tokens_this_turn": turn_total_tokens,
                    "turn_latency_ms": round((asyncio.get_running_loop().time() - turn_started) * 1000.0, 2),
                },
            )
            self._active_provider_override = None
            self._active_model_override = None
            self._tool_policy_model_provider = ""
            self._tool_policy_model_name = ""
            self._prompt_cache_scope = {}

        if final_content is None:
            final_content = self._msg(reply_language, "iteration_limit_fallback")
        
        # --- Typing indicator: stop ---
        await self.bus.publish_typing(TypingEvent(
            channel=msg.channel, chat_id=msg.chat_id, is_typing=False,
        ))

        # Save to local cache (optional, mostly for short term context matching if not using DB)
        self._update_history(session_key, "user", msg.content)
        self._update_history(session_key, "assistant", final_content)
        persist_ok = await self._persist_turn_memory(msg, final_content)
        await self._emit_after_turn_hook(
            {
                "session_key": session_key,
                "channel": msg.channel,
                "chat_id": msg.chat_id,
                "sender_id": msg.sender_id,
                "run_id": trajectory_id,
                "status": trajectory_status,
                "memory_context_chars": memory_context_chars,
                "recall_count": recall_count,
                "persist_ok": persist_ok,
                "tool_calls_executed": turn_tool_calls_executed,
            }
        )
        
        if pending_media:
            logger.info(f"Sending response with media: {pending_media}")
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            media=pending_media,
        )

