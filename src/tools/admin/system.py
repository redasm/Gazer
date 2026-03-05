from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import FileResponse
from typing import Dict, Any, List, Optional
import time
import json
import logging
from datetime import datetime
from runtime.resilience import classify_error_message
# Backward-compat aliases for static helpers that are NOT runtime-injected
from tools.admin.auth import verify_admin_token
import tools.admin._shared as _shared
from security.pairing import get_pairing_manager
from tools.admin.coding_helpers import _parse_tool_error_result
from tools.admin.state import (
    CANVAS_STATE,
    _FAVICON_ICO_PATH,
    LLM_ROUTER,
    ORCHESTRATOR,
    TOOL_REGISTRY,
    TRAJECTORY_STORE,
    _WEB_ONBOARDING_GUIDE_PATH,
    _coding_benchmark_history,
    _coding_benchmark_scheduler_state,
    _coding_quality_history,
    config,
    get_canvas_state,
    get_llm_router,
    get_orchestrator,
    get_prompt_cache_tracker,
    get_tool_batching_tracker,
    get_tool_registry,
    get_trajectory_store,
    get_usage_tracker,
    _policy_audit_buffer,
    _workflow_run_history,
)
from tools.admin.strategy_helpers import (
    _append_policy_audit,
    _get_tool_governance_snapshot,
    _is_success_status,
    _merge_error_code_counts,
)
from tools.admin.utils import _resolve_export_output_path
from tools.admin._shared import get_owner_manager, get_provider_registry
def _get_training_job_manager():
    global _TRAINING_JOB_MANAGER
    if _TRAINING_JOB_MANAGER is None:
        from eval.trainer import TrainingJobManager
        _TRAINING_JOB_MANAGER = TrainingJobManager()
    return _TRAINING_JOB_MANAGER
_TRAINING_JOB_MANAGER = None

def _get_training_bridge_manager():
    global _TRAINING_BRIDGE_MANAGER
    if _TRAINING_BRIDGE_MANAGER is None:
        from eval.training_bridge import TrainingBridgeManager
        _TRAINING_BRIDGE_MANAGER = TrainingBridgeManager()
    return _TRAINING_BRIDGE_MANAGER
_TRAINING_BRIDGE_MANAGER = None
app = APIRouter()
logger = logging.getLogger('system')

def _build_coding_quality_metrics(window: int = 50, kind: Optional[str] = None) -> Dict[str, Any]:
    kind_filter = str(kind or "").strip().lower()
    items = list(_coding_quality_history)
    if kind_filter:
        items = [item for item in items if str(item.get("kind", "")).strip().lower() == kind_filter]
    items = items[-max(1, min(window, 500)) :]
    if not items:
        return {
            "total_runs": 0,
            "success_runs": 0,
            "pass_rate": 1.0,
            "avg_duration_ms": 0.0,
            "avg_files_changed": 0.0,
            "avg_test_commands": 0.0,
            "window": max(1, min(window, 500)),
        }
    total = len(items)
    success = sum(1 for item in items if bool(item.get("success", False)))
    avg_duration = sum(float(item.get("duration_ms", 0.0) or 0.0) for item in items) / total
    duration_values = sorted(float(item.get("duration_ms", 0.0) or 0.0) for item in items)
    avg_files_changed = sum(float(item.get("files_changed", 0.0) or 0.0) for item in items) / total
    avg_test_commands = sum(float(item.get("tests_total", 0.0) or 0.0) for item in items) / total
    conflict_recoveries = sum(int(item.get("recovery_count", 0) or 0) for item in items)
    p95_idx = min(len(duration_values) - 1, max(0, int(round((len(duration_values) - 1) * 0.95))))
    p95_duration = duration_values[p95_idx] if duration_values else 0.0
    return {
        "total_runs": total,
        "success_runs": success,
        "pass_rate": round(success / total, 4),
        "avg_duration_ms": round(avg_duration, 2),
        "p95_duration_ms": round(float(p95_duration), 2),
        "avg_files_changed": round(avg_files_changed, 2),
        "avg_test_commands": round(avg_test_commands, 2),
        "conflict_recoveries": conflict_recoveries,
        "window": max(1, min(window, 500)),
        "recent": items[-10:],
    }

def _build_coding_benchmark_leaderboard(window: int = 20) -> Dict[str, Any]:
    size = max(1, min(int(window), 200))
    items = list(_coding_benchmark_history)[-size:]
    ranked = sorted(
        items,
        key=lambda x: (
            float(x.get("score", 0.0) or 0.0),
            int(x.get("success_cases", 0) or 0),
            -float(x.get("duration_ms", 0.0) or 0.0),
        ),
        reverse=True,
    )
    top = []
    for rec in ranked[:20]:
        top.append(
            {
                "name": rec.get("name"),
                "score": rec.get("score"),
                "success_cases": rec.get("success_cases"),
                "total_cases": rec.get("total_cases"),
                "duration_ms": rec.get("duration_ms"),
                "ts": rec.get("ts"),
            }
        )
    return {
        "total_runs": len(items),
        "window": size,
        "top": top,
    }

def _build_coding_benchmark_observability(window: int = 60) -> Dict[str, Any]:
    size = max(1, min(int(window), 400))
    items = list(_coding_benchmark_history)[-size:]
    if not items:
        return {
            "window": size,
            "total_runs": 0,
            "avg_score": 0.0,
            "trend": [],
            "failure_reasons": [],
        }

    scores = [float(item.get("score", 0.0) or 0.0) for item in items]
    trend_map: Dict[str, Dict[str, Any]] = {}
    reason_counter: Dict[str, int] = {}
    for rec in items:
        ts = float(rec.get("ts", 0.0) or 0.0)
        day = time.strftime("%Y-%m-%d", time.localtime(ts if ts > 0 else time.time()))
        bucket = trend_map.setdefault(day, {"date": day, "runs": 0, "avg_score": 0.0, "success_cases": 0, "total_cases": 0})
        bucket["runs"] += 1
        bucket["avg_score"] += float(rec.get("score", 0.0) or 0.0)
        bucket["success_cases"] += int(rec.get("success_cases", 0) or 0)
        bucket["total_cases"] += int(rec.get("total_cases", 0) or 0)

        for case in list(rec.get("cases", []) or []):
            if bool(case.get("success", False)):
                continue
            errors = list(case.get("contains_errors", []) or [])
            if not errors:
                reason_counter["unknown_failure"] = reason_counter.get("unknown_failure", 0) + 1
                continue
            for err in errors:
                reason = str(err or "").strip()[:160] or "unknown_failure"
                reason_counter[reason] = reason_counter.get(reason, 0) + 1

    trend = []
    for _, bucket in sorted(trend_map.items(), key=lambda kv: kv[0]):
        runs = max(1, int(bucket["runs"]))
        trend.append(
            {
                "date": bucket["date"],
                "runs": runs,
                "avg_score": round(float(bucket["avg_score"]) / runs, 4),
                "success_cases": int(bucket["success_cases"]),
                "total_cases": int(bucket["total_cases"]),
            }
        )
    reasons = sorted(
        [{"reason": key, "count": int(val)} for key, val in reason_counter.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:20]
    return {
        "window": size,
        "total_runs": len(items),
        "avg_score": round(sum(scores) / len(scores), 4),
        "trend": trend,
        "failure_reasons": reasons,
    }

async def _invoke_gui_action_via_tool_registry(action: str, args: Dict[str, Any], target: str) -> Dict[str, Any]:
    if TOOL_REGISTRY is None:
        return {
            "ok": False,
            "code": "TOOL_REGISTRY_UNAVAILABLE",
            "message": "Tool registry unavailable",
            "raw": "",
        }
    payload: Dict[str, Any] = {"action": str(action or "").strip(), "args": dict(args or {})}
    if str(target or "").strip():
        payload["target"] = str(target).strip()
    text = await TOOL_REGISTRY.execute(
        "node_invoke",
        payload,
    )
    raw = str(text or "")
    if raw.startswith("Error"):
        parsed = _parse_tool_error_result(raw)
        return {
            "ok": False,
            "code": parsed.get("code", "UNKNOWN_ERROR"),
            "message": parsed.get("message", raw),
            "raw": raw,
        }
    return {"ok": True, "code": "", "message": raw[:300], "raw": raw}

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

def _build_efficiency_window_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    runs = len(items)
    if runs == 0:
        return {
            "runs": 0,
            "success_runs": 0,
            "success_rate": 1.0,
            "avg_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "total_tokens": 0,
            "avg_tokens_per_run": 0.0,
            "total_tool_rounds": 0,
            "avg_tool_rounds_per_run": 0.0,
            "total_tool_calls": 0,
            "tool_error_count": 0,
            "tool_error_rate": 0.0,
            "top_error_codes": [],
            "sample_run_ids": [],
        }

    success_runs = sum(1 for item in items if _is_success_status(item.get("status")))
    latencies = [
        float(item.get("turn_latency_ms", 0.0) or 0.0)
        for item in items
        if isinstance(item.get("turn_latency_ms"), (int, float))
    ]
    total_tokens = sum(max(0, int(item.get("tokens", 0) or 0)) for item in items)
    total_tool_rounds = sum(max(0, int(item.get("tool_rounds", 0) or 0)) for item in items)
    total_tool_calls = sum(max(0, int(item.get("tool_calls", 0) or 0)) for item in items)
    tool_error_count = sum(max(0, int(item.get("tool_failures", 0) or 0)) for item in items)

    merged_codes = _merge_error_code_counts(
        [item.get("error_codes", {}) if isinstance(item.get("error_codes"), dict) else {} for item in items]
    )
    top_error_codes = sorted(
        (
            {"code": str(key), "count": int(value)}
            for key, value in merged_codes.items()
            if int(value) > 0
        ),
        key=lambda row: row["count"],
        reverse=True,
    )[:5]

    return {
        "runs": runs,
        "success_runs": success_runs,
        "success_rate": round(success_runs / runs, 4),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": _p95(latencies),
        "total_tokens": int(total_tokens),
        "avg_tokens_per_run": round(total_tokens / runs, 2),
        "total_tool_rounds": int(total_tool_rounds),
        "avg_tool_rounds_per_run": round(total_tool_rounds / runs, 4),
        "total_tool_calls": int(total_tool_calls),
        "tool_error_count": int(tool_error_count),
        "tool_error_rate": round(tool_error_count / max(1, total_tool_calls), 4),
        "top_error_codes": top_error_codes,
        "sample_run_ids": [str(item.get("run_id", "")) for item in items[:20] if str(item.get("run_id", "")).strip()],
    }

def _p95(values: List[float]) -> float:
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    if not numbers:
        return 0.0
    numbers.sort()
    idx = int(0.95 * (len(numbers) - 1))
    return round(numbers[idx], 2)

def _build_workflow_observability_metrics(limit: int = 200) -> Dict[str, Any]:
    workflow_metrics: Dict[str, Any] = {
        "total_runs": 0,
        "failures": 0,
        "success_rate": 1.0,
        "p95_latency_ms": 0.0,
        "p95_node_duration_ms": 0.0,
        "p95_trace_nodes": 0.0,
        "error_classes": {},
        "workflows": [],
    }
    workflow_history = list(_workflow_run_history)
    if not workflow_history:
        return workflow_metrics

    sampled = workflow_history[-max(1, min(limit, 1000)):]
    by_workflow: Dict[str, Dict[str, Any]] = {}
    all_latency: List[float] = []
    all_node_duration: List[float] = []
    all_trace_nodes: List[float] = []
    failures = 0
    error_classes: Dict[str, int] = {}

    for item in sampled:
        workflow_id = str(item.get("workflow_id", "")).strip() or "unknown"
        entry = by_workflow.setdefault(
            workflow_id,
            {
                "workflow_id": workflow_id,
                "workflow_name": str(item.get("workflow_name", "")).strip() or workflow_id,
                "runs": 0,
                "failures": 0,
                "latencies": [],
                "node_durations": [],
                "trace_nodes": [],
                "error_classes": {},
            },
        )
        entry["runs"] += 1
        status = str(item.get("status", "")).strip().lower()
        duration_ms = float(item.get("total_duration_ms", 0) or 0)
        node_duration_ms = float(item.get("node_duration_ms", 0) or 0)
        trace_nodes = float(item.get("trace_nodes", 0) or 0)
        entry["latencies"].append(duration_ms)
        entry["node_durations"].append(node_duration_ms)
        entry["trace_nodes"].append(trace_nodes)
        all_latency.append(duration_ms)
        all_node_duration.append(node_duration_ms)
        all_trace_nodes.append(trace_nodes)
        if status != "ok":
            failures += 1
            entry["failures"] += 1
            err_class = classify_error_message(str(item.get("error", "")))
            entry["error_classes"][err_class] = entry["error_classes"].get(err_class, 0) + 1
            error_classes[err_class] = error_classes.get(err_class, 0) + 1

    workflow_items: List[Dict[str, Any]] = []
    for item in by_workflow.values():
        runs = int(item["runs"])
        fails = int(item["failures"])
        workflow_items.append(
            {
                "workflow_id": item["workflow_id"],
                "workflow_name": item["workflow_name"],
                "runs": runs,
                "failures": fails,
                "success_rate": round((runs - fails) / runs, 4) if runs else 1.0,
                "p95_latency_ms": _p95(item["latencies"]),
                "p95_node_duration_ms": _p95(item["node_durations"]),
                "p95_trace_nodes": _p95(item["trace_nodes"]),
                "error_classes": item["error_classes"],
            }
        )
    workflow_items.sort(key=lambda x: x["runs"], reverse=True)
    total_runs = len(sampled)
    workflow_metrics = {
        "total_runs": total_runs,
        "failures": failures,
        "success_rate": round((total_runs - failures) / total_runs, 4) if total_runs else 1.0,
        "p95_latency_ms": _p95(all_latency),
        "p95_node_duration_ms": _p95(all_node_duration),
        "p95_trace_nodes": _p95(all_trace_nodes),
        "error_classes": error_classes,
        "workflows": workflow_items,
    }
    return workflow_metrics

def _latest_persona_consistency_signal() -> Dict[str, Any]:
    from tools.admin.persona_routes import _get_persona_eval_manager
    manager = _get_persona_eval_manager()
    datasets = manager.list_datasets(limit=20)
    latest_score = 0.0
    latest_dataset_id = ""
    latest_ts = 0.0
    for dataset in datasets:
        dataset_id = str(dataset.get("id", "")).strip()
        if not dataset_id:
            continue
        run = manager.get_latest_run(dataset_id)
        if not isinstance(run, dict):
            continue
        created_at = float(run.get("created_at", 0.0) or 0.0)
        if created_at < latest_ts:
            continue
        latest_ts = created_at
        latest_dataset_id = dataset_id
        latest_score = float(run.get("consistency_score", 0.0) or 0.0)
    return {
        "dataset_id": latest_dataset_id,
        "latest_score": round(latest_score, 4),
        "has_data": bool(latest_dataset_id),
        "updated_at": latest_ts,
    }

def _build_persona_consistency_weekly_report(window_days: int = 7, source: str = "persona_eval") -> Dict[str, Any]:
    from tools.admin.persona_routes import _get_persona_runtime_manager, _get_persona_eval_manager
    window = max(1, min(int(window_days or 7), 30))
    now = time.time()
    window_seconds = float(window * 86400)
    current_start = now - window_seconds
    previous_start = current_start - window_seconds

    runtime_mgr = _get_persona_runtime_manager()
    signals = runtime_mgr.list_signals(limit=5000, source=source)

    def _to_ts(item: Dict[str, Any]) -> float:
        try:
            return float(item.get("created_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _count_levels(items: List[Dict[str, Any]]) -> Dict[str, int]:
        out = {"healthy": 0, "warning": 0, "critical": 0}
        for item in items:
            level = str(item.get("level", "healthy")).strip().lower() or "healthy"
            if level not in out:
                level = "healthy"
            out[level] += 1
        return out

    current_signals = [item for item in signals if current_start <= _to_ts(item) <= now]
    previous_signals = [item for item in signals if previous_start <= _to_ts(item) < current_start]
    current_levels = _count_levels(current_signals)
    previous_levels = _count_levels(previous_signals)

    persona_mgr = _get_persona_eval_manager()
    score_current: List[float] = []
    score_previous: List[float] = []
    for dataset in persona_mgr.list_datasets(limit=300):
        dataset_id = str(dataset.get("id", "")).strip()
        if not dataset_id:
            continue
        for run in persona_mgr.list_runs(dataset_id, limit=100):
            ts = _to_ts(run)
            score = float(run.get("consistency_score", 0.0) or 0.0)
            if current_start <= ts <= now:
                score_current.append(score)
            elif previous_start <= ts < current_start:
                score_previous.append(score)

    avg_current = round(sum(score_current) / len(score_current), 4) if score_current else None
    avg_previous = round(sum(score_previous) / len(score_previous), 4) if score_previous else None
    score_delta = None
    if avg_current is not None and avg_previous is not None:
        score_delta = round(avg_current - avg_previous, 4)

    warning_delta = int(current_levels["warning"] - previous_levels["warning"])
    critical_delta = int(current_levels["critical"] - previous_levels["critical"])
    trend = "stable"
    if warning_delta > 0 or critical_delta > 0:
        trend = "worse"
    elif warning_delta < 0 and critical_delta < 0:
        trend = "improving"

    return {
        "status": "ok",
        "generated_at": now,
        "window_days": window,
        "source": str(source or "persona_eval"),
        "current_window": {
            "start_at": current_start,
            "end_at": now,
            "signal_total": len(current_signals),
            "levels": current_levels,
            "consistency_score_avg": avg_current,
            "consistency_score_count": len(score_current),
        },
        "previous_window": {
            "start_at": previous_start,
            "end_at": current_start,
            "signal_total": len(previous_signals),
            "levels": previous_levels,
            "consistency_score_avg": avg_previous,
            "consistency_score_count": len(score_previous),
        },
        "trend": {
            "warning_delta": warning_delta,
            "critical_delta": critical_delta,
            "consistency_score_delta": score_delta,
            "direction": trend,
        },
    }

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

def _build_training_gain_summary(limit: int = 50) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    summary: Dict[str, Any] = {
        "job_count": 0,
        "avg_score": None,
        "latest_score": None,
        "latest_score_delta_vs_prev": None,
        "avg_reward_proxy_delta": None,
    }
    try:
        manager = _get_training_job_manager()
        jobs = manager.list_jobs(limit=safe_limit, status=None)
    except Exception:
        return summary
    if not isinstance(jobs, list):
        return summary

    scored: List[float] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        score_payload = _score_training_job(job)
        score = score_payload.get("score")
        if isinstance(score, (int, float)):
            scored.append(float(score))
    summary["job_count"] = len(scored)
    if scored:
        summary["avg_score"] = round(sum(scored) / len(scored), 4)
        summary["latest_score"] = round(scored[0], 4)
        if len(scored) >= 2:
            summary["latest_score_delta_vs_prev"] = round(scored[0] - scored[1], 4)

    reward_deltas: List[float] = []
    try:
        bridge_manager = _get_training_bridge_manager()
        exports = bridge_manager.list_exports(limit=safe_limit)
        by_dataset: Dict[str, int] = {}
        for export in exports:
            if not isinstance(export, dict):
                continue
            dataset_id = str(export.get("dataset_id", "")).strip()
            if not dataset_id:
                continue
            by_dataset[dataset_id] = by_dataset.get(dataset_id, 0) + 1
        for dataset_id, count in by_dataset.items():
            if count < 2:
                continue
            compared = bridge_manager.compare_with_baseline(dataset_id, baseline_index=1)
            if not isinstance(compared, dict):
                continue
            reward_delta = compared.get("reward_proxy_delta") if isinstance(compared.get("reward_proxy_delta"), dict) else {}
            value = reward_delta.get("trajectory_success_rate")
            if isinstance(value, (int, float)):
                reward_deltas.append(float(value))
    except Exception:
        reward_deltas = []
    if reward_deltas:
        summary["avg_reward_proxy_delta"] = round(sum(reward_deltas) / len(reward_deltas), 4)
    return summary

def _build_training_bridge_policy_scoreboard(
    *,
    limit: int = 50,
    dataset_id: Optional[str] = None,
) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    dataset_key = str(dataset_id or "").strip()
    scoreboard: Dict[str, Any] = {
        "generated_at": time.time(),
        "total_datasets": 0,
        "datasets": [],
        "global": {
            "avg_policy_score": None,
            "best_dataset": None,
            "worst_dataset": None,
        },
    }
    try:
        manager = _get_training_bridge_manager()
        exports = manager.list_exports(limit=safe_limit, dataset_id=dataset_key or None)
    except Exception:
        return scoreboard
    if not isinstance(exports, list) or not exports:
        return scoreboard

    by_dataset: Dict[str, List[Dict[str, Any]]] = {}
    for item in exports:
        if not isinstance(item, dict):
            continue
        ds = str(item.get("dataset_id", "")).strip()
        if not ds:
            continue
        bucket = by_dataset.setdefault(ds, [])
        bucket.append(item)

    dataset_rows: List[Dict[str, Any]] = []
    for ds in sorted(by_dataset.keys()):
        history = by_dataset[ds]
        latest = history[0] if history else {}
        summary = latest.get("summary", {}) if isinstance(latest.get("summary"), dict) else {}
        offline = summary.get("offline_policy_eval", {}) if isinstance(summary.get("offline_policy_eval"), dict) else {}

        try:
            trajectory_success = float(offline.get("trajectory_success_rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            trajectory_success = 0.0
        eval_pass_raw = summary.get("eval_pass_rate")
        try:
            eval_pass = float(eval_pass_raw) if eval_pass_raw is not None else trajectory_success
        except (TypeError, ValueError):
            eval_pass = trajectory_success
        try:
            terminal_error_rate = float(summary.get("terminal_error_rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            terminal_error_rate = 0.0
        persona_raw = offline.get("persona_consistency_score_avg")
        try:
            persona_score = float(persona_raw) if persona_raw is not None else 0.75
        except (TypeError, ValueError):
            persona_score = 0.75

        compare = manager.compare_with_baseline(ds, baseline_index=1)
        reward_delta = compare.get("reward_proxy_delta", {}) if isinstance(compare, dict) else {}
        try:
            delta_success = float(reward_delta.get("trajectory_success_rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            delta_success = 0.0
        try:
            delta_feedback = float(reward_delta.get("avg_feedback_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            delta_feedback = 0.0

        base_score = (
            0.45 * trajectory_success
            + 0.2 * eval_pass
            + 0.2 * max(0.0, 1.0 - terminal_error_rate)
            + 0.15 * persona_score
        )
        policy_score = max(
            0.0,
            min(
                1.0,
                base_score + (0.08 * max(-1.0, min(1.0, delta_success))) + (0.02 * max(-1.0, min(1.0, delta_feedback))),
            ),
        )
        policy_score = round(policy_score, 4)
        if policy_score >= 0.8:
            tier = "good"
        elif policy_score >= 0.6:
            tier = "warning"
        else:
            tier = "critical"

        failure_types = offline.get("tool_failure_types", {}) if isinstance(offline.get("tool_failure_types"), dict) else {}
        top_failure_types = sorted(
            (
                {"error_code": str(code), "count": int(count)}
                for code, count in failure_types.items()
            ),
            key=lambda item: (-int(item["count"]), str(item["error_code"])),
        )[:5]

        dataset_rows.append(
            {
                "dataset_id": ds,
                "latest_export_id": str(latest.get("export_id", "")).strip(),
                "history_count": len(history),
                "sample_count": int(latest.get("sample_count", 0) or 0),
                "policy_score": policy_score,
                "tier": tier,
                "metrics": {
                    "trajectory_success_rate": round(trajectory_success, 4),
                    "eval_pass_rate": round(eval_pass, 4) if eval_pass is not None else None,
                    "terminal_error_rate": round(terminal_error_rate, 4),
                    "persona_consistency_score_avg": round(persona_score, 4) if persona_raw is not None else None,
                },
                "trend": {
                    "trajectory_success_rate_delta": round(delta_success, 4),
                    "avg_feedback_score_delta": round(delta_feedback, 4),
                },
                "top_failure_types": top_failure_types,
            }
        )

    dataset_rows.sort(key=lambda item: (-float(item.get("policy_score", 0.0)), str(item.get("dataset_id", ""))))
    scoreboard["datasets"] = dataset_rows
    scoreboard["total_datasets"] = len(dataset_rows)
    if dataset_rows:
        scores = [float(item.get("policy_score", 0.0) or 0.0) for item in dataset_rows]
        scoreboard["global"]["avg_policy_score"] = round(sum(scores) / len(scores), 4)
        scoreboard["global"]["best_dataset"] = str(dataset_rows[0].get("dataset_id", ""))
        scoreboard["global"]["worst_dataset"] = str(dataset_rows[-1].get("dataset_id", ""))
    return scoreboard

def _build_alignment_baseline_panel(limit: int = 200, window_days: int = 7) -> Dict[str, Any]:
    from tools.admin.observability import _build_tool_governance_slo
    tool_slo = _build_tool_governance_slo(limit=limit)
    workflow = _build_workflow_observability_metrics(limit=limit)
    persona = _build_persona_consistency_weekly_report(window_days=window_days, source="persona_eval")
    training = _build_training_gain_summary(limit=50)

    persona_current = None
    current_window = persona.get("current_window") if isinstance(persona.get("current_window"), dict) else {}
    if isinstance(current_window, dict):
        score = current_window.get("consistency_score_avg")
        if isinstance(score, (int, float)):
            persona_current = float(score)

    workflow_p95 = float(workflow.get("p95_latency_ms", 0.0) or 0.0)
    checks = {
        "tool_success_rate_ok": bool((tool_slo.get("checks") or {}).get("tool_success_rate_ok", True)),
        "workflow_p95_ok": workflow_p95 <= 5000.0 if int(workflow.get("total_runs", 0) or 0) > 0 else True,
        "persona_consistency_ok": (persona_current is None) or (persona_current >= 0.8),
        "training_gain_ok": (
            (training.get("avg_score") is None)
            or (float(training.get("avg_score") or 0.0) >= 0.5)
        ),
    }
    return {
        "generated_at": time.time(),
        "window_days": max(1, min(int(window_days or 7), 30)),
        "metrics": {
            "tool_success_rate": ((tool_slo.get("metrics") or {}).get("tool_success_rate")),
            "workflow_p95_latency_ms": workflow_p95,
            "persona_consistency_score": persona_current,
            "training_avg_score": training.get("avg_score"),
            "training_reward_proxy_delta": training.get("avg_reward_proxy_delta"),
        },
        "components": {
            "tool_governance_slo": tool_slo,
            "workflow_observability": workflow,
            "persona_consistency_weekly": persona,
            "training_gain": training,
        },
        "checks": checks,
        "passed": all(bool(v) for v in checks.values()),
    }

def _build_self_evolution_offline_report(case_limit: int = 5) -> Dict[str, Any]:
    safe_limit = max(1, min(int(case_limit), 20))
    from eval.self_evolution_replay import build_default_replays, compare_planning_strategies
    replays = build_default_replays()[:safe_limit]
    report = compare_planning_strategies(replays, beam_width=3, horizon=2)
    report["generated_at"] = time.time()
    report["experiment"] = "self_evolution_light_planning_v1"
    report["case_limit"] = safe_limit
    return report

def _build_inbound_media_profile(limit: int = 200) -> Dict[str, Any]:
    profile: Dict[str, Any] = {
        "events": 0,
        "media_entries": 0,
        "successful_entries": 0,
        "failed_entries": 0,
        "success_rate": 1.0,
        "by_source": {},
        "by_type": {},
    }
    if TRAJECTORY_STORE is None:
        return profile

    recent = TRAJECTORY_STORE.list_recent(limit=max(1, min(limit, 1000)))
    by_source: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    events = 0
    entries = 0
    success = 0
    failed = 0

    def _inc(bucket: Dict[str, int], key: str) -> None:
        k = str(key or "unknown").strip().lower() or "unknown"
        bucket[k] = bucket.get(k, 0) + 1

    for item in recent:
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        traj = TRAJECTORY_STORE.get_trajectory(run_id)
        if not isinstance(traj, dict):
            continue
        for event in list(traj.get("events") or []):
            if not isinstance(event, dict):
                continue
            if str(event.get("action", "")).strip().lower() != "inbound_metadata":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                continue
            events += 1

            source = "unknown"
            if "feishu_message_type" in metadata or "feishu_media" in metadata:
                source = "feishu"
            elif "web_media" in metadata:
                source = "web"
            elif "telegram_message_type" in metadata:
                source = "telegram"
            _inc(by_source, source)

            # Feishu/Web can include multiple media entries.
            entry_items: List[Dict[str, Any]] = []
            feishu_media = metadata.get("feishu_media")
            if isinstance(feishu_media, list):
                for row in feishu_media:
                    if isinstance(row, dict):
                        entry_items.append(
                            {
                                "type": str(row.get("message_type", "") or metadata.get("feishu_message_type", "")),
                                "path": str(row.get("path", "") or "").strip(),
                            }
                        )
            web_media = metadata.get("web_media")
            if isinstance(web_media, list):
                for row in web_media:
                    if isinstance(row, dict):
                        entry_items.append(
                            {
                                "type": str(row.get("mime", "") or row.get("source", "") or "web_media"),
                                "path": str(row.get("path", "") or row.get("url", "") or "").strip(),
                            }
                        )

            # Telegram metadata is currently one media item per event.
            telegram_type = str(metadata.get("telegram_message_type", "") or "").strip()
            if telegram_type:
                entry_items.append({"type": telegram_type, "path": ""})

            for media_item in entry_items:
                entries += 1
                media_type = str(media_item.get("type", "") or "unknown")
                _inc(by_type, media_type)
                media_path = str(media_item.get("path", "") or "").strip()
                if media_path:
                    success += 1
                else:
                    # Telegram currently does not persist path in metadata; count as observed success.
                    if source == "telegram":
                        success += 1
                    else:
                        failed += 1

    profile["events"] = events
    profile["media_entries"] = entries
    profile["successful_entries"] = success
    profile["failed_entries"] = failed
    profile["success_rate"] = round(success / entries, 4) if entries else 1.0
    profile["by_source"] = {k: by_source[k] for k in sorted(by_source)}
    profile["by_type"] = {k: by_type[k] for k in sorted(by_type)}
    return profile

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve a real favicon file for browser requests."""
    if _FAVICON_ICO_PATH.is_file():
        return FileResponse(path=_FAVICON_ICO_PATH, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/web/help/onboarding", dependencies=[Depends(verify_admin_token)])
async def get_web_onboarding_help():
    if _WEB_ONBOARDING_GUIDE_PATH.is_file():
        try:
            content = _WEB_ONBOARDING_GUIDE_PATH.read_text(encoding="utf-8")
        except Exception:
            content = ""
    else:
        content = ""
    if not content:
        content = (
            "# Web Onboarding Guide\n\n"
            "1. Configure provider and model.\n"
            "2. Enable at least one channel.\n"
            "3. Set owner channel IDs and secure DM policy.\n"
            "4. Run /web/config/validate and fix warnings.\n"
        )
    return {"status": "ok", "path": str(_WEB_ONBOARDING_GUIDE_PATH), "content": content}

@app.get("/experiments/self-evolution/offline-replay", dependencies=[Depends(verify_admin_token)])
async def get_self_evolution_offline_replay(case_limit: int = 5):
    report = _build_self_evolution_offline_report(case_limit=max(1, min(case_limit, 20)))
    return {"status": "ok", "report": report}

@app.post("/experiments/self-evolution/offline-replay/export", dependencies=[Depends(verify_admin_token)])
async def export_self_evolution_offline_replay(payload: Dict[str, Any]):
    case_limit_raw = payload.get("case_limit", 5) if isinstance(payload, dict) else 5
    try:
        case_limit = max(1, min(int(case_limit_raw), 20))
    except (TypeError, ValueError):
        case_limit = 5
    report = _build_self_evolution_offline_report(case_limit=case_limit)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str((payload or {}).get("output_path", "")).strip() if isinstance(payload, dict) else "",
        default_filename=f"SELF_EVOLUTION_EXPERIMENT_{stamp}.md",
    )

    baseline = report.get("baseline", {}) if isinstance(report.get("baseline"), dict) else {}
    planned = report.get("light_planning", {}) if isinstance(report.get("light_planning"), dict) else {}
    delta = report.get("delta", {}) if isinstance(report.get("delta"), dict) else {}
    lines = [
        "# Self-Evolution Offline Replay Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- experiment: {report.get('experiment')}",
        f"- dataset_size: {report.get('dataset_size')}",
        "",
        "## Baseline (No Planning)",
        f"- success_rate: {baseline.get('success_rate')}",
        f"- avg_cost: {baseline.get('avg_cost')}",
        f"- avg_steps: {baseline.get('avg_steps')}",
        "",
        "## Light Planning",
        f"- success_rate: {planned.get('success_rate')}",
        f"- avg_cost: {planned.get('avg_cost')}",
        f"- avg_steps: {planned.get('avg_steps')}",
        "",
        "## Delta",
        f"- success_rate: {delta.get('success_rate')}",
        f"- avg_cost: {delta.get('avg_cost')}",
        f"- avg_steps: {delta.get('avg_steps')}",
        f"- failure_type_shift: {delta.get('failure_type_shift')}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "report": report}

@app.get("/health")
async def health_check():
    """Simple liveness probe."""
    return {"status": "ok", "timestamp": time.time()}

@app.get("/health/doctor", dependencies=[Depends(verify_admin_token)])
async def run_doctor():
    """Run diagnostic checks across the system (inspired by OpenClaw's doctor).

    Checks:
    - LLM API key presence
    - DM policy safety
    - Tool security configuration
    - Memory index health
    - Disk space
    - Critical config values
    """
    import platform as _plat
    import psutil as _psutil

    checks = []

    # 1. LLM API keys
    providers = get_provider_registry().list_providers()
    for name, prov in providers.items():
        key = prov.get("api_key", "")
        has_key = bool(key) and key != "ollama"
        checks.append({
            "name": f"llm_api_key_{name}",
            "status": "ok" if has_key else "warning",
            "message": f"API key configured" if has_key else f"No API key for provider '{name}'",
        })

    # 2. DM policy check
    dm_policy = config.get("security.dm_policy", "open")
    if dm_policy == "open":
        checks.append({
            "name": "dm_policy",
            "status": "warning",
            "message": "DM policy is 'open' -- all senders can interact. Consider 'pairing' or 'allowlist'.",
        })
    else:
        checks.append({
            "name": "dm_policy",
            "status": "ok",
            "message": f"DM policy is '{dm_policy}'",
        })

    # 3. Admin token
    admin_token = get_owner_manager().admin_token
    if not admin_token:
        checks.append({
            "name": "admin_token",
            "status": "warning",
            "message": "Admin token not set -- Admin API endpoints are unprotected.",
        })
    else:
        checks.append({
            "name": "admin_token",
            "status": "ok",
            "message": "Admin token configured.",
        })

    # 4. Tool policy / security config
    tool_denylist = config.get("security.tool_denylist", [])
    tool_allowlist = config.get("security.tool_allowlist", [])
    tool_groups = config.get("security.tool_groups", {})
    owner_only_count = sum(1 for t in TOOL_REGISTRY._tools.values() if t.owner_only) if TOOL_REGISTRY else 0
    checks.append({
        "name": "tool_security",
        "status": "ok",
        "message": (
            f"Owner-only tools: {owner_only_count}, "
            f"allowlist: {len(tool_allowlist)}, "
            f"denylist: {len(tool_denylist)}, "
            f"groups: {len(tool_groups) if isinstance(tool_groups, dict) else 0}."
        ),
    })

    # 5. Disk space
    try:
        if os.name == "nt":
            disk_path = os.path.splitdrive(os.getcwd())[0] + os.sep
        else:
            disk_path = "/"
        disk = _psutil.disk_usage(disk_path)
        disk_status = "ok" if disk.percent < 90 else "warning"
        checks.append({
            "name": "disk_space",
            "status": disk_status,
            "message": f"Disk usage: {disk.percent}% ({disk.free // (1024**3)} GB free)",
        })
    except Exception as e:
        checks.append({"name": "disk_space", "status": "error", "message": str(e)})

    # 6. Memory index
    try:
        _get_memory_manager().index.fts_search("__doctor_test__", limit=1)
        checks.append(
            {
                "name": "memory_index",
                "status": "ok",
                "message": "OpenViking memory search adapter is healthy.",
            }
        )
    except Exception as e:
        checks.append({"name": "memory_index", "status": "error", "message": f"FTS error: {e}"})

    # 7. Pairing pending count
    pending = get_pairing_manager().list_pending()
    if pending:
        checks.append({
            "name": "pairing_pending",
            "status": "info",
            "message": f"{len(pending)} pending pairing request(s) awaiting approval.",
        })

    # Summary
    warnings = sum(1 for c in checks if c["status"] == "warning")
    errors = sum(1 for c in checks if c["status"] == "error")
    overall = "healthy" if errors == 0 and warnings == 0 else ("degraded" if errors == 0 else "unhealthy")

    return {
        "overall": overall,
        "warnings": warnings,
        "errors": errors,
        "checks": checks,
        "platform": f"{_plat.system()} {_plat.release()}",
        "python": _plat.python_version(),
    }

@app.get("/health/usage", dependencies=[Depends(verify_admin_token)])
async def get_usage_stats():
    """Return accumulated LLM token usage."""
    _prompt_cache = get_prompt_cache_tracker()
    _tool_batching = get_tool_batching_tracker()
    _usage = get_usage_tracker()

    prompt_cache = {}
    tool_batching = {}
    if _prompt_cache is not None and hasattr(_prompt_cache, "summary"):
        try:
            prompt_cache = _prompt_cache.summary()
        except Exception:
            logger.debug("Failed to read prompt cache summary", exc_info=True)
    if _tool_batching is not None and hasattr(_tool_batching, "summary"):
        try:
            tool_batching = _tool_batching.summary()
        except Exception:
            logger.debug("Failed to read tool batching summary", exc_info=True)
    if _usage is None:
        ipc_snapshot = _shared.IPC_USAGE_SNAPSHOT
        if ipc_snapshot:
            return {
                "status": "ok",
                "usage": ipc_snapshot,
                "prompt_cache": prompt_cache,
                "tool_batching": tool_batching,
            }
        payload: Dict[str, Any] = {"status": "ok", "note": "Usage tracker not injected yet."}
        if prompt_cache:
            payload["prompt_cache"] = prompt_cache
        if tool_batching:
            payload["tool_batching"] = tool_batching
        return payload
    return {
        "status": "ok",
        "usage": _usage.summary(),
        "prompt_cache": prompt_cache,
        "tool_batching": tool_batching,
    }

@app.get("/health/tool-governance", dependencies=[Depends(verify_admin_token)])
async def get_tool_governance_health(limit: int = 50):
    """Return runtime tool-governance snapshot (budget + recent rejections)."""
    response = {
        "status": "ok",
        "tool_governance": _get_tool_governance_snapshot(limit=limit),
    }
    return response

async def set_llm_router_strategy(payload: dict) -> dict:
    template_name = payload.get("template")
    if not template_name:
        return {"ok": False, "status": "error", "message": "template name required"}
    try:
        from llm.router import resolve_router_strategy_template
        template = resolve_router_strategy_template(template_name)
    except Exception as e:
        return {"ok": False, "status": "error", "message": str(e)}
        
    router = get_llm_router()
    router.set_strategy(template["strategy"])
    if "budget" in template:
        router.set_budget_policy(template["budget"])
    if "outlier_ejection" in template:
        router.set_outlier_policy(template["outlier_ejection"])
    
    config.set_many({
        "models.router.strategy": template["strategy"],
        "models.router.strategy_template": template_name,
        "models.router.budget": template.get("budget", {}),
        "models.router.outlier_ejection": template.get("outlier_ejection", {})
    })
    
    return {
        "ok": True,
        "status": "ok", 
        "strategy": template["strategy"],
        "template": template_name
    }
