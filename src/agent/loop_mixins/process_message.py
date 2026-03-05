"""Mixin for Agent loop message processing."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bus.events import InboundMessage, OutboundMessage, TypingEvent
from tools.registry import ToolPolicy

logger = logging.getLogger(__name__)


@dataclass
class TurnContext:
    """Bag of state built once at the start of a turn and threaded through sub-methods."""

    messages: List[Dict[str, Any]]
    memory_context_chars: int = 0
    recall_count: int = 0
    sender_is_owner: bool = False
    tool_policy: Optional[ToolPolicy] = None
    retry_budget: Any = None  # RetryBudget
    max_tool_calls_per_turn: int = 0
    max_parallel_tool_calls: int = 0


@dataclass
class ToolResultOutcome:
    """Result of processing a single tool call result through the standard pipeline."""

    should_abort: bool = False
    abort_content: Optional[str] = None
    trajectory_status: str = ""


class ProcessMessageMixin:
    """Provides methods for breaking down _process_message."""

    # ------------------------------------------------------------------
    # 1) _build_turn_context  (prompt construction + tool governance)
    # ------------------------------------------------------------------
    async def _build_turn_context(
        self,
        msg: InboundMessage,
        session_key: str,
        reply_language: str,
        trajectory_id: str,
    ) -> TurnContext:
        """Build LLM messages and resolve tool governance for the turn.

        Extracts prompt construction, memory preparation, plan-then-execute
        pre-step, and tool policy / governance resolution.
        """
        # Prompt cache scope
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

        # Memory pre-fetch
        if hasattr(self.context, "prepare_memory_context"):
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

        # Plan-then-Execute pre-step
        retry_budget = self._build_retry_budget()
        if self._should_plan(msg.content, history_len=len(history)):
            plan = await self._generate_plan(messages, retry_budget=retry_budget)
            if plan:
                messages = self.context.add_assistant_message(messages, f"## Plan\n{plan}", None)
                logger.info("Plan generated for complex task.")

        # Tool governance
        sender_is_owner = self._is_sender_owner(msg)
        tool_policy = self._resolve_tool_policy()
        max_tool_calls_per_turn, max_parallel_tool_calls = self._get_tool_governance_limits()

        return TurnContext(
            messages=messages,
            memory_context_chars=memory_context_chars,
            recall_count=recall_count,
            sender_is_owner=sender_is_owner,
            tool_policy=tool_policy,
            retry_budget=retry_budget,
            max_tool_calls_per_turn=max_tool_calls_per_turn,
            max_parallel_tool_calls=max_parallel_tool_calls,
        )

    # ------------------------------------------------------------------
    # 2) _process_single_tool_result  (deduplicated tool result pipeline)
    # ------------------------------------------------------------------
    async def _process_single_tool_result(
        self,
        tc: Any,
        result: Any,
        *,
        msg: InboundMessage,
        session_key: str,
        trajectory_id: str,
        reply_language: str,
        messages: List[Dict[str, Any]],
        recent_tool_failures: List[Dict[str, Any]],
        tool_failure_counts: Dict[str, int],
        abort_after_repeats: int,
        pending_media: List[str],
    ) -> Tuple[List[Dict[str, Any]], ToolResultOutcome]:
        """Run the standard tool-result processing pipeline for one call.

        Handles trajectory event logging, stream events, after-tool hook,
        failure tracking, replan hints, media extraction, and message append.

        Returns:
            (updated_messages, outcome) — outcome.should_abort is True when
            a repeated-failure abort is warranted.
        """
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

        outcome = ToolResultOutcome()

        # Failure tracking
        if result_payload.get("status") == "error":
            fp = self._tool_failure_fingerprint(tc, str(result))
            tool_failure_counts[fp] = tool_failure_counts.get(fp, 0) + 1
            recent_tool_failures.append(result_payload)
            if len(recent_tool_failures) > 6:
                del recent_tool_failures[:-6]
            code = str(result_payload.get("error_code", "") or "")
            if (
                tool_failure_counts[fp] >= abort_after_repeats
                and code
                and self._should_abort_on_repeat(code)
            ):
                outcome.should_abort = True
                outcome.abort_content = self._build_tool_repeat_abort_message(
                    lang=reply_language, failures=recent_tool_failures
                )
                outcome.trajectory_status = "tool_error"

        # Replan hint
        replan_hint = self._build_replan_hint(tool_name=tc.name, tool_result=str(result))
        if replan_hint:
            self.trajectory_store.add_event(
                trajectory_id,
                stage="plan",
                action="replan_hint",
                payload={"tool": tc.name, "hint": replan_hint[:240]},
            )
            messages.append({"role": "system", "content": replan_hint})

        # Media extraction
        extracted = self._extract_media(result)
        if extracted:
            logger.info("Extracted media from %s: %s", tc.name, extracted)
        pending_media.extend(extracted)

        # Compact and append to context
        compacted = self._compact_tool_result_for_context(
            tool_name=tc.name,
            result=result,
        )
        messages = self.context.add_tool_result(messages, tc.id, tc.name, compacted)

        return messages, outcome

    # ------------------------------------------------------------------
    # 3) _execute_llm_turns  (the agent loop)
    # ------------------------------------------------------------------
    async def _execute_llm_turns(
        self,
        msg: InboundMessage,
        ctx: "TurnContext",
        trajectory_id: str,
        reply_language: str,
        session_key: str,
    ) -> Tuple[Optional[str], str, List[str]]:
        """Run the iterative LLM↔tool loop.

        Returns:
            (final_content, trajectory_status, pending_media)
        """
        from agent.constants import MAX_CONTEXT_OVERFLOW_RETRIES

        messages = ctx.messages
        iteration = 0
        final_content = None
        overflow_retries = 0
        pending_media: List[str] = []
        recent_tool_failures: List[Dict[str, Any]] = []
        tool_failure_counts: Dict[str, int] = {}
        abort_after_repeats = 2
        trajectory_status = "success"

        tool_calls_executed = 0
        turn_total_tokens = 0
        turn_tool_rounds = 0
        turn_parallel_rounds = 0
        turn_tool_calls_requested = 0
        turn_tool_calls_executed = 0
        turn_tool_deduped = 0
        turn_batch_groups = 0

        while iteration < self.max_iterations:
            iteration += 1

            # --- LLM call ---
            model_provider, model_name = self._current_tool_policy_model_context()
            tool_defs = self.tools.get_definitions(
                policy=ctx.tool_policy,
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
                    "Passing %d tools to LLM (sender_is_owner=%s policy=%s): %s",
                    len(tool_defs),
                    ctx.sender_is_owner,
                    bool(
                        ctx.tool_policy
                        and (
                            ctx.tool_policy.allow_names
                            or ctx.tool_policy.allow_providers
                            or ctx.tool_policy.deny_names
                            or ctx.tool_policy.deny_providers
                        )
                    ),
                    tool_names,
                )
            response = await self._call_llm_with_retries(
                messages=messages,
                tools=tool_defs,
                model=self.model,
                call_name="Agent LLM call",
                retry_budget=ctx.retry_budget,
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
            if response.usage:
                self.usage.add(response.usage, model=response.model or "")
                turn_total_tokens += int(response.usage.get("total_tokens", 0) or 0)

            # --- Context overflow ---
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
                continue

            if response.error and not response.has_tool_calls:
                logger.warning("LLM call returned error: %s", response.content or "unknown_error")
                final_content = self._msg(
                    reply_language,
                    "llm_error",
                    detail=(response.content or "unknown error"),
                )
                trajectory_status = "llm_error"
                break

            # --- Handle tool calls ---
            logger.info(
                "LLM response: has_tool_calls=%s, finish=%s, content_len=%d",
                response.has_tool_calls,
                response.finish_reason,
                len(response.content or ""),
            )
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(messages, response.content, tool_call_dicts)

                tool_calls = response.tool_calls
                requested_calls = len(tool_calls)
                planner_plan = None
                batch_plan = None
                effective_calls = requested_calls
                if requested_calls > 1:
                    planner_plan = self._plan_tool_calls(
                        tool_calls,
                        max_parallel_calls=ctx.max_parallel_tool_calls,
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

                # Check turn limit
                remaining_calls = max(0, ctx.max_tool_calls_per_turn - tool_calls_executed)
                if remaining_calls <= 0 or effective_calls > remaining_calls:
                    final_content = self._build_tool_call_limit_message(
                        lang=reply_language,
                        limit=ctx.max_tool_calls_per_turn,
                        executed=tool_calls_executed,
                        requested=effective_calls,
                    )
                    trajectory_status = "tool_error"
                    self.trajectory_store.add_event(
                        trajectory_id,
                        stage="act",
                        action="tool_limit_blocked",
                        payload={
                            "limit": ctx.max_tool_calls_per_turn,
                            "executed": tool_calls_executed,
                            "requested": requested_calls,
                            "effective_requested": effective_calls,
                        },
                    )
                    break

                if len(tool_calls) > 1:
                    # --- Batched parallel execution ---
                    for tc in tool_calls:
                        call_payload = self._build_tool_call_payload(tc)
                        self.trajectory_store.add_event(
                            trajectory_id, stage="act", action="tool_call", payload=call_payload,
                        )
                        await self._emit_tool_call_stream_event(
                            channel=msg.channel, chat_id=msg.chat_id,
                            event_type="call", payload=call_payload,
                        )
                    results, executed_plan = await self._execute_tool_calls_with_batching(
                        tool_calls,
                        policy=ctx.tool_policy,
                        retry_budget=ctx.retry_budget,
                        sender_id=msg.sender_id,
                        channel=msg.channel,
                        session_key=session_key,
                        max_parallel_calls=ctx.max_parallel_tool_calls,
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
                        messages, outcome = await self._process_single_tool_result(
                            tc, result,
                            msg=msg, session_key=session_key,
                            trajectory_id=trajectory_id, reply_language=reply_language,
                            messages=messages, recent_tool_failures=recent_tool_failures,
                            tool_failure_counts=tool_failure_counts,
                            abort_after_repeats=abort_after_repeats,
                            pending_media=pending_media,
                        )
                        if outcome.should_abort:
                            final_content = outcome.abort_content
                            trajectory_status = outcome.trajectory_status
                            break
                    if final_content is not None:
                        break
                else:
                    # --- Sequential single-call execution ---
                    if tool_calls:
                        turn_tool_rounds += 1
                        turn_batch_groups += 1
                        turn_tool_calls_requested += len(tool_calls)
                    for tool_call in tool_calls:
                        if self._cancel_token and self._cancel_token.is_cancelled:
                            logger.info("Skipping remaining tool calls (cancelled).")
                            break
                        if tool_calls_executed >= ctx.max_tool_calls_per_turn:
                            final_content = self._build_tool_call_limit_message(
                                lang=reply_language,
                                limit=ctx.max_tool_calls_per_turn,
                                executed=tool_calls_executed,
                                requested=1,
                            )
                            trajectory_status = "tool_error"
                            self.trajectory_store.add_event(
                                trajectory_id,
                                stage="act",
                                action="tool_limit_blocked",
                                payload={
                                    "limit": ctx.max_tool_calls_per_turn,
                                    "executed": tool_calls_executed,
                                    "requested": 1,
                                },
                            )
                            break
                        logger.info("Executing tool: %s", tool_call.name)
                        call_payload = self._build_tool_call_payload(tool_call)
                        self.trajectory_store.add_event(
                            trajectory_id, stage="act", action="tool_call", payload=call_payload,
                        )
                        await self._emit_tool_call_stream_event(
                            channel=msg.channel, chat_id=msg.chat_id,
                            event_type="call", payload=call_payload,
                        )
                        result = await self._execute_single_tool_call(
                            tool_call,
                            policy=ctx.tool_policy,
                            retry_budget=ctx.retry_budget,
                            sender_id=msg.sender_id,
                            channel=msg.channel,
                            session_key=session_key,
                        )
                        tool_calls_executed += 1
                        turn_tool_calls_executed += 1
                        messages, outcome = await self._process_single_tool_result(
                            tool_call, result,
                            msg=msg, session_key=session_key,
                            trajectory_id=trajectory_id, reply_language=reply_language,
                            messages=messages, recent_tool_failures=recent_tool_failures,
                            tool_failure_counts=tool_failure_counts,
                            abort_after_repeats=abort_after_repeats,
                            pending_media=pending_media,
                        )
                        if outcome.should_abort:
                            final_content = outcome.abort_content
                            trajectory_status = outcome.trajectory_status
                            break
                    if final_content is not None:
                        break
            else:
                # No tool calls — check fake action
                content = response.content or ""
                if self._is_fake_tool_call(content) and iteration < 3:
                    logger.warning(
                        "Detected fake tool call in response, retrying (iter=%d)", iteration,
                    )
                    messages = self.context.add_assistant_message(messages, content, None)
                    messages.append(
                        {"role": "user", "content": self._msg(reply_language, "fake_tool_retry")}
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
                retry_budget=ctx.retry_budget,
                reply_language=reply_language,
                recent_tool_failures=recent_tool_failures,
            )
            trajectory_status = "max_iterations"

        # Store turn metrics for later finalization
        self._last_turn_metrics = {
            "iterations": iteration,
            "overflow_retries": overflow_retries,
            "retry_budget_remaining": ctx.retry_budget.remaining,
            "tool_rounds": turn_tool_rounds,
            "tool_calls_requested": turn_tool_calls_requested,
            "tool_calls_executed": turn_tool_calls_executed,
            "tool_deduped_calls": turn_tool_deduped,
            "parallel_rounds": turn_parallel_rounds,
            "batch_groups": turn_batch_groups,
            "tokens_this_turn": turn_total_tokens,
        }
        self._last_turn_batching = {
            "total_tokens": turn_total_tokens,
            "tool_rounds": turn_tool_rounds,
            "parallel_rounds": turn_parallel_rounds,
            "tool_calls_requested": turn_tool_calls_requested,
            "tool_calls_executed": turn_tool_calls_executed,
            "deduped_calls": turn_tool_deduped,
            "batch_groups": turn_batch_groups,
        }

        return final_content, trajectory_status, pending_media

    # ------------------------------------------------------------------
    # 4) _finalize_turn  (persona guard, trajectory, history, output)
    # ------------------------------------------------------------------
    async def _finalize_turn(
        self,
        msg: InboundMessage,
        session_key: str,
        trajectory_id: str,
        reply_language: str,
        turn_started: float,
        final_content: Optional[str],
        trajectory_status: str,
        error_message: str,
        pending_media: List[str],
        memory_context_chars: int,
        recall_count: int,
        turn_tool_calls_executed: int,
    ) -> OutboundMessage:
        """Persona guard, trajectory finalization, history persistence, output."""
        # Persona runtime guard
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

        # Batching tracker
        metrics = getattr(self, "_last_turn_metrics", {})
        batching = getattr(self, "_last_turn_batching", {})
        self.tool_batching_tracker.record_turn(**batching) if batching else None

        # Trajectory finalization
        metrics["turn_latency_ms"] = round(
            (asyncio.get_running_loop().time() - turn_started) * 1000.0, 2
        )
        self.trajectory_store.finalize(
            trajectory_id,
            status=trajectory_status,
            final_content=final_content or error_message or "",
            usage=self.usage.summary(),
            metrics=metrics,
        )

        # Reset per-turn state
        self._active_provider_override = None
        self._active_model_override = None
        self._tool_policy_model_provider = ""
        self._tool_policy_model_name = ""
        self._prompt_cache_scope = {}

        if final_content is None:
            final_content = self._msg(reply_language, "iteration_limit_fallback")

        # Typing off
        await self.bus.publish_typing(
            TypingEvent(channel=msg.channel, chat_id=msg.chat_id, is_typing=False)
        )

        # History persistence
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
            logger.info("Sending response with media: %s", pending_media)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            media=pending_media,
        )

    # ------------------------------------------------------------------
    # Existing helpers (channel command + soul turn)
    # ------------------------------------------------------------------
    async def _handle_channel_command_impl(
        self,
        msg: InboundMessage,
        session_key: str,
        parsed_command: Tuple[str, str],
    ) -> OutboundMessage:
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


