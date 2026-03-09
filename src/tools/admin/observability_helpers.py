"""Observability, profiling, and legacy helper functions extracted from _shared.py."""

from __future__ import annotations
import collections
import logging
import time
from typing import Any, Dict, List

from runtime.config_manager import config
from runtime.resilience import classify_error_message
from tools.admin.state import (
    _llm_history,
    get_tool_registry,
    get_trajectory_store,
)

logger = logging.getLogger('GazerAdminAPI')


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
    traj_store = get_trajectory_store()
    if traj_store is None:
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

    recent = traj_store.list_recent(limit=max(1, min(limit, 1000)))
    for item in recent:
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        traj = traj_store.get_trajectory(run_id)
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
    traj_store = get_trajectory_store()
    if traj_store is None:
        return profile

    latencies: List[float] = []
    by_tool_values: Dict[str, List[float]] = {}
    success_timestamps: Dict[str, List[float]] = {}

    recent = traj_store.list_recent(limit=max(1, min(limit, 1000)))
    for item in recent:
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        traj = traj_store.get_trajectory(run_id)
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

def _get_eval_benchmark_manager():
    from eval.benchmark import EvalBenchmarkManager
    from tools.admin.state import _PROJECT_ROOT
    
    manager = getattr(_get_eval_benchmark_manager, "_instance", None)
    if manager is None:
        manager = EvalBenchmarkManager(_PROJECT_ROOT)
        setattr(_get_eval_benchmark_manager, "_instance", manager)
    return manager
