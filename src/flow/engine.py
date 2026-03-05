"""FlowEngine — core workflow orchestrator.

Executes deterministic YAML-defined pipelines step-by-step, delegating to
:class:`ToolRegistry` for tool calls and :class:`LLMTaskStep` for LLM
reasoning.  Supports conditional steps, ``each`` fan-out, approval gates
with HMAC resume tokens, ``on_complete`` state updates, and timeout
enforcement.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flow.approval import (
    create_resume_token,
    verify_resume_token,
    restore_context,
    snapshot_context,
)
from flow.llm_task import LLMTaskStep
from flow.models import (
    FlowContext,
    FlowDefinition,
    FlowResult,
    FlowStep,
    StepResult,
)
from flow.parser import discover_flows, interpolate
from flow.safe_eval import safe_eval, safe_eval_bool, SafeEvalError
from flow.state import StateStore
from runtime.resilience import RetryBudget, classify_error_message

logger = logging.getLogger("FlowEngine")


class FlowEngine:
    """Execute GazerFlow workflow definitions.

    Usage::

        engine = FlowEngine(tool_registry=registry, llm_provider=provider)
        result = await engine.run("my_flow", {"repo": "owner/repo"})
    """

    def __init__(
        self,
        tool_registry: Any,
        llm_provider: Any = None,
        state_store: Optional[StateStore] = None,
        flow_dirs: Optional[List[Path]] = None,
    ) -> None:
        self._tools = tool_registry
        self._llm = LLMTaskStep(llm_provider) if llm_provider else None
        self._state = state_store or StateStore()
        self._flow_dirs = flow_dirs or [Path("workflows")]
        self._flows: Dict[str, FlowDefinition] = {}
        self.reload()

    # ------------------------------------------------------------------
    # Flow discovery
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-discover workflow definitions from disk."""
        self._flows = discover_flows(self._flow_dirs)
        logger.info("Discovered %d flow(s): %s", len(self._flows), list(self._flows.keys()))

    def list_flows(self) -> List[Dict[str, Any]]:
        """Return metadata for all discovered flows."""
        return [
            {
                "name": f.name,
                "description": f.description,
                "args": {k: {"type": a.type, "default": a.default} for k, a in f.args.items()},
            }
            for f in self._flows.values()
        ]

    def get_flow(self, name: str) -> Optional[FlowDefinition]:
        return self._flows.get(name)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(
        self,
        flow_name: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> FlowResult:
        """Run a workflow from the beginning.

        Args:
            flow_name: Registered flow name.
            args: Arguments to pass (merged with defaults).

        Returns:
            A :class:`FlowResult` — status is ``completed``, ``needs_approval``,
            or ``error``.
        """
        flow = self._flows.get(flow_name)
        if not flow:
            return FlowResult(status="error", error=f"Flow '{flow_name}' not found")

        # Build effective args (defaults + overrides)
        effective_args: Dict[str, Any] = {}
        for k, arg_def in flow.args.items():
            effective_args[k] = arg_def.default
        if args:
            effective_args.update(args)

        # Load persisted state merged with definition defaults
        state = self._state.load(flow_name, defaults=dict(flow.state))

        ctx = FlowContext(args=effective_args, state=state)
        budget = RetryBudget.from_total(flow.config.retry_budget)
        return await self._execute_steps(
            flow,
            ctx,
            start_index=0,
            retry_budget=budget,
            sender_is_owner=sender_is_owner,
            policy=policy,
        )

    async def resume_interrupted(
        self,
        flow_name: str,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> FlowResult:
        """Resume a workflow from the latest persisted checkpoint."""
        flow = self._flows.get(flow_name)
        if not flow:
            return FlowResult(status="error", error=f"Flow '{flow_name}' not found")

        checkpoint = self._state.load_checkpoint(flow_name)
        if not checkpoint:
            return FlowResult(status="error", error=f"No checkpoint found for flow '{flow_name}'")

        ctx_snapshot = checkpoint.get("ctx")
        start_index = checkpoint.get("next_index")
        if not isinstance(ctx_snapshot, dict) or not isinstance(start_index, int):
            return FlowResult(status="error", error="Invalid checkpoint payload")

        ctx = restore_context(ctx_snapshot)
        budget = RetryBudget.from_total(flow.config.retry_budget)
        return await self._execute_steps(
            flow,
            ctx,
            start_index=start_index,
            retry_budget=budget,
            sender_is_owner=sender_is_owner,
            policy=policy,
        )

    # ------------------------------------------------------------------
    # Resume (after approval gate)
    # ------------------------------------------------------------------

    async def resume(
        self,
        token: str,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> FlowResult:
        """Resume a workflow past an approval gate.

        Args:
            token: The HMAC-signed resume token returned by a previous
                ``needs_approval`` result.
        """
        payload = verify_resume_token(token)
        if payload is None:
            return FlowResult(status="error", error="Invalid or expired resume token")

        flow_name = payload["flow"]
        step_id = payload["step"]
        flow = self._flows.get(flow_name)
        if not flow:
            return FlowResult(status="error", error=f"Flow '{flow_name}' not found")

        # Restore context from token snapshot
        ctx = restore_context(payload["ctx"])

        # Find the step index to resume from (the step *after* the approval gate)
        start_index = None
        for i, step in enumerate(flow.steps):
            if step.id == step_id:
                start_index = i + 1
                break
        if start_index is None:
            return FlowResult(status="error", error=f"Step '{step_id}' not found in flow '{flow_name}'")

        budget = RetryBudget.from_total(flow.config.retry_budget)
        return await self._execute_steps(
            flow,
            ctx,
            start_index=start_index,
            retry_budget=budget,
            sender_is_owner=sender_is_owner,
            policy=policy,
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, flow_name: str) -> Dict[str, Any]:
        """Return persisted state for a flow."""
        flow = self._flows.get(flow_name)
        if not flow:
            return {"error": f"Flow '{flow_name}' not found"}
        state = self._state.load(flow_name, defaults=dict(flow.state))
        checkpoint = self._state.load_checkpoint(flow_name)
        return {
            "flow": flow_name,
            "state": state,
            "checkpoint": checkpoint,
            "can_resume": checkpoint is not None,
        }

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    async def _execute_steps(
        self,
        flow: FlowDefinition,
        ctx: FlowContext,
        start_index: int,
        retry_budget: RetryBudget,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
        confirmed: bool = False,
    ) -> FlowResult:
        """Walk through steps starting at *start_index*."""
        deadline = time.monotonic() + flow.config.timeout_ms / 1000.0

        for i in range(start_index, len(flow.steps)):
            step = flow.steps[i]
            self._save_checkpoint(flow.name, ctx, next_index=i)

            # Timeout check
            if time.monotonic() > deadline:
                return FlowResult(
                    status="error",
                    output=ctx.steps,
                    error=f"Flow '{flow.name}' timed out after {flow.config.timeout_ms}ms",
                )

            # Condition evaluation
            if step.condition and not self._eval_condition(step.condition, ctx):
                ctx.steps[step.id] = StepResult(skipped=True)
                self._save_checkpoint(flow.name, ctx, next_index=i + 1)
                continue

            dep_error = self._validate_step_dependencies(step, ctx)
            if dep_error:
                result = StepResult(error=dep_error)
                ctx.steps[step.id] = result
                return FlowResult(status="error", output=ctx.steps, error=result.error)

            # Approval gate (checked *before* execution)
            if step.approve:
                prompt = interpolate(step.approve.prompt, ctx)
                preview = interpolate(step.approve.preview, ctx) if step.approve.preview else None
                token = create_resume_token(flow.name, step.id, snapshot_context(ctx))
                self._save_checkpoint(flow.name, ctx, next_index=i + 1)
                return FlowResult(
                    status="needs_approval",
                    output=ctx.steps,
                    pending_step=step.id,
                    prompt=str(prompt),
                    preview=preview,
                    resume_token=token,
                )

            # Fan-out with `each`
            result = await self._execute_step_with_resilience(
                step,
                ctx,
                deadline,
                retry_budget,
                sender_is_owner=sender_is_owner,
                policy=policy,
            )

            ctx.steps[step.id] = result

            if result.error:
                return FlowResult(status="error", output=ctx.steps, error=result.error)

            # on_complete state updates
            if step.on_complete:
                self._apply_on_complete(step.on_complete, ctx)

            # Output size guard
            output_size = len(json.dumps(result.output, default=str)) if result.output else 0
            if output_size > flow.config.max_output_bytes:
                logger.warning(
                    "Step '%s' output (%d bytes) exceeds max_output_bytes (%d)",
                    step.id, output_size, flow.config.max_output_bytes,
                )
            self._save_checkpoint(flow.name, ctx, next_index=i + 1)

        # All steps done — persist state
        self._state.save(flow.name, ctx.state)
        self._state.clear_checkpoint(flow.name)

        return FlowResult(status="completed", output=ctx.steps)

    def _save_checkpoint(self, flow_name: str, ctx: FlowContext, *, next_index: int) -> None:
        self._state.save_checkpoint(
            flow_name,
            {
                "flow": flow_name,
                "next_index": next_index,
                "updated_at": time.time(),
                "ctx": snapshot_context(ctx),
            },
        )

    @staticmethod
    def _validate_step_dependencies(step: FlowStep, ctx: FlowContext) -> Optional[str]:
        for dep in step.depends_on:
            dep_result = ctx.steps.get(dep)
            if dep_result is None:
                return f"Step '{step.id}' dependency '{dep}' not completed"
            if dep_result.error:
                return f"Step '{step.id}' dependency '{dep}' failed: {dep_result.error}"
        return None

    async def _execute_step_with_resilience(
        self,
        step: FlowStep,
        ctx: FlowContext,
        deadline: float,
        retry_budget: RetryBudget,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> StepResult:
        retries = max(0, int(step.retry_max or 0))
        backoff = max(0, int(step.retry_backoff_ms or 0))
        for attempt in range(retries + 1):
            result = await self._execute_step_once_with_timeout(
                step,
                ctx,
                deadline,
                sender_is_owner=sender_is_owner,
                policy=policy,
            )
            if not result.error:
                return result
            if attempt >= retries:
                return result
            if classify_error_message(result.error) == "non_retryable":
                return result
            if not retry_budget.consume(1):
                return StepResult(error=f"{result.error} Retry budget exhausted.")
            if backoff > 0:
                await asyncio.sleep((backoff * (attempt + 1)) / 1000.0)
        return StepResult(error=f"Step '{step.id}' failed unexpectedly")

    async def _execute_step_once_with_timeout(
        self,
        step: FlowStep,
        ctx: FlowContext,
        deadline: float,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> StepResult:
        if step.each:
            coro = self._execute_each(
                step,
                ctx,
                deadline,
                sender_is_owner=sender_is_owner,
                policy=policy,
            )
        else:
            coro = self._execute_single_step(
                step,
                ctx,
                sender_is_owner=sender_is_owner,
                policy=policy,
            )

        if step.timeout_ms is None:
            return await coro
        timeout_ms = int(step.timeout_ms)
        if timeout_ms <= 0:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            return StepResult(error=f"Step '{step.id}' timed out after {timeout_ms}ms")

    async def _execute_single_step(
        self,
        step: FlowStep,
        ctx: FlowContext,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> StepResult:
        """Execute one tool or LLM-task step."""
        try:
            resolved_args = interpolate(step.args, ctx)

            if step.tool == "llm_task":
                return await self._run_llm_task(resolved_args)
            elif step.tool:
                return await self._run_tool(
                    step.tool,
                    resolved_args,
                    sender_is_owner=sender_is_owner,
                    policy=policy,
                )
            else:
                # No tool — treat as a pass-through (args become output)
                return StepResult(output=resolved_args)

        except Exception as exc:
            logger.exception("Step '%s' failed", step.id)
            return StepResult(error=str(exc))

    async def _run_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> StepResult:
        """Delegate to ToolRegistry."""
        result = await self._tools.execute(
            tool_name,
            params,
            sender_is_owner=sender_is_owner,
            policy=policy,
        )
        # ToolRegistry.execute always returns a string
        if isinstance(result, str) and result.startswith("Error"):
            return StepResult(error=result)
        # Try to parse JSON output from tools
        output: Any = result
        if isinstance(result, str):
            try:
                output = json.loads(result)
            except (json.JSONDecodeError, ValueError):
                pass
        return StepResult(output=output)

    async def _run_llm_task(self, args: Dict[str, Any]) -> StepResult:
        """Delegate to LLMTaskStep."""
        if not self._llm:
            return StepResult(error="No LLM provider configured for llm_task steps")
        output = await self._llm.execute(
            prompt=args.get("prompt", ""),
            input_data=args.get("input"),
            schema=args.get("schema"),
            model=args.get("model"),
        )
        return StepResult(output=output)

    # ------------------------------------------------------------------
    # Fan-out (each)
    # ------------------------------------------------------------------

    async def _execute_each(
        self,
        step: FlowStep,
        ctx: FlowContext,
        deadline: float,
        *,
        sender_is_owner: Optional[Any] = None,
        policy: Optional[Any] = None,
    ) -> StepResult:
        """Execute a step once per item in the ``each`` iterable."""
        items = interpolate(step.each, ctx)
        if not isinstance(items, (list, tuple)):
            return StepResult(error=f"'each' expression did not yield a list: {type(items).__name__}")

        results: List[Any] = []
        for idx, item in enumerate(items):
            if time.monotonic() > deadline:
                return StepResult(error="Timed out during 'each' iteration")
            # Temporarily set item context
            ctx.item = item
            ctx.item_index = idx
            single = await self._execute_single_step(
                step,
                ctx,
                sender_is_owner=sender_is_owner,
                policy=policy,
            )
            if single.error:
                return StepResult(error=f"each[{idx}]: {single.error}")
            results.append(single.output)

        # Clear item context
        ctx.item = None
        ctx.item_index = 0
        return StepResult(output=results)

    # ------------------------------------------------------------------
    # Condition evaluation (safe AST-based sandbox)
    # ------------------------------------------------------------------

    @staticmethod
    def _eval_condition(expr: str, ctx: FlowContext) -> bool:
        """Evaluate a condition expression using safe AST-based evaluation.

        Available names: ``args``, ``steps``, ``state``, ``item``, ``len``,
        ``any``, ``all``, ``True``, ``False``, ``None``, plus safe builtins.

        Uses flow.safe_eval to prevent code injection attacks.
        """
        names = {
            "args": ctx.args,
            "steps": ctx.steps,
            "state": ctx.state,
            "item": ctx.item,
        }
        return safe_eval_bool(expr, names)

    # ------------------------------------------------------------------
    # on_complete state updates
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_on_complete(updates: Dict[str, str], ctx: FlowContext) -> None:
        """Apply ``on_complete`` state mutations.

        Each value is an expression evaluated using safe AST-based evaluation,
        with the result stored into ``ctx.state[key]``.

        Uses flow.safe_eval to prevent code injection attacks.
        """
        names = {
            "args": ctx.args,
            "steps": ctx.steps,
            "state": ctx.state,
            "item": ctx.item,
        }
        for key, expr in updates.items():
            try:
                ctx.state[key] = safe_eval(expr, names)
            except SafeEvalError as exc:
                logger.warning("on_complete '%s = %s' failed: %s", key, expr, exc)
