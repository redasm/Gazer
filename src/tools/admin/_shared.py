"""Shared state and utilities for Admin API routers.

This module re-exports symbols from focused sub-modules for backward
compatibility.  New code should import from the canonical locations:

    * ``tools.admin.state``  — runtime globals, getters, buffers, path constants
    * ``tools.admin.utils``  — JSONL, config redaction, path validation helpers
    * ``runtime.task_store``  — ``TaskExecutionStore`` class
"""

from __future__ import annotations

from fastapi import HTTPException
import collections
import contextvars
import asyncio
import copy
from flow.flowise_interop import flowise_migration_suggestion
import json
import logging
import re
from datetime import datetime
import time
from tools.base import ToolSafetyTier
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import dataclasses
import importlib
import os
import shutil
import tempfile
from collections import defaultdict

from runtime.config_manager import config, is_sensitive_config_path

if TYPE_CHECKING:
    from tools.canvas import CanvasState
    from scheduler.cron import CronScheduler

# ---------------------------------------------------------------------------
# Re-export from tools.admin.state (runtime globals, getters, buffers, paths)
# ---------------------------------------------------------------------------
from tools.admin.state import (  # noqa: F401
    logger,
    # Runtime globals
    API_QUEUES,
    CANVAS_STATE,
    GMAIL_PUSH_MANAGER,
    CRON_SCHEDULER,
    _LOCAL_CRON_SCHEDULER_ACTIVE,
    TOOL_REGISTRY,
    LLM_ROUTER,
    ORCHESTRATOR,
    PROMPT_CACHE_TRACKER,
    TOOL_BATCHING_TRACKER,
    TRAJECTORY_STORE,
    EVAL_BENCHMARK_MANAGER,
    TRAINING_JOB_MANAGER,
    TRAINING_BRIDGE_MANAGER,
    ONLINE_POLICY_LOOP_MANAGER,
    PERSONA_EVAL_MANAGER,
    PERSONA_RUNTIME_MANAGER,
    HOOK_BUS,
    HOOK_TOKEN,
    WHATSAPP_CHANNEL,
    TEAMS_CHANNEL,
    GOOGLE_CHAT_CHANNEL,
    USAGE_TRACKER,
    IPC_USAGE_SNAPSHOT,
    IPC_ROUTER_STATUS,
    # Accessor functions
    get_usage_tracker,
    get_llm_router,
    get_trajectory_store,
    get_prompt_cache_tracker,
    get_tool_batching_tracker,
    get_tool_registry,
    get_orchestrator,
    get_canvas_state,
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
from runtime.task_store import TaskExecutionStore  # noqa: F401

TASK_RUN_STORE = TaskExecutionStore()

def _record_coding_quality_event(event: Dict[str, Any]):
    import time
    event["ts"] = time.time()
    _coding_quality_history.append(event)

# MCP rate-limit events
_mcp_rate_events = collections.defaultdict(collections.deque)




# ---------------------------------------------------------------------------
# Migrated helper functions (from admin_api.py)
# ---------------------------------------------------------------------------

def _get_nested_payload_value(payload: Any, dot_path: str) -> Any:
    current = payload
    for segment in str(dot_path).split("."):
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current



def _collect_atomic_object_updates(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    updates: Dict[str, Any] = {}
    for dot_path in _ATOMIC_OBJECT_UPDATE_PATHS:
        value = _get_nested_payload_value(payload, dot_path)
        if value is _MISSING:
            continue
        updates[dot_path] = value
    return updates



def _reject_provider_config_in_settings(payload: Any) -> None:
    """Disallow provider registry updates via /config."""
    if not isinstance(payload, dict):
        return
    models = payload.get("models")
    if not isinstance(models, dict):
        return
    if "providers" in models:
        raise HTTPException(
            status_code=400,
            detail=(
                "models.providers is managed in provider registry. "
                "Use /model-providers endpoints instead."
            ),
        )



def _validate_provider_entry(provider_name: str, provider: Any) -> Dict[str, Any]:
    """Validate and normalize a provider config payload."""
    if not isinstance(provider, dict):
        raise HTTPException(status_code=400, detail="provider config must be an object")

    name = str(provider_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="provider name is required")

    alias_map = {
        "baseUrl": "base_url",
        "apiKey": "api_key",
        "api_mode": "api",
        "auth_header": "authHeader",
        "strictApiMode": "strict_api_mode",
        "reasoningParam": "reasoning_param",
    }
    allowed_keys = {
        "base_url",
        "baseUrl",
        "api_key",
        "apiKey",
        "default_model",
        "api",
        "api_mode",
        "auth",
        "authHeader",
        "auth_header",
        "headers",
        "strict_api_mode",
        "strictApiMode",
        "reasoning_param",
        "reasoningParam",
        "models",
        "agents",
    }
    unknown_keys = sorted(
        key for key in provider.keys() if str(key) not in allowed_keys
    )
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{name}' has unknown fields {unknown_keys}. "
                "Allowed fields: api/base_url/api_key/auth/authHeader/headers/"
                "strict_api_mode/reasoning_param/models/agents/default_model."
            ),
        )

    candidate: Dict[str, Any] = {}
    for key, value in provider.items():
        source_key = str(key)
        target_key = alias_map.get(source_key, source_key)
        if target_key in provider:
            # Keep canonical field when both canonical + alias are provided.
            if target_key != source_key:
                continue
        candidate[target_key] = value

    known_api_modes = {
        "openai-responses",
        "openai-completions",
        "responses",
        "chat-completions",
        "openai_response",
    }
    valid_input_types = {"text", "image", "audio"}
    valid_auth_modes = {"", "api-key", "bearer", "none"}
    api = str(candidate.get("api", "") or "").strip()
    api_mode = str(candidate.get("api_mode", "") or "").strip()
    if api_mode and not api:
        api = api_mode

    raw_api_key = candidate.get("api_key")
    if isinstance(raw_api_key, str):
        api_key_trimmed = raw_api_key.strip()
        if not api and api_key_trimmed.lower() in known_api_modes:
            raise HTTPException(
                status_code=400,
                detail="api_key value looks like API mode. Put it in 'api' and use api_key for credentials.",
            )

    base_url_raw = candidate.get("base_url", "")
    if base_url_raw is None:
        base_url_raw = ""
    if not isinstance(base_url_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'base_url' must be a string.")
    base_url = base_url_raw.strip()

    api_key_raw = candidate.get("api_key", "")
    if api_key_raw is None:
        api_key_raw = ""
    if not isinstance(api_key_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'api_key' must be a string.")
    api_key = api_key_raw.strip()

    default_model_raw = candidate.get("default_model", "")
    if default_model_raw is None:
        default_model_raw = ""
    if not isinstance(default_model_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'default_model' must be a string.")
    default_model = default_model_raw.strip()

    auth_raw = candidate.get("auth", "")
    if auth_raw is None:
        auth_raw = ""
    if not isinstance(auth_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'auth' must be a string.")
    auth_mode = auth_raw.strip().lower()
    if auth_mode not in valid_auth_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{name}' field 'auth' must be one of {sorted(valid_auth_modes)}.",
        )

    headers_raw = candidate.get("headers")
    if headers_raw is not None and not isinstance(headers_raw, dict):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'headers' must be an object.")
    headers: Dict[str, str] = {}
    if isinstance(headers_raw, dict):
        for key, value in headers_raw.items():
            header_name = str(key or "").strip()
            if not header_name:
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' headers has an empty key.",
                )
            if isinstance(value, (dict, list)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' headers['{header_name}'] must be scalar.",
                )
            headers[header_name] = str(value)

    auth_header_raw = candidate.get("authHeader")
    if auth_header_raw is not None and not isinstance(auth_header_raw, bool):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'authHeader' must be boolean.")
    auth_header = bool(auth_header_raw) if isinstance(auth_header_raw, bool) else False

    strict_api_mode_raw = candidate.get("strict_api_mode")
    if strict_api_mode_raw is not None and not isinstance(strict_api_mode_raw, bool):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{name}' field 'strict_api_mode' must be boolean.",
        )
    strict_api_mode = bool(strict_api_mode_raw) if isinstance(strict_api_mode_raw, bool) else True

    reasoning_param_raw = candidate.get("reasoning_param")
    if reasoning_param_raw is not None and not isinstance(reasoning_param_raw, bool):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{name}' field 'reasoning_param' must be boolean.",
        )
    reasoning_param = reasoning_param_raw if isinstance(reasoning_param_raw, bool) else None

    models_cfg = candidate.get("models")
    normalized_models: List[Dict[str, Any]] = []
    if models_cfg is not None:
        if not isinstance(models_cfg, list):
            raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'models' must be an array.")
        for idx, entry in enumerate(models_cfg):
            if not isinstance(entry, dict):
                raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}] must be an object.")
            model_id = str(entry.get("id") or entry.get("name") or "").strip()
            if not model_id:
                raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}] requires 'id' or 'name'.")
            normalized_entry: Dict[str, Any] = {}
            if "id" in entry:
                normalized_entry["id"] = str(entry.get("id") or "").strip()
            if "name" in entry:
                normalized_entry["name"] = str(entry.get("name") or "").strip()
            input_types = entry.get("input")
            if input_types is not None:
                if not isinstance(input_types, list):
                    raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}].input must be an array.")
                invalid_inputs = sorted(
                    str(item).strip().lower()
                    for item in input_types
                    if str(item).strip().lower() not in valid_input_types
                )
                if invalid_inputs:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Provider '{name}' models[{idx}].input has invalid values "
                            f"{invalid_inputs}; allowed: {sorted(valid_input_types)}."
                        ),
                    )
                normalized_entry["input"] = [str(item).strip().lower() for item in input_types if str(item).strip()]
            for keys in (("maxTokens", "max_tokens"), ("contextWindow", "context_window")):
                raw_value = None
                key_name = keys[0]
                for key in keys:
                    if key in entry:
                        raw_value = entry.get(key)
                        key_name = key
                        break
                if raw_value is not None:
                    try:
                        parsed = int(raw_value)
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' models[{idx}].{key_name} must be an integer.",
                        )
                    if parsed <= 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' models[{idx}].{key_name} must be > 0.",
                        )
                    normalized_entry[key_name] = parsed
            if "reasoning" in entry and not isinstance(entry.get("reasoning"), bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' models[{idx}].reasoning must be boolean.",
                )
            if "reasoning" in entry:
                normalized_entry["reasoning"] = bool(entry.get("reasoning"))
            cost_cfg = entry.get("cost")
            if cost_cfg is not None:
                if not isinstance(cost_cfg, dict):
                    raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}].cost must be an object.")
                normalized_cost: Dict[str, float] = {}
                for cost_key in ("input", "output", "cacheRead", "cacheWrite"):
                    if cost_key not in cost_cfg:
                        continue
                    try:
                        normalized_cost[cost_key] = float(cost_cfg.get(cost_key))
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' models[{idx}].cost.{cost_key} must be numeric.",
                        )
                normalized_entry["cost"] = normalized_cost

            for key, value in entry.items():
                if key in normalized_entry:
                    continue
                if key in {"max_tokens", "context_window"}:
                    continue
                normalized_entry[key] = value
            if "id" not in normalized_entry and model_id:
                normalized_entry["id"] = model_id
            normalized_models.append(normalized_entry)

    agents_cfg = candidate.get("agents")
    if agents_cfg is None:
        normalized_agents: Dict[str, Any] = {}
    else:
        if not isinstance(agents_cfg, dict):
            raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'agents' must be an object.")
        normalized_agents = dict(agents_cfg)
        defaults_cfg = normalized_agents.get("defaults")
        if defaults_cfg is not None and not isinstance(defaults_cfg, dict):
            raise HTTPException(status_code=400, detail=f"Provider '{name}' agents.defaults must be an object.")
        if isinstance(defaults_cfg, dict):
            model_cfg = defaults_cfg.get("model")
            if model_cfg is not None and not isinstance(model_cfg, dict):
                raise HTTPException(status_code=400, detail=f"Provider '{name}' agents.defaults.model must be an object.")
            if isinstance(model_cfg, dict):
                primary = model_cfg.get("primary")
                if primary is not None and not isinstance(primary, str):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider '{name}' agents.defaults.model.primary must be a string.",
                    )
            models_alias_cfg = defaults_cfg.get("models")
            if models_alias_cfg is not None and not isinstance(models_alias_cfg, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.models must be an object.",
                )
            workspace = defaults_cfg.get("workspace")
            if workspace is not None and not isinstance(workspace, str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.workspace must be a string.",
                )
            compaction_cfg = defaults_cfg.get("compaction")
            if compaction_cfg is not None and not isinstance(compaction_cfg, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.compaction must be an object.",
                )
            if isinstance(compaction_cfg, dict) and "mode" in compaction_cfg and not isinstance(compaction_cfg.get("mode"), str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.compaction.mode must be a string.",
                )
            for key_name in ("maxConcurrent",):
                if key_name in defaults_cfg:
                    try:
                        parsed = int(defaults_cfg.get(key_name))
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' agents.defaults.{key_name} must be an integer.",
                        )
                    if parsed <= 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' agents.defaults.{key_name} must be > 0.",
                        )
            subagents_cfg = defaults_cfg.get("subagents")
            if subagents_cfg is not None and not isinstance(subagents_cfg, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.subagents must be an object.",
                )
            if isinstance(subagents_cfg, dict) and "maxConcurrent" in subagents_cfg:
                try:
                    parsed = int(subagents_cfg.get("maxConcurrent"))
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider '{name}' agents.defaults.subagents.maxConcurrent must be an integer.",
                    )
                if parsed <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider '{name}' agents.defaults.subagents.maxConcurrent must be > 0.",
                    )

    normalized: Dict[str, Any] = {
        "base_url": base_url,
        "api_key": api_key,
        "default_model": default_model,
        "api": api,
        "auth": auth_mode,
        "authHeader": auth_header,
        "headers": headers,
        "strict_api_mode": strict_api_mode,
        "reasoning_param": reasoning_param,
        "models": normalized_models,
        "agents": normalized_agents,
    }
    return normalized



def _validate_deployment_target_entry(target_id: str, target: Any) -> Dict[str, Any]:
    """Validate and normalize one deployment target payload."""
    if not isinstance(target, dict):
        raise HTTPException(status_code=400, detail="deployment target must be an object")
    name = str(target_id or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="target_id is required")

    provider_name = str(target.get("provider", "")).strip()
    if not provider_name:
        raise HTTPException(status_code=400, detail="deployment target 'provider' is required")

    target_type = str(target.get("type", "gateway") or "gateway").strip().lower()
    valid_types = {"local", "gateway", "dedicated", "provider"}
    if target_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"deployment target 'type' must be one of {sorted(valid_types)}",
        )

    provider_cfg = get_provider_registry().get_provider(provider_name)
    explicit_base_url = str(target.get("base_url", "") or "").strip()
    explicit_model = str(target.get("default_model", "") or "").strip()
    if not provider_cfg and (not explicit_base_url or not explicit_model):
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider '{provider_name}' not found in provider registry; "
                "set both 'base_url' and 'default_model' for explicit target override."
            ),
        )

    enabled_raw = target.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise HTTPException(status_code=400, detail="deployment target 'enabled' must be boolean")

    capacity_raw = target.get("capacity_rpm", None)
    latency_raw = target.get("latency_target_ms", None)
    weight_raw = target.get("traffic_weight", None)
    if capacity_raw is not None:
        try:
            if int(capacity_raw) <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'capacity_rpm' must be an integer > 0")
    if latency_raw is not None:
        try:
            if float(latency_raw) <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'latency_target_ms' must be > 0")
    if weight_raw is not None:
        try:
            if float(weight_raw) <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'traffic_weight' must be > 0")

    headers_raw = target.get("headers", None)
    if headers_raw is not None and not isinstance(headers_raw, dict):
        raise HTTPException(status_code=400, detail="'headers' must be an object when provided")

    normalized = {
        "provider": provider_name,
        "type": target_type,
        "enabled": bool(enabled_raw),
        "profile": str(target.get("profile", "") or "").strip(),
        "health_url": str(target.get("health_url", "") or "").strip(),
        "base_url": explicit_base_url,
        "api_key": target.get("api_key", ""),
        "default_model": explicit_model,
        "api": str(target.get("api", "") or "").strip(),
        "cost_tier": str(target.get("cost_tier", "") or "").strip(),
        "headers": headers_raw if isinstance(headers_raw, dict) else {},
    }
    if capacity_raw is not None:
        normalized["capacity_rpm"] = int(capacity_raw)
    if latency_raw is not None:
        normalized["latency_target_ms"] = float(latency_raw)
    if weight_raw is not None:
        normalized["traffic_weight"] = float(weight_raw)
    return normalized



def _parse_tool_error_result(text: str) -> Dict[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return {"code": "UNKNOWN_ERROR", "message": ""}
    match = _TOOL_ERROR_PATTERN.match(raw)
    if not match:
        return {"code": "UNKNOWN_ERROR", "message": raw[:200]}
    return {
        "code": str(match.group(1) or "UNKNOWN_ERROR").strip().upper(),
        "message": str(match.group(2) or "").strip(),
    }



def _assess_coding_benchmark_health(window: int = 20) -> Dict[str, Any]:
    size = max(1, min(int(window), 200))
    items = list(_coding_benchmark_history)[-size:]
    threshold_cfg = config.get("security.coding_benchmark_gate", {}) or {}
    if not isinstance(threshold_cfg, dict):
        threshold_cfg = {}

    def _float_cfg(key: str, default: float) -> float:
        try:
            return float(threshold_cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _int_cfg(key: str, default: int) -> int:
        try:
            return int(threshold_cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    thresholds = {
        "warning_avg_score": _float_cfg("warning_avg_score", 0.8),
        "critical_avg_score": _float_cfg("critical_avg_score", 0.6),
        "warning_latest_score": _float_cfg("warning_latest_score", 0.75),
        "critical_latest_score": _float_cfg("critical_latest_score", 0.5),
        "warning_fail_runs": max(1, _int_cfg("warning_fail_runs", 2)),
        "critical_fail_runs": max(1, _int_cfg("critical_fail_runs", 3)),
    }

    if not items:
        return {
            "level": "unknown",
            "recommend_block_high_risk": False,
            "message": "coding_benchmark_no_data",
            "signals": {},
            "thresholds": thresholds,
            "window": size,
        }

    scores = [float(item.get("score", 0.0) or 0.0) for item in items]
    avg_score = sum(scores) / len(scores)
    latest_score = float(items[-1].get("score", 0.0) or 0.0)
    fail_runs = sum(1 for score in scores if score < thresholds["warning_avg_score"])
    signals = {
        "avg_score": round(avg_score, 4),
        "latest_score": round(latest_score, 4),
        "fail_runs": fail_runs,
        "total_runs": len(items),
    }

    critical = (
        avg_score < thresholds["critical_avg_score"]
        or latest_score < thresholds["critical_latest_score"]
        or fail_runs >= thresholds["critical_fail_runs"]
    )
    warning = (
        avg_score < thresholds["warning_avg_score"]
        or latest_score < thresholds["warning_latest_score"]
        or fail_runs >= thresholds["warning_fail_runs"]
    )

    if critical:
        return {
            "level": "critical",
            "recommend_block_high_risk": True,
            "message": "coding_benchmark_critical",
            "signals": signals,
            "thresholds": thresholds,
            "window": size,
        }
    if warning:
        return {
            "level": "warning",
            "recommend_block_high_risk": False,
            "message": "coding_benchmark_warning",
            "signals": signals,
            "thresholds": thresholds,
            "window": size,
        }
    return {
        "level": "healthy",
        "recommend_block_high_risk": False,
        "message": "coding_benchmark_healthy",
        "signals": signals,
        "thresholds": thresholds,
        "window": size,
    }



def _auto_link_release_gate_by_coding_benchmark(
    *,
    manager: EvalBenchmarkManager,
    gate: Dict[str, Any],
    health: Dict[str, Any],
) -> Dict[str, Any]:
    current_blocked = bool((gate or {}).get("blocked", False))
    recommend_block = bool((health or {}).get("recommend_block_high_risk", False))
    level = str((health or {}).get("level", "")).strip().lower()
    actions: Dict[str, Any] = {"changed_gate": False, "created_task": False, "resolved_tasks": 0}

    if recommend_block != current_blocked:
        manager.set_release_gate_status(
            blocked=recommend_block,
            reason=str((health or {}).get("message", "")).strip() or "coding_benchmark_auto_link",
            source="coding_benchmark_auto_link",
            metadata={"level": level, "signals": health.get("signals", {})},
        )
        actions["changed_gate"] = True
        actions["blocked"] = recommend_block

    fail_threshold_raw = config.get("security.optimization_fail_streak_threshold", 2)
    try:
        fail_threshold = max(1, int(fail_threshold_raw))
    except (TypeError, ValueError):
        fail_threshold = 2
    dataset_id = "coding_benchmark_auto"
    if recommend_block:
        synthetic_report = {
            "quality_gate": {
                "blocked": True,
                "reasons": [str((health or {}).get("message", "")).strip() or "coding_benchmark_degraded"],
            }
        }
        optimization = manager.register_gate_result(
            dataset_id=dataset_id,
            report=synthetic_report,
            fail_streak_threshold=fail_threshold,
        )
        actions["created_task"] = bool((optimization or {}).get("task_created", False))
        actions["fail_streak"] = int((optimization or {}).get("fail_streak", 0) or 0)
    else:
        open_items = manager.list_optimization_tasks(limit=200, status="open", dataset_id=dataset_id)
        resolved = 0
        for item in open_items:
            task_id = str(item.get("task_id", "")).strip()
            if not task_id:
                continue
            updated = manager.set_optimization_task_status(
                task_id=task_id,
                status="resolved",
                note="auto_resolved_by_coding_benchmark_recovery",
            )
            if updated is not None:
                resolved += 1
        actions["resolved_tasks"] = resolved
    return actions



def _execute_deterministic_coding_loop(
    *,
    task_id: str,
    run_id: str,
    goal: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    started = time.time()
    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        raise HTTPException(status_code=400, detail="deterministic mode requires non-empty 'edits'")
    max_retries_raw = payload.get("max_retries", 1)
    try:
        max_retries = max(0, min(int(max_retries_raw), 3))
    except Exception:
        max_retries = 1
    timeout_raw = payload.get("test_timeout_seconds", 240)
    try:
        timeout_seconds = max(10, min(int(timeout_raw), 1200))
    except Exception:
        timeout_seconds = 240
    test_commands = payload.get("test_commands")
    if not isinstance(test_commands, list):
        test_commands = []
    test_commands = [str(item).strip() for item in test_commands if str(item).strip()]

    TASK_RUN_STORE.add_checkpoint(task_id, stage="discover", status="running", note="collect_input")
    search_pattern = str(payload.get("search_pattern", "") or "").strip()
    discover_hits: List[str] = []
    if search_pattern:
        for rec in edits:
            fp = str((rec or {}).get("file", "") or "").strip()
            if fp:
                discover_hits.append(fp)
    TASK_RUN_STORE.add_checkpoint(
        task_id,
        stage="discover",
        status="ok",
        note="input_ready",
        metadata={"search_pattern": search_pattern, "hits": discover_hits[:50]},
    )

    def apply_round(edit_items: List[Dict[str, Any]]) -> tuple[List[str], Dict[str, str], List[Dict[str, Any]]]:
        changed_files: List[str] = []
        backups: Dict[str, str] = {}
        applied: List[Dict[str, Any]] = []
        staged_updates: Dict[str, str] = {}
        for idx, item in enumerate(edit_items):
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail=f"edits[{idx}] must be an object")
            rel_file = str(item.get("file", "") or "").strip()
            if not rel_file:
                raise HTTPException(status_code=400, detail=f"edits[{idx}] requires file")
            path = _safe_task_path(rel_file)
            path_key = str(path)
            original = staged_updates[path_key] if path_key in staged_updates else path.read_text(encoding="utf-8")
            if path_key not in backups:
                backups[path_key] = path.read_text(encoding="utf-8")

            updated, ok, mode = _apply_edit_operation(original, item)
            if not ok:
                op = str(item.get("operation", "replace") or "replace")
                raise HTTPException(
                    status_code=400,
                    detail=f"edits[{idx}] operation={op} cannot apply in {rel_file} ({mode})",
                )
            staged_updates[path_key] = updated
            if updated != backups[path_key]:
                changed_files.append(rel_file)
            applied.append({"file": rel_file, "match_mode": mode, "operation": str(item.get("operation", "replace"))})

        # Atomic commit for this round: only write after all edits are validated.
        for p, txt in staged_updates.items():
            Path(p).write_text(txt, encoding="utf-8")
        return sorted(set(changed_files)), backups, applied

    retries = 0
    all_verify_runs: List[Dict[str, Any]] = []
    fallback_used = False
    recovery_count = 0
    final_changed: List[str] = []
    final_applied: List[Dict[str, Any]] = []
    while True:
        TASK_RUN_STORE.add_checkpoint(
            task_id, stage="patch", status="running", note=f"apply_round_{retries + 1}"
        )
        changed_files, backups, applied_info = apply_round(edits)
        final_changed = list(changed_files)
        final_applied = list(applied_info)
        TASK_RUN_STORE.add_checkpoint(
            task_id,
            stage="patch",
            status="ok",
            note="patch_applied",
            metadata={"files_changed": changed_files, "applied": applied_info},
        )

        verify_ok = True
        verify_runs: List[Dict[str, Any]] = []
        if test_commands:
            TASK_RUN_STORE.add_checkpoint(task_id, stage="verify", status="running", note="running_tests")
            for cmd in test_commands:
                one = _run_verify_command(cmd, _PROJECT_ROOT, timeout_seconds=timeout_seconds)
                verify_runs.append(one)
                if not one.get("ok", False):
                    verify_ok = False
            all_verify_runs.extend(verify_runs)
            TASK_RUN_STORE.add_checkpoint(
                task_id,
                stage="verify",
                status="ok" if verify_ok else "error",
                note="tests_completed",
                metadata={"results": verify_runs},
            )

        if verify_ok:
            break

        if retries >= max_retries:
            # Rollback to keep workspace clean on failed deterministic loop.
            for p, txt in backups.items():
                Path(p).write_text(txt, encoding="utf-8")
            TASK_RUN_STORE.add_checkpoint(
                task_id,
                stage="rollback",
                status="ok",
                note="verify_failed_restored_backup",
            )
            raise HTTPException(status_code=400, detail="Verification failed after retries; changes rolled back.")

        retries += 1
        fallback_edits = payload.get("fallback_edits")
        if isinstance(fallback_edits, list) and fallback_edits:
            edits = fallback_edits
            fallback_used = True
            recovery_count += 1
        else:
            # Retry same edits once with rollback first.
            for p, txt in backups.items():
                Path(p).write_text(txt, encoding="utf-8")
            recovery_count += 1

    duration_ms = round((time.time() - started) * 1000.0, 2)
    tests_total = len(test_commands)
    tests_passed = sum(1 for item in all_verify_runs if bool(item.get("ok", False)))
    output = {
        "mode": "deterministic",
        "goal": goal,
        "run_id": run_id,
        "files_changed": final_changed,
        "applied": final_applied,
        "verify": all_verify_runs,
        "verify_ok": tests_passed == tests_total if tests_total > 0 else True,
        "tests_total": tests_total,
        "tests_passed": tests_passed,
        "retries": retries,
        "fallback_used": fallback_used,
        "recovery_count": recovery_count,
        "duration_ms": duration_ms,
    }
    _record_coding_quality_event(
        {
            "task_id": task_id,
            "run_id": run_id,
            "kind": "coding_loop",
            "success": bool(output.get("verify_ok", False)),
            "duration_ms": duration_ms,
            "files_changed": len(final_changed),
            "tests_total": tests_total,
            "tests_passed": tests_passed,
            "retries": retries,
            "fallback_used": fallback_used,
            "recovery_count": recovery_count,
        }
    )
    return output



def _run_coding_benchmark_suite(payload: Dict[str, Any]) -> Dict[str, Any]:
    suite_name = str(payload.get("name", "default_suite") or "default_suite").strip()
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise HTTPException(status_code=400, detail="'cases' must be a non-empty array")
    timeout_raw = payload.get("test_timeout_seconds", 120)
    try:
        timeout_seconds = max(10, min(int(timeout_raw), 1200))
    except Exception:
        timeout_seconds = 120

    case_results: List[Dict[str, Any]] = []
    started = time.time()
    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            raise HTTPException(status_code=400, detail=f"cases[{idx}] must be an object")
        case_name = str(case.get("id", f"case_{idx+1}") or f"case_{idx+1}")
        files = case.get("files")
        edits = case.get("edits")
        if not isinstance(files, dict) or not isinstance(edits, list):
            raise HTTPException(status_code=400, detail=f"cases[{idx}] requires files(object) and edits(array)")
        verify_contains = case.get("verify_contains", {})
        if not isinstance(verify_contains, dict):
            verify_contains = {}
        test_commands = case.get("test_commands", [])
        if not isinstance(test_commands, list):
            test_commands = []

        with tempfile.TemporaryDirectory(prefix="gazer_coding_bench_") as tmpd:
            root = Path(tmpd)
            for rel, content in files.items():
                relp = str(rel or "").strip()
                if not relp:
                    continue
                p = (root / relp).resolve()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(content or ""), encoding="utf-8")

            local_payload = {
                "edits": edits,
                "max_retries": int(case.get("max_retries", 1) or 1),
                "test_commands": [str(cmd).strip() for cmd in test_commands if str(cmd).strip()],
                "test_timeout_seconds": timeout_seconds,
                "fallback_edits": case.get("fallback_edits", []),
            }

            old_root = _PROJECT_ROOT
            try:
                globals()["_PROJECT_ROOT"] = root
                out = _execute_deterministic_coding_loop(
                    task_id=f"bench_{suite_name}_{case_name}",
                    run_id=f"bench_{case_name}",
                    goal=str(case.get("goal", case_name)),
                    payload=local_payload,
                )
                verify_ok = bool(out.get("verify_ok", False))
                contains_ok = True
                contains_errors: List[str] = []
                for rel, needle in verify_contains.items():
                    fp = (root / str(rel)).resolve()
                    text = fp.read_text(encoding="utf-8") if fp.is_file() else ""
                    if str(needle) not in text:
                        contains_ok = False
                        contains_errors.append(f"{rel}:missing:{needle}")
                case_ok = bool(verify_ok and contains_ok)
                case_results.append(
                    {
                        "id": case_name,
                        "success": case_ok,
                        "verify_ok": verify_ok,
                        "contains_ok": contains_ok,
                        "contains_errors": contains_errors,
                        "duration_ms": out.get("duration_ms", 0.0),
                        "files_changed": len(out.get("files_changed", []) or []),
                        "retries": int(out.get("retries", 0) or 0),
                        "recovery_count": int(out.get("recovery_count", 0) or 0),
                    }
                )
            except HTTPException as exc:
                case_results.append(
                    {
                        "id": case_name,
                        "success": False,
                        "verify_ok": False,
                        "contains_ok": False,
                        "contains_errors": [str(exc.detail)],
                        "duration_ms": 0.0,
                        "files_changed": 0,
                        "retries": 0,
                        "recovery_count": 0,
                    }
                )
            finally:
                globals()["_PROJECT_ROOT"] = old_root

    total = len(case_results)
    success = sum(1 for item in case_results if bool(item.get("success", False)))
    score = round(success / total, 4) if total > 0 else 0.0
    duration_ms = round((time.time() - started) * 1000.0, 2)
    summary = {
        "name": suite_name,
        "total_cases": total,
        "success_cases": success,
        "score": score,
        "duration_ms": duration_ms,
        "cases": case_results,
        "ts": time.time(),
    }
    _coding_benchmark_history.append(summary)
    return summary



def _maybe_run_scheduled_coding_benchmark(*, force: bool = False) -> Dict[str, Any]:
    """Run scheduled coding benchmark suite when due."""
    sched_cfg = config.get("security.coding_benchmark_scheduler", {}) or {}
    if not isinstance(sched_cfg, dict):
        sched_cfg = {}
    enabled = bool(sched_cfg.get("enabled", False))
    if not enabled and not force:
        return {"ran": False, "reason": "disabled"}

    payload = sched_cfg.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return {"ran": False, "reason": "empty_cases"}

    interval_raw = sched_cfg.get("interval_seconds", 1800)
    try:
        interval_seconds = max(30, min(int(interval_raw), 86400))
    except Exception:
        interval_seconds = 1800

    now = time.time()
    last_run = float(_coding_benchmark_scheduler_state.get("last_run_ts", 0.0) or 0.0)
    if not force and last_run > 0 and (now - last_run) < interval_seconds:
        return {"ran": False, "reason": "not_due", "next_in_seconds": round(interval_seconds - (now - last_run), 2)}

    summary = _run_coding_benchmark_suite(payload)
    _coding_benchmark_scheduler_state["last_run_ts"] = time.time()
    _coding_benchmark_scheduler_state["last_result"] = summary

    auto_link = bool(sched_cfg.get("auto_link_release_gate", True))
    auto_actions: Dict[str, Any] = {}
    gate: Optional[Dict[str, Any]] = None
    health: Optional[Dict[str, Any]] = None
    if auto_link:
        window_raw = sched_cfg.get("window", 20)
        try:
            window = max(1, min(int(window_raw), 200))
        except Exception:
            window = 20
        manager = _get_eval_benchmark_manager()
        gate = manager.get_release_gate_status()
        health = _assess_coding_benchmark_health(window=window)
        auto_actions = _auto_link_release_gate_by_coding_benchmark(manager=manager, gate=gate, health=health)
        gate = manager.get_release_gate_status()

    result = {
        "ran": True,
        "summary": summary,
        "auto_link": auto_actions,
        "health": health,
        "gate": gate,
    }
    _coding_benchmark_scheduler_state["last_result"] = result
    return result


def _summarize_flowise_errors(errors: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {"total": 0, "node": 0, "edge": 0, "by_code": {}}
    if not isinstance(errors, list):
        return summary
    for item in errors:
        if not isinstance(item, dict):
            continue
        summary["total"] += 1
        level = str(item.get("level", "node")).strip().lower()
        if level not in {"node", "edge"}:
            level = "node"
        summary[level] += 1
        code = str(item.get("code") or item.get("reason") or "unknown_error").strip() or "unknown_error"
        by_code = summary["by_code"]
        by_code[code] = int(by_code.get(code, 0)) + 1
    return summary



def _flowise_migration_replacement(node_name: str) -> Dict[str, str]:
    return flowise_migration_suggestion(node_name)



def _classify_workflow_validation_error(detail: Any) -> str:
    text = str(detail or "").strip().lower()
    if "cycle" in text:
        return "dag_cycle"
    if "reachable output" in text:
        return "no_reachable_output"
    if "condition node" in text and ("tagged" in text or "duplicate" in text):
        return "condition_edges_invalid"
    return "workflow_invalid"



def _safe_task_path(rel_path: str) -> Path:
    target = (_PROJECT_ROOT / rel_path).resolve()
    if not str(target).startswith(str(_PROJECT_ROOT.resolve())):
        raise ValueError("Path traversal detected")
    return target


def _run_verify_command(cmd: str, cwd: Path, timeout_seconds: int = 120) -> Dict[str, Any]:
    import subprocess
    try:
        res = subprocess.run(
            cmd, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout_seconds
        )
        return {
            "exit_code": res.returncode,
            "logs": res.stdout + "\n" + res.stderr
        }
    except subprocess.TimeoutExpired as e:
        return {
            "exit_code": 124,
            "logs": f"Timeout after {timeout_seconds}s\n" + (e.stdout or "") + "\n" + (e.stderr or "")
        }
    except Exception as e:
        return {
            "exit_code": 1,
            "logs": str(e)
        }


def _render_workflow_template(template: Any, ctx: Dict[str, Any]) -> Any:
    if isinstance(template, str):
        result = template
        for k, v in ctx.items():
            if k == "node_outputs":
                continue
            result = result.replace(f"{{{{{k}}}}}", str(v))
        if "node_outputs" in ctx and isinstance(ctx["node_outputs"], dict):
            for node_id, out_val in ctx["node_outputs"].items():
                result = result.replace(f"{{{{node.{node_id}}}}}", str(out_val))
        return result
    elif isinstance(template, dict):
        return {k: _render_workflow_template(v, ctx) for k, v in template.items()}
    elif isinstance(template, list):
        return [_render_workflow_template(item, ctx) for item in template]
    return template



def _default_flowise_roundtrip_cases() -> List[Dict[str, Any]]:
    return [
        {
            "name": "chat_prompt_tool_output",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "p1", "type": "customNode", "data": {"name": "chatPromptTemplate", "inputs": {"template": "Q={{prev}}"}}},
                    {"id": "t1", "type": "customNode", "data": {"name": "tool", "inputs": {"toolName": "echo"}}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "{{prev}}"}}},
                ],
                "edges": [
                    {"source": "in1", "target": "p1"},
                    {"source": "p1", "target": "t1"},
                    {"source": "t1", "target": "out1"},
                ],
            },
        },
        {
            "name": "condition_branch",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "c1", "type": "customNode", "data": {"name": "ifElse", "inputs": {"operator": "contains", "value": "yes"}}},
                    {"id": "ot", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "TRUE:{{prev}}"}}},
                    {"id": "of", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "FALSE:{{prev}}"}}},
                ],
                "edges": [
                    {"source": "in1", "target": "c1"},
                    {"source": "c1", "target": "ot", "label": "true"},
                    {"source": "c1", "target": "of", "label": "false"},
                ],
            },
        },
        {
            "name": "memory_retriever_agent_toolchain",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "m1", "type": "customNode", "data": {"name": "bufferWindowMemory", "inputs": {"memoryPrompt": "M={{prev}}"}}},
                    {"id": "r1", "type": "customNode", "data": {"name": "vectorStoreRetriever"}},
                    {"id": "a1", "type": "customNode", "data": {"name": "conversationalAgent", "inputs": {"systemMessage": "Assistant"}}},
                    {"id": "tc1", "type": "customNode", "data": {"name": "toolChain", "inputs": {"toolName": "echo"}}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
                ],
                "edges": [
                    {"source": "in1", "target": "m1"},
                    {"source": "m1", "target": "r1"},
                    {"source": "r1", "target": "a1"},
                    {"source": "a1", "target": "tc1"},
                    {"source": "tc1", "target": "out1"},
                ],
            },
        },
        {
            "name": "retrieval_qa_chain",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "rq1", "type": "customNode", "data": {"name": "retrievalQAChain"}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "{{prev}}"}}},
                ],
                "edges": [
                    {"source": "in1", "target": "rq1"},
                    {"source": "rq1", "target": "out1"},
                ],
            },
        },
        {
            "name": "web_search_tool_path",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "w1", "type": "customNode", "data": {"name": "webSearch"}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
                ],
                "edges": [
                    {"source": "in1", "target": "w1"},
                    {"source": "w1", "target": "out1"},
                ],
            },
        },
    ]



def _workflow_roundtrip_semantic_signature(workflow: Dict[str, Any]) -> Dict[str, Any]:
    nodes_raw = workflow.get("nodes", []) if isinstance(workflow.get("nodes"), list) else []
    edges_raw = workflow.get("edges", []) if isinstance(workflow.get("edges"), list) else []

    node_sig: List[Dict[str, Any]] = []
    for item in nodes_raw:
        if not isinstance(item, dict):
            continue
        node_type = str(item.get("type", "")).strip().lower()
        cfg_raw = item.get("config", {})
        cfg = dict(cfg_raw) if isinstance(cfg_raw, dict) else {}
        cfg.pop("_flowise", None)
        if node_type == "input":
            normalized = {"default": str(cfg.get("default", ""))}
        elif node_type == "prompt":
            normalized = {"prompt": str(cfg.get("prompt", "{{prev}}"))}
        elif node_type == "tool":
            normalized = {
                "tool_name": str(cfg.get("tool_name", "")).strip(),
                "args": cfg.get("args", {}) if isinstance(cfg.get("args"), dict) else {},
            }
        elif node_type == "condition":
            normalized = {
                "operator": str(cfg.get("operator", "contains")).strip().lower(),
                "value": str(cfg.get("value", "")),
            }
        elif node_type == "output":
            normalized = {"text": str(cfg.get("text", "{{prev}}"))}
        else:
            normalized = cfg
        node_sig.append(
            {
                "id": str(item.get("id", "")).strip(),
                "type": node_type,
                "config": normalized,
            }
        )
    node_sig.sort(key=lambda item: str(item.get("id", "")))

    edge_sig: List[Dict[str, Any]] = []
    for item in edges_raw:
        if not isinstance(item, dict):
            continue
        edge_sig.append(
            {
                "source": str(item.get("source", "")).strip(),
                "target": str(item.get("target", "")).strip(),
                "when": str(item.get("when", "")).strip().lower(),
            }
        )
    edge_sig.sort(key=lambda item: (item["source"], item["target"], item["when"]))
    return {"nodes": node_sig, "edges": edge_sig}



def _simulate_workflow_roundtrip_output(workflow: Dict[str, Any], input_text: str) -> str:
    nodes_raw = workflow.get("nodes", []) if isinstance(workflow.get("nodes"), list) else []
    edges_raw = workflow.get("edges", []) if isinstance(workflow.get("edges"), list) else []
    node_map: Dict[str, Dict[str, Any]] = {}
    for item in nodes_raw:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id", "")).strip()
        if not node_id:
            continue
        node_map[node_id] = item

    incoming_count: Dict[str, int] = {node_id: 0 for node_id in node_map.keys()}
    outgoing: Dict[str, List[Dict[str, Any]]] = {node_id: [] for node_id in node_map.keys()}
    for edge in edges_raw:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in node_map and target in node_map:
            outgoing[source].append(edge)
            incoming_count[target] = incoming_count.get(target, 0) + 1

    pending_predecessors = dict(incoming_count)
    incoming_values: Dict[str, List[str]] = {node_id: [] for node_id in node_map.keys()}
    queue: List[str] = [node_id for node_id, cnt in pending_predecessors.items() if cnt == 0]
    queued = set(queue)
    completed: set[str] = set()

    ctx: Dict[str, Any] = {"input": str(input_text or ""), "prev": str(input_text or ""), "node_outputs": {}}
    final_output = ""
    visited = 0
    max_steps = max(1, min(500, len(node_map) * 8 if node_map else 1))

    while queue and visited < max_steps:
        visited += 1
        current_id = queue.pop(0)
        queued.discard(current_id)
        if current_id in completed:
            continue
        node = node_map.get(current_id)
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", "")).strip().lower()
        node_inputs = incoming_values.get(current_id, [])
        node_prev = str(node_inputs[-1]) if node_inputs else str(ctx.get("prev", ""))
        config = node.get("config", {}) if isinstance(node.get("config"), dict) else {}
        condition_outcome: Optional[bool] = None

        if not bool(node.get("enabled", True)):
            result_text = node_prev
        elif incoming_count.get(current_id, 0) > 0 and not node_inputs:
            result_text = node_prev
        elif node_type == "input":
            default_text = str(config.get("default", "")).strip()
            result_text = str(input_text or "") if str(input_text or "") else default_text
        elif node_type == "prompt":
            prompt = str(config.get("prompt", "{{prev}}"))
            result_text = str(
                _render_workflow_template(
                    prompt,
                    {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
                )
            )
        elif node_type == "tool":
            tool_name = str(config.get("tool_name", "")).strip() or "echo"
            raw_args = config.get("args", {}) if isinstance(config.get("args"), dict) else {}
            rendered_args = _render_workflow_template(
                raw_args,
                {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
            )
            result_text = f"tool:{tool_name}:{json.dumps(rendered_args, ensure_ascii=False, sort_keys=True)}"
        elif node_type == "condition":
            operator = str(config.get("operator", "contains")).strip().lower()
            expected = str(
                _render_workflow_template(
                    config.get("value", ""),
                    {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
                )
            )
            if operator == "equals":
                condition_outcome = node_prev == expected
            elif operator == "not_contains":
                condition_outcome = expected not in node_prev
            else:
                condition_outcome = expected in node_prev
            result_text = "true" if condition_outcome else "false"
        elif node_type == "output":
            result_text = str(
                _render_workflow_template(
                    config.get("text", "{{prev}}"),
                    {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
                )
            )
            final_output = result_text
        else:
            result_text = node_prev

        ctx["node_outputs"][current_id] = result_text
        ctx["prev"] = result_text
        completed.add(current_id)

        outgoing_edges = outgoing.get(current_id, [])
        selected_targets: set[str] = set()
        if node_type == "condition" and condition_outcome is not None:
            tagged_edges = [edge for edge in outgoing_edges if str(edge.get("when", "")).strip().lower() in {"true", "false", "default"}]
            if tagged_edges:
                branch_key = "true" if condition_outcome else "false"
                matched = [
                    edge for edge in tagged_edges if str(edge.get("when", "")).strip().lower() == branch_key
                ]
                if not matched:
                    matched = [
                        edge
                        for edge in tagged_edges
                        if str(edge.get("when", "")).strip().lower() in {"default", ""}
                    ]
                selected_targets = {str(edge.get("target", "")).strip() for edge in matched}
            elif outgoing_edges:
                selected_targets = {str(outgoing_edges[0].get("target", "")).strip()}
        else:
            selected_targets = {str(edge.get("target", "")).strip() for edge in outgoing_edges}

        for edge in outgoing_edges:
            target = str(edge.get("target", "")).strip()
            if target not in pending_predecessors:
                continue
            if target in selected_targets:
                incoming_values[target].append(result_text)
            pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
            if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                queue.append(target)
                queued.add(target)

    return final_output or str(ctx.get("prev", ""))



def _plugin_loader() -> PluginLoader:
    return PluginLoader(workspace=Path.cwd())



def _plugin_install_base(global_install: bool = False) -> Path:
    if global_install:
        return Path.home() / ".gazer" / "extensions"
    return Path("extensions")



def _scan_plugin_source_for_threats(source: Path) -> Dict[str, Any]:
    scan_cfg = config.get("security.threat_scan", {}) or {}
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}
    return threat_scan_directory(source, scan_cfg)



def _plugin_market_snapshot() -> Dict[str, Any]:
    loader = _plugin_loader()
    manifests = loader.discover()
    enabled = {
        str(item).strip()
        for item in (config.get("plugins.enabled", []) or [])
        if str(item).strip()
    }
    disabled = {
        str(item).strip()
        for item in (config.get("plugins.disabled", []) or [])
        if str(item).strip()
    }
    items: List[Dict[str, Any]] = []
    for manifest in manifests.values():
        items.append(
            {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "slot": manifest.slot.value,
                "optional": bool(manifest.optional),
                "description": manifest.description,
                "base_dir": str(manifest.base_dir) if manifest.base_dir else "",
                "enabled": manifest.id in enabled and manifest.id not in disabled,
                "disabled": manifest.id in disabled,
                "integrity_ok": bool(manifest.integrity_ok),
                "signature_ok": bool(manifest.signature_ok),
                "verification_error": str(manifest.verification_error or ""),
            }
        )
    items.sort(key=lambda item: item["id"])
    return {
        "items": items,
        "total": len(items),
        "failed_ids": sorted(loader.failed_ids),
    }


def _memory_recall_regression_settings() -> Dict[str, Any]:
    raw = config.get("memory.recall_regression", {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    thresholds = raw.get("thresholds", {}) if isinstance(raw.get("thresholds", {}), dict) else {}
    gate = raw.get("gate", {}) if isinstance(raw.get("gate", {}), dict) else {}

    def _as_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(low, min(parsed, high))

    def _as_float(value: Any, default: float, low: float, high: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(low, min(parsed, high))

    mode = str(gate.get("mode", "warn")).strip().lower() or "warn"
    if mode not in {"warn", "block", "disabled"}:
        mode = "warn"

    return {
        "enabled": bool(raw.get("enabled", True)),
        "window_days": _as_int(raw.get("window_days", 7), 7, 1, 30),
        "query_set_path": str(raw.get("query_set_path", "")).strip(),
        "top_k": _as_int(raw.get("top_k", 5), 5, 1, 20),
        "min_match_score": _as_float(raw.get("min_match_score", 0.18), 0.18, 0.0, 1.0),
        "thresholds": {
            "min_precision_proxy": _as_float(
                thresholds.get("min_precision_proxy", 0.45), 0.45, 0.0, 1.0
            ),
            "min_recall_proxy": _as_float(
                thresholds.get("min_recall_proxy", 0.45), 0.45, 0.0, 1.0
            ),
            "warning_drop": _as_float(thresholds.get("warning_drop", 0.05), 0.05, 0.0, 1.0),
            "critical_drop": _as_float(thresholds.get("critical_drop", 0.12), 0.12, 0.0, 1.0),
        },
        "gate": {
            "link_release_gate": bool(gate.get("link_release_gate", True)),
            "mode": mode,
            "source": str(gate.get("source", "memory_recall_regression")).strip()
            or "memory_recall_regression",
            "reason_warning": str(
                gate.get("reason_warning", "memory_recall_regression_warning")
            ).strip()
            or "memory_recall_regression_warning",
            "reason_critical": str(
                gate.get("reason_critical", "memory_recall_regression_critical")
            ).strip()
            or "memory_recall_regression_critical",
        },
    }



def _apply_memory_recall_gate_linkage(
    *,
    report: Dict[str, Any],
    enabled: bool,
    apply_gate: bool,
    gate_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    manager = _get_eval_benchmark_manager()
    current_gate = manager.get_release_gate_status()
    level = str((report.get("gate", {}) or {}).get("level", "healthy")).strip().lower()
    mode = str(gate_cfg.get("mode", "warn")).strip().lower() or "warn"
    linkage: Dict[str, Any] = {
        "enabled": bool(enabled and gate_cfg.get("link_release_gate", True)),
        "applied": bool(apply_gate),
        "mode": mode,
        "level": level,
        "alert_only": mode != "block",
        "changed_gate": False,
        "gate": current_gate,
        "signal": {
            "active": level in {"warning", "critical"},
            "reason": str(
                gate_cfg.get("reason_critical", "memory_recall_regression_critical")
                if level == "critical"
                else gate_cfg.get("reason_warning", "memory_recall_regression_warning")
            ),
        },
    }
    if not linkage["enabled"] or not apply_gate or mode in {"disabled", ""}:
        return linkage

    if mode == "block":
        should_block = level == "critical"
        reason = str(
            gate_cfg.get("reason_critical", "memory_recall_regression_critical")
            if should_block
            else "memory_recall_regression_recovered"
        )
        source = str(gate_cfg.get("source", "memory_recall_regression")).strip() or "memory_recall_regression"
        current_blocked = bool(current_gate.get("blocked", False))
        current_source = str(current_gate.get("source", "")).strip()
        should_update = False
        if should_block:
            should_update = (not current_blocked) or (current_source != source)
        else:
            should_update = current_blocked and current_source == source
        if should_update:
            current_gate = manager.set_release_gate_status(
                blocked=should_block,
                reason=reason,
                source=source,
                metadata={
                    "report": "memory_recall_regression",
                    "level": level,
                    "quality_score": (report.get("current_window", {}).get("metrics", {}) or {}).get(
                        "quality_score", 0.0
                    ),
                },
            )
            linkage["changed_gate"] = True
            linkage["gate"] = current_gate
        return linkage

    # warn-mode linkage: signal only, keep gate block status untouched.
    return linkage



def _workflow_graph_path(workflow_id: str) -> Path:
    safe = str(workflow_id or "").strip().replace("/", "_").replace("\\", "_")
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid workflow id")
    _WORKFLOW_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    return _WORKFLOW_GRAPH_DIR / f"{safe}.json"



def _validate_workflow_graph(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Workflow payload must be an object")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="'name' is required")

    nodes_raw = payload.get("nodes", [])
    edges_raw = payload.get("edges", [])
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list):
        raise HTTPException(status_code=400, detail="'nodes' and 'edges' must be arrays")

    allowed_types = {"input", "prompt", "tool", "condition", "output"}
    nodes: List[Dict[str, Any]] = []
    node_ids = set()
    node_type_map: Dict[str, str] = {}
    for idx, item in enumerate(nodes_raw):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}]' must be an object")
        node_id = str(item.get("id", "")).strip()
        node_type = str(item.get("type", "")).strip().lower()
        if not node_id:
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}].id' is required")
        if node_id in node_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate node id '{node_id}'")
        if node_type not in allowed_types:
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}].type' must be one of {sorted(allowed_types)}")
        node_ids.add(node_id)
        node_type_map[node_id] = node_type
        cfg = item.get("config", {})
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}].config' must be an object")
        position = item.get("position", {})
        if position is None:
            position = {}
        if not isinstance(position, dict):
            position = {}
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "label": str(item.get("label", node_id)),
                "enabled": bool(item.get("enabled", True)),
                "locked": bool(item.get("locked", False)),
                "config": cfg,
                "position": {
                    "x": int(position.get("x", 40)),
                    "y": int(position.get("y", 40)),
                },
            }
        )

    edges: List[Dict[str, Any]] = []
    for idx, item in enumerate(edges_raw):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"'edges[{idx}]' must be an object")
        source = str(item.get("source", "")).strip()
        target = str(item.get("target", "")).strip()
        if source not in node_ids or target not in node_ids:
            raise HTTPException(status_code=400, detail=f"'edges[{idx}]' references unknown node")
        when_raw = item.get("when", None)
        when = str(when_raw).strip().lower() if when_raw is not None else ""
        if when not in {"", "true", "false", "default"}:
            raise HTTPException(status_code=400, detail=f"'edges[{idx}].when' must be one of ['', 'true', 'false', 'default']")
        source_type = node_type_map.get(source, "")
        if when and source_type != "condition":
            raise HTTPException(status_code=400, detail=f"'edges[{idx}].when' is only allowed when source node is 'condition'")
        edges.append(
            {
                "id": str(item.get("id", f"edge_{idx}")).strip() or f"edge_{idx}",
                "source": source,
                "target": target,
                "when": when,
            }
        )

    # DAG guardrail: workflow graph must be acyclic.
    graph_incoming: Dict[str, int] = {node_id: 0 for node_id in node_ids}
    graph_outgoing: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in graph_outgoing and target in graph_incoming:
            graph_outgoing[source].append(target)
            graph_incoming[target] += 1

    topo_queue: List[str] = [node_id for node_id, cnt in graph_incoming.items() if cnt == 0]
    visited_count = 0
    while topo_queue:
        current = topo_queue.pop(0)
        visited_count += 1
        for nxt in graph_outgoing.get(current, []):
            graph_incoming[nxt] = max(0, graph_incoming[nxt] - 1)
            if graph_incoming[nxt] == 0:
                topo_queue.append(nxt)
    if visited_count != len(node_ids):
        raise HTTPException(status_code=400, detail="Workflow graph contains a cycle; only DAG is supported")

    # Condition branch consistency checks.
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        if str(node.get("type", "")).strip().lower() != "condition":
            continue
        outgoing_edges = [edge for edge in edges if str(edge.get("source", "")).strip() == node_id]
        if not outgoing_edges:
            continue
        tagged = [edge for edge in outgoing_edges if str(edge.get("when", "")).strip() in {"true", "false", "default"}]
        untagged = [edge for edge in outgoing_edges if str(edge.get("when", "")).strip() == ""]
        if tagged and untagged:
            raise HTTPException(
                status_code=400,
                detail=f"Condition node '{node_id}' cannot mix tagged (true/false/default) and untagged edges",
            )
        if tagged:
            when_values = [str(edge.get("when", "")).strip() for edge in tagged]
            for label in ("true", "false", "default"):
                if when_values.count(label) > 1:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Condition node '{node_id}' has duplicate '{label}' edges",
                    )

    # Reachability guardrail: at least one output node must be reachable from a start node.
    start_nodes = [node_id for node_id, cnt in graph_incoming.items() if cnt == 0]
    input_nodes = [
        str(node.get("id", "")).strip()
        for node in nodes
        if str(node.get("type", "")).strip().lower() == "input"
    ]
    seed_nodes = [node_id for node_id in input_nodes if node_id in graph_outgoing] or start_nodes
    reachable = set(seed_nodes)
    bfs_queue = list(seed_nodes)
    while bfs_queue:
        current = bfs_queue.pop(0)
        for nxt in graph_outgoing.get(current, []):
            if nxt in reachable:
                continue
            reachable.add(nxt)
            bfs_queue.append(nxt)
    reachable_output = any(
        str(node.get("type", "")).strip().lower() == "output" and str(node.get("id", "")).strip() in reachable
        for node in nodes
    )
    if not reachable_output:
        raise HTTPException(status_code=400, detail="Workflow graph has no reachable output node")

    return {
        "id": str(payload.get("id", "")).strip(),
        "name": name,
        "description": str(payload.get("description", "")).strip(),
        "version": int(payload.get("version", 1) or 1),
        "created_at": float(payload.get("created_at", time.time())),
        "updated_at": float(time.time()),
        "nodes": nodes,
        "edges": edges,
    }



async def _execute_workflow_graph(graph: Dict[str, Any], *, input_text: str = "") -> Dict[str, Any]:
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    node_map = {str(item.get("id", "")): item for item in nodes if isinstance(item, dict)}
    incoming_count: Dict[str, int] = {node_id: 0 for node_id in node_map.keys()}
    outgoing: Dict[str, List[Dict[str, Any]]] = {node_id: [] for node_id in node_map.keys()}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in outgoing and target in incoming_count:
            outgoing[source].append(
                {
                    "source": source,
                    "target": target,
                    "when": str(edge.get("when", "")).strip().lower(),
                }
            )
            incoming_count[target] += 1

    start_candidates = [node_id for node_id, count in incoming_count.items() if count == 0]
    if not start_candidates:
        return {"status": "error", "error": "No start node found", "trace": []}
    queue: List[str] = sorted(start_candidates)
    queued = set(queue)
    completed = set()
    pending_predecessors = dict(incoming_count)
    incoming_values: Dict[str, List[str]] = {node_id: [] for node_id in node_map.keys()}
    visited = 0
    max_steps = 200
    trace: List[Dict[str, Any]] = []
    ctx: Dict[str, Any] = {"input": str(input_text or ""), "prev": str(input_text or ""), "node_outputs": {}}
    final_output = str(input_text or "")

    run_started_at = time.perf_counter()

    def _summarize_trace(trace_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        status_counts: Dict[str, int] = {"ok": 0, "warning": 0, "skipped": 0, "error": 0}
        node_duration_ms = 0
        for step in trace_items:
            step_status = str(step.get("status", "")).strip().lower()
            if step_status in status_counts:
                status_counts[step_status] += 1
            try:
                node_duration_ms += max(0, int(step.get("duration_ms", 0) or 0))
            except (TypeError, ValueError):
                continue
        return {
            "trace_nodes": len(trace_items),
            "ok_nodes": status_counts["ok"],
            "warning_nodes": status_counts["warning"],
            "skipped_nodes": status_counts["skipped"],
            "error_nodes": status_counts["error"],
            "node_duration_ms": node_duration_ms,
        }

    while queue and visited < max_steps:
        visited += 1
        current_id = queue.pop(0)
        queued.discard(current_id)
        if current_id in completed:
            continue
        node = node_map.get(current_id)
        if node is None:
            break
        node_type = str(node.get("type", "")).strip().lower()
        node_inputs = incoming_values.get(current_id, [])
        node_prev = str(node_inputs[-1]) if node_inputs else str(ctx.get("prev", ""))
        if not bool(node.get("enabled", True)):
            result_text = node_prev
            trace.append(
                {
                    "node_id": current_id,
                    "node_type": node_type,
                    "status": "skipped",
                    "output": result_text,
                    "reason": "node_disabled",
                    "duration_ms": 0,
                }
            )
            ctx["node_outputs"][current_id] = result_text
            ctx["prev"] = result_text
            completed.add(current_id)
            for edge in outgoing.get(current_id, []):
                target = edge.get("target", "")
                if target not in pending_predecessors:
                    continue
                pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
                if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                    queue.append(target)
                    queued.add(target)
            continue

        if incoming_count.get(current_id, 0) > 0 and not node_inputs:
            result_text = node_prev
            trace.append(
                {
                    "node_id": current_id,
                    "node_type": node_type,
                    "status": "skipped",
                    "output": result_text,
                    "reason": "no_active_input",
                    "duration_ms": 0,
                }
            )
            ctx["node_outputs"][current_id] = result_text
            ctx["prev"] = result_text
            completed.add(current_id)
            for edge in outgoing.get(current_id, []):
                target = edge.get("target", "")
                if target not in pending_predecessors:
                    continue
                pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
                if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                    queue.append(target)
                    queued.add(target)
            continue

        config = node.get("config", {}) if isinstance(node.get("config"), dict) else {}
        result_text = ""
        status = "ok"
        error_text = ""
        condition_outcome: Optional[bool] = None
        timeout_raw = config.get("timeout_ms", 0)
        retries_raw = config.get("retry_count", 0)
        on_error = str(config.get("on_error", "fail")).strip().lower()
        if on_error not in {"fail", "continue", "fallback"}:
            on_error = "fail"
        try:
            timeout_ms = max(0, min(int(timeout_raw), 120000))
        except (TypeError, ValueError):
            timeout_ms = 0
        try:
            retry_count = max(0, min(int(retries_raw), 5))
        except (TypeError, ValueError):
            retry_count = 0
        attempts_total = retry_count + 1
        attempts_used = 0
        node_started_at = time.perf_counter()

        async def _execute_node_once() -> tuple[str, Optional[bool]]:
            node_ctx: Dict[str, Any] = {
                "input": str(input_text or ""),
                "prev": node_prev,
                "node_outputs": ctx.get("node_outputs", {}),
            }
            if node_type == "input":
                default_text = str(config.get("default", "")).strip()
                return (node_ctx["input"] if node_ctx["input"] else default_text), None
            if node_type == "prompt":
                prompt = str(config.get("prompt", "{{prev}}"))
                rendered = str(_render_workflow_template(prompt, node_ctx))
                if LLM_ROUTER is not None and hasattr(LLM_ROUTER, "chat"):
                    resp = await LLM_ROUTER.chat(
                        messages=[{"role": "user", "content": rendered}],
                        tools=[],
                    )
                    text = str(getattr(resp, "content", "") or "")
                    if not text and getattr(resp, "error", None):
                        text = str(getattr(resp, "content", "") or "LLM error")
                    return text, None
                return rendered, None
            if node_type == "tool":
                if TOOL_REGISTRY is None:
                    raise RuntimeError("Tool registry unavailable")
                tool_name = str(config.get("tool_name", "")).strip()
                if not tool_name:
                    raise RuntimeError("tool_name is required")
                raw_args = config.get("args", {})
                if not isinstance(raw_args, dict):
                    raw_args = {}
                tool_args = _render_workflow_template(raw_args, node_ctx)
                text = await TOOL_REGISTRY.execute(
                    tool_name,
                    tool_args,
                    max_tier=ToolSafetyTier.PRIVILEGED,
                    confirmed=False,
                )
                return str(text), None
            if node_type == "condition":
                operator = str(config.get("operator", "contains")).strip().lower()
                expected = str(_render_workflow_template(config.get("value", ""), node_ctx))
                current_text = str(node_ctx.get("prev", ""))
                if operator == "equals":
                    outcome = current_text == expected
                elif operator == "not_contains":
                    outcome = expected not in current_text
                else:
                    outcome = expected in current_text
                return ("true" if outcome else "false"), outcome
            if node_type == "output":
                return str(_render_workflow_template(config.get("text", "{{prev}}"), node_ctx)), None
            return str(node_ctx.get("prev", "")), None

        for attempt_index in range(attempts_total):
            attempts_used = attempt_index + 1
            try:
                if timeout_ms > 0:
                    result_text, condition_outcome = await asyncio.wait_for(
                        _execute_node_once(),
                        timeout=timeout_ms / 1000.0,
                    )
                else:
                    result_text, condition_outcome = await _execute_node_once()
                status = "ok"
                error_text = ""
                break
            except Exception as exc:
                status = "error"
                if isinstance(exc, asyncio.TimeoutError):
                    error_text = f"timeout after {timeout_ms}ms"
                else:
                    error_text = str(exc)
                result_text = f"Error: {error_text}"
                if attempt_index < attempts_total - 1:
                    continue

        if status == "error":
            if on_error == "continue":
                status = "warning"
                result_text = node_prev
            elif on_error == "fallback":
                status = "warning"
                fallback_template = str(config.get("fallback_output", "{{prev}}"))
                fallback_ctx: Dict[str, Any] = {
                    "input": str(input_text or ""),
                    "prev": node_prev,
                    "node_outputs": ctx.get("node_outputs", {}),
                    "error": error_text,
                }
                result_text = str(_render_workflow_template(fallback_template, fallback_ctx))
            else:
                node_duration_ms = max(0, int((time.perf_counter() - node_started_at) * 1000))
                trace.append(
                    {
                        "node_id": current_id,
                        "node_type": node_type,
                        "status": "error",
                        "output": result_text,
                        "error": error_text,
                        "attempts_used": attempts_used,
                        "attempts_total": attempts_total,
                        "timeout_ms": timeout_ms,
                        "on_error": on_error,
                        "duration_ms": node_duration_ms,
                    }
                )
                metrics = _summarize_trace(trace)
                metrics["total_duration_ms"] = max(0, int((time.perf_counter() - run_started_at) * 1000))
                return {
                    "status": "error",
                    "error": error_text or "node_execution_failed",
                    "failed_node_id": current_id,
                    "trace": trace,
                    "metrics": metrics,
                }

        if node_type == "output":
            final_output = result_text

        node_duration_ms = max(0, int((time.perf_counter() - node_started_at) * 1000))
        trace.append(
            {
                "node_id": current_id,
                "node_type": node_type,
                "status": status,
                "output": result_text,
                "error": error_text,
                "attempts_used": attempts_used,
                "attempts_total": attempts_total,
                "timeout_ms": timeout_ms,
                "on_error": on_error,
                "duration_ms": node_duration_ms,
            }
        )
        ctx["node_outputs"][current_id] = result_text
        ctx["prev"] = result_text
        completed.add(current_id)

        outgoing_edges = outgoing.get(current_id, [])
        selected_targets: set[str] = set()
        if status != "error":
            if node_type == "condition" and condition_outcome is not None:
                tagged_edges = [edge for edge in outgoing_edges if edge.get("when") in {"true", "false", "default"}]
                if tagged_edges:
                    branch_key = "true" if condition_outcome else "false"
                    matched = [edge for edge in tagged_edges if edge.get("when") == branch_key]
                    if not matched:
                        matched = [edge for edge in tagged_edges if edge.get("when") in {"default", ""}]
                    selected_targets = {str(edge.get("target", "")).strip() for edge in matched}
                else:
                    fallback = outgoing_edges[0:1]
                    selected_targets = {str(edge.get("target", "")).strip() for edge in fallback}
            else:
                selected_targets = {str(edge.get("target", "")).strip() for edge in outgoing_edges}

        for edge in outgoing_edges:
            target = str(edge.get("target", "")).strip()
            if target not in pending_predecessors:
                continue
            if target in selected_targets:
                incoming_values[target].append(result_text)
            pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
            if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                queue.append(target)
                queued.add(target)

    remaining_nodes = [node_id for node_id in node_map.keys() if node_id not in completed]
    if not final_output:
        final_output = str(ctx.get("prev", ""))
    metrics = _summarize_trace(trace)
    metrics["total_duration_ms"] = max(0, int((time.perf_counter() - run_started_at) * 1000))
    return {
        "status": "ok",
        "output": final_output,
        "trace": trace,
        "metrics": metrics,
        "truncated": visited >= max_steps or bool(remaining_nodes),
        "remaining_nodes": remaining_nodes,
    }



def _mcp_actor(request: Optional[Request]) -> str:
    if request is None:
        return "direct"
    host = "unknown"
    try:
        host = str(request.client.host if request.client else "unknown").strip() or "unknown"
    except Exception:
        host = "unknown"
    return f"ip:{host}"

_mcp_rate_events = collections.defaultdict(collections.deque)

def _mcp_rate_limit_check(actor: str, policy: Dict[str, Any]) -> tuple[bool, int]:
    max_requests = int(policy.get("rate_limit_requests", 120))
    window_seconds = int(policy.get("rate_limit_window_seconds", 60))
    now = time.time()
    cutoff = now - float(window_seconds)
    events = _mcp_rate_events[actor]
    while events and events[0] < cutoff:
        events.popleft()
    if len(events) >= max_requests:
        retry_after = int((events[0] + float(window_seconds)) - now) + 1 if events else 1
        return False, max(1, retry_after)
    events.append(now)
    return True, 0



def _append_policy_audit(action: str, details: Dict[str, Any]) -> None:
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "details": copy.deepcopy(details) if isinstance(details, dict) else {},
    }
    _policy_audit_buffer.append(entry)
    _append_jsonl_record(_POLICY_AUDIT_LOG_PATH, entry)
    logger.info("Policy audit event: %s", action)



def _capture_strategy_snapshot(
    *,
    category: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
    actor: str,
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    before_snapshot = copy.deepcopy(before) if isinstance(before, dict) else {}
    after_snapshot = copy.deepcopy(after) if isinstance(after, dict) else {}
    entry = {
        "snapshot_id": f"strategy_{uuid.uuid4().hex[:12]}",
        "created_at": time.time(),
        "category": str(category or "general").strip() or "general",
        "source": str(source or "admin_api").strip() or "admin_api",
        "actor": str(actor or "admin").strip() or "admin",
        "rollback_snapshot": before_snapshot,
        "apply_snapshot": after_snapshot,
        "metadata": copy.deepcopy(metadata) if isinstance(metadata, dict) else {},
    }
    _strategy_change_history.append(entry)
    _append_jsonl_record(_STRATEGY_SNAPSHOT_LOG_PATH, entry)
    _append_policy_audit(
        action="strategy.snapshot.created",
        details={
            "snapshot_id": entry["snapshot_id"],
            "category": entry["category"],
            "source": entry["source"],
            "keys": sorted(set(list(before_snapshot.keys()) + list(after_snapshot.keys()))),
        },
    )
    return entry



def _find_strategy_snapshot(snapshot_id: str) -> Optional[Dict[str, Any]]:
    target = str(snapshot_id or "").strip()
    if not target:
        return None
    for item in reversed(list(_strategy_change_history)):
        if str(item.get("snapshot_id", "")) == target:
            return dict(item)
    for item in reversed(_read_jsonl_tail(_STRATEGY_SNAPSHOT_LOG_PATH, limit=2000)):
        if str(item.get("snapshot_id", "")) == target:
            return dict(item)
    return None



def _apply_strategy_snapshot(snapshot: Dict[str, Any], mode: str = "rollback") -> Dict[str, Any]:
    selected_mode = str(mode or "rollback").strip().lower()
    snapshot_key = "rollback_snapshot" if selected_mode == "rollback" else "apply_snapshot"
    raw_snapshot = snapshot.get(snapshot_key)
    values = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    if not values:
        return {"mode": selected_mode, "applied_keys": [], "router_updated": False}

    unknown_keys = [str(key) for key in values.keys() if not _is_strategy_rollback_key_allowed(str(key))]
    if unknown_keys:
        raise ValueError(f"Snapshot contains unsupported rollback keys: {sorted(unknown_keys)}")
    filtered_values = {str(k): copy.deepcopy(v) for k, v in values.items() if _is_strategy_rollback_key_allowed(str(k))}

    if hasattr(config, "set_many"):
        config.set_many(filtered_values)
    else:
        for key, value in filtered_values.items():
            config.set(key, value)
    _save_config_if_supported()

    router_updated = False
    if LLM_ROUTER is not None:
        strategy = filtered_values.get("models.router.strategy")
        if strategy is not None and hasattr(LLM_ROUTER, "set_strategy"):
            try:
                LLM_ROUTER.set_strategy(str(strategy))
                router_updated = True
            except Exception:
                logger.debug("Failed to apply router strategy rollback", exc_info=True)
        budget_policy = filtered_values.get("models.router.budget")
        if isinstance(budget_policy, dict) and hasattr(LLM_ROUTER, "set_budget_policy"):
            try:
                LLM_ROUTER.set_budget_policy(dict(budget_policy))
                router_updated = True
            except Exception:
                logger.debug("Failed to apply router budget rollback", exc_info=True)
        outlier_policy = filtered_values.get("models.router.outlier_ejection")
        if isinstance(outlier_policy, dict) and hasattr(LLM_ROUTER, "set_outlier_policy"):
            try:
                LLM_ROUTER.set_outlier_policy(dict(outlier_policy))
                router_updated = True
            except Exception:
                logger.debug("Failed to apply router outlier rollback", exc_info=True)

    return {
        "mode": selected_mode,
        "applied_keys": sorted(filtered_values.keys()),
        "router_updated": router_updated,
    }



def _is_success_status(status: Any) -> bool:
    marker = str(status or "").strip().lower()
    return marker in {"success", "ok", "completed"}



def _merge_error_code_counts(rows: List[Dict[str, int]]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            marker = str(key).strip() or "UNKNOWN"
            try:
                count = int(value or 0)
            except (TypeError, ValueError):
                count = 0
            merged[marker] = merged.get(marker, 0) + max(0, count)
    return merged



def _append_workflow_run_metric(
    *,
    workflow_id: str,
    workflow_name: str,
    result: Dict[str, Any],
) -> None:
    metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
    trace_items = result.get("trace", []) if isinstance(result, dict) else []
    status = str(result.get("status", "error")).strip().lower() if isinstance(result, dict) else "error"
    error_text = str(result.get("error", "")).strip() if isinstance(result, dict) else ""
    failed_node_id = str(result.get("failed_node_id", "")).strip() if isinstance(result, dict) else ""
    try:
        total_duration_ms = int(metrics.get("total_duration_ms", 0) or 0)
    except (TypeError, ValueError):
        total_duration_ms = 0
    try:
        trace_nodes = int(metrics.get("trace_nodes", len(trace_items)) or 0)
    except (TypeError, ValueError):
        trace_nodes = len(trace_items) if isinstance(trace_items, list) else 0
    try:
        node_duration_ms = int(metrics.get("node_duration_ms", 0) or 0)
    except (TypeError, ValueError):
        node_duration_ms = 0
    _workflow_run_history.append(
        {
            "timestamp": datetime.utcnow().isoformat(),
            "workflow_id": workflow_id,
            "workflow_name": workflow_name or workflow_id,
            "status": status,
            "error": error_text,
            "failed_node_id": failed_node_id,
            "total_duration_ms": max(0, total_duration_ms),
            "trace_nodes": max(0, trace_nodes),
            "node_duration_ms": max(0, node_duration_ms),
        }
    )



_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS: Dict[str, Any] = {
    "warning_success_rate": 0.90,
    "critical_success_rate": 0.75,
    "warning_failures": 1,
    "critical_failures": 3,
    "warning_p95_latency_ms": 2500,
    "critical_p95_latency_ms": 4000,
    "warning_persona_consistency_score": 0.82,
    "critical_persona_consistency_score": 0.70,
}

def _get_release_gate_health_thresholds() -> Dict[str, Any]:
    raw = config.get("observability.release_gate_health_thresholds", {})
    raw_dict = raw if isinstance(raw, dict) else {}

    def _float_value(key: str, default: float) -> float:
        try:
            value = float(raw_dict.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(1.0, value)) if "rate" in key else max(0.0, value)

    def _int_value(key: str, default: int) -> int:
        try:
            value = int(raw_dict.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(0, value)

    warning_success_rate = _float_value(
        "warning_success_rate",
        float(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["warning_success_rate"]),
    )
    critical_success_rate = _float_value(
        "critical_success_rate",
        float(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["critical_success_rate"]),
    )
    if critical_success_rate > warning_success_rate:
        critical_success_rate = warning_success_rate

    warning_failures = _int_value(
        "warning_failures",
        int(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["warning_failures"]),
    )
    critical_failures = _int_value(
        "critical_failures",
        int(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["critical_failures"]),
    )
    if critical_failures < warning_failures:
        critical_failures = warning_failures

    warning_p95 = _int_value(
        "warning_p95_latency_ms",
        int(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["warning_p95_latency_ms"]),
    )
    critical_p95 = _int_value(
        "critical_p95_latency_ms",
        int(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["critical_p95_latency_ms"]),
    )
    if critical_p95 < warning_p95:
        critical_p95 = warning_p95

    warning_persona_score = _float_value(
        "warning_persona_consistency_score",
        float(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["warning_persona_consistency_score"]),
    )
    critical_persona_score = _float_value(
        "critical_persona_consistency_score",
        float(_DEFAULT_RELEASE_GATE_HEALTH_THRESHOLDS["critical_persona_consistency_score"]),
    )
    if critical_persona_score > warning_persona_score:
        critical_persona_score = warning_persona_score

    return {
        "warning_success_rate": round(warning_success_rate, 4),
        "critical_success_rate": round(critical_success_rate, 4),
        "warning_failures": warning_failures,
        "critical_failures": critical_failures,
        "warning_p95_latency_ms": warning_p95,
        "critical_p95_latency_ms": critical_p95,
        "warning_persona_consistency_score": round(warning_persona_score, 4),
        "critical_persona_consistency_score": round(critical_persona_score, 4),
    }



def _persona_runtime_thresholds() -> Dict[str, Any]:
    runtime_cfg = config.get("personality.runtime", {}) or {}
    if not isinstance(runtime_cfg, dict):
        runtime_cfg = {}
    signals_cfg = runtime_cfg.get("signals", {}) or {}
    if not isinstance(signals_cfg, dict):
        signals_cfg = {}

    def _to_float(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(0.0, min(1.0, parsed))

    warning_score = _to_float(signals_cfg.get("warning_score", 0.82), 0.82)
    critical_score = _to_float(signals_cfg.get("critical_score", 0.70), 0.70)
    if critical_score > warning_score:
        critical_score = warning_score

    retain_raw = signals_cfg.get("retain", 500)
    try:
        retain = max(50, min(int(retain_raw), 5000))
    except (TypeError, ValueError):
        retain = 500

    return {
        "enabled": bool(runtime_cfg.get("enabled", True)),
        "signals_enabled": bool(signals_cfg.get("enabled", True)),
        "warning_score": round(warning_score, 4),
        "critical_score": round(critical_score, 4),
        "retain": retain,
    }



def _validate_satellite_node_auth(node_id: str, token: str) -> tuple[bool, str]:
    node_id = str(node_id or "").strip()
    token = str(token or "").strip()
    if not node_id:
        return False, "Node ID is required."
    if not token:
        return False, "Node token is required."
    node_cfg = _get_satellite_node_config(node_id)
    expected_token = str(node_cfg.get("token", "")).strip()
    if not expected_token:
        return False, f"Node '{node_id}' is not configured for satellite auth."
    if token != expected_token:
        return False, "Invalid node token."
    return True, ""



def _decode_frame_payload(frame: Dict[str, Any]) -> bytes:
    payload = frame.get("payload")
    if not isinstance(payload, str) or not payload.strip():
        raise SatelliteProtocolError("frame.payload must be a non-empty base64 string.")
    try:
        return base64.b64decode(payload.encode("utf-8"), validate=True)
    except Exception as exc:
        raise SatelliteProtocolError(f"Invalid frame payload: {exc}") from exc



def _consume_satellite_frame_budget(
    *,
    state: Dict[str, Any],
    size_bytes: int,
    now_ts: Optional[float] = None,
    window_seconds: float = 2.0,
    max_bytes_per_window: int = 4 * 1024 * 1024,
) -> bool:
    """Enforce rolling per-connection frame budget for backpressure."""
    now = float(now_ts) if now_ts is not None else time.time()
    window = max(0.1, float(window_seconds or 2.0))
    max_bytes = max(1, int(max_bytes_per_window or (4 * 1024 * 1024)))
    queue = state.setdefault("frames", collections.deque())
    total = int(state.get("total_bytes", 0) or 0)

    while queue and (now - float(queue[0][0])) > window:
        _ts, old_size = queue.popleft()
        total = max(0, total - int(old_size))

    candidate = max(0, int(size_bytes))
    if total + candidate > max_bytes:
        state["total_bytes"] = total
        return False

    queue.append((now, candidate))
    state["total_bytes"] = total + candidate
    return True



def _require_orchestrator() -> Any:
    if ORCHESTRATOR is None:
        raise HTTPException(status_code=503, detail="Orchestrator is not available")
    return ORCHESTRATOR



def _get_tool_governance_snapshot(limit: int = 50) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    if TOOL_REGISTRY is None:
        return {
            "available": False,
            "budget": {},
            "recent_rejections": [],
        }

    budget: Dict[str, Any] = {}
    recent_rejections: List[Dict[str, Any]] = []
    if hasattr(TOOL_REGISTRY, "get_budget_runtime_status"):
        try:
            budget = TOOL_REGISTRY.get_budget_runtime_status()
        except Exception:
            logger.debug("Failed to read tool budget runtime status", exc_info=True)
    if hasattr(TOOL_REGISTRY, "get_recent_rejection_events"):
        try:
            recent_rejections = TOOL_REGISTRY.get_recent_rejection_events(limit=safe_limit)
        except Exception:
            logger.debug("Failed to read tool rejection events", exc_info=True)

    return {
        "available": True,
        "budget": budget,
        "recent_rejections": recent_rejections,
    }



def _enqueue_chat_message(*, content: str, session_id: str, source: str, sender_id: str = "owner") -> None:
    if API_QUEUES["input"] is None:
        raise HTTPException(status_code=503, detail="Brain disconnected")
    text = str(content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    if len(text) > _MAX_CHAT_MESSAGE_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Message too long (max {_MAX_CHAT_MESSAGE_CHARS} characters)",
        )
    API_QUEUES["input"].put(
        {
            "type": "chat",
            "content": text,
            "source": source,
            "chat_id": str(session_id),
            "sender_id": str(sender_id),
        }
    )



def _prepare_training_inputs(
    *,
    dataset_id: str,
    report: Dict[str, Any],
    max_samples: int,
) -> Dict[str, Any]:
    """Build trainer inputs from trajectory and eval report context."""
    def _quality_tier(score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.45:
            return "medium"
        return "low"

    def _is_negative_feedback(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        return any(token in normalized for token in {"unsafe", "wrong", "bad", "fail", "error"})

    def _score_sample(eval_item: Dict[str, Any], traj_item: Optional[Dict[str, Any]]) -> float:
        score = 0.0
        passed = eval_item.get("passed")
        if passed is True:
            score += 0.35
        elif passed is False:
            score += 0.08
        else:
            score += 0.18

        try:
            eval_score = float(eval_item.get("composite_score", eval_item.get("score")))
        except (TypeError, ValueError):
            eval_score = None
        if eval_score is not None:
            normalized = max(0.0, min(1.0, eval_score if eval_score <= 1.0 else eval_score / 100.0))
            score += 0.35 * normalized
        else:
            score += 0.2

        final_status = ""
        feedback_text = ""
        if isinstance(traj_item, dict):
            final = traj_item.get("final") if isinstance(traj_item.get("final"), dict) else {}
            final_status = str(final.get("status", "")).strip().lower()
            feedback_items = traj_item.get("feedback") if isinstance(traj_item.get("feedback"), list) else []
            if feedback_items:
                feedback_text = str((feedback_items[-1] or {}).get("feedback", ""))
        if final_status in {"done", "ok", "success", "completed"}:
            score += 0.15
        elif final_status in {"error", "failed", "llm_error", "incomplete"}:
            score += 0.0
        else:
            score += 0.08

        score += 0.0 if _is_negative_feedback(feedback_text) else 0.15
        return round(max(0.0, min(1.0, score)), 4)

    def _bucket_name(eval_item: Dict[str, Any], traj_item: Optional[Dict[str, Any]]) -> str:
        passed = eval_item.get("passed")
        final_status = ""
        if isinstance(traj_item, dict):
            final = traj_item.get("final") if isinstance(traj_item.get("final"), dict) else {}
            final_status = str(final.get("status", "")).strip().lower()
        has_error = final_status in {"error", "failed", "llm_error", "incomplete"}
        if passed is True:
            return "pass_error" if has_error else "pass_clean"
        if passed is False:
            return "fail_error" if has_error else "fail_clean"
        return "unknown"

    def _select_stratified(
        candidates: List[Dict[str, Any]],
        *,
        limit: int,
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        if len(candidates) <= limit:
            counts: Dict[str, int] = {}
            for item in candidates:
                bucket = str(item.get("_bucket", "unknown"))
                counts[bucket] = counts.get(bucket, 0) + 1
            return list(candidates), counts

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in candidates:
            bucket = str(item.get("_bucket", "unknown"))
            grouped.setdefault(bucket, []).append(item)
        for items in grouped.values():
            items.sort(
                key=lambda value: (
                    -float(value.get("_quality_score", 0.0)),
                    str((value.get("eval") or {}).get("run_id", "")),
                )
            )

        selected: List[Dict[str, Any]] = []
        for bucket in sorted(grouped.keys()):
            if len(selected) >= limit:
                break
            if grouped[bucket]:
                selected.append(grouped[bucket].pop(0))

        if len(selected) < limit:
            remaining: List[Dict[str, Any]] = []
            for items in grouped.values():
                remaining.extend(items)
            remaining.sort(
                key=lambda value: (
                    -float(value.get("_quality_score", 0.0)),
                    str((value.get("eval") or {}).get("run_id", "")),
                )
            )
            selected.extend(remaining[: max(0, limit - len(selected))])

        bucket_counts: Dict[str, int] = {}
        for item in selected:
            bucket = str(item.get("_bucket", "unknown"))
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        selected.sort(
            key=lambda value: (
                str((value.get("eval") or {}).get("run_id", "")),
            )
        )
        return selected, bucket_counts

    eval_results = list(report.get("results", []) or [])
    candidates: List[Dict[str, Any]] = []
    for item in eval_results:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        traj = TRAJECTORY_STORE.get_trajectory(run_id) if TRAJECTORY_STORE is not None else None
        quality_score = _score_sample(item, traj if isinstance(traj, dict) else None)
        bucket = _bucket_name(item, traj if isinstance(traj, dict) else None)
        candidates.append(
            {
                "eval": item,
                "traj": traj if isinstance(traj, dict) else None,
                "_quality_score": quality_score,
                "_bucket": bucket,
            }
        )

    selected, bucket_counts = _select_stratified(candidates, limit=max(1, max_samples))
    selected_run_ids = [
        str((entry.get("eval") or {}).get("run_id", "")).strip()
        for entry in selected
        if str((entry.get("eval") or {}).get("run_id", "")).strip()
    ]
    selected_set = set(selected_run_ids)

    eval_samples: List[Dict[str, Any]] = []
    trajectory_samples: List[Dict[str, Any]] = []
    quality_scores: List[float] = []
    for entry in selected:
        eval_item = (entry.get("eval") or {}) if isinstance(entry.get("eval"), dict) else {}
        traj = entry.get("traj") if isinstance(entry.get("traj"), dict) else None
        quality_score = float(entry.get("_quality_score", 0.0))
        quality_scores.append(quality_score)
        eval_samples.append(
            {
                **eval_item,
                "quality_score": quality_score,
                "quality_tier": _quality_tier(quality_score),
                "sampling_bucket": str(entry.get("_bucket", "unknown")),
            }
        )

        run_id = str(eval_item.get("run_id", "")).strip()
        if not run_id or not traj:
            continue
        meta = traj.get("meta") if isinstance(traj.get("meta"), dict) else {}
        final = traj.get("final") if isinstance(traj.get("final"), dict) else {}
        feedback_items = traj.get("feedback") if isinstance(traj.get("feedback"), list) else []
        feedback_text = ""
        if feedback_items:
            feedback_text = str((feedback_items[-1] or {}).get("feedback", ""))
        trajectory_samples.append(
            {
                "run_id": run_id,
                "user_content": meta.get("user_content", ""),
                "assistant_output": final.get("final_content", ""),
                "status": final.get("status", ""),
                "feedback": feedback_text,
                "quality_score": quality_score,
                "quality_tier": _quality_tier(quality_score),
                "sampling_bucket": str(entry.get("_bucket", "unknown")),
            }
        )

    return {
        "dataset_id": dataset_id,
        "trajectory_samples": trajectory_samples,
        "eval_samples": eval_samples,
        "sampling": {
            "strategy": "quality_stratified_v1",
            "requested_max_samples": max(1, max_samples),
            "selected_count": len(selected),
            "available_count": len(candidates),
            "selected_run_ids": selected_run_ids,
            "selected_coverage": round(len(selected_set) / max(1, len(candidates)), 4),
            "bucket_counts": bucket_counts,
            "quality": {
                "avg": round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else None,
                "min": round(min(quality_scores), 4) if quality_scores else None,
                "max": round(max(quality_scores), 4) if quality_scores else None,
            },
        },
    }



def _build_rule_prompt_patch(report: Dict[str, Any]) -> Dict[str, Any]:
    quality_gate = report.get("quality_gate", {}) if isinstance(report, dict) else {}
    reasons = [str(item) for item in (quality_gate.get("reasons", []) or [])]
    rules: List[str] = []
    if "composite_score_below_threshold" in reasons:
        rules.append("Prioritize deterministic and concise responses for benchmark-critical prompts.")
    if "pass_rate_below_threshold" in reasons:
        rules.append("When confidence is low, ask one clarification question before invoking tools.")
    if "error_rate_above_threshold" in reasons:
        rules.append("Avoid repeating failed actions; return explicit fallback and recovery guidance.")
    if not rules:
        rules.append("Maintain persona consistency while minimizing avoidable tool errors.")
    return {
        "stage": "rule_prompt_optimization",
        "rules": rules,
        "source": "benchmark_gate_failure",
    }



def _normalize_trajectory_steps(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build normalized replay steps from trajectory events."""
    events = list((payload or {}).get("events") or [])
    steps: List[Dict[str, Any]] = []
    for idx, evt in enumerate(events):
        action = str((evt or {}).get("action", "") or "").strip().lower()
        if action not in {"tool_call", "tool_result"}:
            continue
        raw_payload = (evt or {}).get("payload") or {}
        step = {
            "index": idx,
            "ts": evt.get("ts"),
            "stage": str((evt or {}).get("stage", "") or ""),
            "action": action,
            "tool": str(raw_payload.get("tool", "") or ""),
            "tool_call_id": str(raw_payload.get("tool_call_id", "") or ""),
            "status": str(raw_payload.get("status", "") or ""),
            "error_code": str(raw_payload.get("error_code", "") or ""),
            "args_hash": str(raw_payload.get("args_hash", "") or ""),
            "args_preview": str(raw_payload.get("args_preview", "") or ""),
            "result_preview": str(raw_payload.get("result_preview", "") or ""),
            "has_media": bool(raw_payload.get("has_media", False)),
            "media_paths": list(raw_payload.get("media_paths", []) or []),
        }
        step["signature"] = "|".join(
            [
                step["action"],
                step["tool"],
                step["tool_call_id"],
                step["status"],
                step["error_code"],
                step["args_hash"],
            ]
        )
        steps.append(step)
    return steps



def _build_task_view(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize one trajectory into task-level observability metrics."""
    events = list((payload or {}).get("events") or [])
    stage_counts: Dict[str, int] = {}
    action_counts: Dict[str, int] = {}
    stage_first_ts: Dict[str, float] = {}
    stage_last_ts: Dict[str, float] = {}
    error_count = 0
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None

    for evt in events:
        stage = str((evt or {}).get("stage", "") or "unknown")
        action = str((evt or {}).get("action", "") or "unknown")
        ts = evt.get("ts")
        if isinstance(ts, (int, float)):
            tsv = float(ts)
            if first_ts is None or tsv < first_ts:
                first_ts = tsv
            if last_ts is None or tsv > last_ts:
                last_ts = tsv
            if stage not in stage_first_ts or tsv < stage_first_ts[stage]:
                stage_first_ts[stage] = tsv
            if stage not in stage_last_ts or tsv > stage_last_ts[stage]:
                stage_last_ts[stage] = tsv

        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        action_counts[action] = action_counts.get(action, 0) + 1
        raw_payload = (evt or {}).get("payload") or {}
        if action == "tool_result" and str(raw_payload.get("status", "")).lower() == "error":
            error_count += 1

    stages: List[Dict[str, Any]] = []
    for stage, count in stage_counts.items():
        s0 = stage_first_ts.get(stage)
        s1 = stage_last_ts.get(stage)
        duration_ms: Optional[float] = None
        if s0 is not None and s1 is not None:
            duration_ms = round(max(0.0, (s1 - s0) * 1000.0), 2)
        stages.append({"stage": stage, "count": count, "duration_ms": duration_ms})
    stages.sort(key=lambda x: x["stage"])

    total_duration_ms: Optional[float] = None
    if first_ts is not None and last_ts is not None:
        total_duration_ms = round(max(0.0, (last_ts - first_ts) * 1000.0), 2)

    final = (payload or {}).get("final") or {}
    return {
        "run_id": (payload or {}).get("run_id"),
        "status": str(final.get("status", "") or "running"),
        "event_count": len(events),
        "error_count": error_count,
        "stage_counts": stage_counts,
        "action_counts": action_counts,
        "stages": stages,
        "duration_ms": total_duration_ms,
        "turn_latency_ms": ((final.get("metrics") or {}).get("turn_latency_ms")),
    }



def _compare_replay_steps(
    run_steps: List[Dict[str, Any]],
    baseline_steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    run_signatures = [str(item.get("signature", "")) for item in run_steps]
    baseline_signatures = [str(item.get("signature", "")) for item in baseline_steps]
    shared = set(run_signatures) & set(baseline_signatures)
    missing_from_run = [sig for sig in baseline_signatures if sig not in shared]
    added_in_run = [sig for sig in run_signatures if sig not in shared]
    max_len = max(len(run_signatures), len(baseline_signatures), 1)
    overlap_ratio = round(len(shared) / max_len, 4)
    return {
        "run_steps": len(run_signatures),
        "baseline_steps": len(baseline_signatures),
        "shared_steps": len(shared),
        "overlap_ratio": overlap_ratio,
        "missing_from_run": missing_from_run[:20],
        "added_in_run": added_in_run[:20],
    }



def _build_resume_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a resume draft for interrupted/failed trajectories."""
    meta = (payload or {}).get("meta") or {}
    final = (payload or {}).get("final") or {}
    events = list((payload or {}).get("events") or [])

    last_error: Dict[str, Any] = {}
    for evt in reversed(events):
        if str((evt or {}).get("action", "")).lower() != "tool_result":
            continue
        p = (evt or {}).get("payload") or {}
        if str(p.get("status", "")).lower() == "error":
            last_error = {
                "tool": str(p.get("tool", "") or ""),
                "tool_call_id": str(p.get("tool_call_id", "") or ""),
                "error_code": str(p.get("error_code", "") or ""),
                "result_preview": str(p.get("result_preview", "") or ""),
            }
            break

    status = str(final.get("status", "") or "running")
    can_resume = status in {"running", "error", "incomplete", "llm_error"}
    user_content = str(meta.get("user_content", "") or "")
    final_preview = str(final.get("final_content", "") or "")[:240]
    error_line = ""
    if last_error:
        error_line = (
            f"最近失败工具: {last_error.get('tool')}, "
            f"error_code={last_error.get('error_code')}, "
            f"result={last_error.get('result_preview')[:120]}"
        )

    resume_message = (
        f"继续上次任务（run_id={payload.get('run_id', '')}）。\n"
        f"原始用户目标: {user_content}\n"
        f"上次结束状态: {status}\n"
        f"{error_line}\n"
        f"上次最终输出预览: {final_preview}\n"
        "要求：基于上述上下文继续执行，避免重复失败调用；如无法继续，请给出可执行替代方案。"
    ).strip()

    return {
        "run_id": payload.get("run_id"),
        "status": status,
        "can_resume": can_resume,
        "resume_message": resume_message,
        "last_error": last_error,
    }



def _build_training_publish_diff(job: Dict[str, Any]) -> Dict[str, Any]:
    output = (job.get("output") or {}) if isinstance(job, dict) else {}
    prompt_patch = output.get("prompt_patch") if isinstance(output, dict) else {}
    policy_patch = output.get("policy_patch") if isinstance(output, dict) else {}
    router_patch = output.get("router_patch") if isinstance(output, dict) else {}
    prompt_rules = _unique_str_list((prompt_patch or {}).get("rules", []))
    deny_add = _unique_str_list((policy_patch or {}).get("security.tool_denylist.add", []))
    suggested_tier = str((policy_patch or {}).get("security.tool_max_tier.suggested", "")).strip().lower()
    if suggested_tier not in {"safe", "standard", "privileged"}:
        suggested_tier = ""

    before_prompt = str(config.get("personality.system_prompt", ""))
    before_deny = _unique_str_list(config.get("security.tool_denylist", []) or [])
    before_tier = str(config.get("security.tool_max_tier", "standard")).strip().lower() or "standard"
    before_router_strategy = str(config.get("models.router.strategy", "priority")).strip().lower() or "priority"
    before_router_strategy = _normalize_router_strategy(before_router_strategy, fallback="priority")
    before_router_template = str(config.get("models.router.strategy_template", "")).strip()
    before_router_budget = config.get("models.router.budget", {})
    if not isinstance(before_router_budget, dict):
        before_router_budget = {}
    before_router_outlier = config.get("models.router.outlier_ejection", {})
    if not isinstance(before_router_outlier, dict):
        before_router_outlier = {}

    after_prompt = _apply_trainer_prompt_patch(
        before_prompt,
        rules=prompt_rules,
        job_id=str(job.get("job_id", "")),
    )
    after_deny = sorted(set(before_deny + deny_add))
    after_tier = suggested_tier or before_tier
    after_router_strategy = _normalize_router_strategy(
        (router_patch or {}).get("strategy", (router_patch or {}).get("models.router.strategy", before_router_strategy)),
        fallback=before_router_strategy,
    )
    after_router_template = str(
        (router_patch or {}).get(
            "strategy_template",
            (router_patch or {}).get("models.router.strategy_template", before_router_template),
        )
        or before_router_template
    ).strip()
    after_router_budget = (router_patch or {}).get("budget", (router_patch or {}).get("models.router.budget", before_router_budget))
    if not isinstance(after_router_budget, dict):
        after_router_budget = dict(before_router_budget)
    after_router_outlier = (router_patch or {}).get(
        "outlier_ejection",
        (router_patch or {}).get("models.router.outlier_ejection", before_router_outlier),
    )
    if not isinstance(after_router_outlier, dict):
        after_router_outlier = dict(before_router_outlier)

    before = {
        "personality.system_prompt": before_prompt,
        "security.tool_denylist": before_deny,
        "security.tool_max_tier": before_tier,
        "models.router.strategy": before_router_strategy,
        "models.router.strategy_template": before_router_template,
        "models.router.budget": dict(before_router_budget),
        "models.router.outlier_ejection": dict(before_router_outlier),
    }
    after = {
        "personality.system_prompt": after_prompt,
        "security.tool_denylist": after_deny,
        "security.tool_max_tier": after_tier,
        "models.router.strategy": after_router_strategy,
        "models.router.strategy_template": after_router_template,
        "models.router.budget": dict(after_router_budget),
        "models.router.outlier_ejection": dict(after_router_outlier),
    }
    strategy_package = {
        "version": "training_strategy_package_v1",
        "job_id": str(job.get("job_id", "")),
        "components": {
            "prompt": {
                "kind": "prompt",
                "patch": dict(prompt_patch) if isinstance(prompt_patch, dict) else {},
                "before": before_prompt,
                "after": after_prompt,
                "changed": before_prompt != after_prompt,
            },
            "policy": {
                "kind": "policy",
                "patch": dict(policy_patch) if isinstance(policy_patch, dict) else {},
                "before": {
                    "security.tool_denylist": before_deny,
                    "security.tool_max_tier": before_tier,
                },
                "after": {
                    "security.tool_denylist": after_deny,
                    "security.tool_max_tier": after_tier,
                },
                "changed": before_deny != after_deny or before_tier != after_tier,
            },
            "router": {
                "kind": "router",
                "patch": dict(router_patch) if isinstance(router_patch, dict) else {},
                "before": {
                    "models.router.strategy": before_router_strategy,
                    "models.router.strategy_template": before_router_template,
                    "models.router.budget": dict(before_router_budget),
                    "models.router.outlier_ejection": dict(before_router_outlier),
                },
                "after": {
                    "models.router.strategy": after_router_strategy,
                    "models.router.strategy_template": after_router_template,
                    "models.router.budget": dict(after_router_budget),
                    "models.router.outlier_ejection": dict(after_router_outlier),
                },
                "changed": (
                    before_router_strategy != after_router_strategy
                    or before_router_template != after_router_template
                    or dict(before_router_budget) != dict(after_router_budget)
                    or dict(before_router_outlier) != dict(after_router_outlier)
                ),
            },
        },
        "rollback_snapshot": dict(before),
        "apply_snapshot": dict(after),
    }
    return {
        "before": before,
        "after": after,
        "strategy_package": strategy_package,
        "summary": {
            "prompt_rules_added": len(prompt_rules),
            "denylist_added": sorted(set(after_deny) - set(before_deny)),
            "tool_max_tier_changed": before_tier != after_tier,
            "router_strategy_changed": before_router_strategy != after_router_strategy,
            "router_strategy": after_router_strategy,
        },
    }



def _score_training_job(job: Dict[str, Any]) -> Dict[str, Any]:
    output = (job.get("output") or {}) if isinstance(job, dict) else {}
    summary = output.get("training_summary") if isinstance(output, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    fail_count = int(summary.get("fail_count", 0) or 0)
    trajectory_count = int(summary.get("trajectory_count", 0) or 0)
    eval_count = int(summary.get("eval_count", 0) or 0)
    rule_count = len(_unique_str_list(((output.get("prompt_patch") or {}).get("rules", []))))
    baseline = max(1, trajectory_count + eval_count)
    score = max(0.0, min(1.0, 1.0 - (fail_count / baseline)))
    return {
        "score": round(score, 4),
        "fail_count": fail_count,
        "rule_count": rule_count,
        "trajectory_count": trajectory_count,
        "eval_count": eval_count,
    }



def _build_training_release_explanation(
    *,
    release: Dict[str, Any],
    job: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    release_status = str(release.get("status", "")).strip().lower()
    rollout_mode = str((release.get("rollout", {}) or {}).get("mode", "direct")).strip().lower() or "direct"
    approval = release.get("approval", {}) if isinstance(release.get("approval"), dict) else {}
    rollback_note = str(release.get("rollback_note", "")).strip()
    rollback_actor = str(release.get("rollback_actor", "")).strip()

    job_payload = job if isinstance(job, dict) else {}
    output = job_payload.get("output", {}) if isinstance(job_payload.get("output"), dict) else {}
    summary = output.get("training_summary", {}) if isinstance(output.get("training_summary"), dict) else {}
    prompt_patch = output.get("prompt_patch", {}) if isinstance(output.get("prompt_patch"), dict) else {}
    policy_patch = output.get("policy_patch", {}) if isinstance(output.get("policy_patch"), dict) else {}
    router_patch = output.get("router_patch", {}) if isinstance(output.get("router_patch"), dict) else {}

    fail_count = int(summary.get("fail_count", 0) or 0)
    trajectory_count = int(summary.get("trajectory_count", 0) or 0)
    eval_count = int(summary.get("eval_count", 0) or 0)
    rule_count = len(_unique_str_list(prompt_patch.get("rules", [])))
    deny_add = _unique_str_list(policy_patch.get("security.tool_denylist.add", []))
    router_strategy = str(router_patch.get("strategy", "")).strip().lower() or None

    reasons_effective: List[str] = []
    reasons_failed: List[str] = []
    decision_trace: List[str] = []

    if release_status == "pending_approval":
        outcome = "pending"
        reasons_failed.append("release pending manual approval; strategy package not applied yet")
        decision_trace.append("state:pending_approval")
    elif release_status == "rolled_back":
        outcome = "failed"
        reasons_failed.append("release rolled back after canary or gate checks")
        decision_trace.append("state:rolled_back")
    else:
        outcome = "effective"
        reasons_effective.append("release applied to runtime config")
        decision_trace.append(f"state:{release_status or 'published'}")

    if rollout_mode == "canary":
        decision_trace.append("rollout:canary")
        reasons_effective.append("canary rollout limits blast radius before full promotion")
    else:
        decision_trace.append("rollout:direct")

    if bool(approval.get("required", False)):
        if bool(approval.get("approved", False)):
            reasons_effective.append(
                f"manual approval passed by {str(approval.get('approved_by', 'admin')).strip() or 'admin'}"
            )
            decision_trace.append("approval:approved")
        else:
            reasons_failed.append("manual approval required but not approved")
            decision_trace.append("approval:pending")
    else:
        decision_trace.append("approval:not_required")

    if rollback_note:
        reasons_failed.append(f"rollback note: {rollback_note}")
        decision_trace.append(f"rollback_note:{rollback_note}")
    if rollback_actor:
        decision_trace.append(f"rollback_actor:{rollback_actor}")

    if fail_count > 0:
        reasons_failed.append(
            f"trainer summary shows failures ({fail_count}/{max(1, trajectory_count + eval_count)})"
        )
    else:
        reasons_effective.append("trainer summary shows no hard failures in sampled data")

    if rule_count > 0:
        reasons_effective.append(f"prompt patch added {rule_count} rule(s)")
    if deny_add:
        reasons_effective.append(f"policy patch extended denylist by {len(deny_add)} item(s)")
    if router_strategy:
        reasons_effective.append(f"router strategy proposed: {router_strategy}")

    tool_error_code_count = (
        summary.get("tool_error_code_count", {})
        if isinstance(summary.get("tool_error_code_count"), dict)
        else {}
    )
    label_counts: Dict[str, int] = {
        "tool_parameter_error": 0,
        "permission_error": 0,
        "environment_error": 0,
        "strategy_error": 0,
    }
    top_errors = sorted(
        ((str(code), int(count)) for code, count in tool_error_code_count.items()),
        key=lambda item: (-item[1], item[0]),
    )[:10]
    for code, count in top_errors:
        label = _classify_training_failure_label(code)
        label_counts[label] = label_counts.get(label, 0) + int(count)
    if top_errors:
        reasons_failed.append("top tool error codes observed in trainer summary")
        decision_trace.append("trainer:tool_error_code_count_present")

    return {
        "release_id": str(release.get("release_id", "")),
        "job_id": str(release.get("job_id", "")),
        "outcome": outcome,
        "release": {
            "status": release_status,
            "rollout_mode": rollout_mode,
            "actor": str(release.get("actor", "")),
            "created_at": release.get("created_at"),
        },
        "training_summary": {
            "trajectory_count": trajectory_count,
            "eval_count": eval_count,
            "fail_count": fail_count,
            "rule_count": rule_count,
            "denylist_add_count": len(deny_add),
            "router_strategy": router_strategy,
            "top_tool_error_codes": [{"error_code": code, "count": count} for code, count in top_errors],
        },
        "failure_attribution": {
            "by_label": label_counts,
        },
        "why_effective": reasons_effective[:12],
        "why_failed": reasons_failed[:12],
        "decision_trace": decision_trace[:20],
    }



def _resolve_training_publish_rollout(rollout_payload: Dict[str, Any]) -> Dict[str, Any]:
    rollout = dict(rollout_payload) if isinstance(rollout_payload, dict) else {}
    mode = str(rollout.get("mode", "")).strip().lower()
    if not mode:
        mode = "canary" if bool(config.get("trainer.canary.auto_rollout_on_publish", False)) else "direct"
    if mode not in {"direct", "canary"}:
        mode = "direct"
    rollout["mode"] = mode
    try:
        default_percent = int(config.get("trainer.canary.default_percent", 10) or 10)
    except (TypeError, ValueError):
        default_percent = 10
    default_percent = max(1, min(100, default_percent))
    percent_raw = rollout.get("percent", default_percent if mode == "canary" else 100)
    try:
        percent = int(percent_raw)
    except (TypeError, ValueError):
        percent = default_percent if mode == "canary" else 100
    rollout["percent"] = max(1, min(100, percent))
    return rollout



def _resolve_training_release_approval(
    *,
    actor: str,
    dry_run: bool,
    rollout_mode: str,
    approval_payload: Dict[str, Any],
) -> Dict[str, Any]:
    approval_cfg = config.get("trainer.release_approval", {}) or {}
    if not isinstance(approval_cfg, dict):
        approval_cfg = {}
    required_modes_raw = approval_cfg.get("required_modes", ["canary"])
    required_modes = (
        [str(item).strip().lower() for item in required_modes_raw if str(item).strip()]
        if isinstance(required_modes_raw, list)
        else ["canary"]
    )
    if not required_modes:
        required_modes = ["canary"]
    approval_input = approval_payload if isinstance(approval_payload, dict) else {}
    required_override = "required" in approval_input
    required = bool(approval_input.get("required", False)) if required_override else (
        bool(approval_cfg.get("enabled", False)) and rollout_mode in set(required_modes)
    )
    if dry_run:
        required = False
    approved = bool(approval_input.get("approved", False))
    approved_by = str(approval_input.get("approved_by", "")).strip()
    note = str(approval_input.get("note", "")).strip()
    if approved and not approved_by:
        approved_by = actor
    if required and approved and bool(approval_cfg.get("require_note", False)) and not note:
        raise HTTPException(status_code=400, detail="Approval note is required by trainer.release_approval.require_note")
    if required and not approved:
        state = "pending"
    elif required and approved:
        state = "approved"
    else:
        state = "not_required"
    return {
        "required": required,
        "state": state,
        "approved": approved if required else False,
        "approved_by": approved_by if required else "",
        "approved_at": time.time() if required and approved else None,
        "note": note,
    }



def _evaluate_training_release_canary_guard(
    *,
    rollout_mode: str,
    canary_health: Dict[str, Any],
) -> Dict[str, Any]:
    release_gate_snapshot: Dict[str, Any] = {}
    release_gate_health: Dict[str, Any] = {}
    if rollout_mode == "canary":
        gate = _get_eval_benchmark_manager().get_release_gate_status()
        release_gate_snapshot = dict(gate) if isinstance(gate, dict) else {}
        release_gate_health = _assess_release_gate_workflow_health(
            gate_status=release_gate_snapshot,
            workflow_metrics=_build_workflow_observability_metrics(limit=200),
            persona_metrics=_latest_persona_consistency_signal(),
            coding_metrics=_build_coding_quality_metrics(window=100),
        )
    should_rollback_on_gate = bool(
        release_gate_snapshot.get("blocked", False)
        or release_gate_health.get("recommend_block_high_risk", False)
    )
    should_rollback_on_canary = bool(canary_health) and not bool(canary_health.get("passed", True))
    return {
        "release_gate": release_gate_snapshot,
        "release_gate_health": release_gate_health,
        "should_rollback_on_gate": should_rollback_on_gate,
        "should_rollback_on_canary": should_rollback_on_canary,
    }

def _audit_mcp_response(status: str, code: Optional[int] = None, message: Optional[str] = None) -> None:
    # A placeholder for future MCP audit tracing or stats; avoids NameError
    pass

def _mcp_response_ok(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    _audit_mcp_response(status="ok")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}



def _mcp_response_error(request_id: Any, code: int, message: str, data: Optional[Any] = None) -> Dict[str, Any]:
    _audit_mcp_response(status="error", code=code, message=message)
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload



def _mcp_text_resource(uri: str, name: str, data: Any) -> Dict[str, Any]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    return {
        "uri": uri,
        "name": name,
        "mimeType": "application/json",
        "text": text,
    }



def _summarize_training_output(output: Any) -> Dict[str, Any]:
    payload = output if isinstance(output, dict) else {}
    prompt_patch = payload.get("prompt_patch") if isinstance(payload.get("prompt_patch"), dict) else {}
    policy_patch = payload.get("policy_patch") if isinstance(payload.get("policy_patch"), dict) else {}
    router_patch = payload.get("router_patch") if isinstance(payload.get("router_patch"), dict) else {}
    rules = prompt_patch.get("rules") if isinstance(prompt_patch.get("rules"), list) else []
    denylist_add = policy_patch.get("security.tool_denylist.add")
    denylist = denylist_add if isinstance(denylist_add, list) else []
    return {
        "has_prompt_patch": bool(prompt_patch),
        "prompt_rule_count": len(rules),
        "has_policy_patch": bool(policy_patch),
        "denylist_add_count": len(denylist),
        "suggested_max_tier": str(policy_patch.get("security.tool_max_tier.suggested", "")).strip() or None,
        "has_router_patch": bool(router_patch),
        "router_strategy": str(router_patch.get("strategy", "")).strip() or None,
    }



def _resolve_online_policy_gate_thresholds(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config.get("trainer.online_policy_loop.gate", {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    patch = payload if isinstance(payload, dict) else {}

    def _get_bool(key: str, default: bool) -> bool:
        if key not in patch:
            return bool(cfg.get(key, default))
        return bool(patch.get(key))

    def _get_float(key: str, default: float) -> float:
        raw = patch.get(key, cfg.get(key, default))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    return {
        "require_release_gate_open": _get_bool("require_release_gate_open", True),
        "min_eval_pass_rate": _get_float("min_eval_pass_rate", 0.55),
        "min_trajectory_success_rate": _get_float("min_trajectory_success_rate", 0.6),
        "max_terminal_error_rate": _get_float("max_terminal_error_rate", 0.4),
    }



def _resolve_online_policy_offpolicy_config(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    loop_cfg = config.get("trainer.online_policy_loop", {}) or {}
    if not isinstance(loop_cfg, dict):
        loop_cfg = {}
    cfg = loop_cfg.get("offpolicy", {})
    if not isinstance(cfg, dict):
        cfg = {}
    patch = payload if isinstance(payload, dict) else {}

    def _get_bool(key: str, default: bool) -> bool:
        if key not in patch:
            return bool(cfg.get(key, default))
        return bool(patch.get(key))

    def _get_int(key: str, default: int, minimum: int = 1) -> int:
        raw = patch.get(key, cfg.get(key, default))
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = int(default)
        return max(minimum, parsed)

    def _get_float(key: str, default: float) -> float:
        raw = patch.get(key, cfg.get(key, default))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    return {
        "enabled": _get_bool("enabled", True),
        "auto_run_on_create": _get_bool("auto_run_on_create", True),
        "baseline_index": _get_int("baseline_index", 1, minimum=1),
        "bootstrap_rounds": _get_int("bootstrap_rounds", 300, minimum=20),
        "min_reward_threshold": max(0.0, min(1.0, _get_float("min_reward_threshold", 0.6))),
        "min_samples_for_confidence": _get_int("min_samples_for_confidence", 20, minimum=1),
    }





# ---------------------------------------------------------------------------
# Auto-recovered definitions (from admin_api_legacy.py)
# ---------------------------------------------------------------------------



_MAX_WS_MESSAGE_BYTES = int(config.get("api.max_ws_message_bytes", 256 * 1024))
_MAX_CHAT_MESSAGE_CHARS = int(config.get("api.max_chat_message_chars", 8000))
_mcp_request_ctx: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "mcp_request_ctx",
    default=None,
)
def _parse_tool_result_stats(events: Any) -> tuple[int, int, Dict[str, int]]:
    if not isinstance(events, list):
        return 0, 0, {}
    total = 0
    failures = 0
    by_code: Dict[str, int] = {}
    for rec in events:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("action", "")).strip() != "tool_result":
            continue
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        total += 1
        status = str(payload.get("status", "")).strip().lower()
        if status != "error":
            continue
        failures += 1
        code = str(payload.get("error_code", "")).strip() or "UNKNOWN"
        by_code[code] = by_code.get(code, 0) + 1
    return total, failures, by_code
def _p95(values: List[float]) -> float:
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    if not numbers:
        return 0.0
    numbers.sort()
    idx = int(0.95 * (len(numbers) - 1))
    return round(numbers[idx], 2)
def _build_llm_tool_failure_profile(limit: int = 200) -> Dict[str, Any]:
    label_order = [
        "tool_parameter_error",
        "permission_error",
        "environment_error",
        "strategy_error",
    ]

    def _classify_failure_attribution(error_code: str, error_text: str, *, source_action: str) -> str:
        code = str(error_code or "").strip().lower()
        text = str(error_text or "").strip().lower()
        merged = f"{code} {text}"

        parameter_markers = (
            "invalid parameter",
            "invalid argument",
            "invalid args",
            "bad_request",
            "schema",
            "json decode",
            "parse error",
            "must be",
            "missing required",
            "validation",
            "type error",
        )
        permission_markers = (
            "permission denied",
            "forbidden",
            "unauthorized",
            "access denied",
            "not permitted",
            "owner only",
            "auth",
            "token",
            "403",
        )
        environment_markers = (
            "timeout",
            "timed out",
            "network",
            "connection reset",
            "connection aborted",
            "service unavailable",
            "dependency",
            "temporarily unavailable",
            "dns",
            "disk full",
            "i/o error",
        )
        strategy_markers = (
            "policy",
            "router",
            "route",
            "planning",
            "planner",
            "replan",
            "tool budget",
            "circuit open",
            "release_gate",
            "fallback exhausted",
        )

        if code in {
            "invalid_parameter",
            "invalid_arguments",
            "tool_invalid_params",
            "schema_validation_failed",
        }:
            return "tool_parameter_error"
        if code in {
            "tool_not_permitted",
            "forbidden",
            "unauthorized",
            "permission_denied",
            "tool_tier_blocked",
        }:
            return "permission_error"
        if code in {
            "timeout",
            "network_timeout",
            "service_unavailable",
            "dependency_error",
        }:
            return "environment_error"
        if code in {
            "tool_budget_exceeded",
            "tool_circuit_open",
            "router_policy_blocked",
            "strategy_conflict",
        }:
            return "strategy_error"

        if any(marker in merged for marker in parameter_markers):
            return "tool_parameter_error"
        if any(marker in merged for marker in permission_markers):
            return "permission_error"
        if any(marker in merged for marker in environment_markers):
            return "environment_error"
        if any(marker in merged for marker in strategy_markers):
            return "strategy_error"

        if source_action == "llm_response":
            cls = classify_error_message(error_text)
            if cls == "retryable":
                return "environment_error"
        return "strategy_error"

    profile: Dict[str, Any] = {
        "llm": {
            "calls": 0,
            "failures": 0,
            "success_rate": 1.0,
            "error_classes": {},
        },
        "tool": {
            "calls": 0,
            "failures": 0,
            "success_rate": 1.0,
            "error_codes": {},
            "by_tool_failures": [],
        },
        "failure_attribution": {
            "total_failures": 0,
            "by_label": {label: 0 for label in label_order},
            "by_source": {"llm_response": 0, "tool_result": 0},
            "top_examples": [],
        },
        "replan_hints": 0,
    }
    if TRAJECTORY_STORE is None:
        return profile

    llm_error_classes: Dict[str, int] = {}
    tool_error_codes: Dict[str, int] = {}
    tool_failures: Dict[str, int] = {}
    llm_calls = 0
    llm_failures = 0
    tool_calls = 0
    tool_failures_total = 0
    replan_hints = 0
    attribution_counts: Dict[str, int] = {label: 0 for label in label_order}
    attribution_source_counts: Dict[str, int] = {"llm_response": 0, "tool_result": 0}
    attribution_examples: List[Dict[str, Any]] = []

    recent = TRAJECTORY_STORE.list_recent(limit=max(1, min(limit, 1000)))
    for item in recent:
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        traj = TRAJECTORY_STORE.get_trajectory(run_id)
        if not isinstance(traj, dict):
            continue
        events = traj.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            action = str(event.get("action", "")).strip().lower()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if action == "llm_response":
                llm_calls += 1
                llm_error = str(payload.get("error", "")).strip()
                if llm_error:
                    llm_failures += 1
                    cls = classify_error_message(llm_error)
                    llm_error_classes[cls] = llm_error_classes.get(cls, 0) + 1
                    label = _classify_failure_attribution("", llm_error, source_action="llm_response")
                    attribution_counts[label] = attribution_counts.get(label, 0) + 1
                    attribution_source_counts["llm_response"] = attribution_source_counts.get("llm_response", 0) + 1
                    if len(attribution_examples) < 25:
                        attribution_examples.append(
                            {
                                "source": "llm_response",
                                "label": label,
                                "run_id": run_id,
                                "tool": "",
                                "error_code": "",
                                "preview": llm_error[:160],
                            }
                        )
            elif action == "tool_call":
                tool_calls += 1
            elif action == "tool_result":
                status = str(payload.get("status", "")).strip().lower()
                if status not in {"ok", "success"}:
                    tool_failures_total += 1
                    tool_name = str(payload.get("tool", "")).strip() or "unknown"
                    tool_failures[tool_name] = tool_failures.get(tool_name, 0) + 1
                    code = str(payload.get("error_code", "")).strip().lower()
                    if not code:
                        code = classify_error_message(str(payload.get("result_preview", "")))
                    tool_error_codes[code] = tool_error_codes.get(code, 0) + 1
                    preview = str(payload.get("result_preview", "")).strip()
                    label = _classify_failure_attribution(code, preview, source_action="tool_result")
                    attribution_counts[label] = attribution_counts.get(label, 0) + 1
                    attribution_source_counts["tool_result"] = attribution_source_counts.get("tool_result", 0) + 1
                    if len(attribution_examples) < 25:
                        attribution_examples.append(
                            {
                                "source": "tool_result",
                                "label": label,
                                "run_id": run_id,
                                "tool": tool_name,
                                "error_code": code,
                                "preview": preview[:160],
                            }
                        )
            elif action == "replan_hint":
                replan_hints += 1

    by_tool = [{"tool": k, "failures": v} for k, v in sorted(tool_failures.items(), key=lambda kv: kv[1], reverse=True)]
    by_code = {k: v for k, v in sorted(tool_error_codes.items(), key=lambda kv: kv[1], reverse=True)}
    llm_success = round((llm_calls - llm_failures) / llm_calls, 4) if llm_calls else 1.0
    tool_success = round((tool_calls - tool_failures_total) / tool_calls, 4) if tool_calls else 1.0

    profile["llm"] = {
        "calls": llm_calls,
        "failures": llm_failures,
        "success_rate": llm_success,
        "error_classes": llm_error_classes,
    }
    profile["tool"] = {
        "calls": tool_calls,
        "failures": tool_failures_total,
        "success_rate": tool_success,
        "error_codes": by_code,
        "by_tool_failures": by_tool[:10],
    }
    sorted_labels = sorted(
        (
            {"label": label, "count": int(attribution_counts.get(label, 0))}
            for label in label_order
        ),
        key=lambda item: (-int(item["count"]), str(item["label"])),
    )
    profile["failure_attribution"] = {
        "total_failures": int(llm_failures + tool_failures_total),
        "by_label": {label: int(attribution_counts.get(label, 0)) for label in label_order},
        "ranked_labels": sorted_labels,
        "by_source": {
            "llm_response": int(attribution_source_counts.get("llm_response", 0)),
            "tool_result": int(attribution_source_counts.get("tool_result", 0)),
        },
        "top_examples": attribution_examples[:10],
    }
    profile["replan_hints"] = replan_hints
    return profile
def _build_tool_timing_profile(limit: int = 200) -> Dict[str, Any]:
    profile: Dict[str, Any] = {
        "sample_count": 0,
        "p95_latency_ms": 0.0,
        "by_tool": [],
        "success_timestamps_by_tool": {},
    }
    if TRAJECTORY_STORE is None:
        return profile

    latencies: List[float] = []
    by_tool_values: Dict[str, List[float]] = {}
    success_timestamps: Dict[str, List[float]] = {}

    recent = TRAJECTORY_STORE.list_recent(limit=max(1, min(limit, 1000)))
    for item in recent:
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        traj = TRAJECTORY_STORE.get_trajectory(run_id)
        if not isinstance(traj, dict):
            continue
        events = list(traj.get("events") or [])
        pending_calls: Dict[str, Dict[str, Any]] = {}
        for idx, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            action = str(event.get("action", "")).strip().lower()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if action == "tool_call":
                tool = str(payload.get("tool", "")).strip() or "unknown"
                call_id = str(payload.get("tool_call_id", "")).strip() or f"{tool}#{idx}"
                ts = event.get("ts")
                if isinstance(ts, (int, float)):
                    pending_calls[call_id] = {"tool": tool, "ts": float(ts)}
            elif action == "tool_result":
                tool = str(payload.get("tool", "")).strip() or "unknown"
                status = str(payload.get("status", "")).strip().lower()
                ts = event.get("ts")
                ts_value = float(ts) if isinstance(ts, (int, float)) else None
                if status in {"ok", "success"} and ts_value is not None:
                    success_timestamps.setdefault(tool, []).append(ts_value)
                call_id = str(payload.get("tool_call_id", "")).strip()
                if not call_id or call_id not in pending_calls or ts_value is None:
                    continue
                call = pending_calls.pop(call_id)
                started = float(call.get("ts", 0.0) or 0.0)
                delta_ms = max(0.0, (ts_value - started) * 1000.0)
                if delta_ms > 0:
                    latencies.append(delta_ms)
                    by_tool_values.setdefault(str(call.get("tool", tool)), []).append(delta_ms)

    by_tool: List[Dict[str, Any]] = []
    for tool_name, values in by_tool_values.items():
        by_tool.append(
            {
                "tool": tool_name,
                "sample_count": len(values),
                "p95_latency_ms": _p95(values),
            }
        )
    by_tool.sort(key=lambda item: int(item.get("sample_count", 0)), reverse=True)
    profile["sample_count"] = len(latencies)
    profile["p95_latency_ms"] = _p95(latencies)
    profile["by_tool"] = by_tool[:20]
    profile["success_timestamps_by_tool"] = {
        key: sorted(value)
        for key, value in success_timestamps.items()
    }
    return profile


# ---------------------------------------------------------------------------
# Re-exported external helpers (lazy to avoid circular imports at startup)
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

