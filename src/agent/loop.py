"""Agent loop: the core processing engine."""

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, List, Dict

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
from agent.usage_tracker import UsageTracker
from tools.base import CancellationToken
from tools.batching import ToolBatchPlan, ToolBatchPlanner, ToolBatchingTracker
from tools.planner import ToolPlanner, ToolPlannerPlan
from tools.media_marker import MEDIA_MARKER
from tools.registry import ToolRegistry, ToolPolicy, normalize_tool_policy
from agent.session_store import SessionStore
from agent.trajectory import TrajectoryStore
from eval.benchmark import EvalBenchmarkManager
from multi_agent.models import MultiAgentExecutionContext
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
from agent.context_engine import ContextEngine
from agent.context_engine_registry import ensure_context_engines_initialized

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
    TRUSTED_LOCAL_COMMAND_CHANNELS, _REPLAN_ERROR_HINTS,
)

from agent.constants import _LANG_MESSAGES


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
        auto_route_turn_callback: Optional[
            Callable[[InboundMessage, MultiAgentExecutionContext], Awaitable[Optional[str]]]
        ] = None,

    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self._base_model = model
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self._slow_provider_resolver = slow_provider_resolver
        self._persist_turn_callback = persist_turn_callback
        self._auto_route_turn_callback = auto_route_turn_callback

        self._turn_hooks = turn_hooks
        self._active_provider_override: Optional[LLMProvider] = None
        self._active_model_override: Optional[str] = None
        
        # Fast brain for simple conversational responses (greetings, acknowledgements)
        self._fast_provider = fast_provider
        self._fast_model = fast_model
        
        self.context = context_builder or ContextBuilder(workspace)
        self.tools = ToolRegistry()
        self._tool_policy_raw = dict(tool_policy or {})

        # Context engine (pluggable, defaults to legacy) -- id resolved below after _cfg
        ensure_context_engines_initialized()
        self._context_engine: Optional[ContextEngine] = None
        self._context_engine_id: str = "legacy"  # overridden below when _cfg is available

        # Persistent session store (single source of truth for history)
        self.session_store = SessionStore()
        self.trajectory_store = TrajectoryStore()
        self._eval_benchmark_manager = EvalBenchmarkManager()

        # Token usage tracking
        self.usage = UsageTracker()

        # Rate limiter: configurable via config, defaults to 20 req / 60s per sender
        from runtime.config_manager import config as _cfg
        self.agent_id: str = str(_cfg.get("agent.id", "") or "")
        self._context_engine_id = str(_cfg.get("models.context_engine", "legacy") or "legacy")
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
        # Per-turn accumulation of tool-emitted RenderHint objects. Reset at
        # the top of each _process_message call; tool_execution pushes into
        # this list after each successful tool execution. _finalize_turn
        # serializes them onto OutboundMessage.metadata["render_hints"].
        self._pending_render_hints: List[Any] = []
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

                # Hook: message:received + session:start (check before any processing)
                if self._turn_hooks:
                    _is_first_msg = not bool(self._get_history(msg.session_key))
                    await self._turn_hooks.emit_message_received({
                        "channel": msg.channel,
                        "chat_id": msg.chat_id,
                        "sender_id": msg.sender_id,
                        "session_key": msg.session_key,
                        "content_length": len(msg.content),
                    })
                    if _is_first_msg:
                        await self._turn_hooks.emit_session_start({
                            "channel": msg.channel,
                            "chat_id": msg.chat_id,
                            "sender_id": msg.sender_id,
                            "session_key": msg.session_key,
                            "agent_id": self.agent_id,
                        })

                # Rate limiting check
                rate_key = f"{msg.channel}:{msg.sender_id}"
                if not self._rate_limiter.allow(rate_key):
                    logger.warning("Rate limited: %s", rate_key)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="You're sending messages too quickly. Please wait a moment.",
                        reply_to=self._resolve_outbound_reply_to(msg),
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
                        # Hook: message:sent
                        if self._turn_hooks:
                            await self._turn_hooks.emit_message_sent({
                                "channel": response.channel,
                                "chat_id": response.chat_id,
                                "session_key": msg.session_key,
                                "content_length": len(response.content or ""),
                            })
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
                        TypingEvent(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            is_typing=False,
                            reply_to=self._resolve_outbound_reply_to(msg),
                        ),
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=self._msg(reply_language, "timeout", seconds=int(turn_timeout)),
                            reply_to=self._resolve_outbound_reply_to(msg),
                        )
                    )
                except Exception as e:
                    reply_language = self._detect_user_language(msg.content)
                    logger.error("Error processing message: %s", e, exc_info=True)
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=self._msg(reply_language, "runtime_error"),
                        reply_to=self._resolve_outbound_reply_to(msg),
                    ))
                finally:
                    # Hook: session:end (fires after every processed turn)
                    if self._turn_hooks:
                        _msg_count = len(self._get_history(msg.session_key))
                        await self._turn_hooks.emit_session_end({
                            "channel": msg.channel,
                            "chat_id": msg.chat_id,
                            "sender_id": msg.sender_id,
                            "session_key": msg.session_key,
                            "agent_id": self.agent_id,
                            "message_count": _msg_count,
                        })
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
        # Hook: session:reset (schedule as background task if loop is running)
        if self._turn_hooks:
            try:
                _loop = asyncio.get_running_loop()
                _loop.create_task(
                    self._turn_hooks.emit_session_reset({
                        "session_key": session_key,
                        "agent_id": self.agent_id,
                    })
                )
            except RuntimeError:
                pass  # No running event loop; skip hook

    async def get_context_engine(self) -> ContextEngine:
        """Return the active context engine, initialising it lazily on first call."""
        if self._context_engine is None:
            from agent.context_engine_registry import resolve_context_engine
            try:
                self._context_engine = await resolve_context_engine(self._context_engine_id)
                logger.info("Context engine initialised: %s", self._context_engine.info.id)
            except Exception as exc:
                logger.error(
                    "Failed to initialise context engine %r: %s; falling back to legacy.",
                    self._context_engine_id, exc,
                )
                from agent.legacy_context_engine import LegacyContextEngine
                self._context_engine = LegacyContextEngine(
                    session_store=self.session_store
                )
        return self._context_engine

    async def compact_session(self, session_key: str, force: bool = False) -> dict:
        """Explicitly compact a session's context (e.g., from an admin command)."""
        engine = await self.get_context_engine()
        result = await engine.compact(
            session_key=session_key,
            force=force,
        )
        return {
            "ok": result.ok,
            "compacted": result.compacted,
            "reason": result.reason,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "summary": result.summary,
        }

    async def _maybe_auto_route_turn(
        self,
        *,
        msg: InboundMessage,
        session_key: str,
        trajectory_id: str,
        reply_language: str,
        turn_started: float,
    ) -> Optional[OutboundMessage]:
        callback = self._auto_route_turn_callback
        if callback is None:
            return None

        try:
            execution_context = MultiAgentExecutionContext(
                tool_policy=self._resolve_tool_policy(),
                sender_id=str(msg.sender_id or "").strip(),
                channel=str(msg.channel or "").strip(),
                model_provider=self._tool_policy_model_provider,
                model_name=self._tool_policy_model_name,
                session_key=f"{session_key}:{trajectory_id}",
            )
            routed_content = await callback(msg, execution_context)
        except Exception:
            logger.warning("Auto-route callback failed; continuing with single-agent turn", exc_info=True)
            return None

        if routed_content is None:
            return None

        self.trajectory_store.add_event(
            trajectory_id,
            stage="dispatch",
            action="auto_route_multi_agent",
            payload={"mode": "multi_agent"},
        )
        self._last_turn_metrics = {}
        self._last_turn_batching = {}
        return await self._finalize_turn(
            msg=msg,
            session_key=session_key,
            trajectory_id=trajectory_id,
            reply_language=reply_language,
            turn_started=turn_started,
            final_content=str(routed_content),
            trajectory_status="success",
            error_message="",
            pending_media=[],
            memory_context_chars=0,
            recall_count=0,
            turn_tool_calls_executed=0,
        )

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
        # Fresh render-hint accumulator for this turn — stale hints from a
        # prior turn would otherwise leak into the next assistant message.
        self._pending_render_hints = []
        if self._slow_provider_resolver:
            try:
                resolved = self._slow_provider_resolver(msg, self.provider)
                if resolved is not None:
                    self._active_provider_override = resolved
                    if self._base_model is None:
                        self._active_model_override = resolved.get_default_model()
            except Exception as exc:
                logger.warning("Slow provider resolver failed; using default provider: %s", exc)

        # P16: restore per-session model override (set via /model override).
        # Applied after slow_provider_resolver so explicit user choices take precedence.
        try:
            _meta_provider, _meta_model = self.session_store.get_model_override(session_key)
            if _meta_model:
                self._active_model_override = _meta_model
                logger.debug(
                    "Restored session model override: provider=%s model=%s session=%s",
                    _meta_provider, _meta_model, session_key,
                )
        except Exception as exc:
            logger.warning("Failed to restore session model override: %s", exc)

        # Session reset trigger
        if msg.metadata.get("_reset_session"):
            self.reset_session(session_key)
            logger.info("Session reset via channel trigger: %s", session_key)

        # Typing indicator: start
        await self.bus.publish_typing(TypingEvent(
            channel=msg.channel,
            chat_id=msg.chat_id,
            is_typing=True,
            reply_to=self._resolve_outbound_reply_to(msg),
        ))

        # --- Early returns ---
        parsed_command = self.channel_command_registry.parse(msg.content)
        if parsed_command is not None:
            return await self._handle_channel_command_impl(msg, session_key, parsed_command)




        # Fast brain shortcut
        fast_response = await self._try_fast_brain(msg)
        if fast_response is not None:
            await self.bus.publish_typing(TypingEvent(
                channel=msg.channel,
                chat_id=msg.chat_id,
                is_typing=False,
                reply_to=self._resolve_outbound_reply_to(msg),
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
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=fast_response,
                reply_to=self._resolve_outbound_reply_to(msg),
            )

        auto_route_response = await self._maybe_auto_route_turn(
            msg=msg,
            session_key=session_key,
            trajectory_id=trajectory_id,
            reply_language=reply_language,
            turn_started=turn_started,
        )
        if auto_route_response is not None:
            return auto_route_response

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


