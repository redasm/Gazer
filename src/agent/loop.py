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
    ProcessMessageMixin,
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
    FAST_BRAIN_PATTERNS,
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
    ProcessMessageMixin,
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
                    logger.warning("Rate limited: %s", rate_key)
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
                    logger.error("Error processing message: %s", e, exc_info=True)
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=self._msg(reply_language, "runtime_error", error=str(e)),
                    ))
            except Exception as e:
                logger.error("Critical error in agent loop: %s", e, exc_info=True)
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
        logger.info("Session reset: %s", session_key)

    async def _process_message(self, msg: InboundMessage) -> Optional[OutboundMessage]:
        """Process a single inbound message.

        Orchestrates turn initialisation, early-exit checks (channel commands,
        soul callbacks, fast-brain), LLM↔tool loop, and turn finalisation.
        The heavy lifting is delegated to methods in ``ProcessMessageMixin``.
        """
        logger.info("Processing message from %s:%s", msg.channel, msg.sender_id)
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

        # Session reset trigger
        if msg.metadata.get("_reset_session"):
            self.reset_session(session_key)
            logger.info("Session reset via channel trigger: %s", session_key)

        # Typing indicator: start
        await self.bus.publish_typing(TypingEvent(
            channel=msg.channel, chat_id=msg.chat_id, is_typing=True,
        ))

        # --- Early returns ---
        parsed_command = self.channel_command_registry.parse(msg.content)
        if parsed_command is not None:
            return await self._handle_channel_command_impl(msg, session_key, parsed_command)

        if self._soul_turn_callback is not None:
            return await self._handle_soul_turn_impl(
                msg=msg, session_key=session_key, reply_language=reply_language,
                trajectory_id=trajectory_id, turn_started=turn_started,
            )

        # Fast brain shortcut
        fast_response = await self._try_fast_brain(msg)
        if fast_response is not None:
            await self.bus.publish_typing(TypingEvent(
                channel=msg.channel, chat_id=msg.chat_id, is_typing=False,
            ))
            self._update_history(session_key, "user", msg.content)
            self._update_history(session_key, "assistant", fast_response)
            persist_ok = await self._persist_turn_memory(msg, fast_response)
            await self._emit_after_turn_hook({
                "session_key": session_key, "channel": msg.channel,
                "chat_id": msg.chat_id, "sender_id": msg.sender_id,
                "status": "fast_brain", "memory_context_chars": 0,
                "recall_count": 0, "persist_ok": persist_ok,
            })
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=fast_response,
            )

        # --- Main turn ---
        ctx = await self._build_turn_context(msg, session_key, reply_language, trajectory_id)
        final_content = None
        pending_media: list[str] = []
        try:
            final_content, trajectory_status, pending_media = await self._execute_llm_turns(
                msg, ctx, trajectory_id, reply_language, session_key,
            )
        except Exception as exc:
            trajectory_status = "error"
            error_message = str(exc)
            raise
        finally:
            turn_metrics = getattr(self, "_last_turn_metrics", None) or {}
            finalize_result = await self._finalize_turn(
                msg=msg,
                session_key=session_key,
                trajectory_id=trajectory_id,
                reply_language=reply_language,
                turn_started=turn_started,
                final_content=final_content,
                trajectory_status=trajectory_status,
                error_message=error_message,
                pending_media=pending_media,
                memory_context_chars=ctx.memory_context_chars,
                recall_count=ctx.recall_count,
                turn_tool_calls_executed=turn_metrics.get("tool_calls_executed", 0),
            )
        return finalize_result



