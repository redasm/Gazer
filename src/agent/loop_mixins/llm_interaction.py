"""AgentLoop mixin: Llm Interaction.

Extracted from loop.py to reduce file size.
Contains 13 methods.
"""

from __future__ import annotations

from agent.constants import *  # noqa: F403
from llm.base import LLMResponse
from runtime.resilience import RetryBudget, classify_error_message
from bus.events import InboundMessage
import asyncio
import logging
import time
logger = logging.getLogger('AgentLoop')

from typing import TYPE_CHECKING
from soul.models import ModelRegistry
from soul.persona_runtime import get_persona_runtime_manager

if TYPE_CHECKING:
    pass  # Add type imports as needed


class LLMInteractionMixin:
    """Mixin providing llm interaction functionality."""

    def _get_tool_timeout_seconds(self) -> float:
        """Return per-tool timeout seconds from config."""
        from runtime.config_manager import config as _cfg

        raw = _cfg.get("security.tool_call_timeout_seconds", DEFAULT_TOOL_CALL_TIMEOUT_SECONDS)
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            timeout = DEFAULT_TOOL_CALL_TIMEOUT_SECONDS
        return timeout if timeout > 0 else DEFAULT_TOOL_CALL_TIMEOUT_SECONDS

    @staticmethod
    def _get_turn_timeout_seconds() -> float:
        """Return per-message processing timeout seconds from config."""
        from runtime.config_manager import config as _cfg

        raw = _cfg.get("security.turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS)
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            timeout = DEFAULT_TURN_TIMEOUT_SECONDS
        return timeout if timeout > 0 else DEFAULT_TURN_TIMEOUT_SECONDS

    @staticmethod
    def _apply_persona_runtime_guard(
        *,
        content: str,
        reply_language: str,
        run_id: str,
        channel: str,
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        from runtime.config_manager import config as _cfg

        runtime_cfg = _cfg.get("personality.runtime", {}) or {}
        if not isinstance(runtime_cfg, dict) or not bool(runtime_cfg.get("enabled", True)):
            return content, None
        signals_cfg = runtime_cfg.get("signals", {}) or {}
        if not isinstance(signals_cfg, dict):
            signals_cfg = {}
        auto_cfg = runtime_cfg.get("auto_correction", {}) or {}
        if not isinstance(auto_cfg, dict):
            auto_cfg = {}
        trigger_levels_raw = auto_cfg.get("trigger_levels", ["critical"])
        trigger_levels = (
            [str(item).strip().lower() for item in trigger_levels_raw if str(item).strip()]
            if isinstance(trigger_levels_raw, list)
            else ["critical"]
        )
        retain_raw = signals_cfg.get("retain", 500)
        try:
            retain = max(50, min(int(retain_raw), 5000))
        except (TypeError, ValueError):
            retain = 500

        manager = get_persona_runtime_manager()
        kwargs: Dict[str, Any] = {
            "content": str(content or ""),
            "source": "agent_loop",
            "run_id": str(run_id or ""),
            "language": str(reply_language or _LANG_DEFAULT),
            "auto_correct_enabled": bool(auto_cfg.get("enabled", False)),
            "strategy": str(auto_cfg.get("strategy", "rewrite")).strip().lower() or "rewrite",
            "trigger_levels": trigger_levels,
            "metadata": {"channel": str(channel or "")},
            "retain": retain,
            "ab_config": auto_cfg.get("ab", {}) if isinstance(auto_cfg.get("ab", {}), dict) else {},
            "assignment_key": f"{channel}:{run_id}",
        }
        try:
            processed = manager.process_output(**kwargs)
        except TypeError:
            kwargs.pop("ab_config", None)
            kwargs.pop("assignment_key", None)
            processed = manager.process_output(**kwargs)
        signal = processed.get("signal") if isinstance(processed.get("signal"), dict) else None
        return str(processed.get("final_content", content or "")), signal

    @staticmethod
    def _get_llm_retry_settings() -> tuple[int, float]:
        """Return (max_retries, backoff_seconds) for LLM calls."""
        from runtime.config_manager import config as _cfg

        raw_retries = _cfg.get("security.llm_max_retries", DEFAULT_LLM_MAX_RETRIES)
        raw_backoff = _cfg.get("security.llm_retry_backoff_seconds", DEFAULT_LLM_RETRY_BACKOFF_SECONDS)
        try:
            retries = int(raw_retries)
        except (TypeError, ValueError):
            retries = DEFAULT_LLM_MAX_RETRIES
        retries = min(max(retries, 0), 5)
        try:
            backoff = float(raw_backoff)
        except (TypeError, ValueError):
            backoff = DEFAULT_LLM_RETRY_BACKOFF_SECONDS
        backoff = backoff if backoff >= 0 else DEFAULT_LLM_RETRY_BACKOFF_SECONDS
        return retries, backoff

    @staticmethod
    def _get_tool_retry_settings() -> tuple[int, float]:
        """Return (max_retries, backoff_seconds) for tool calls."""
        from runtime.config_manager import config as _cfg

        raw_retries = _cfg.get("security.tool_retry_max", DEFAULT_TOOL_RETRY_MAX)
        raw_backoff = _cfg.get("security.tool_retry_backoff_seconds", DEFAULT_TOOL_RETRY_BACKOFF_SECONDS)
        try:
            retries = int(raw_retries)
        except (TypeError, ValueError):
            retries = DEFAULT_TOOL_RETRY_MAX
        retries = min(max(retries, 0), 5)
        try:
            backoff = float(raw_backoff)
        except (TypeError, ValueError):
            backoff = DEFAULT_TOOL_RETRY_BACKOFF_SECONDS
        backoff = backoff if backoff >= 0 else DEFAULT_TOOL_RETRY_BACKOFF_SECONDS
        return retries, backoff

    @staticmethod
    def _resolve_active_provider_agents_defaults() -> Dict[str, Any]:
        provider_name, _ = ModelRegistry.resolve_model_ref("slow_brain")
        if not provider_name:
            return {}
        provider_cfg = ModelRegistry.get_provider_config(provider_name)
        if not isinstance(provider_cfg, dict):
            return {}
        agents_cfg = provider_cfg.get("agents")
        if not isinstance(agents_cfg, dict):
            return {}
        defaults_cfg = agents_cfg.get("defaults")
        return defaults_cfg if isinstance(defaults_cfg, dict) else {}

    @staticmethod
    def _build_retry_budget() -> RetryBudget:
        from runtime.config_manager import config as _cfg

        raw = _cfg.get("security.retry_budget_total", DEFAULT_RETRY_BUDGET_TOTAL)
        try:
            total = int(raw)
        except (TypeError, ValueError):
            total = DEFAULT_RETRY_BUDGET_TOTAL
        return RetryBudget.from_total(total)

    @staticmethod
    def _resolve_llm_provider_key(provider: Optional[LLMProvider]) -> str:
        if provider is None:
            return ""
        explicit = str(getattr(provider, "provider_name", "") or "").strip().lower()
        if explicit:
            return explicit
        cls_name = type(provider).__name__.strip().lower()
        if cls_name.endswith("provider") and len(cls_name) > len("provider"):
            cls_name = cls_name[: -len("provider")]
        return cls_name

    async def _call_llm_with_retries(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: Optional[str],
        call_name: str,
        retry_budget: RetryBudget,
    ) -> LLMResponse:
        """Call the LLM with retry for transient transport/provider failures."""
        max_retries, backoff_seconds = self._get_llm_retry_settings()
        attempts = max_retries + 1
        last_exc: Optional[Exception] = None
        last_error_response: Optional[LLMResponse] = None
        budget_exhausted = False

        for attempt in range(1, attempts + 1):
            try:
                selected_provider = self._active_provider_override or self.provider
                selected_model = model
                if self._active_model_override and (model is None or model == self.model):
                    selected_model = self._active_model_override
                provider_key = self._resolve_llm_provider_key(selected_provider)
                if provider_key:
                    self._tool_policy_model_provider = provider_key
                if selected_model:
                    self._tool_policy_model_name = str(selected_model).strip().lower()
                self.prompt_cache.observe(
                    messages=messages,
                    tools=tools,
                    model=selected_model,
                    scope=self._prompt_cache_scope,
                )
                response = await selected_provider.chat(
                    messages=messages,
                    tools=tools,
                    model=selected_model,
                )
                resolved_model = str(response.model or selected_model or "").strip().lower()
                if resolved_model:
                    self._tool_policy_model_name = resolved_model
                if not response.error:
                    return response

                last_error_response = response
                if self._is_context_overflow(response):
                    return response
                error_kind = classify_error_message(response.content or "")
                if error_kind != "retryable":
                    return response
                if attempt >= attempts:
                    return response
                if not retry_budget.consume(1):
                    return LLMResponse(
                        content="Retry budget exhausted before LLM recovery.",
                        finish_reason="error",
                        error=True,
                    )
                logger.warning(
                    "%s failed with provider error (attempt %d/%d): %s",
                    call_name,
                    attempt,
                    attempts,
                    response.content or "unknown_error",
                )
            except Exception as exc:
                last_exc = exc
                error_kind = classify_error_message(str(exc))
                if error_kind != "retryable":
                    return LLMResponse(
                        content=f"{call_name} failed: {exc}",
                        finish_reason="error",
                        error=True,
                    )
                if attempt >= attempts:
                    break
                if not retry_budget.consume(1):
                    budget_exhausted = True
                    break
                logger.warning(
                    "%s raised exception (attempt %d/%d): %s",
                    call_name,
                    attempt,
                    attempts,
                    exc,
                )
            if backoff_seconds > 0:
                await asyncio.sleep(backoff_seconds * attempt)

        if last_error_response is not None:
            return last_error_response
        if budget_exhausted:
            return LLMResponse(
                content="Retry budget exhausted before LLM recovery.",
                finish_reason="error",
                error=True,
            )
        return LLMResponse(
            content=f"LLM request failed after {attempts} attempts: {last_exc or 'unknown error'}",
            finish_reason="error",
            error=True,
        )

    async def _build_iteration_limit_final_response(
        self,
        *,
        messages: List[Dict[str, Any]],
        retry_budget: RetryBudget,
        reply_language: str,
        recent_tool_failures: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Generate a deterministic final response when iteration limit is reached."""
        diag = self._format_recent_tool_failures(
            lang=reply_language,
            failures=list(recent_tool_failures or []),
        )
        finalize_messages = [
            *messages,
            {
                "role": "system",
                "content": self._msg(reply_language, "iteration_finalize_system"),
            },
        ]
        if diag:
            finalize_messages.append(
                {
                    "role": "system",
                    "content": f"Diagnostic context (do not omit):\n{diag}",
                }
            )
        try:
            response = await self._call_llm_with_retries(
                messages=finalize_messages,
                tools=[],
                model=self.model,
                call_name="Iteration-limit finalization",
                retry_budget=retry_budget,
            )
            content = str(response.content or "").strip()
            if not response.error and content:
                return content
        except Exception as exc:
            logger.warning("Iteration-limit finalization failed: %s", exc)
        fallback = self._msg(reply_language, "iteration_limit_fallback")
        if diag:
            return f"{fallback}\n{diag}".strip()
        return fallback

    async def _try_fast_brain(self, msg: InboundMessage) -> Optional[str]:
        """Attempt to handle simple messages with the fast_brain model.

        Returns a response string if handled, or None to fall through
        to the full agent loop.

        NOTE: The fast_brain is typically a vision model (e.g. qwen-vl-plus).
        It should NOT be used for pure-text conversations -- those go through
        the slow_brain which is better at language and reasoning.
        """
        if not self._fast_provider or not self._fast_model:
            return None

        # Fast brain is a vision model; only use it when media is present.
        # Pure-text messages (even simple greetings) should go through the
        # slow brain for better conversational quality.
        if not msg.media:
            return None

        content = msg.content.strip()

        # Only use fast brain for short messages with media
        if len(content) > FAST_BRAIN_MAX_LENGTH:
            return None

        logger.info("Fast brain handling: %r", content[:30])
        try:
            session_key = msg.session_key
            history = self._get_history(session_key)
            # Build a minimal prompt (no tools, no skills, no memory lookup)
            system_prompt = self.context.build_system_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                *history[-6:],  # last few messages for context
                {"role": "user", "content": content},
            ]
            response = await self._fast_provider.chat(
                messages=messages, tools=[], model=self._fast_model,
            )
            # Log fast brain call for LLM history tracking
            if response.request_id:
                logger.info(
                    "Fast brain call: model=%s request_id=%s tokens=%s",
                    response.model, response.request_id,
                    response.usage.get("total_tokens", "?"),
                    extra={
                        "request_id": response.request_id,
                        "model": response.model,
                        "tokens": response.usage,
                    },
                )
            if response.usage:
                self.usage.add(response.usage, model=response.model or "")
            if response.content and not response.has_tool_calls:
                return response.content
        except Exception as e:
            logger.warning("Fast brain failed, falling through: %s", e)

        return None

    @staticmethod
    def _is_context_overflow(response) -> bool:
        """Detect if the LLM returned a context-length-exceeded error."""
        if not response.error:
            return False
        content = (response.content or "").lower()
        overflow_markers = [
            "context_length_exceeded",
            "context length",
            "maximum context",
            "token limit",
            "too many tokens",
            "max_tokens",
            "reduce the length",
        ]
        return any(m in content for m in overflow_markers)

    @staticmethod
    def _compact_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compact messages to fit within context window.

        Strategy:
        1. Keep the system prompt (index 0).
        2. Keep the last 6 messages.
        3. Truncate large tool results in the middle (head+tail preservation).
        4. Drop the oldest middle messages until under budget.
        """
        if len(messages) <= 8:
            return messages  # nothing to compact

        keep_start = 1  # system prompt
        keep_end = 6    # recent messages
        head = messages[:keep_start]
        tail = messages[-keep_end:]
        middle = messages[keep_start:-keep_end]

        # Phase 1: Truncate large tool results (preserve head + tail of output)
        MAX_TOOL_CHARS = 1500
        HEAD_CHARS = 500
        TAIL_CHARS = 500
        compacted_middle: List[Dict[str, Any]] = []
        for msg in middle:
            content = msg.get("content", "")
            if msg.get("role") == "tool" and isinstance(content, str) and len(content) > MAX_TOOL_CHARS:
                trimmed = (
                    content[:HEAD_CHARS]
                    + f"\n\n[...{len(content) - HEAD_CHARS - TAIL_CHARS} chars omitted...]\n\n"
                    + content[-TAIL_CHARS:]
                )
                msg = {**msg, "content": trimmed}
            compacted_middle.append(msg)

        # Phase 2: Drop oldest messages from middle until small enough
        total_dropped = 0
        if len(compacted_middle) > 10:
            drop_count = len(compacted_middle) // 2
            total_dropped = drop_count
            compacted_middle = compacted_middle[drop_count:]

        # Insert a summary placeholder
        summary = {
            "role": "system",
            "content": f"[Context compacted: {total_dropped} older messages removed to fit context window.]",
        }
        result = head + [summary] + compacted_middle + tail
        logger.info("Context compacted: %s -> %s messages (%s dropped).", len(messages), len(result), total_dropped)
        return result

