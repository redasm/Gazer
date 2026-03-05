from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List, Optional
import time
import json
from datetime import datetime
# Backward-compat aliases for static references only
from tools.admin.auth import verify_admin_token
import tools.admin._shared as _shared
from tools.admin.system import _build_workflow_observability_metrics, _latest_persona_consistency_signal, _build_training_bridge_policy_scoreboard, _build_inbound_media_profile, _build_alignment_baseline_panel, _build_efficiency_window_summary, _invoke_gui_action_via_tool_registry
from eval.gui_simple_benchmark import GuiSimpleBenchmarkRunner, build_default_gui_simple_cases
from runtime.resilience import classify_error_message
from tools.admin.memory import _resolve_openviking_backend_dir
from memory.quality_eval import build_memory_quality_report
from tools.admin.observability_helpers import (
    _build_llm_tool_failure_profile,
    _build_tool_timing_profile,
    _p95,
    _parse_tool_result_stats,
)
from tools.admin.state import (
    LLM_ROUTER,
    TOOL_REGISTRY,
    TRAJECTORY_STORE,
    _alert_buffer,
    config,
    get_llm_router,
    get_tool_batching_tracker,
    get_tool_registry,
    get_trajectory_store,
    get_usage_tracker,
    _gui_simple_benchmark_history,
    logger,
    _policy_audit_buffer,
    _workflow_run_history,
)
from tools.admin.strategy_helpers import _append_policy_audit, _get_tool_governance_snapshot
from tools.admin.utils import _resolve_export_output_path

app = APIRouter()

async def _run_gui_simple_benchmark_suite(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    target = str(data.get("target", "")).strip()
    stop_on_failure = bool(data.get("stop_on_failure", False))
    cases_raw = data.get("cases")
    cases = cases_raw if isinstance(cases_raw, list) else build_default_gui_simple_cases()
    runner = GuiSimpleBenchmarkRunner(invoker=_invoke_gui_action_via_tool_registry)
    report = await runner.run(
        target=target,
        cases=cases,
        stop_on_failure=stop_on_failure,
    )
    _gui_simple_benchmark_history.append(dict(report))
    return report

def _build_gui_simple_benchmark_observability(window: int = 20) -> Dict[str, Any]:
    size = max(1, min(int(window), 200))
    items = list(_gui_simple_benchmark_history)[-size:]
    if not items:
        return {
            "window": size,
            "total_runs": 0,
            "avg_success_rate": None,
            "latest": None,
            "failure_reasons": [],
            "trend": [],
        }
    success_rates = [
        float(item.get("success_rate", 0.0) or 0.0)
        for item in items
        if isinstance(item, dict)
    ]
    reason_counter: Dict[str, int] = {}
    trend: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = float(item.get("generated_at", 0.0) or 0.0)
        trend.append(
            {
                "run_id": str(item.get("run_id", "")),
                "generated_at": ts,
                "success_rate": float(item.get("success_rate", 0.0) or 0.0),
                "failed_cases": int(item.get("failed_cases", 0) or 0),
                "total_cases": int(item.get("total_cases", 0) or 0),
            }
        )
        for reason in list(item.get("failure_reasons", []) or []):
            if not isinstance(reason, dict):
                continue
            key = str(reason.get("code", "")).strip() or "UNKNOWN"
            reason_counter[key] = reason_counter.get(key, 0) + int(reason.get("count", 0) or 0)
    top_reasons = sorted(
        [{"code": key, "count": int(val)} for key, val in reason_counter.items()],
        key=lambda x: (-int(x["count"]), str(x["code"])),
    )[:20]
    latest = items[-1] if items else None
    return {
        "window": size,
        "total_runs": len(items),
        "avg_success_rate": round(sum(success_rates) / max(1, len(success_rates)), 4),
        "latest": latest,
        "failure_reasons": top_reasons,
        "trend": trend[-50:],
    }

def _append_alert(level: str, category: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": str(level or "warning"),
        "category": str(category or "general"),
        "message": str(message or ""),
        "details": details or {},
    }
    _alert_buffer.append(entry)
    logger.warning("Alert raised: %s/%s %s", entry["level"], entry["category"], entry["message"])

def _build_observability_trends(window: int = 50) -> Dict[str, Any]:
    policy_entries = list(_policy_audit_buffer)[-max(1, min(window, 500)) :]
    workflow_entries = list(_workflow_run_history)[-max(1, min(window, 500)) :]
    alert_entries = list(_alert_buffer)[-max(1, min(window, 500)) :]

    actions: Dict[str, int] = {}
    for item in policy_entries:
        action = str(item.get("action", "")).strip() or "unknown"
        actions[action] = actions.get(action, 0) + 1

    workflow_failures = 0
    workflow_successes = 0
    workflow_latencies: List[float] = []
    for item in workflow_entries:
        status = str(item.get("status", "")).strip().lower()
        if status in {"ok", "success"}:
            workflow_successes += 1
        else:
            workflow_failures += 1
        latency = item.get("total_duration_ms")
        if isinstance(latency, (int, float)):
            workflow_latencies.append(float(latency))

    alerts_by_level: Dict[str, int] = {}
    for item in alert_entries:
        level = str(item.get("level", "")).strip().lower() or "warning"
        alerts_by_level[level] = alerts_by_level.get(level, 0) + 1

    return {
        "window": max(1, min(window, 500)),
        "policy_actions": actions,
        "workflow": {
            "runs": len(workflow_entries),
            "successes": workflow_successes,
            "failures": workflow_failures,
            "p95_latency_ms": _p95(workflow_latencies),
        },
        "alerts": {
            "count": len(alert_entries),
            "by_level": alerts_by_level,
            "latest": alert_entries[-1] if alert_entries else None,
        },
    }

def _build_cost_quality_slo_report(window: int = 100) -> Dict[str, Any]:
    safe_window = max(1, min(int(window or 100), 1000))
    _traj = get_trajectory_store()
    trajectories: List[Dict[str, Any]] = []
    if _traj is not None and hasattr(_traj, "list_recent"):
        try:
            trajectories = _traj.list_recent(limit=safe_window)
        except Exception:
            trajectories = []

    statuses = [str(item.get("status", "")).strip().lower() for item in trajectories]
    total_runs = len(statuses)
    success_runs = sum(1 for marker in statuses if marker in {"success", "ok", "completed"})
    success_rate = round((success_runs / total_runs), 4) if total_runs else 1.0

    latencies = [
        float(item.get("turn_latency_ms"))
        for item in trajectories
        if isinstance(item.get("turn_latency_ms"), (int, float))
    ]
    avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    p95_latency_ms = _p95(latencies)

    retries_total = 0
    for item in trajectories:
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        if _traj is None or not hasattr(_traj, "get_trajectory"):
            continue
        try:
            payload = _traj.get_trajectory(run_id)
        except Exception:
            payload = None
        final = (payload or {}).get("final", {}) if isinstance(payload, dict) else {}
        metrics = final.get("metrics", {}) if isinstance(final, dict) else {}
        try:
            iterations = max(1, int(metrics.get("iterations", 1) or 1))
        except (TypeError, ValueError):
            iterations = 1
        retries_total += max(0, iterations - 1)
    avg_retries_per_run = round((retries_total / total_runs), 4) if total_runs else 0.0

    _usage = get_usage_tracker()
    usage = _usage.summary() if (_usage is not None and hasattr(_usage, "summary")) else {}
    total_tokens = int((usage or {}).get("total_tokens", 0) or 0)
    avg_tokens_per_run = round((total_tokens / total_runs), 2) if total_runs else 0.0
    primary_ref = str(config.get("agents.defaults.model.primary", "") or "").strip()
    active_provider = primary_ref.split("/", 1)[0].strip().lower() if "/" in primary_ref else ""
    provider_costs = config.get("models.router.budget.provider_cost_per_1k_tokens", {}) or {}
    unit_cost = 0.0
    if isinstance(provider_costs, dict):
        try:
            unit_cost = float(provider_costs.get(active_provider, 0.0) or 0.0)
        except (TypeError, ValueError):
            unit_cost = 0.0
    estimated_cost_usd = round((total_tokens / 1000.0) * unit_cost, 6) if unit_cost > 0 else None

    router_status = {}
    _router = get_llm_router()
    if _router is not None and hasattr(_router, "get_status"):
        try:
            router_status = _router.get_status()
        except Exception:
            router_status = {}
    providers = router_status.get("providers", []) if isinstance(router_status, dict) else []
    total_router_calls = 0
    downgrade_events = 0
    for item in providers if isinstance(providers, list) else []:
        if not isinstance(item, dict):
            continue
        total_router_calls += int(item.get("calls", 0) or 0)
        errors = item.get("error_classes", {}) if isinstance(item.get("error_classes"), dict) else {}
        downgrade_events += int(errors.get("outlier_ejected", 0) or 0)
        downgrade_events += int(errors.get("budget_exceeded", 0) or 0)
    downgrade_trigger_rate = (
        round(downgrade_events / total_router_calls, 4) if total_router_calls > 0 else 0.0
    )

    targets_raw = config.get("observability.cost_quality_slo_targets", {}) or {}
    targets = targets_raw if isinstance(targets_raw, dict) else {}
    min_success_rate = float(targets.get("min_success_rate", 0.9) or 0.9)
    max_p95_latency_ms = float(targets.get("max_p95_latency_ms", 3000.0) or 3000.0)
    max_avg_retries = float(targets.get("max_avg_retries_per_run", 1.5) or 1.5)
    max_downgrade_rate = float(targets.get("max_downgrade_trigger_rate", 0.2) or 0.2)

    checks = {
        "success_rate_ok": bool(success_rate >= min_success_rate),
        "latency_ok": bool(p95_latency_ms <= max_p95_latency_ms),
        "retry_ok": bool(avg_retries_per_run <= max_avg_retries),
        "router_ok": bool(downgrade_trigger_rate <= max_downgrade_rate),
    }
    passed = all(checks.values())

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "window": safe_window,
        "passed": bool(passed),
        "targets": {
            "min_success_rate": min_success_rate,
            "max_p95_latency_ms": max_p95_latency_ms,
            "max_avg_retries_per_run": max_avg_retries,
            "max_downgrade_trigger_rate": max_downgrade_rate,
        },
        "metrics": {
            "runs": total_runs,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency_ms,
            "p95_latency_ms": p95_latency_ms,
            "retries_total": retries_total,
            "avg_retries_per_run": avg_retries_per_run,
            "total_tokens": total_tokens,
            "avg_tokens_per_run": avg_tokens_per_run,
            "estimated_cost_usd": estimated_cost_usd,
            "router_downgrade_trigger_rate": downgrade_trigger_rate,
            "router_budget_degrade_active": bool(router_status.get("budget_degrade_active", False))
            if isinstance(router_status, dict)
            else False,
        },
        "checks": checks,
    }

def _render_cost_quality_slo_markdown(report: Dict[str, Any]) -> str:
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    targets = report.get("targets", {}) if isinstance(report.get("targets"), dict) else {}
    checks = report.get("checks", {}) if isinstance(report.get("checks"), dict) else {}
    lines = [
        "# Cost & Quality SLO Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window: {report.get('window')}",
        f"- passed: {bool(report.get('passed', False))}",
        "",
        "## Metrics",
        f"- runs: {metrics.get('runs', 0)}",
        f"- success_rate: {metrics.get('success_rate', 1.0)}",
        f"- avg_latency_ms: {metrics.get('avg_latency_ms', 0.0)}",
        f"- p95_latency_ms: {metrics.get('p95_latency_ms', 0.0)}",
        f"- retries_total: {metrics.get('retries_total', 0)}",
        f"- avg_retries_per_run: {metrics.get('avg_retries_per_run', 0.0)}",
        f"- total_tokens: {metrics.get('total_tokens', 0)}",
        f"- avg_tokens_per_run: {metrics.get('avg_tokens_per_run', 0.0)}",
        f"- estimated_cost_usd: {metrics.get('estimated_cost_usd')}",
        f"- router_downgrade_trigger_rate: {metrics.get('router_downgrade_trigger_rate', 0.0)}",
        f"- router_budget_degrade_active: {bool(metrics.get('router_budget_degrade_active', False))}",
        "",
        "## Targets",
        f"- min_success_rate: {targets.get('min_success_rate')}",
        f"- max_p95_latency_ms: {targets.get('max_p95_latency_ms')}",
        f"- max_avg_retries_per_run: {targets.get('max_avg_retries_per_run')}",
        f"- max_downgrade_trigger_rate: {targets.get('max_downgrade_trigger_rate')}",
        "",
        "## Checks",
        f"- success_rate_ok: {bool(checks.get('success_rate_ok', False))}",
        f"- latency_ok: {bool(checks.get('latency_ok', False))}",
        f"- retry_ok: {bool(checks.get('retry_ok', False))}",
        f"- router_ok: {bool(checks.get('router_ok', False))}",
        "",
    ]
    return "\n".join(lines)

def _build_efficiency_baseline_report(window_days: int = 7, limit: int = 400) -> Dict[str, Any]:
    safe_window_days = max(1, min(int(window_days or 7), 30))
    safe_limit = max(1, min(int(limit or 400), 5000))
    trajectories: List[Dict[str, Any]] = []
    if TRAJECTORY_STORE is not None and hasattr(TRAJECTORY_STORE, "list_recent"):
        try:
            trajectories = TRAJECTORY_STORE.list_recent(limit=safe_limit)
        except Exception:
            trajectories = []

    now_ts = float(time.time())
    window_seconds = float(safe_window_days * 86400)
    current_start = now_ts - window_seconds
    previous_start = current_start - window_seconds

    current_rows: List[Dict[str, Any]] = []
    previous_rows: List[Dict[str, Any]] = []

    for item in trajectories:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        try:
            ts = float(item.get("ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts < previous_start:
            continue

        payload = None
        if TRAJECTORY_STORE is not None and hasattr(TRAJECTORY_STORE, "get_trajectory"):
            try:
                payload = TRAJECTORY_STORE.get_trajectory(run_id)
            except Exception:
                payload = None
        final = payload.get("final", {}) if isinstance(payload, dict) else {}
        metrics = final.get("metrics", {}) if isinstance(final, dict) else {}
        usage = final.get("usage", {}) if isinstance(final, dict) else {}
        events = payload.get("events", []) if isinstance(payload, dict) else []

        tool_results_total, tool_failures, error_codes = _parse_tool_result_stats(events)
        try:
            tokens = int(usage.get("total_tokens", 0) or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens <= 0:
            try:
                tokens = int(metrics.get("tokens_this_turn", 0) or 0)
            except (TypeError, ValueError):
                tokens = 0
        try:
            turn_latency_ms = float(item.get("turn_latency_ms", 0.0) or 0.0)
        except (TypeError, ValueError):
            turn_latency_ms = 0.0
        if turn_latency_ms <= 0:
            try:
                turn_latency_ms = float(metrics.get("turn_latency_ms", 0.0) or 0.0)
            except (TypeError, ValueError):
                turn_latency_ms = 0.0
        try:
            tool_rounds = int(metrics.get("tool_rounds", 0) or 0)
        except (TypeError, ValueError):
            tool_rounds = 0
        try:
            tool_calls = int(metrics.get("tool_calls_executed", tool_results_total) or 0)
        except (TypeError, ValueError):
            tool_calls = tool_results_total
        if tool_calls <= 0:
            tool_calls = tool_results_total

        row = {
            "run_id": run_id,
            "ts": ts,
            "status": str(item.get("status", "")),
            "turn_latency_ms": round(max(0.0, turn_latency_ms), 4),
            "tokens": max(0, int(tokens)),
            "tool_rounds": max(0, int(tool_rounds)),
            "tool_calls": max(0, int(tool_calls)),
            "tool_failures": max(0, int(tool_failures)),
            "error_codes": error_codes,
        }
        if ts >= current_start:
            current_rows.append(row)
        elif ts >= previous_start:
            previous_rows.append(row)

    current_rows.sort(key=lambda rec: float(rec.get("ts", 0.0)), reverse=True)
    previous_rows.sort(key=lambda rec: float(rec.get("ts", 0.0)), reverse=True)

    current_window = _build_efficiency_window_summary(current_rows)
    previous_window = _build_efficiency_window_summary(previous_rows)

    delta = {
        "success_rate": round(
            float(current_window.get("success_rate", 1.0) or 1.0)
            - float(previous_window.get("success_rate", 1.0) or 1.0),
            4,
        ),
        "p95_latency_ms": round(
            float(current_window.get("p95_latency_ms", 0.0) or 0.0)
            - float(previous_window.get("p95_latency_ms", 0.0) or 0.0),
            2,
        ),
        "avg_tokens_per_run": round(
            float(current_window.get("avg_tokens_per_run", 0.0) or 0.0)
            - float(previous_window.get("avg_tokens_per_run", 0.0) or 0.0),
            2,
        ),
        "avg_tool_rounds_per_run": round(
            float(current_window.get("avg_tool_rounds_per_run", 0.0) or 0.0)
            - float(previous_window.get("avg_tool_rounds_per_run", 0.0) or 0.0),
            4,
        ),
        "tool_error_rate": round(
            float(current_window.get("tool_error_rate", 0.0) or 0.0)
            - float(previous_window.get("tool_error_rate", 0.0) or 0.0),
            4,
        ),
    }

    targets_raw = config.get("observability.efficiency_baseline_targets", {}) or {}
    targets = targets_raw if isinstance(targets_raw, dict) else {}
    min_success_rate = float(targets.get("min_success_rate", 0.90) or 0.90)
    max_p95_latency_ms = float(targets.get("max_p95_latency_ms", 3000.0) or 3000.0)
    max_avg_tokens_per_run = float(targets.get("max_avg_tokens_per_run", 6000.0) or 6000.0)
    max_tool_error_rate = float(targets.get("max_tool_error_rate", 0.20) or 0.20)

    checks = {
        "success_rate_ok": bool(float(current_window.get("success_rate", 1.0) or 1.0) >= min_success_rate),
        "latency_ok": bool(float(current_window.get("p95_latency_ms", 0.0) or 0.0) <= max_p95_latency_ms),
        "tokens_ok": bool(
            float(current_window.get("avg_tokens_per_run", 0.0) or 0.0) <= max_avg_tokens_per_run
        ),
        "tool_error_rate_ok": bool(
            float(current_window.get("tool_error_rate", 0.0) or 0.0) <= max_tool_error_rate
        ),
    }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "window_days": safe_window_days,
        "limit": safe_limit,
        "passed": bool(all(checks.values())),
        "targets": {
            "min_success_rate": min_success_rate,
            "max_p95_latency_ms": max_p95_latency_ms,
            "max_avg_tokens_per_run": max_avg_tokens_per_run,
            "max_tool_error_rate": max_tool_error_rate,
        },
        "windows": {
            "current_start_ts": current_start,
            "previous_start_ts": previous_start,
            "now_ts": now_ts,
        },
        "current_window": current_window,
        "previous_window": previous_window,
        "delta": delta,
        "checks": checks,
    }

def _render_efficiency_baseline_markdown(report: Dict[str, Any]) -> str:
    current = report.get("current_window", {}) if isinstance(report.get("current_window"), dict) else {}
    previous = report.get("previous_window", {}) if isinstance(report.get("previous_window"), dict) else {}
    delta = report.get("delta", {}) if isinstance(report.get("delta"), dict) else {}
    targets = report.get("targets", {}) if isinstance(report.get("targets"), dict) else {}
    checks = report.get("checks", {}) if isinstance(report.get("checks"), dict) else {}
    lines = [
        "# Efficiency Baseline Weekly Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_days: {report.get('window_days')}",
        f"- limit: {report.get('limit')}",
        f"- passed: {bool(report.get('passed', False))}",
        "",
        "## Current Window",
        f"- runs: {current.get('runs', 0)}",
        f"- success_rate: {current.get('success_rate', 1.0)}",
        f"- p95_latency_ms: {current.get('p95_latency_ms', 0.0)}",
        f"- avg_tokens_per_run: {current.get('avg_tokens_per_run', 0.0)}",
        f"- avg_tool_rounds_per_run: {current.get('avg_tool_rounds_per_run', 0.0)}",
        f"- tool_error_rate: {current.get('tool_error_rate', 0.0)}",
        f"- sample_run_ids: {', '.join(current.get('sample_run_ids', [])[:10])}",
        "",
        "## Previous Window",
        f"- runs: {previous.get('runs', 0)}",
        f"- success_rate: {previous.get('success_rate', 1.0)}",
        f"- p95_latency_ms: {previous.get('p95_latency_ms', 0.0)}",
        f"- avg_tokens_per_run: {previous.get('avg_tokens_per_run', 0.0)}",
        f"- avg_tool_rounds_per_run: {previous.get('avg_tool_rounds_per_run', 0.0)}",
        f"- tool_error_rate: {previous.get('tool_error_rate', 0.0)}",
        "",
        "## Delta (Current - Previous)",
        f"- success_rate: {delta.get('success_rate', 0.0)}",
        f"- p95_latency_ms: {delta.get('p95_latency_ms', 0.0)}",
        f"- avg_tokens_per_run: {delta.get('avg_tokens_per_run', 0.0)}",
        f"- avg_tool_rounds_per_run: {delta.get('avg_tool_rounds_per_run', 0.0)}",
        f"- tool_error_rate: {delta.get('tool_error_rate', 0.0)}",
        "",
        "## Targets",
        f"- min_success_rate: {targets.get('min_success_rate')}",
        f"- max_p95_latency_ms: {targets.get('max_p95_latency_ms')}",
        f"- max_avg_tokens_per_run: {targets.get('max_avg_tokens_per_run')}",
        f"- max_tool_error_rate: {targets.get('max_tool_error_rate')}",
        "",
        "## Checks",
        f"- success_rate_ok: {bool(checks.get('success_rate_ok', False))}",
        f"- latency_ok: {bool(checks.get('latency_ok', False))}",
        f"- tokens_ok: {bool(checks.get('tokens_ok', False))}",
        f"- tool_error_rate_ok: {bool(checks.get('tool_error_rate_ok', False))}",
        "",
    ]
    return "\n".join(lines)

def _build_product_health_weekly_report(
    *,
    window_days: int = 7,
    cost_window: int = 100,
    efficiency_limit: int = 400,
    memory_stale_days: int = 14,
    include_persona_drift: bool = True,
    persona_source: str = "persona_eval",
) -> Dict[str, Any]:
    safe_window_days = max(1, min(int(window_days or 7), 30))
    safe_cost_window = max(1, min(int(cost_window or 100), 1000))
    safe_eff_limit = max(1, min(int(efficiency_limit or 400), 5000))
    safe_stale_days = max(1, min(int(memory_stale_days or 14), 365))

    cost = _build_cost_quality_slo_report(window=safe_cost_window)
    efficiency = _build_efficiency_baseline_report(window_days=safe_window_days, limit=safe_eff_limit)

    memory_backend_dir = _resolve_openviking_backend_dir()
    memory_quality = build_memory_quality_report(
        backend_dir=memory_backend_dir,
        window_days=safe_window_days,
        stale_days=safe_stale_days,
        include_samples=False,
        sample_limit=10,
    )
    persona_drift: Dict[str, Any] = {}
    if include_persona_drift:
        from tools.admin.memory import _build_persona_memory_joint_drift_report
        joint = _build_persona_memory_joint_drift_report(
            window_days=safe_window_days,
            source=str(persona_source or "persona_eval"),
        )
        memory_drift = joint.get("memory", {}) if isinstance(joint.get("memory"), dict) else {}
        persona_drift = {
            "source": str(persona_source or "persona_eval"),
            "joint_risk_level": str((joint.get("joint", {}) or {}).get("risk_level", "unknown")),
            "memory_drift": memory_drift.get("drift", {}) if isinstance(memory_drift, dict) else {},
        }
        memory_quality["persona_drift"] = persona_drift

    memory_current = (
        memory_quality.get("current_window", {})
        if isinstance(memory_quality.get("current_window"), dict)
        else {}
    )
    memory_scores = memory_current.get("scores", {}) if isinstance(memory_current.get("scores"), dict) else {}
    memory_level = str(memory_scores.get("quality_level", "healthy")).strip().lower() or "healthy"

    risks: List[Dict[str, Any]] = []
    if not bool(cost.get("passed", False)):
        risks.append(
            {
                "area": "cost_quality_slo",
                "severity": "warning",
                "message": "cost-quality-slo check not passed",
            }
        )
    if not bool(efficiency.get("passed", False)):
        risks.append(
            {
                "area": "efficiency_baseline",
                "severity": "warning",
                "message": "efficiency-baseline check not passed",
            }
        )
    if memory_level in {"warning", "critical"}:
        risks.append(
            {
                "area": "memory_quality",
                "severity": memory_level,
                "message": f"memory quality level={memory_level}",
            }
        )
    joint_level = str(persona_drift.get("joint_risk_level", "")).strip().lower()
    if joint_level in {"warning", "critical"}:
        risks.append(
            {
                "area": "persona_memory_joint",
                "severity": joint_level,
                "message": f"persona-memory joint risk={joint_level}",
            }
        )

    overall_level = "healthy"
    if any(str(item.get("severity", "")).lower() == "critical" for item in risks):
        overall_level = "critical"
    elif risks:
        overall_level = "warning"

    recommendations: List[str] = []
    if not bool(cost.get("passed", False)):
        recommendations.append("Tune model routing and retry strategy to recover cost-quality-slo checks.")
    if not bool(efficiency.get("passed", False)):
        recommendations.append("Reduce tool/error overhead and token usage to recover efficiency baseline.")
    if memory_level in {"warning", "critical"}:
        recommendations.append("Review memory extraction/recall quality and refresh query-set thresholds.")
    if joint_level in {"warning", "critical"}:
        recommendations.append("Review persona-memory drift report and apply prompt/runtime corrections.")
    if not recommendations:
        recommendations.append("Keep current guardrails and continue weekly monitoring.")

    return {
        "status": "ok",
        "generated_at": datetime.utcnow().isoformat(),
        "window_days": safe_window_days,
        "cost_window": safe_cost_window,
        "efficiency_limit": safe_eff_limit,
        "memory_stale_days": safe_stale_days,
        "cost_quality_slo": cost,
        "efficiency_baseline": efficiency,
        "memory_quality": memory_quality,
        "summary": {
            "overall_level": overall_level,
            "risk_count": len(risks),
            "risks": risks,
            "recommendations": recommendations,
        },
    }

def _render_product_health_weekly_markdown(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    cost = report.get("cost_quality_slo", {}) if isinstance(report.get("cost_quality_slo"), dict) else {}
    efficiency = (
        report.get("efficiency_baseline", {})
        if isinstance(report.get("efficiency_baseline"), dict)
        else {}
    )
    memory = report.get("memory_quality", {}) if isinstance(report.get("memory_quality"), dict) else {}

    cost_metrics = cost.get("metrics", {}) if isinstance(cost.get("metrics"), dict) else {}
    eff_current = efficiency.get("current_window", {}) if isinstance(efficiency.get("current_window"), dict) else {}
    mem_scores = (
        memory.get("current_window", {}).get("scores", {})
        if isinstance(memory.get("current_window"), dict)
        and isinstance((memory.get("current_window") or {}).get("scores"), dict)
        else {}
    )
    lines = [
        "# Product Health Weekly Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_days: {report.get('window_days')}",
        f"- overall_level: {summary.get('overall_level', 'unknown')}",
        f"- risk_count: {summary.get('risk_count', 0)}",
        "",
        "## Cost Quality SLO",
        f"- passed: {bool(cost.get('passed', False))}",
        f"- runs: {cost_metrics.get('runs', 0)}",
        f"- success_rate: {cost_metrics.get('success_rate', 1.0)}",
        f"- p95_latency_ms: {cost_metrics.get('p95_latency_ms', 0.0)}",
        f"- avg_retries_per_run: {cost_metrics.get('avg_retries_per_run', 0.0)}",
        f"- estimated_cost_usd: {cost_metrics.get('estimated_cost_usd')}",
        "",
        "## Efficiency Baseline",
        f"- passed: {bool(efficiency.get('passed', False))}",
        f"- runs: {eff_current.get('runs', 0)}",
        f"- success_rate: {eff_current.get('success_rate', 1.0)}",
        f"- p95_latency_ms: {eff_current.get('p95_latency_ms', 0.0)}",
        f"- avg_tokens_per_run: {eff_current.get('avg_tokens_per_run', 0.0)}",
        f"- tool_error_rate: {eff_current.get('tool_error_rate', 0.0)}",
        "",
        "## Memory Quality",
        f"- quality_score: {mem_scores.get('quality_score', 0.0)}",
        f"- quality_level: {mem_scores.get('quality_level', 'unknown')}",
        f"- trend_direction: {(memory.get('trend', {}) if isinstance(memory.get('trend'), dict) else {}).get('direction', 'stable')}",
        "",
        "## Risks",
    ]
    risks = summary.get("risks", []) if isinstance(summary.get("risks"), list) else []
    if risks:
        for item in risks:
            lines.append(
                f"- [{item.get('severity', 'warning')}] {item.get('area', 'unknown')}: {item.get('message', '')}"
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Recommended Actions")
    for item in summary.get("recommendations", []) if isinstance(summary.get("recommendations"), list) else []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)

def _build_tool_governance_slo(limit: int = 200) -> Dict[str, Any]:
    failure_profile = _build_llm_tool_failure_profile(limit=limit)
    tool_profile = failure_profile.get("tool", {}) if isinstance(failure_profile.get("tool"), dict) else {}
    calls = int(tool_profile.get("calls", 0) or 0)
    failures = int(tool_profile.get("failures", 0) or 0)
    success_rate = round(float(tool_profile.get("success_rate", 1.0) or 1.0), 4)

    timing = _build_tool_timing_profile(limit=limit)
    tool_p95_latency_ms = float(timing.get("p95_latency_ms", 0.0) or 0.0)
    success_ts_by_tool = timing.get("success_timestamps_by_tool", {})
    if not isinstance(success_ts_by_tool, dict):
        success_ts_by_tool = {}

    rejection_events: List[Dict[str, Any]] = []
    if TOOL_REGISTRY is not None and hasattr(TOOL_REGISTRY, "get_recent_rejection_events"):
        try:
            rejection_events = list(TOOL_REGISTRY.get_recent_rejection_events(limit=max(50, min(limit * 5, 1000))))
        except Exception:
            rejection_events = []

    budget_hit_count = 0
    circuit_open_count = 0
    circuit_recovery_samples: List[float] = []
    unrecovered_circuit_count = 0
    for event in rejection_events:
        if not isinstance(event, dict):
            continue
        code = str(event.get("code", "")).strip().upper()
        if code == "TOOL_BUDGET_EXCEEDED":
            budget_hit_count += 1
        if code != "TOOL_CIRCUIT_OPEN":
            continue
        circuit_open_count += 1
        tool_name = str(event.get("tool", "")).strip() or "unknown"
        ts_raw = event.get("ts", 0.0)
        try:
            opened_at = float(ts_raw)
        except (TypeError, ValueError):
            opened_at = 0.0
        recovered = False
        for ts in list(success_ts_by_tool.get(tool_name, []) or []):
            if ts > opened_at and opened_at > 0:
                circuit_recovery_samples.append(round((ts - opened_at) * 1000.0, 2))
                recovered = True
                break
        if not recovered:
            unrecovered_circuit_count += 1

    budget_hit_rate = round((budget_hit_count / calls), 4) if calls > 0 else 0.0
    circuit_recovery_p95_ms = _p95(circuit_recovery_samples)

    raw_targets = config.get("observability.tool_governance_slo_targets", {}) or {}
    if not isinstance(raw_targets, dict):
        raw_targets = {}

    def _as_float(key: str, default: float) -> float:
        try:
            return float(raw_targets.get(key, default))
        except (TypeError, ValueError):
            return default

    targets = {
        "min_tool_success_rate": _as_float("min_tool_success_rate", 0.95),
        "max_tool_p95_latency_ms": _as_float("max_tool_p95_latency_ms", 4000.0),
        "max_budget_hit_rate": _as_float("max_budget_hit_rate", 0.05),
        "max_circuit_recovery_ms": _as_float("max_circuit_recovery_ms", 60000.0),
    }
    checks = {
        "tool_success_rate_ok": success_rate >= float(targets["min_tool_success_rate"]),
        "tool_p95_latency_ok": (
            tool_p95_latency_ms <= float(targets["max_tool_p95_latency_ms"])
            if int(timing.get("sample_count", 0) or 0) > 0
            else True
        ),
        "budget_hit_rate_ok": budget_hit_rate <= float(targets["max_budget_hit_rate"]),
        "circuit_recovery_ok": (
            circuit_recovery_p95_ms <= float(targets["max_circuit_recovery_ms"])
            if len(circuit_recovery_samples) > 0
            else True
        ),
    }

    return {
        "generated_at": time.time(),
        "limit": int(max(1, min(limit, 1000))),
        "metrics": {
            "tool_calls": calls,
            "tool_failures": failures,
            "tool_success_rate": success_rate,
            "tool_p95_latency_ms": round(tool_p95_latency_ms, 2),
            "tool_timing_sample_count": int(timing.get("sample_count", 0) or 0),
            "budget_hit_count": budget_hit_count,
            "budget_hit_rate": budget_hit_rate,
            "circuit_open_count": circuit_open_count,
            "circuit_recovery_sample_count": len(circuit_recovery_samples),
            "circuit_recovery_p95_ms": round(circuit_recovery_p95_ms, 2),
            "unrecovered_circuit_count": unrecovered_circuit_count,
            "top_tool_failures": list(tool_profile.get("by_tool_failures", []) or [])[:10],
            "top_error_codes": dict(tool_profile.get("error_codes", {}) or {}),
            "tool_latency_by_tool": list(timing.get("by_tool", []) or []),
        },
        "targets": targets,
        "checks": checks,
        "passed": all(bool(val) for val in checks.values()),
    }

def _build_unified_trace_spec(limit: int = 200) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), 1000))
    spec: Dict[str, Any] = {
        "version": "gazer_trace_spec_v1",
        "generated_at": time.time(),
        "limit": safe_limit,
        "trace_id_format": "trc_<channel>_<id> (fallback: run:<run_id>)",
        "event_requirements": {
            "llm_request": {"required_fields": ["trace_id", "run_id", "action"], "recommended_fields": ["request_id", "model"]},
            "tool_call": {"required_fields": ["trace_id", "run_id", "action"], "recommended_fields": ["tool", "tool_call_id"]},
            "workflow_step": {"required_fields": ["trace_id", "run_id", "action"], "recommended_fields": ["workflow_id", "node_id"]},
        },
        "coverage": {
            "llm_request": {"events": 0, "with_trace_id": 0},
            "tool_call": {"events": 0, "with_trace_id": 0},
            "workflow_step": {"events": 0, "with_trace_id": 0},
            "total_events": 0,
            "total_with_trace_id": 0,
            "trace_id_coverage_rate": 0.0,
        },
        "links": {"trace_count": 0, "full_chain_trace_count": 0, "samples": []},
    }
    if TRAJECTORY_STORE is None:
        return spec

    by_trace: Dict[str, Dict[str, Any]] = {}
    recent = TRAJECTORY_STORE.list_recent(limit=safe_limit)
    for item in recent:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        trajectory = TRAJECTORY_STORE.get_trajectory(run_id)
        if not isinstance(trajectory, dict):
            continue
        events = trajectory.get("events", [])
        if not isinstance(events, list):
            continue
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            action = str(event.get("action", "")).strip().lower()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

            event_type = ""
            category = ""
            if action.startswith("llm_"):
                event_type = "llm_request"
                category = "llm"
            elif action.startswith("tool_"):
                event_type = "tool_call"
                category = "tool"
            elif "workflow" in action:
                event_type = "workflow_step"
                category = "workflow"
            else:
                continue

            trace_id = str(payload.get("trace_id", "") or event.get("trace_id", "")).strip()
            if not trace_id:
                trace_id = str(payload.get("request_id", "")).strip()
            if not trace_id:
                trace_id = str(payload.get("tool_call_id", "")).strip()
            has_trace_id = bool(trace_id)
            if not trace_id:
                trace_id = f"run:{run_id}"

            bucket = spec["coverage"][event_type]
            bucket["events"] += 1
            spec["coverage"]["total_events"] += 1
            if has_trace_id:
                bucket["with_trace_id"] += 1
                spec["coverage"]["total_with_trace_id"] += 1

            linked = by_trace.setdefault(
                trace_id,
                {
                    "trace_id": trace_id,
                    "run_ids": set(),
                    "categories": set(),
                    "events": 0,
                    "sample_actions": [],
                },
            )
            linked["run_ids"].add(run_id)
            linked["categories"].add(category)
            linked["events"] += 1
            if len(linked["sample_actions"]) < 6:
                linked["sample_actions"].append({"action": action, "event_index": index})

    total_events = int(spec["coverage"]["total_events"])
    with_trace = int(spec["coverage"]["total_with_trace_id"])
    spec["coverage"]["trace_id_coverage_rate"] = round((with_trace / total_events), 4) if total_events else 0.0

    samples: List[Dict[str, Any]] = []
    full_chain_count = 0
    for item in by_trace.values():
        categories = set(item.get("categories", set()))
        if {"llm", "tool", "workflow"}.issubset(categories):
            full_chain_count += 1
        if len(samples) < 20:
            samples.append(
                {
                    "trace_id": item.get("trace_id", ""),
                    "run_ids": sorted(item.get("run_ids", set())),
                    "categories": sorted(categories),
                    "events": int(item.get("events", 0)),
                    "sample_actions": list(item.get("sample_actions", [])),
                }
            )
    spec["links"] = {
        "trace_count": len(by_trace),
        "full_chain_trace_count": full_chain_count,
        "samples": samples,
    }
    return spec

@app.get("/observability/trends", dependencies=[Depends(verify_admin_token)])
async def get_observability_trends(window: int = 50):
    return {"status": "ok", "trends": _build_observability_trends(window=window)}

@app.get("/observability/cost-quality-slo", dependencies=[Depends(verify_admin_token)])
async def get_cost_quality_slo(window: int = 100):
    return {"status": "ok", "report": _build_cost_quality_slo_report(window=window)}

@app.post("/observability/cost-quality-slo/export", dependencies=[Depends(verify_admin_token)])
async def export_cost_quality_slo(payload: Dict[str, Any]):
    data = payload if isinstance(payload, dict) else {}
    window_raw = data.get("window", 100)
    try:
        window = max(1, min(int(window_raw), 1000))
    except (TypeError, ValueError):
        window = 100
    report = _build_cost_quality_slo_report(window=window)
    fmt = str(data.get("format", "markdown") or "markdown").strip().lower()
    if fmt not in {"markdown", "json"}:
        fmt = "markdown"

    stamp = time.strftime("%Y-%m-%d")
    default_name = (
        f"COST_QUALITY_SLO_{stamp}.json"
        if fmt == "json"
        else f"COST_QUALITY_SLO_{stamp}.md"
    )
    output_path = _resolve_export_output_path(
        output_raw=str(data.get("output_path", "")).strip(),
        default_filename=default_name,
    )
    if fmt == "json":
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output_path.write_text(_render_cost_quality_slo_markdown(report), encoding="utf-8")
    return {"status": "ok", "format": fmt, "path": str(output_path), "report": report}

@app.get("/observability/efficiency-baseline", dependencies=[Depends(verify_admin_token)])
async def get_observability_efficiency_baseline(window_days: int = 7, limit: int = 400):
    report = _build_efficiency_baseline_report(window_days=window_days, limit=limit)
    return {"status": "ok", "report": report}

@app.post("/observability/efficiency-baseline/export", dependencies=[Depends(verify_admin_token)])
async def export_observability_efficiency_baseline(payload: Dict[str, Any]):
    data = payload if isinstance(payload, dict) else {}
    window_raw = data.get("window_days", 7)
    limit_raw = data.get("limit", 400)
    try:
        window_days = max(1, min(int(window_raw), 30))
    except (TypeError, ValueError):
        window_days = 7
    try:
        limit = max(1, min(int(limit_raw), 5000))
    except (TypeError, ValueError):
        limit = 400
    report = _build_efficiency_baseline_report(window_days=window_days, limit=limit)

    fmt = str(data.get("format", "markdown") or "markdown").strip().lower()
    if fmt not in {"markdown", "json"}:
        fmt = "markdown"
    stamp = time.strftime("%Y-%m-%d")
    default_name = (
        f"EFFICIENCY_BASELINE_{stamp}.json"
        if fmt == "json"
        else f"EFFICIENCY_BASELINE_{stamp}.md"
    )
    output_path = _resolve_export_output_path(
        output_raw=str(data.get("output_path", "")).strip(),
        default_filename=default_name,
    )
    if fmt == "json":
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output_path.write_text(_render_efficiency_baseline_markdown(report), encoding="utf-8")
    return {"status": "ok", "format": fmt, "path": str(output_path), "report": report}

@app.get("/observability/product-health-weekly", dependencies=[Depends(verify_admin_token)])
async def get_observability_product_health_weekly(
    window_days: int = 7,
    cost_window: int = 100,
    efficiency_limit: int = 400,
    memory_stale_days: int = 14,
    include_persona_drift: bool = True,
    persona_source: str = "persona_eval",
):
    report = _build_product_health_weekly_report(
        window_days=window_days,
        cost_window=cost_window,
        efficiency_limit=efficiency_limit,
        memory_stale_days=memory_stale_days,
        include_persona_drift=include_persona_drift,
        persona_source=persona_source,
    )
    return {"status": "ok", "report": report}

@app.post("/observability/product-health-weekly/export", dependencies=[Depends(verify_admin_token)])
async def export_observability_product_health_weekly(payload: Dict[str, Any]):
    data = payload if isinstance(payload, dict) else {}
    try:
        window_days = max(1, min(int(data.get("window_days", 7)), 30))
    except (TypeError, ValueError):
        window_days = 7
    try:
        cost_window = max(1, min(int(data.get("cost_window", 100)), 1000))
    except (TypeError, ValueError):
        cost_window = 100
    try:
        efficiency_limit = max(1, min(int(data.get("efficiency_limit", 400)), 5000))
    except (TypeError, ValueError):
        efficiency_limit = 400
    try:
        memory_stale_days = max(1, min(int(data.get("memory_stale_days", 14)), 365))
    except (TypeError, ValueError):
        memory_stale_days = 14

    report = _build_product_health_weekly_report(
        window_days=window_days,
        cost_window=cost_window,
        efficiency_limit=efficiency_limit,
        memory_stale_days=memory_stale_days,
        include_persona_drift=bool(data.get("include_persona_drift", True)),
        persona_source=str(data.get("persona_source", "persona_eval") or "persona_eval"),
    )

    fmt = str(data.get("format", "markdown") or "markdown").strip().lower()
    if fmt not in {"markdown", "json"}:
        fmt = "markdown"
    stamp = time.strftime("%Y-%m-%d")
    default_name = (
        f"PRODUCT_HEALTH_WEEKLY_{stamp}.json"
        if fmt == "json"
        else f"PRODUCT_HEALTH_WEEKLY_{stamp}.md"
    )
    output_path = _resolve_export_output_path(
        output_raw=str(data.get("output_path", "")).strip(),
        default_filename=default_name,
    )
    if fmt == "json":
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output_path.write_text(_render_product_health_weekly_markdown(report), encoding="utf-8")
    return {"status": "ok", "format": fmt, "path": str(output_path), "report": report}

@app.get("/observability/gui-simple-benchmark", dependencies=[Depends(verify_admin_token)])
async def get_observability_gui_simple_benchmark(window: int = 20):
    return {
        "status": "ok",
        "benchmark": _build_gui_simple_benchmark_observability(window=max(1, min(window, 200))),
    }

@app.post("/observability/gui-simple-benchmark/run", dependencies=[Depends(verify_admin_token)])
async def run_gui_simple_benchmark(payload: Optional[Dict[str, Any]] = None):
    if TOOL_REGISTRY is None:
        raise HTTPException(status_code=503, detail="Tool registry unavailable")
    if TOOL_REGISTRY.get("node_invoke") is None:
        raise HTTPException(status_code=503, detail="node_invoke tool unavailable")
    report = await _run_gui_simple_benchmark_suite(payload if isinstance(payload, dict) else {})
    _append_policy_audit(
        action="observability.gui_simple_benchmark.run",
        details={
            "run_id": str(report.get("run_id", "")),
            "success_rate": report.get("success_rate"),
            "failed_cases": report.get("failed_cases"),
            "total_cases": report.get("total_cases"),
        },
    )
    if float(report.get("success_rate", 0.0) or 0.0) < 0.75:
        _append_alert(
            "warning",
            "gui_simple_benchmark",
            "gui_simple_benchmark_warning",
            {
                "run_id": str(report.get("run_id", "")),
                "success_rate": float(report.get("success_rate", 0.0) or 0.0),
                "failed_cases": int(report.get("failed_cases", 0) or 0),
            },
        )
    return {"status": "ok", "report": report}

@app.get("/observability/alerts", dependencies=[Depends(verify_admin_token)])
async def get_observability_alerts(limit: int = 100):
    entries = list(_alert_buffer)
    return {"status": "ok", "items": entries[-max(1, min(limit, 500)) :], "total": len(entries)}

@app.delete("/observability/alerts", dependencies=[Depends(verify_admin_token)])
async def clear_observability_alerts():
    _alert_buffer.clear()
    return {"status": "success", "message": "Observability alerts cleared"}

@app.get("/observability/tool-governance-slo", dependencies=[Depends(verify_admin_token)])
async def get_tool_governance_slo(limit: int = 200):
    return {"status": "ok", "slo": _build_tool_governance_slo(limit=max(1, min(limit, 1000)))}

@app.post("/observability/tool-governance-slo/export", dependencies=[Depends(verify_admin_token)])
async def export_tool_governance_slo(payload: Dict[str, Any]):
    limit_raw = payload.get("limit", 200) if isinstance(payload, dict) else 200
    try:
        limit = max(1, min(int(limit_raw), 1000))
    except (TypeError, ValueError):
        limit = 200
    slo = _build_tool_governance_slo(limit=limit)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str((payload or {}).get("output_path", "")).strip() if isinstance(payload, dict) else "",
        default_filename=f"TOOL_GOVERNANCE_SLO_{stamp}.md",
    )

    metrics = slo.get("metrics", {}) if isinstance(slo.get("metrics"), dict) else {}
    checks = slo.get("checks", {}) if isinstance(slo.get("checks"), dict) else {}
    targets = slo.get("targets", {}) if isinstance(slo.get("targets"), dict) else {}
    lines = [
        "# Tool Governance SLO Report",
        "",
        f"- generated_at: {slo.get('generated_at')}",
        f"- limit: {slo.get('limit')}",
        f"- passed: {bool(slo.get('passed', False))}",
        "",
        "## Metrics",
        f"- tool_calls: {metrics.get('tool_calls', 0)}",
        f"- tool_failures: {metrics.get('tool_failures', 0)}",
        f"- tool_success_rate: {metrics.get('tool_success_rate', 1.0)}",
        f"- tool_p95_latency_ms: {metrics.get('tool_p95_latency_ms', 0.0)}",
        f"- budget_hit_rate: {metrics.get('budget_hit_rate', 0.0)}",
        f"- circuit_recovery_p95_ms: {metrics.get('circuit_recovery_p95_ms', 0.0)}",
        "",
        "## Targets",
        f"- min_tool_success_rate: {targets.get('min_tool_success_rate')}",
        f"- max_tool_p95_latency_ms: {targets.get('max_tool_p95_latency_ms')}",
        f"- max_budget_hit_rate: {targets.get('max_budget_hit_rate')}",
        f"- max_circuit_recovery_ms: {targets.get('max_circuit_recovery_ms')}",
        "",
        "## Checks",
        f"- tool_success_rate_ok: {bool(checks.get('tool_success_rate_ok', False))}",
        f"- tool_p95_latency_ok: {bool(checks.get('tool_p95_latency_ok', False))}",
        f"- budget_hit_rate_ok: {bool(checks.get('budget_hit_rate_ok', False))}",
        f"- circuit_recovery_ok: {bool(checks.get('circuit_recovery_ok', False))}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "slo": slo}

@app.get("/observability/trace-spec", dependencies=[Depends(verify_admin_token)])
async def get_observability_trace_spec(limit: int = 200):
    return {"status": "ok", "trace_spec": _build_unified_trace_spec(limit=max(1, min(limit, 1000)))}

@app.post("/observability/trace-spec/export", dependencies=[Depends(verify_admin_token)])
async def export_observability_trace_spec(payload: Dict[str, Any]):
    limit_raw = payload.get("limit", 200) if isinstance(payload, dict) else 200
    try:
        limit = max(1, min(int(limit_raw), 1000))
    except (TypeError, ValueError):
        limit = 200
    spec = _build_unified_trace_spec(limit=limit)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str((payload or {}).get("output_path", "")).strip() if isinstance(payload, dict) else "",
        default_filename=f"TRACE_SPEC_REPORT_{stamp}.md",
    )

    coverage = spec.get("coverage", {}) if isinstance(spec.get("coverage"), dict) else {}
    links = spec.get("links", {}) if isinstance(spec.get("links"), dict) else {}
    lines = [
        "# Unified Trace Spec Report",
        "",
        f"- generated_at: {spec.get('generated_at')}",
        f"- limit: {spec.get('limit')}",
        f"- trace_id_format: {spec.get('trace_id_format')}",
        "",
        "## Coverage",
        f"- total_events: {coverage.get('total_events', 0)}",
        f"- total_with_trace_id: {coverage.get('total_with_trace_id', 0)}",
        f"- trace_id_coverage_rate: {coverage.get('trace_id_coverage_rate', 0.0)}",
        f"- llm_request_events: {(coverage.get('llm_request') or {}).get('events', 0)}",
        f"- tool_call_events: {(coverage.get('tool_call') or {}).get('events', 0)}",
        f"- workflow_step_events: {(coverage.get('workflow_step') or {}).get('events', 0)}",
        "",
        "## Linking",
        f"- trace_count: {links.get('trace_count', 0)}",
        f"- full_chain_trace_count: {links.get('full_chain_trace_count', 0)}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "trace_spec": spec}

@app.get("/observability/baseline-panel", dependencies=[Depends(verify_admin_token)])
async def get_observability_baseline_panel(limit: int = 200, window_days: int = 7):
    panel = _build_alignment_baseline_panel(
        limit=max(1, min(limit, 1000)),
        window_days=max(1, min(window_days, 30)),
    )
    return {"status": "ok", "panel": panel}

@app.post("/observability/baseline-panel/export", dependencies=[Depends(verify_admin_token)])
async def export_observability_baseline_panel(payload: Dict[str, Any]):
    limit_raw = payload.get("limit", 200) if isinstance(payload, dict) else 200
    window_raw = payload.get("window_days", 7) if isinstance(payload, dict) else 7
    try:
        limit = max(1, min(int(limit_raw), 1000))
    except (TypeError, ValueError):
        limit = 200
    try:
        window_days = max(1, min(int(window_raw), 30))
    except (TypeError, ValueError):
        window_days = 7
    panel = _build_alignment_baseline_panel(limit=limit, window_days=window_days)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str((payload or {}).get("output_path", "")).strip() if isinstance(payload, dict) else "",
        default_filename=f"ALIGNMENT_BASELINE_PANEL_{stamp}.md",
    )

    metrics = panel.get("metrics", {}) if isinstance(panel.get("metrics"), dict) else {}
    checks = panel.get("checks", {}) if isinstance(panel.get("checks"), dict) else {}
    lines = [
        "# Alignment Baseline Panel",
        "",
        f"- generated_at: {panel.get('generated_at')}",
        f"- passed: {bool(panel.get('passed', False))}",
        "",
        "## Metrics",
        f"- tool_success_rate: {metrics.get('tool_success_rate')}",
        f"- workflow_p95_latency_ms: {metrics.get('workflow_p95_latency_ms')}",
        f"- persona_consistency_score: {metrics.get('persona_consistency_score')}",
        f"- training_avg_score: {metrics.get('training_avg_score')}",
        f"- training_reward_proxy_delta: {metrics.get('training_reward_proxy_delta')}",
        "",
        "## Checks",
        f"- tool_success_rate_ok: {bool(checks.get('tool_success_rate_ok', False))}",
        f"- workflow_p95_ok: {bool(checks.get('workflow_p95_ok', False))}",
        f"- persona_consistency_ok: {bool(checks.get('persona_consistency_ok', False))}",
        f"- training_gain_ok: {bool(checks.get('training_gain_ok', False))}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "panel": panel}

@app.get("/observability/tool-batching", dependencies=[Depends(verify_admin_token)])
async def get_tool_batching_observability():
    _tb = get_tool_batching_tracker()
    if _tb is None or not hasattr(_tb, "summary"):
        return {"status": "ok", "available": False, "metrics": {}}
    try:
        return {"status": "ok", "available": True, "metrics": _tb.summary()}
    except Exception as exc:
        logger.warning("Failed to fetch tool batching metrics: %s", exc)
        return {"status": "ok", "available": False, "metrics": {}, "error": str(exc)}

@app.get("/observability/policy-scoreboard", dependencies=[Depends(verify_admin_token)])
async def get_observability_policy_scoreboard(limit: int = 50, dataset_id: Optional[str] = None):
    try:
        safe_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        safe_limit = 50
    dataset_key = str(dataset_id or "").strip() or None
    scoreboard = _build_training_bridge_policy_scoreboard(limit=safe_limit, dataset_id=dataset_key)
    return {"status": "ok", "scoreboard": scoreboard}

@app.get("/observability/failure-attribution", dependencies=[Depends(verify_admin_token)])
async def get_observability_failure_attribution(limit: int = 200):
    try:
        safe_limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        safe_limit = 200
    profile = _build_llm_tool_failure_profile(limit=safe_limit)
    attribution = profile.get("failure_attribution", {}) if isinstance(profile, dict) else {}
    return {"status": "ok", "failure_attribution": attribution}

@app.get("/observability/metrics", dependencies=[Depends(verify_admin_token)])
async def get_observability_metrics(limit: int = 200):
    provider_metrics: Dict[str, Any] = {
        "total_calls": 0,
        "total_failures": 0,
        "success_rate": 1.0,
        "p95_latency_ms": 0.0,
        "error_classes": {},
        "budget": {},
        "providers": [],
    }
    model_map: Dict[str, Dict[str, Any]] = {}

    _llm_router = get_llm_router()
    _status_raw = _llm_router.get_status() if _llm_router is not None else _shared.IPC_ROUTER_STATUS
    if _status_raw is not None:
        status = _status_raw
        providers = status.get("providers", []) if isinstance(status, dict) else []
        all_latencies: List[float] = []
        total_calls = 0
        total_failures = 0
        error_classes: Dict[str, int] = {}
        for item in providers:
            calls = int(item.get("calls", 0) or 0)
            failures = int(item.get("failures", 0) or 0)
            p95_ms = float(item.get("p95_latency_ms", 0.0) or 0.0)
            total_calls += calls
            total_failures += failures
            if p95_ms > 0:
                all_latencies.append(p95_ms)
            for key, val in (item.get("error_classes", {}) or {}).items():
                k = str(key)
                error_classes[k] = error_classes.get(k, 0) + int(val or 0)

            model_name = str(item.get("model", "")).strip() or "unknown"
            model_entry = model_map.setdefault(
                model_name,
                {"model": model_name, "calls": 0, "failures": 0, "p95_latency_samples": [], "error_classes": {}},
            )
            model_entry["calls"] += calls
            model_entry["failures"] += failures
            if p95_ms > 0:
                model_entry["p95_latency_samples"].append(p95_ms)
            for key, val in (item.get("error_classes", {}) or {}).items():
                kk = str(key)
                model_entry["error_classes"][kk] = model_entry["error_classes"].get(kk, 0) + int(val or 0)

        provider_metrics = {
            "total_calls": total_calls,
            "total_failures": total_failures,
            "success_rate": round((total_calls - total_failures) / total_calls, 4) if total_calls else 1.0,
            "p95_latency_ms": _p95(all_latencies),
            "error_classes": error_classes,
            "budget": status.get("budget", {}),
            "budget_degrade_active": bool(status.get("budget_degrade_active", False)),
            "providers": providers,
        }

    model_metrics: List[Dict[str, Any]] = []
    for item in model_map.values():
        calls = int(item["calls"])
        failures = int(item["failures"])
        model_metrics.append(
            {
                "model": item["model"],
                "calls": calls,
                "failures": failures,
                "success_rate": round((calls - failures) / calls, 4) if calls else 1.0,
                "p95_latency_ms": _p95(item["p95_latency_samples"]),
                "error_classes": item["error_classes"],
            }
        )
    model_metrics.sort(key=lambda x: x["calls"], reverse=True)

    agent_items: List[Dict[str, Any]] = []
    _traj_store = get_trajectory_store()
    if _traj_store is not None:
        recent = _traj_store.list_recent(limit=max(1, min(limit, 1000)))
        by_agent: Dict[str, Dict[str, Any]] = {}
        for item in recent:
            agent_id = "main"
            key = by_agent.setdefault(
                agent_id,
                {"agent_id": agent_id, "turns": 0, "failures": 0, "latencies": [], "error_classes": {}},
            )
            key["turns"] += 1
            status = str(item.get("status", "")).strip().lower()
            if status not in {"success", "ok"}:
                key["failures"] += 1
                err = classify_error_message(str(item.get("final_preview", "")))
                key["error_classes"][err] = key["error_classes"].get(err, 0) + 1
            latency = item.get("turn_latency_ms")
            if isinstance(latency, (int, float)):
                key["latencies"].append(float(latency))
        for key in by_agent.values():
            turns = int(key["turns"])
            failures = int(key["failures"])
            agent_items.append(
                {
                    "agent_id": key["agent_id"],
                    "turns": turns,
                    "failures": failures,
                    "success_rate": round((turns - failures) / turns, 4) if turns else 1.0,
                    "p95_latency_ms": _p95(key["latencies"]),
                    "error_classes": key["error_classes"],
                }
            )

    workflow_metrics = _build_workflow_observability_metrics(limit=limit)
    persona_metrics = _latest_persona_consistency_signal()
    llm_tool_profile = _build_llm_tool_failure_profile(limit=limit)
    failure_attribution = (
        llm_tool_profile.get("failure_attribution", {})
        if isinstance(llm_tool_profile, dict)
        else {}
    )
    inbound_media_profile = _build_inbound_media_profile(limit=limit)
    tool_governance = _get_tool_governance_snapshot(limit=limit)
    policy_scoreboard = _build_training_bridge_policy_scoreboard(limit=max(1, min(limit, 200)))
    gui_simple_benchmark = _build_gui_simple_benchmark_observability(window=max(1, min(limit, 200)))

    return {
        "status": "ok",
        "provider": provider_metrics,
        "model": model_metrics,
        "agent": agent_items,
        "workflow": workflow_metrics,
        "persona": persona_metrics,
        "llm_tool": llm_tool_profile,
        "failure_attribution": failure_attribution,
        "inbound_media": inbound_media_profile,
        "tool_governance": tool_governance,
        "policy_scoreboard": policy_scoreboard,
        "gui_simple_benchmark": gui_simple_benchmark,
    }
