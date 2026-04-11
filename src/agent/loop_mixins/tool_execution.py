"""AgentLoop mixin: Tool Execution.

Extracted from loop.py to reduce file size.
Contains 13 methods.
"""

from __future__ import annotations

from agent.constants import *  # noqa: F403
from tools.base import CancellationToken
from tools.registry import ToolPolicy
from tools.batching import ToolBatchPlan
from tools.planner import ToolPlannerPlan
from runtime.resilience import RetryBudget, classify_error_message
import asyncio
import json
import logging
logger = logging.getLogger('AgentLoop')

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    pass  # Add type imports as needed


class ToolExecutionMixin:
    """Mixin providing tool execution functionality."""

    @staticmethod
    def _normalize_tool_arguments(arguments: Any) -> Dict[str, Any]:
        """Normalize tool arguments into a dictionary."""
        if isinstance(arguments, dict):
            return arguments
        if arguments is None:
            return {}
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments.strip() or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON arguments: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("Tool arguments JSON must decode to an object")
            return parsed
        raise ValueError(f"Unsupported tool arguments type: {type(arguments).__name__}")

    async def _execute_single_tool_call(
        self,
        tool_call: Any,
        *,
        policy: ToolPolicy,
        retry_budget: RetryBudget,
        sender_id: str,
        channel: str,
        chat_id: str = "",
        session_key: str = "",
    ) -> str:
        """Execute one tool call with normalization, timeout, and fault isolation."""
        name = str(getattr(tool_call, "name", "") or "").strip()
        if not name:
            return "Error [TOOL_CALL_INVALID]: Invalid tool call (missing tool name)."
        if self._cancel_token and self._cancel_token.is_cancelled:
            return f"Error [TOOL_CANCELLED]: Operation cancelled before executing '{name}'."

        gate_block_message = self._check_release_gate_for_tool(
            tool_name=name,
            sender_id=sender_id,
            channel=channel,
        )
        if gate_block_message:
            return gate_block_message

        try:
            params = self._normalize_tool_arguments(getattr(tool_call, "arguments", {}))
        except ValueError as exc:
            logger.warning("Tool '%s' rejected invalid arguments: %s", name, exc)
            recovery = self._build_tool_failure_recovery_template(
                tool_name=name,
                retryable=False,
                budget_remaining=retry_budget.remaining,
            )
            return (
                f"Error [TOOL_ARGS_INVALID]: Invalid parameters for tool '{name}': {exc} "
                f"(trace_id={self._new_trace_id()})\n"
                "Hint: Provide a JSON object matching the tool schema.\n"
                f"{recovery}"
            )

        progress_counter = 0
        progress_limit = 16

        async def _progress_callback(event: Dict[str, Any]) -> None:
            nonlocal progress_counter
            if progress_counter >= progress_limit:
                return
            if not isinstance(event, dict):
                return
            message = str(event.get("message", "") or "").strip()
            if not message:
                return
            progress_counter += 1
            payload = self._build_tool_progress_payload(
                tool_call,
                stage=str(event.get("stage", "") or "").strip(),
                message=message,
                sequence=progress_counter,
                extra={
                    "stream": str(event.get("stream", "") or "").strip(),
                    "line_count": int(event.get("line_count", 0) or 0),
                },
            )
            await self._emit_tool_call_stream_event(
                channel=channel,
                chat_id=chat_id,
                event_type="progress",
                payload=payload,
            )

        params["_progress_callback"] = _progress_callback

        blocked = self._tool_call_hooks.before_tool_call(
            session_key=session_key,
            tool_name=name,
            params=params,
        )
        if isinstance(blocked, dict):
            code = str(blocked.get("code", "TOOL_BLOCKED_BY_HOOK")).strip() or "TOOL_BLOCKED_BY_HOOK"
            message = str(blocked.get("message", "blocked by governance hook")).strip() or "blocked by governance hook"
            return (
                f"Error [{code}]: {message} (trace_id={self._new_trace_id()})\n"
                "Hint: Adjust tool plan/arguments and avoid repeated identical calls."
            )

        timeout = self._get_tool_timeout_seconds()
        max_retries, backoff_seconds = self._get_tool_retry_settings()
        attempts = max_retries + 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            retryable = True
            try:
                model_provider, model_name = self._current_tool_policy_model_context()
                result = await asyncio.wait_for(
                    self.tools.execute(
                        name,
                        params,
                        policy=policy,
                        cancel_token=self._cancel_token,
                        sender_id=sender_id,
                        channel=channel,
                        model_provider=model_provider,
                        model_name=model_name,
                    ),
                    timeout=timeout,
                )
                if isinstance(result, str) and result.startswith("Error") and "Recovery Template:" not in result:
                    retryable_result = classify_error_message(result) != "non_retryable"
                    recovery = self._build_tool_failure_recovery_template(
                        tool_name=name,
                        retryable=retryable_result,
                        budget_remaining=retry_budget.remaining,
                    )
                    result = f"{result}\n{recovery}"
                self._tool_call_hooks.after_tool_call(
                    _session_key=session_key,
                    _tool_name=name,
                    _result=str(result),
                )
                return result
            except asyncio.TimeoutError:
                recovery = self._build_tool_failure_recovery_template(
                    tool_name=name,
                    retryable=True,
                    budget_remaining=retry_budget.remaining,
                )
                last_error = (
                    f"Error [TOOL_TIMEOUT]: Tool '{name}' timed out after {timeout:.1f}s "
                    f"(trace_id={self._new_trace_id()})\n"
                    "Hint: Reduce scope/inputs, or increase security.tool_call_timeout_seconds.\n"
                    f"{recovery}"
                )
                self._tool_call_hooks.after_tool_call(
                    _session_key=session_key,
                    _tool_name=name,
                    _result=last_error,
                )
                retryable = True
            except Exception as exc:
                logger.error("Tool '%s' crashed unexpectedly: %s", name, exc, exc_info=True)
                retryable = classify_error_message(str(exc)) != "non_retryable"
                recovery = self._build_tool_failure_recovery_template(
                    tool_name=name,
                    retryable=retryable,
                    budget_remaining=retry_budget.remaining,
                )
                last_error = (
                    f"Error [TOOL_EXECUTION_FAILED]: Error executing {name}: {exc} "
                    f"(trace_id={self._new_trace_id()})\n"
                    "Hint: Check logs/dependencies/permissions; avoid repeating identical calls.\n"
                    f"{recovery}"
                )
                self._tool_call_hooks.after_tool_call(
                    _session_key=session_key,
                    _tool_name=name,
                    _result=last_error,
                )

            if attempt >= attempts:
                return last_error
            if not retryable:
                return last_error
            if not retry_budget.consume(1):
                return f"{last_error}\nRetry budget exhausted."
            if backoff_seconds > 0:
                await asyncio.sleep(backoff_seconds * attempt)

        return last_error

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # Tools are injected by runtime/brain plugin loading; keep this as explicit no-op.
        pass

    def _classify_tool_parallel_lane(self, tool_name: str) -> str:
        name = str(tool_name or "").strip().lower()
        if not name:
            return "default"
        if name in {"node_invoke", "node_describe", "node_list"}:
            return "device"

        tool = self.tools.get(name)
        provider = str(getattr(tool, "provider", "") or "").strip().lower() if tool is not None else ""

        if provider in {"devices", "desktop", "satellite", "hardware"}:
            return "device"
        if provider in {"web", "browser", "email", "hooks"}:
            return "network"
        if provider in {"coding", "system", "runtime", "canvas", "core"}:
            return "io"

        if name.startswith("web_") or name.startswith("browser") or name.startswith("email_"):
            return "network"
        if name in {"exec", "read_file", "write_file", "edit_file", "list_dir", "find_files", "grep"}:
            return "io"
        return "default"

    def _plan_tool_batches(self, tool_calls: List[Any], max_parallel_calls: int) -> ToolBatchPlan:
        return self.tool_batch_planner.plan(
            tool_calls,
            lane_resolver=self._classify_tool_parallel_lane,
            max_parallel_calls=max_parallel_calls,
        )

    def _plan_tool_calls(self, tool_calls: List[Any], max_parallel_calls: int) -> ToolPlannerPlan:
        return self.tool_planner.plan(
            tool_calls,
            lane_resolver=self._classify_tool_parallel_lane,
            max_parallel_calls=max_parallel_calls,
            batch_planner=self.tool_batch_planner,
        )

    def _compact_tool_result_for_context(self, *, tool_name: str, result: Any) -> str:
        return self.tool_planner.compact_tool_result(tool_name=tool_name, result=result)

    async def _execute_tool_calls_with_batching(
        self,
        tool_calls: List[Any],
        *,
        policy: ToolPolicy,
        retry_budget: RetryBudget,
        sender_id: str,
        channel: str,
        chat_id: str,
        max_parallel_calls: int,
        session_key: str = "",
        plan: Optional[ToolBatchPlan] = None,
    ) -> tuple[List[str], ToolBatchPlan]:
        plan = plan or self._plan_tool_batches(tool_calls, max_parallel_calls=max_parallel_calls)
        result_by_call_id: Dict[str, str] = {}
        for batch in plan.batches:
            batch_results = await self._execute_tools_parallel(
                batch,
                policy=policy,
                retry_budget=retry_budget,
                sender_id=sender_id,
                channel=channel,
                chat_id=chat_id,
                session_key=session_key,
                max_parallel_calls=max_parallel_calls,
            )
            for call, result in zip(batch, batch_results):
                call_id = str(getattr(call, "id", "") or "")
                if call_id:
                    result_by_call_id[call_id] = str(result)

        ordered_results: List[str] = []
        for call in tool_calls:
            call_id = str(getattr(call, "id", "") or "")
            resolved_id = plan.duplicate_of.get(call_id, call_id)
            result = result_by_call_id.get(resolved_id)
            if result is None:
                name = str(getattr(call, "name", "") or "unknown_tool")
                result = (
                    f"Error [TOOL_EXECUTION_FAILED]: batched call result missing for {name} "
                    f"(trace_id={self._new_trace_id()})"
                )
            ordered_results.append(result)
        return ordered_results, plan

    async def _execute_tools_parallel(
        self,
        tool_calls,
        policy: ToolPolicy,
        retry_budget: RetryBudget,
        sender_id: str,
        channel: str,
        chat_id: str,
        max_parallel_calls: int,
        session_key: str = "",
    ) -> List[str]:
        """Execute multiple tool calls concurrently with fault isolation."""
        concurrency = max(1, int(max_parallel_calls or 1))
        semaphore = asyncio.Semaphore(concurrency)
        lane_limits = self._get_parallel_tool_lane_limits()
        lane_semaphores = {
            lane: asyncio.Semaphore(max(1, int(limit)))
            for lane, limit in lane_limits.items()
        }
        default_lane_semaphore = lane_semaphores.get("default", asyncio.Semaphore(1))

        async def _run_one(tc):
            lane = self._classify_tool_parallel_lane(str(getattr(tc, "name", "") or ""))
            lane_semaphore = lane_semaphores.get(lane, default_lane_semaphore)
            async with semaphore:
                async with lane_semaphore:
                    logger.info("Executing tool (parallel lane=%s): %s", lane, tc.name)
                    return await self._execute_single_tool_call(
                        tc,
                        policy=policy,
                        retry_budget=retry_budget,
                        sender_id=sender_id,
                        channel=channel,
                        chat_id=chat_id,
                        session_key=session_key,
                    )

        results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls], return_exceptions=True)
        safe_results: List[str] = []
        for tc, result in zip(tool_calls, results):
            if isinstance(result, Exception):
                name = str(getattr(tc, "name", "") or "unknown_tool")
                logger.error("Parallel tool call '%s' failed unexpectedly: %s", name, result, exc_info=True)
                recovery = self._build_tool_failure_recovery_template(
                    tool_name=name,
                    retryable=False,
                    budget_remaining=retry_budget.remaining,
                )
                safe_results.append(
                    f"Error [TOOL_EXECUTION_FAILED]: Error executing {name}: {result} "
                    f"(trace_id={self._new_trace_id()})\n"
                    "Hint: Check logs/dependencies/permissions; avoid repeating identical calls.\n"
                    f"{recovery}"
                )
                continue
            safe_results.append(result)
        return safe_results
