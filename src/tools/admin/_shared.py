"""Shared state and utilities for Admin API routers.

This module re-exports symbols from focused sub-modules for backward
compatibility.  New code should import from the canonical locations:

    * ``tools.admin.state``  — runtime globals, getters, buffers, path constants
    * ``tools.admin.utils``  — JSONL, config redaction, path validation helpers
    * ``tools.admin.validation``  — config/provider validation helpers
    * ``tools.admin.coding_helpers``  — coding loop, benchmark helpers
    * ``tools.admin.workflow_helpers``  — workflow graph, Flowise, plugin helpers
    * ``tools.admin.strategy_helpers``  — policy audit, strategy, MCP, satellite
    * ``tools.admin.training_helpers``  — training pipeline, online policy, release
    * ``tools.admin.observability_helpers``  — profiling, Tool/LLM failure analysis
    * ``runtime.task_store``  — ``TaskExecutionStore`` class
"""

from __future__ import annotations

import contextvars
from typing import Any, Dict, Optional

from runtime.config_manager import config

# ---------------------------------------------------------------------------
# Re-export from tools.admin.state (runtime globals, getters, buffers, paths)
# ---------------------------------------------------------------------------
from tools.admin.state import (  # noqa: F401
    logger,
    # Runtime globals (still module-level)
    API_QUEUES,
    EVAL_BENCHMARK_MANAGER,
    TRAINING_JOB_MANAGER,
    TRAINING_BRIDGE_MANAGER,
    ONLINE_POLICY_LOOP_MANAGER,
    PERSONA_EVAL_MANAGER,
    PERSONA_RUNTIME_MANAGER,
    # Accessor functions
    get_usage_tracker,
    get_llm_router,
    get_trajectory_store,
    get_prompt_cache_tracker,
    get_tool_batching_tracker,
    get_tool_registry,
    get_canvas_state,
    get_cron_scheduler,
    get_hook_bus,
    get_hook_token,
    get_gmail_push_manager,
    get_whatsapp_channel,
    get_teams_channel,
    get_google_chat_channel,
    # Satellite
    SATELLITE_SOURCES,
    SATELLITE_SESSION_MANAGER,
    # Path constants
    _PROJECT_ROOT,
    _FAVICON_ICO_PATH,
    _WORKFLOW_GRAPH_DIR,
    _POLICY_AUDIT_LOG_PATH,
    _STRATEGY_SNAPSHOT_LOG_PATH,
    _WEB_ONBOARDING_GUIDE_PATH,
    _MEMORY_TURN_HEALTH_LOG_PATH,
    _TOOL_PERSIST_LOG_PATH,
    _EXPORT_DEFAULT_DIR,
    _EXPORT_DEFAULT_ALLOWED_DIRS,
    _PROTECTED_EXPORT_TARGETS,
    _ATOMIC_OBJECT_UPDATE_PATHS,
    # Buffers
    _log_buffer,
    _policy_audit_buffer,
    _strategy_change_history,
    _llm_history,
    _workflow_run_history,
    _alert_buffer,
    _coding_quality_history,
    _coding_benchmark_history,
    _coding_benchmark_scheduler_state,
    _gui_simple_benchmark_history,
    _mcp_rate_counts,
    _mcp_audit_buffer,
)

# ---------------------------------------------------------------------------
# Re-export from tools.admin.utils (helpers)
# ---------------------------------------------------------------------------
from tools.admin.utils import (  # noqa: F401
    _append_jsonl_record,
    _read_jsonl_tail,
    _dedupe_dict_rows,
    _is_sensitive_config_keypath,
    _redact_config,
    _filter_masked_sensitive,
    _flatten_config,
    _resolve_export_output_path,
    _is_subpath,
    _MISSING,
    _TOOL_ERROR_PATTERN,
)

# ---------------------------------------------------------------------------
# Re-export from runtime.task_store
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Re-export from runtime.task_store
# ---------------------------------------------------------------------------
from runtime.task_store import TaskExecutionStore  # noqa: F401

# ---------------------------------------------------------------------------
# Re-export from tools.admin.validation
# ---------------------------------------------------------------------------
from tools.admin.validation import (  # noqa: F401
    _get_nested_payload_value,
    _collect_atomic_object_updates,
    _reject_provider_config_in_settings,
    _validate_provider_entry,
    _validate_deployment_target_entry,
)

# ---------------------------------------------------------------------------
# Re-export from tools.admin.coding_helpers
# ---------------------------------------------------------------------------
from tools.admin.coding_helpers import (  # noqa: F401
    _record_coding_quality_event,
    _parse_tool_error_result,
    _assess_coding_benchmark_health,
    _auto_link_release_gate_by_coding_benchmark,
    _apply_edit_operation,
    _execute_deterministic_coding_loop,
    _run_coding_benchmark_suite,
    _maybe_run_scheduled_coding_benchmark,
    TASK_RUN_STORE,
)

# ---------------------------------------------------------------------------
# Re-export from tools.admin.workflow_helpers
# ---------------------------------------------------------------------------
from tools.admin.workflow_helpers import (  # noqa: F401
    _summarize_flowise_errors,
    _flowise_migration_replacement,
    _classify_workflow_validation_error,
    _safe_task_path,
    _run_verify_command,
    _render_workflow_template,
    _default_flowise_roundtrip_cases,
    _workflow_roundtrip_semantic_signature,
    _simulate_workflow_roundtrip_output,
    _plugin_loader,
    _plugin_install_base,
    _scan_plugin_source_for_threats,
    _plugin_market_snapshot,
    _memory_recall_regression_settings,
    _apply_memory_recall_gate_linkage,
    _workflow_graph_path,
    _validate_workflow_graph,
    _execute_workflow_graph,
)

# ---------------------------------------------------------------------------
# Re-export from tools.admin.strategy_helpers
# ---------------------------------------------------------------------------
from tools.admin.strategy_helpers import (  # noqa: F401
    _mcp_actor,
    _mcp_rate_limit_check,
    _mcp_rate_events,
    _append_policy_audit,
    _capture_strategy_snapshot,
    _find_strategy_snapshot,
    _apply_strategy_snapshot,
    _is_success_status,
    _merge_error_code_counts,
    _append_workflow_run_metric,
    _DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS,
    _get_release_gate_health_thresholds,
    _persona_runtime_thresholds,
    _get_satellite_node_config,
    _validate_satellite_node_auth,
    _decode_frame_payload,
    _consume_satellite_frame_budget,
    _get_tool_governance_snapshot,
    _enqueue_chat_message,
)

# ---------------------------------------------------------------------------
# Re-export from tools.admin.training_helpers
# ---------------------------------------------------------------------------
from tools.admin.training_helpers import (  # noqa: F401
    _prepare_training_inputs,
    _build_rule_prompt_patch,
    _normalize_trajectory_steps,
    _build_task_view,
    _compare_replay_steps,
    _build_resume_payload,
    _unique_str_list,
    _normalize_router_strategy,
    _apply_trainer_prompt_patch,
    _build_training_publish_diff,
    _score_training_job,
    _classify_training_failure_label,
    _build_training_release_explanation,
    _resolve_training_publish_rollout,
    _resolve_training_release_approval,
    _evaluate_training_release_canary_guard,
    _audit_mcp_response,
    _mcp_response_ok,
    _mcp_response_error,
    _mcp_text_resource,
    _summarize_training_output,
    _resolve_online_policy_gate_thresholds,
    _resolve_online_policy_offpolicy_config,
)

# ---------------------------------------------------------------------------
# Re-export from tools.admin.observability_helpers
# ---------------------------------------------------------------------------
from tools.admin.observability_helpers import (  # noqa: F401
    _parse_tool_result_stats,
    _p95,
    _build_llm_tool_failure_profile,
    _build_tool_timing_profile,
    _get_eval_benchmark_manager,
)

# ---------------------------------------------------------------------------
# Legacy constants (kept here for backward compatibility)
# ---------------------------------------------------------------------------
_MAX_WS_MESSAGE_BYTES = int(config.get("api.max_ws_message_bytes", 256 * 1024))
_MAX_CHAT_MESSAGE_CHARS = int(config.get("api.max_chat_message_chars", 8000))

from tools.admin.state import _mcp_request_ctx  # noqa: F401

# ---------------------------------------------------------------------------
# Lazy re-exports (to avoid circular imports at startup)
# ---------------------------------------------------------------------------

def get_provider_registry():
    """Lazy import from runtime.provider_registry."""
    from runtime.provider_registry import get_provider_registry as _impl
    return _impl()


def get_deployment_orchestrator():
    """Lazy import from runtime.deployment_orchestrator."""
    from runtime.deployment_orchestrator import get_deployment_orchestrator as _impl
    return _impl()


def get_evolution():
    """Lazy import from soul.evolution."""
    from soul.evolution import get_evolution as _impl
    return _impl()


def get_owner_manager():
    """Lazy import from security.owner."""
    from security.owner import get_owner_manager as _impl
    return _impl()

def _assess_release_gate_workflow_health(*args, **kwargs):
    from tools.admin.debug import _assess_release_gate_workflow_health as _impl
    return _impl(*args, **kwargs)

def _build_workflow_observability_metrics(*args, **kwargs):
    from tools.admin.system import _build_workflow_observability_metrics as _impl
    return _impl(*args, **kwargs)

def _latest_persona_consistency_signal(*args, **kwargs):
    from tools.admin.system import _latest_persona_consistency_signal as _impl
    return _impl(*args, **kwargs)

def _build_coding_quality_metrics(*args, **kwargs):
    from tools.admin.system import _build_coding_quality_metrics as _impl
    return _impl(*args, **kwargs)


# ---------------------------------------------------------------------------
# Backward-compat: module-level __getattr__ for removed globals
# ---------------------------------------------------------------------------
_COMPAT_GETTERS = {
    'CANVAS_STATE': 'get_canvas_state',
    'GMAIL_PUSH_MANAGER': 'get_gmail_push_manager',
    'CRON_SCHEDULER': 'get_cron_scheduler',
    'TOOL_REGISTRY': 'get_tool_registry',
    'LLM_ROUTER': 'get_llm_router',
    'PROMPT_CACHE_TRACKER': 'get_prompt_cache_tracker',
    'TOOL_BATCHING_TRACKER': 'get_tool_batching_tracker',
    'TRAJECTORY_STORE': 'get_trajectory_store',
    'HOOK_BUS': 'get_hook_bus',
    'HOOK_TOKEN': 'get_hook_token',
    'WHATSAPP_CHANNEL': 'get_whatsapp_channel',
    'TEAMS_CHANNEL': 'get_teams_channel',
    'GOOGLE_CHAT_CHANNEL': 'get_google_chat_channel',
    'USAGE_TRACKER': 'get_usage_tracker',
}

def __getattr__(name: str):
    getter_name = _COMPAT_GETTERS.get(name)
    if getter_name is not None:
        from tools.admin import state as _st
        return getattr(_st, getter_name)()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
