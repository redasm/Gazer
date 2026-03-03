"""Mixin for Agent loop message processing."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple, List

from bus.events import InboundMessage, OutboundMessage, TypingEvent

logger = logging.getLogger(__name__)

class ProcessMessageMixin:
    """Provides methods for breaking down _process_message."""

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

    async def _handle_soul_turn_impl(
        self,
        msg: InboundMessage,
        session_key: str,
        reply_language: str,
        trajectory_id: str,
        turn_started: float,
    ) -> OutboundMessage:
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

