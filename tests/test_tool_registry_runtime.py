from tools.registry_runtime import BudgetSettings, ToolRegistryRuntimeState


def test_runtime_state_tracks_budget_and_rejections() -> None:
    state = ToolRegistryRuntimeState()
    settings = BudgetSettings(enabled=True, max_calls=2, window_seconds=60, max_weight=3.0)

    state.record_budget_usage(name="safe_tool", provider="system", weight=1.5, settings=settings)
    status = state.budget_runtime_status(settings)
    state.record_rejection_event(
        code="TOOL_BUDGET_EXCEEDED",
        name="safe_tool",
        provider="system",
        reason="max_calls",
        trace_id="trc_1",
    )

    assert status["used_calls"] == 1
    assert status["used_weight"] == 1.5
    assert state.recent_rejection_events(limit=1)[0]["trace_id"] == "trc_1"


def test_runtime_state_circuit_resets_after_success() -> None:
    state = ToolRegistryRuntimeState()

    state.record_tool_outcome("safe_tool", "Error [X]: fail", enabled=True, threshold=1, cooldown=60)
    assert state.is_circuit_open("safe_tool") is True

    state.record_tool_outcome("safe_tool", "ok", enabled=True, threshold=1, cooldown=60)
    assert state.is_circuit_open("safe_tool") is False
