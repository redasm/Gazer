from __future__ import annotations

from typing import Any

from runtime.rust_gate import push_tool_access_context


def augment_tool_params(
    params: dict[str, Any] | Any,
    *,
    policy: Any,
    sender_id: str,
    channel: str,
    sender_is_owner: bool,
) -> dict[str, Any] | Any:
    """Attach nested access context for tools that trigger further tool calls."""
    if not isinstance(params, dict):
        return params
    effective_params = dict(params)
    effective_params.setdefault("_access_policy", policy)
    effective_params.setdefault("_access_sender_id", str(sender_id or ""))
    effective_params.setdefault("_access_channel", str(channel or ""))
    effective_params.setdefault("_access_sender_is_owner", bool(sender_is_owner))
    return effective_params


async def run_tool_pipeline(
    *,
    tool: Any,
    name: str,
    params: dict[str, Any] | Any,
    hooks: Any = None,
    policy: Any = None,
    sender_id: str = "",
    channel: str = "",
    sender_is_owner: bool = False,
) -> Any:
    """Run before hooks, attach access context, execute the tool, then run after hooks."""
    effective_params = params
    if hooks:
        effective_params = await hooks.run_before_tool_call(name, params)

    effective_params = augment_tool_params(
        effective_params,
        policy=policy,
        sender_id=sender_id,
        channel=channel,
        sender_is_owner=sender_is_owner,
    )

    with push_tool_access_context(channel=str(channel or ""), sender_id=str(sender_id or "")):
        result = await tool.execute(**effective_params)

    if hooks:
        result = await hooks.run_after_tool_call(name, effective_params, result)
    return result


def evaluate_pre_execution_block(
    *,
    access_allowed: bool,
    access_reason: str,
    tool_owner_only: bool,
    trace_id: str,
    name: str,
    provider: str,
    channel: str,
    sender_id: str,
    model_provider: str,
    model_name: str,
    circuit_open: bool,
    budget_exceeded: bool,
    budget_reason: str,
    budget_status: dict[str, Any] | None,
    record_rejection_event: Any,
    error_builder: Any,
) -> str | None:
    """Return a blocking tool error string when pre-execution policy checks fail."""
    if not access_allowed:
        reason = str(access_reason or "policy")
        record_rejection_event(
            code="TOOL_NOT_PERMITTED",
            name=name,
            provider=provider,
            reason=reason,
            trace_id=trace_id,
            metadata={
                "owner_only": bool(tool_owner_only),
                "channel": str(channel or "").strip(),
                "sender_id": str(sender_id or "").strip(),
                "model_provider": str(model_provider or "").strip().lower(),
                "model_name": str(model_name or "").strip().lower(),
            },
        )
        message = (
            f"Tool '{name}' is restricted to owner channels."
            if reason == "blocked_by_owner_only"
            else f"Tool '{name}' is not permitted for the current trust level."
        )
        return error_builder("TOOL_NOT_PERMITTED", message, trace_id=trace_id)

    if circuit_open:
        record_rejection_event(
            code="TOOL_CIRCUIT_OPEN",
            name=name,
            provider=provider,
            reason="circuit_open",
            trace_id=trace_id,
        )
        return error_builder(
            "TOOL_CIRCUIT_OPEN",
            f"Tool '{name}' is temporarily blocked after repeated failures.",
            trace_id=trace_id,
        )

    if budget_exceeded:
        status = dict(budget_status or {})
        record_rejection_event(
            code="TOOL_BUDGET_EXCEEDED",
            name=name,
            provider=provider,
            reason=budget_reason or "budget_exceeded",
            trace_id=trace_id,
            metadata={
                "used_calls": int(status.get("used_calls", 0)),
                "max_calls": int(status.get("max_calls", 0)),
                "used_weight": float(status.get("used_weight", 0.0)),
                "max_weight": float(status.get("max_weight", 0.0)),
            },
        )
        return error_builder(
            "TOOL_BUDGET_EXCEEDED",
            f"Tool execution budget exceeded for current rolling window ({budget_reason}).",
            trace_id=trace_id,
        )

    return None


def prepare_execution_context(
    *,
    tool: Any,
    name: str,
    params: dict[str, Any],
    cancel_token: Any,
    trace_id: str,
    budget_settings: Any,
    resolve_budget_weight: Any,
    record_budget_usage: Any,
    error_builder: Any,
) -> tuple[str | None, float]:
    """Validate params, enforce cancellation, and register budget usage before execution."""
    budget_weight = resolve_budget_weight(
        tool_name=name,
        provider=str(getattr(tool, "provider", "") or "core"),
        group_weights=budget_settings.group_weights,
        tool_weights=budget_settings.tool_weights,
    )
    if budget_settings.enabled:
        record_budget_usage(name=name, provider=str(getattr(tool, "provider", "") or "core"), weight=budget_weight)
    if cancel_token and cancel_token.is_cancelled:
        return (
            error_builder(
                "TOOL_CANCELLED",
                f"Operation cancelled before executing '{name}'.",
                trace_id=trace_id,
            ),
            budget_weight,
        )
    errors = tool.validate_params(params)
    if errors:
        return (
            error_builder(
                "TOOL_PARAMS_INVALID",
                f"Invalid parameters for tool '{name}': " + "; ".join(errors),
                trace_id=trace_id,
            ),
            budget_weight,
        )
    return None, budget_weight
