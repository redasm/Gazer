from __future__ import annotations

import pytest

from tools.registry_execute import (
    augment_tool_params,
    evaluate_pre_execution_block,
    prepare_execution_context,
    run_tool_pipeline,
)


def test_augment_tool_params_injects_access_context() -> None:
    payload = augment_tool_params(
        {"payload": "x"},
        policy="policy",
        sender_id="u1",
        channel="web",
        sender_is_owner=True,
    )

    assert payload["payload"] == "x"
    assert payload["_access_policy"] == "policy"
    assert payload["_access_sender_id"] == "u1"
    assert payload["_access_channel"] == "web"
    assert payload["_access_sender_is_owner"] is True


@pytest.mark.asyncio
async def test_run_tool_pipeline_applies_hooks_and_executes_tool() -> None:
    calls: list[tuple[str, object]] = []

    class _Hooks:
        async def run_before_tool_call(self, tool_name, params):
            calls.append(("before", tool_name))
            return {**params, "hooked": True}

        async def run_after_tool_call(self, tool_name, params, result):
            calls.append(("after", params.get("hooked")))
            return f"{result}:after"

    class _Tool:
        async def execute(self, **kwargs):
            calls.append(("execute", kwargs.get("_access_sender_id")))
            return "ok"

    result = await run_tool_pipeline(
        tool=_Tool(),
        name="dummy",
        params={"payload": "x"},
        hooks=_Hooks(),
        policy="policy",
        sender_id="u1",
        channel="web",
        sender_is_owner=False,
    )

    assert result == "ok:after"
    assert calls == [("before", "dummy"), ("execute", "u1"), ("after", True)]


def test_evaluate_pre_execution_block_returns_budget_error() -> None:
    events = []

    def _record(**kwargs):
        events.append(kwargs)

    def _error(code, message, *, trace_id=""):
        return f"Error [{code}]: {message} ({trace_id})"

    result = evaluate_pre_execution_block(
        access_allowed=True,
        access_reason="allowed",
        tool_owner_only=False,
        trace_id="trc_1",
        name="safe_tool",
        provider="system",
        channel="web",
        sender_id="u1",
        model_provider="openai",
        model_name="gpt-4o-mini",
        circuit_open=False,
        budget_exceeded=True,
        budget_reason="max_calls",
        budget_status={"used_calls": 2, "max_calls": 2, "used_weight": 2.0, "max_weight": 2.0},
        record_rejection_event=_record,
        error_builder=_error,
    )

    assert "TOOL_BUDGET_EXCEEDED" in result
    assert events[0]["code"] == "TOOL_BUDGET_EXCEEDED"
    assert events[0]["reason"] == "max_calls"


def test_prepare_execution_context_returns_validation_error_before_run() -> None:
    class _Tool:
        provider = "system"

        def validate_params(self, _params):
            return ["missing field"]

    def _resolve_budget_weight(**_kwargs):
        return 1.0

    recorded = []

    def _record_budget_usage(**kwargs):
        recorded.append(kwargs)

    def _error(code, message, *, trace_id=""):
        return f"Error [{code}]: {message} ({trace_id})"

    error, weight = prepare_execution_context(
        tool=_Tool(),
        name="dummy",
        params={},
        cancel_token=None,
        trace_id="trc_1",
        budget_settings=type("Budget", (), {"enabled": True, "group_weights": {}, "tool_weights": {}})(),
        resolve_budget_weight=_resolve_budget_weight,
        record_budget_usage=_record_budget_usage,
        error_builder=_error,
    )

    assert weight == 1.0
    assert "TOOL_PARAMS_INVALID" in error
    assert recorded[0]["name"] == "dummy"
