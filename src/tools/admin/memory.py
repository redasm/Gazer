from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List, Optional
import time
import re
import hashlib
import json
from datetime import datetime
from pathlib import Path
from collections import deque
from memory import MemoryManager
from memory.quality_eval import build_memory_quality_report
from memory.recall_regression import build_memory_recall_regression_report
from tools.admin.system import _build_persona_consistency_weekly_report
from tools.admin.auth import verify_admin_token
from tools.admin.state import (
    _MEMORY_TURN_HEALTH_LOG_PATH,
    _TOOL_PERSIST_LOG_PATH,
    get_trajectory_store,
    config,
    logger,
)
from tools.admin.utils import _dedupe_dict_rows, _read_jsonl_tail, _resolve_export_output_path
from tools.admin.workflow_helpers import _apply_memory_recall_gate_linkage, _memory_recall_regression_settings

app = APIRouter()

_memory_manager = None

def _get_memory_manager() -> MemoryManager:
    """Lazy-init the memory manager to avoid import-time I/O."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager

def _tool_result_persistence_policy() -> Dict[str, Any]:
    raw = config.get("memory.tool_result_persistence", {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    mode = str(raw.get("mode", "allowlist") or "allowlist").strip().lower()
    if mode not in {"allowlist", "denylist"}:
        mode = "allowlist"
    allow_tools = raw.get(
        "allow_tools",
        [
            "web_search",
            "web_fetch",
            "web_report",
            "read_file",
            "grep",
            "find_files",
            "list_dir",
            "email_read",
            "email_search",
            "vision_query",
        ],
    )
    deny_tools = raw.get(
        "deny_tools",
        [
            "exec",
            "write_file",
            "edit_file",
            "node_invoke",
            "gui_task_execute",
            "git_commit",
            "git_push",
            "email_send",
            "hardware_control",
        ],
    )
    allow = [str(item).strip() for item in allow_tools if str(item).strip()]
    deny = [str(item).strip() for item in deny_tools if str(item).strip()]
    try:
        min_chars = max(1, int(raw.get("min_result_chars", 16) or 16))
    except (TypeError, ValueError):
        min_chars = 16
    try:
        max_chars = max(80, int(raw.get("max_result_chars", 1200) or 1200))
    except (TypeError, ValueError):
        max_chars = 1200
    return {
        "enabled": bool(raw.get("enabled", True)),
        "mode": mode,
        "allow_tools": allow,
        "deny_tools": deny,
        "persist_on_error": bool(raw.get("persist_on_error", False)),
        "min_result_chars": min_chars,
        "max_result_chars": max_chars,
    }

def _resolve_openviking_backend_dir() -> Path:
    mm = _get_memory_manager()
    return Path(
        str(
            getattr(
                getattr(mm, "backend", None),
                "data_dir",
                getattr(mm, "base_path", "data/openviking"),
            )
            or "data/openviking"
        )
    )

def _parse_time_like(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0
    text = str(raw or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return 0.0

def _split_rows_by_dual_windows(
    rows: List[Dict[str, Any]],
    *,
    window_days: int,
    ts_getter: Any,
) -> Dict[str, Any]:
    now = time.time()
    window_seconds = float(max(1, int(window_days or 7)) * 86400)
    current_start = now - window_seconds
    previous_start = current_start - window_seconds
    current: List[Dict[str, Any]] = []
    previous: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _parse_time_like(ts_getter(row))
        if current_start <= ts <= now:
            current.append(row)
        elif previous_start <= ts < current_start:
            previous.append(row)
    return {
        "now": now,
        "window_days": max(1, int(window_days or 7)),
        "current_start": current_start,
        "previous_start": previous_start,
        "current": current,
        "previous": previous,
    }

def _load_openviking_long_term_counts(backend_dir: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    long_term_dir = backend_dir / "long_term"
    if not long_term_dir.is_dir():
        return counts
    for path in sorted(long_term_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            counts[path.stem] = len(payload)
        else:
            counts[path.stem] = 0
    return counts

def _build_persona_memory_joint_drift_report(
    window_days: int = 7,
    source: str = "persona_eval",
) -> Dict[str, Any]:
    window = max(1, min(int(window_days or 7), 30))
    persona = _build_persona_consistency_weekly_report(window_days=window, source=source)
    backend_dir = _resolve_openviking_backend_dir()
    events_path = backend_dir / "memory_events.jsonl"
    decisions_path = backend_dir / "extraction_decisions.jsonl"

    events = _read_jsonl_tail(events_path, limit=5000)
    decisions = [
        row
        for row in _read_jsonl_tail(decisions_path, limit=5000)
        if str(row.get("kind", "")).strip() == "memory_extraction"
    ]

    event_windows = _split_rows_by_dual_windows(
        events,
        window_days=window,
        ts_getter=lambda row: row.get("timestamp", row.get("date", "")),
    )
    decision_windows = _split_rows_by_dual_windows(
        decisions,
        window_days=window,
        ts_getter=lambda row: row.get("timestamp", ""),
    )

    def _decision_level_summary(items: List[Dict[str, Any]], *, event_total: int) -> Dict[str, Any]:
        by_decision = {"CREATE": 0, "MERGE": 0, "UPDATE": 0, "SKIP": 0}
        key_counter: Dict[str, int] = {}
        for item in items:
            decision = str(item.get("decision", "")).strip().upper()
            if decision not in by_decision:
                continue
            by_decision[decision] += 1
            marker = f"{str(item.get('category', '')).strip()}::{str(item.get('key', '')).strip()}"
            if marker and marker != "::":
                key_counter[marker] = int(key_counter.get(marker, 0)) + 1
        extraction_total = sum(by_decision.values())
        accepted = by_decision["CREATE"] + by_decision["MERGE"] + by_decision["UPDATE"]
        duplicate_hits = sum((count - 1) for count in key_counter.values() if count > 1)
        return {
            "event_total": int(event_total),
            "extraction_total": extraction_total,
            "accepted_total": accepted,
            "decision_breakdown": by_decision,
            "yield_rate": round(accepted / max(1, event_total), 4),
            "churn_ratio": round((by_decision["MERGE"] + by_decision["UPDATE"]) / max(1, accepted), 4),
            "duplicate_key_ratio": round(duplicate_hits / max(1, extraction_total), 4),
        }

    current_memory = _decision_level_summary(
        decision_windows["current"], event_total=len(event_windows["current"])
    )
    previous_memory = _decision_level_summary(
        decision_windows["previous"], event_total=len(event_windows["previous"])
    )
    yield_drop = max(0.0, float(previous_memory["yield_rate"]) - float(current_memory["yield_rate"]))
    event_volatility = abs(
        int(current_memory["event_total"]) - int(previous_memory["event_total"])
    ) / max(1, int(previous_memory["event_total"]))
    drift_score = min(
        1.0,
        (0.30 * min(1.0, event_volatility))
        + (0.30 * float(current_memory["churn_ratio"]))
        + (0.20 * float(current_memory["duplicate_key_ratio"]))
        + (0.20 * yield_drop),
    )
    drift_level = "healthy"
    if drift_score >= 0.65:
        drift_level = "critical"
    elif drift_score >= 0.35:
        drift_level = "warning"

    persona_trend = (
        str(((persona.get("trend", {}) or {}).get("direction", "stable"))).strip().lower() or "stable"
    )
    joint_direction = "stable"
    if persona_trend == "worse" or drift_level in {"warning", "critical"}:
        joint_direction = "worse"
    elif persona_trend == "improving" and drift_level == "healthy":
        joint_direction = "improving"

    long_term_counts = _load_openviking_long_term_counts(backend_dir)
    return {
        "status": "ok",
        "generated_at": event_windows["now"],
        "window_days": window,
        "source": str(source or "persona_eval"),
        "backend_dir": str(backend_dir),
        "persona": persona,
        "memory": {
            "current_window": current_memory,
            "previous_window": previous_memory,
            "drift": {
                "score": round(drift_score, 4),
                "level": drift_level,
                "event_volatility": round(event_volatility, 4),
                "yield_drop": round(yield_drop, 4),
            },
            "long_term_counts": long_term_counts,
            "long_term_total": int(sum(long_term_counts.values())),
        },
        "joint": {
            "direction": joint_direction,
            "risk_level": "critical" if drift_level == "critical" else ("warning" if joint_direction == "worse" else "healthy"),
            "notes": [
                "persona trend driven by runtime signals and consistency score delta",
                "memory drift uses volatility/churn/duplicate/yield proxies",
            ],
        },
    }

def _build_memory_extraction_quality_report(
    window_days: int = 7,
    high_value_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    window = max(1, min(int(window_days or 7), 30))
    backend_dir = _resolve_openviking_backend_dir()
    events_path = backend_dir / "memory_events.jsonl"
    decisions_path = backend_dir / "extraction_decisions.jsonl"
    events = _read_jsonl_tail(events_path, limit=5000)
    decisions = [
        row
        for row in _read_jsonl_tail(decisions_path, limit=5000)
        if str(row.get("kind", "")).strip() == "memory_extraction"
    ]
    high_value = {
        str(item).strip().lower()
        for item in (high_value_categories or ["profile", "preferences", "entities", "patterns"])
        if str(item).strip()
    }
    if not high_value:
        high_value = {"profile", "preferences", "entities", "patterns"}

    event_windows = _split_rows_by_dual_windows(
        events,
        window_days=window,
        ts_getter=lambda row: row.get("timestamp", row.get("date", "")),
    )
    decision_windows = _split_rows_by_dual_windows(
        decisions,
        window_days=window,
        ts_getter=lambda row: row.get("timestamp", ""),
    )

    def _quality_summary(items: List[Dict[str, Any]], *, event_total: int) -> Dict[str, Any]:
        category_stats: Dict[str, Dict[str, int]] = {}
        high_value_total = 0
        high_value_accepted = 0
        high_value_enriched = 0
        high_value_skipped = 0
        for item in items:
            category = str(item.get("category", "")).strip().lower()
            decision = str(item.get("decision", "")).strip().upper()
            if not category:
                continue
            bucket = category_stats.setdefault(
                category,
                {"total": 0, "accepted": 0, "enriched": 0, "skipped": 0, "create": 0, "merge": 0, "update": 0},
            )
            bucket["total"] += 1
            if decision in {"CREATE", "MERGE", "UPDATE"}:
                bucket["accepted"] += 1
            if decision in {"CREATE", "MERGE"}:
                bucket["enriched"] += 1
            if decision == "SKIP":
                bucket["skipped"] += 1
            if decision == "CREATE":
                bucket["create"] += 1
            if decision == "MERGE":
                bucket["merge"] += 1
            if decision == "UPDATE":
                bucket["update"] += 1

            if category in high_value:
                high_value_total += 1
                if decision in {"CREATE", "MERGE", "UPDATE"}:
                    high_value_accepted += 1
                if decision in {"CREATE", "MERGE"}:
                    high_value_enriched += 1
                if decision == "SKIP":
                    high_value_skipped += 1

        precision_proxy = round(high_value_enriched / max(1, high_value_accepted), 4)
        recall_proxy = round(high_value_accepted / max(1, event_total), 4)
        f1_proxy = 0.0
        if precision_proxy + recall_proxy > 0:
            f1_proxy = round((2 * precision_proxy * recall_proxy) / (precision_proxy + recall_proxy), 4)
        return {
            "event_total": int(event_total),
            "high_value_categories": sorted(high_value),
            "high_value_attempts": high_value_total,
            "high_value_accepted": high_value_accepted,
            "high_value_enriched": high_value_enriched,
            "high_value_skipped": high_value_skipped,
            "precision_proxy": precision_proxy,
            "recall_proxy": recall_proxy,
            "f1_proxy": f1_proxy,
            "by_category": category_stats,
        }

    current = _quality_summary(decision_windows["current"], event_total=len(event_windows["current"]))
    previous = _quality_summary(decision_windows["previous"], event_total=len(event_windows["previous"]))
    precision_delta = round(float(current["precision_proxy"]) - float(previous["precision_proxy"]), 4)
    recall_delta = round(float(current["recall_proxy"]) - float(previous["recall_proxy"]), 4)
    quality_level = "healthy"
    if current["precision_proxy"] < 0.45 or current["recall_proxy"] < 0.35:
        quality_level = "critical"
    elif current["precision_proxy"] < 0.65 or current["recall_proxy"] < 0.55:
        quality_level = "warning"

    return {
        "status": "ok",
        "generated_at": event_windows["now"],
        "window_days": window,
        "backend_dir": str(backend_dir),
        "current_window": current,
        "previous_window": previous,
        "trend": {
            "precision_proxy_delta": precision_delta,
            "recall_proxy_delta": recall_delta,
            "direction": (
                "improving"
                if precision_delta > 0 and recall_delta > 0
                else ("worse" if precision_delta < 0 or recall_delta < 0 else "stable")
            ),
            "quality_level": quality_level,
        },
    }

@app.get("/memory/recent", dependencies=[Depends(verify_admin_token)])
async def get_recent_memory(limit: int = 50):
    """Get recent memory entries."""
    try:
        memory = _get_memory_manager().load_recent(limit=limit)
        return {
            "count": len(memory.memories),
            "entries": [
                {
                    "sender": e.sender,
                    "content": e.content,
                    "timestamp": e.timestamp.isoformat()
                } for e in memory.memories
            ]
        }
    except Exception as e:
        logger.error("Failed to load memory: %s", e)
        return {"count": 0, "entries": [], "error": str(e)}

@app.get("/memory/search", dependencies=[Depends(verify_admin_token)])
async def search_memory(q: str, limit: int = 10, mode: str = "quick"):
    """Search memory via OpenViking-backed index adapter.

    - quick: keyword-first lookup (low latency)
    - deep: hybrid lookup with layered payload (preview + detail)
    """
    try:
        mm = _get_memory_manager()
        search_mode = str(mode or "quick").strip().lower() or "quick"
        if search_mode == "deep":
            rows = await mm.index.hybrid_search(q, limit=limit)
            results = []
            for row in rows:
                content = str(row.get("content", ""))
                results.append(
                    {
                        "sender": str(row.get("sender", "")),
                        "timestamp": str(row.get("timestamp", "")),
                        "score": float(row.get("score", 0.0) or 0.0),
                        "preview": content[:120],
                        "detail": content,
                    }
                )
        else:
            results = mm.index.fts_search(q, limit=limit)
        return {
            "query": q,
            "mode": search_mode,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        logger.error("Search failed: %s", e)
        return {"query": q, "count": 0, "results": [], "error": str(e)}

@app.get("/memory/quality-report", dependencies=[Depends(verify_admin_token)])
async def get_memory_quality_report(
    window_days: int = 7,
    stale_days: int = 14,
    include_samples: bool = False,
    sample_limit: int = 10,
    include_persona_drift: bool = True,
    source: str = "persona_eval",
):
    """Build OpenViking memory quality report (relevance/timeliness/conflict)."""
    try:
        backend_dir = _resolve_openviking_backend_dir()
        report = build_memory_quality_report(
            backend_dir=backend_dir,
            window_days=window_days,
            stale_days=stale_days,
            include_samples=bool(include_samples),
            sample_limit=sample_limit,
        )
        if include_persona_drift:
            joint = _build_persona_memory_joint_drift_report(
                window_days=max(1, min(int(window_days or 7), 30)),
                source=str(source or "persona_eval"),
            )
            memory_drift = {}
            if isinstance(joint.get("memory"), dict):
                memory_drift = joint["memory"].get("drift", {})
            report["persona_drift"] = {
                "source": str(source or "persona_eval"),
                "memory_drift": memory_drift,
                "joint_risk_level": str((joint.get("joint", {}) or {}).get("risk_level", "unknown")),
            }
            if isinstance(report.get("current_window"), dict):
                current = report["current_window"]
                if isinstance(current.get("scores"), dict):
                    current["scores"]["joint_risk_level"] = report["persona_drift"]["joint_risk_level"]
        return report
    except Exception as e:
        logger.error("Failed to build memory quality report: %s", e)
        return {"status": "error", "error": str(e)}

@app.post("/memory/quality-report/export", dependencies=[Depends(verify_admin_token)])
async def export_memory_quality_report(payload: Dict[str, Any]):
    window_days_raw = payload.get("window_days", 7)
    stale_days_raw = payload.get("stale_days", 14)
    sample_limit_raw = payload.get("sample_limit", 10)
    include_samples = bool(payload.get("include_samples", False))
    include_persona_drift = bool(payload.get("include_persona_drift", True))
    source = str(payload.get("source", "persona_eval")).strip() or "persona_eval"
    output_format = str(payload.get("format", "markdown")).strip().lower() or "markdown"

    try:
        window_days = int(window_days_raw)
    except (TypeError, ValueError):
        window_days = 7
    try:
        stale_days = int(stale_days_raw)
    except (TypeError, ValueError):
        stale_days = 14
    try:
        sample_limit = int(sample_limit_raw)
    except (TypeError, ValueError):
        sample_limit = 10

    report = await get_memory_quality_report(
        window_days=window_days,
        stale_days=stale_days,
        include_samples=include_samples,
        sample_limit=sample_limit,
        include_persona_drift=include_persona_drift,
        source=source,
    )

    stamp = time.strftime("%Y-%m-%d")
    fmt = "json" if output_format == "json" else "markdown"
    suffix = "json" if fmt == "json" else "md"
    output_path = _resolve_export_output_path(
        output_raw=str(payload.get("output_path", "")).strip(),
        default_filename=f"MEMORY_QUALITY_REPORT_{stamp}.{suffix}",
    )

    if fmt == "json":
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "ok", "path": str(output_path), "format": fmt, "report": report}

    current = report.get("current_window", {}) if isinstance(report.get("current_window"), dict) else {}
    current_scores = current.get("scores", {}) if isinstance(current.get("scores"), dict) else {}
    current_metrics = current.get("metrics", {}) if isinstance(current.get("metrics"), dict) else {}
    relevance = current_metrics.get("relevance", {}) if isinstance(current_metrics.get("relevance"), dict) else {}
    timeliness = current_metrics.get("timeliness", {}) if isinstance(current_metrics.get("timeliness"), dict) else {}
    conflict = current_metrics.get("conflict", {}) if isinstance(current_metrics.get("conflict"), dict) else {}
    trend = report.get("trend", {}) if isinstance(report.get("trend"), dict) else {}
    persona_drift = report.get("persona_drift", {}) if isinstance(report.get("persona_drift"), dict) else {}
    generated_at_raw = report.get("generated_at")
    generated_at_iso = "unknown"
    try:
        generated_at_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(generated_at_raw)))
    except (TypeError, ValueError):
        generated_at_iso = str(generated_at_raw or "unknown")

    lines = [
        "# Memory Quality Report",
        "",
        f"- generated_at: {generated_at_raw}",
        f"- generated_at_iso: {generated_at_iso}",
        f"- window_days: {report.get('window_days')}",
        f"- backend_dir: {report.get('backend_dir')}",
        f"- quality_score: {current_scores.get('quality_score', 0)}",
        f"- quality_level: {current_scores.get('quality_level', 'unknown')}",
        "",
        "## Core Metrics",
        f"- relevance_score: {current_scores.get('relevance_score', 0)}",
        f"- timeliness_score: {current_scores.get('timeliness_score', 0)}",
        f"- stability_score: {current_scores.get('stability_score', 0)}",
        "",
        "## Relevance",
        f"- yield_rate: {relevance.get('yield_rate', 0)}",
        f"- yield_rate_capped: {relevance.get('yield_rate_capped', relevance.get('yield_rate', 0))}",
        f"- decision_acceptance_rate: {relevance.get('decision_acceptance_rate', 0)}",
        f"- source_binding_rate: {relevance.get('source_binding_rate', 0)}",
        f"- event_binding_rate: {relevance.get('event_binding_rate', 0)}",
        f"- decision_per_event_rate: {relevance.get('decision_per_event_rate', 0)}",
        f"- alignment_avg: {relevance.get('alignment_avg', 0)}",
        "",
        "## Timeliness",
        f"- stale_days_threshold: {timeliness.get('stale_days_threshold', 0)}",
        f"- median_age_days: {timeliness.get('median_age_days', 0)}",
        f"- stale_ratio: {timeliness.get('stale_ratio', 0)}",
        "",
        "## Conflict",
        f"- conflict_rate: {conflict.get('conflict_rate', 0)}",
        f"- duplicate_key_ratio: {conflict.get('duplicate_key_ratio', 0)}",
        f"- churn_ratio: {conflict.get('churn_ratio', 0)}",
        "",
        "## Trend",
        f"- quality_score_delta: {trend.get('quality_score_delta', 0)}",
        f"- direction: {trend.get('direction', 'stable')}",
        f"- baseline_sufficient: {trend.get('baseline_sufficient', True)}",
        f"- baseline_events: {trend.get('baseline_events', 0)}",
        f"- baseline_decisions: {trend.get('baseline_decisions', 0)}",
        f"- interpretation: {trend.get('interpretation', 'normal')}",
        "",
    ]
    if persona_drift:
        lines.extend(
            [
                "## Persona Drift Linkage",
                f"- source: {persona_drift.get('source', 'persona_eval')}",
                f"- joint_risk_level: {persona_drift.get('joint_risk_level', 'unknown')}",
                f"- memory_drift_level: {(persona_drift.get('memory_drift', {}) or {}).get('level', 'unknown')}",
                "",
            ]
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "format": fmt, "report": report}

@app.get("/memory/turn-health", dependencies=[Depends(verify_admin_token)])
async def get_memory_turn_health(limit: int = 50):
    safe_limit = max(1, min(int(limit), 500))
    rows = _read_jsonl_tail(_MEMORY_TURN_HEALTH_LOG_PATH, limit=safe_limit)
    persist_ok_values = [row.get("persist_ok") for row in rows if row.get("persist_ok") is not None]
    persist_ok_rate = 1.0
    if persist_ok_values:
        ok_count = sum(1 for value in persist_ok_values if bool(value))
        persist_ok_rate = round(ok_count / max(1, len(persist_ok_values)), 4)

    recalls = [int(row.get("recall_count", 0) or 0) for row in rows]
    chars = [int(row.get("memory_context_chars", 0) or 0) for row in rows]
    tool_rows = _read_jsonl_tail(_TOOL_PERSIST_LOG_PATH, limit=1000)
    decision_counts = {"memory": 0, "trajectory_only": 0}
    by_tool: Dict[str, Dict[str, int]] = {}
    for row in tool_rows:
        decision = str(row.get("decision", "")).strip().lower()
        tool_name = str(row.get("tool_name", "")).strip() or "unknown"
        if decision in decision_counts:
            decision_counts[decision] += 1
        bucket = by_tool.setdefault(tool_name, {"memory": 0, "trajectory_only": 0})
        if decision in bucket:
            bucket[decision] += 1
    return {
        "status": "ok",
        "limit": safe_limit,
        "items": rows[-safe_limit:],
        "summary": {
            "turn_count": len(rows),
            "avg_recall_count": round((sum(recalls) / max(1, len(recalls))), 4) if recalls else 0.0,
            "avg_memory_context_chars": round((sum(chars) / max(1, len(chars))), 2) if chars else 0.0,
            "persist_ok_rate": persist_ok_rate,
        },
        "tool_persistence": {
            "policy": _tool_result_persistence_policy(),
            "decision_counts": decision_counts,
            "by_tool": by_tool,
        },
    }

@app.get("/memory/recall-regression", dependencies=[Depends(verify_admin_token)])
async def get_memory_recall_regression(
    window_days: Optional[int] = None,
    include_samples: bool = False,
    sample_limit: int = 10,
    persist: bool = True,
    apply_gate: bool = True,
):
    settings = _memory_recall_regression_settings()
    backend_dir = _resolve_openviking_backend_dir()
    if not settings.get("enabled", True):
        return {
            "status": "disabled",
            "message": "memory.recall_regression.enabled=false",
            "backend_dir": str(backend_dir),
        }

    report = build_memory_recall_regression_report(
        backend_dir=backend_dir,
        query_set_path=settings.get("query_set_path", ""),
        window_days=(
            settings.get("window_days", 7)
            if window_days is None
            else max(1, min(int(window_days), 30))
        ),
        top_k=settings.get("top_k", 5),
        min_match_score=settings.get("min_match_score", 0.18),
        min_precision_proxy=(settings.get("thresholds", {}) or {}).get("min_precision_proxy", 0.45),
        min_recall_proxy=(settings.get("thresholds", {}) or {}).get("min_recall_proxy", 0.45),
        warning_drop=(settings.get("thresholds", {}) or {}).get("warning_drop", 0.05),
        critical_drop=(settings.get("thresholds", {}) or {}).get("critical_drop", 0.12),
        include_samples=bool(include_samples),
        sample_limit=max(1, min(int(sample_limit or 10), 50)),
        persist=bool(persist),
    )
    linkage = _apply_memory_recall_gate_linkage(
        report=report,
        enabled=bool(settings.get("enabled", True)),
        apply_gate=bool(apply_gate),
        gate_cfg=settings.get("gate", {}) if isinstance(settings.get("gate", {}), dict) else {},
    )
    report["release_gate_linkage"] = linkage
    return report

@app.post("/memory/recall-regression/export", dependencies=[Depends(verify_admin_token)])
async def export_memory_recall_regression(payload: Dict[str, Any]):
    output_format = str(payload.get("format", "markdown")).strip().lower() or "markdown"
    fmt = "json" if output_format == "json" else "markdown"
    try:
        window_days = int(payload.get("window_days", 0))
        window_days_value: Optional[int] = window_days if window_days > 0 else None
    except (TypeError, ValueError):
        window_days_value = None
    try:
        sample_limit = int(payload.get("sample_limit", 10))
    except (TypeError, ValueError):
        sample_limit = 10

    report = await get_memory_recall_regression(
        window_days=window_days_value,
        include_samples=bool(payload.get("include_samples", False)),
        sample_limit=max(1, min(sample_limit, 50)),
        persist=bool(payload.get("persist", True)),
        apply_gate=bool(payload.get("apply_gate", True)),
    )

    stamp = time.strftime("%Y-%m-%d")
    suffix = "json" if fmt == "json" else "md"
    output_path = _resolve_export_output_path(
        output_raw=str(payload.get("output_path", "")).strip(),
        default_filename=f"MEMORY_RECALL_REGRESSION_{stamp}.{suffix}",
    )

    if fmt == "json":
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "ok", "path": str(output_path), "format": fmt, "report": report}

    current = report.get("current_window", {}) if isinstance(report.get("current_window"), dict) else {}
    metrics = current.get("metrics", {}) if isinstance(current.get("metrics"), dict) else {}
    trend = report.get("trend", {}) if isinstance(report.get("trend"), dict) else {}
    linkage = (
        report.get("release_gate_linkage", {})
        if isinstance(report.get("release_gate_linkage"), dict)
        else {}
    )
    alerts = report.get("alerts", []) if isinstance(report.get("alerts"), list) else []

    lines = [
        "# Memory Recall Regression Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- backend_dir: {report.get('backend_dir')}",
        f"- query_set_path: {report.get('query_set_path')}",
        f"- window_days: {report.get('window_days')}",
        "",
        "## Current Window",
        f"- query_total: {current.get('query_total', 0)}",
        f"- matched_queries: {current.get('matched_queries', 0)}",
        f"- precision_hits: {current.get('precision_hits', 0)}",
        f"- recall_proxy: {metrics.get('recall_proxy', 0)}",
        f"- precision_proxy: {metrics.get('precision_proxy', 0)}",
        f"- quality_score: {metrics.get('quality_score', 0)}",
        f"- quality_level: {metrics.get('quality_level', 'unknown')}",
        "",
        "## Trend",
        f"- direction: {trend.get('direction', 'stable')}",
        f"- quality_score_delta: {trend.get('quality_score_delta', 0)}",
        f"- recall_proxy_delta: {trend.get('recall_proxy_delta', 0)}",
        f"- precision_proxy_delta: {trend.get('precision_proxy_delta', 0)}",
        "",
        "## Release Gate Linkage",
        f"- mode: {linkage.get('mode', 'warn')}",
        f"- alert_only: {linkage.get('alert_only', True)}",
        f"- changed_gate: {linkage.get('changed_gate', False)}",
        f"- level: {linkage.get('level', 'healthy')}",
        "",
    ]
    if alerts:
        lines.append("## Alerts")
        for item in alerts:
            lines.append(
                f"- [{item.get('severity', 'info')}] {item.get('code', '')}: {item.get('detail', '')}"
            )
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "format": fmt, "report": report}

@app.get("/memory/graph", dependencies=[Depends(verify_admin_token)])
async def get_memory_graph():
    """Build a memory graph from OpenViking data sources only."""
    mm = _get_memory_manager()
    backend_dir = Path(
        str(
            getattr(
                getattr(mm, "backend", None),
                "data_dir",
                getattr(mm, "base_path", "data/openviking"),
            )
            or "data/openviking"
        )
    )

    node_map: Dict[str, Dict[str, Any]] = {}
    links: List[Dict[str, str]] = []
    link_seen: set[tuple[str, str, str]] = set()

    def add_node(nid: str, name: str, group: str, content: Optional[str] = None) -> str:
        if nid not in node_map:
            node_map[nid] = {
                "id": nid,
                "name": str(name or nid),
                "group": str(group or "topic"),
                "content": str(content or ""),
            }
        return nid

    def add_link(src: str, tgt: str, label: Optional[str] = None) -> None:
        edge = (str(src), str(tgt), str(label or ""))
        if edge in link_seen:
            return
        link_seen.add(edge)
        links.append({"source": edge[0], "target": edge[1], "label": edge[2]})

    def safe_node_id(seed: str) -> str:
        text = str(seed or "").strip()
        if not text:
            return "node:empty"
        norm = re.sub(r"[^a-zA-Z0-9:_-]+", "_", text)[:72]
        digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:10]
        return f"{norm}:{digest}"

    def read_json_file(path: Path) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return None

    def group_for_long_term(category: str) -> str:
        normalized = str(category or "").strip().lower()
        if normalized in {"profile", "entities"}:
            return "entity"
        if normalized in {"preferences", "patterns"}:
            return "topic"
        if normalized in {"events", "cases"}:
            return "event"
        return "topic"

    add_node("Gazer", "Gazer", "root")

    rels: Dict[str, Any] = {}

    # 1) OpenViking long-term memory.
    long_term_dir = backend_dir / "long_term"
    if long_term_dir.is_dir():
        for category_file in sorted(long_term_dir.glob("*.json")):
            category = category_file.stem
            payload = read_json_file(category_file)
            if not payload:
                continue
            for key, item in payload.items():
                if not isinstance(item, dict):
                    continue
                display_name = str(key or f"{category}_item").strip()[:80] or f"{category}_item"
                detail = str(item.get("content", "") or "").strip()
                nid = safe_node_id(f"lt:{category}:{key}")
                add_node(nid, display_name, group_for_long_term(category), detail[:400])
                add_link("Gazer", nid, category)

    # 2) OpenViking event stream.
    event_file = backend_dir / "memory_events.jsonl"
    try:
        lines = [line.strip() for line in event_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        lines = []
    for idx, line in enumerate(lines[-300:]):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        ts = str(item.get("timestamp", "") or "").strip()
        date_str = str(item.get("date", "") or ts[:10]).strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            date_str = "unknown"
        sender = str(item.get("sender", "unknown") or "unknown").strip() or "unknown"
        content = str(item.get("content", "") or "").strip()
        daily_nid = f"daily:{date_str}"
        add_node(daily_nid, date_str, "daily")
        add_link("Gazer", daily_nid, "daily")

        seed = f"{ts}|{sender}|{content[:96]}|{idx}"
        event_nid = safe_node_id(f"event:{date_str}:{seed}")
        title = f"{sender}@{ts[11:19]}" if len(ts) >= 19 else f"{sender}@{date_str}"
        add_node(event_nid, title, "event", content[:300])
        add_link(daily_nid, event_nid, sender)

    # 3) Relationship graph.
    rel_path = backend_dir / "RELATIONSHIPS.json"
    payload = read_json_file(rel_path)
    if payload:
        rels = payload
        for key, info in rels.items():
            if not isinstance(info, dict):
                continue
            name = str(info.get("name", key) or key).strip() or str(key)
            aliases = info.get("aliases", [])
            alias_list = aliases if isinstance(aliases, list) else []
            display = str(alias_list[0] if alias_list else name)
            content = (
                f"Relationship: {info.get('relationship', '?')}\n"
                f"Mentions: {info.get('mention_count', 0)}\n"
                f"Sentiment: {float(info.get('sentiment', 0) or 0):.2f}"
            )
            rel_nid = safe_node_id(f"rel:{name}")
            add_node(rel_nid, display, "entity", content)
            add_link("Gazer", rel_nid, str(info.get("relationship", "") or "relationship"))

    # 4) Emotions.
    emo_dir = backend_dir / "emotions"
    for emo_file in sorted(emo_dir.glob("*.json")):
        date_str = emo_file.stem
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            continue
        try:
            emo = json.loads(emo_file.read_text(encoding="utf-8"))
        except Exception:
            emo = {}
        mood = str(emo.get("overall_mood", "?") or "?")
        avg = float(emo.get("avg_sentiment", 0) or 0)
        msg_count = int(emo.get("message_count", 0) or 0)
        emo_nid = f"emotion:{date_str}"
        emo_content = f"Mood: {mood}\nSentiment: {avg:.2f}\nMessages: {msg_count}"
        add_node(emo_nid, f"{mood} ({date_str})", "emotion", emo_content)
        daily_nid = f"daily:{date_str}"
        if daily_nid in node_map:
            add_link(daily_nid, emo_nid, "mood")
        else:
            add_link("Gazer", emo_nid, "emotion")

    # 5) Cross-link relationships by last-mentioned date.
    for key, info in rels.items():
        if not isinstance(info, dict):
            continue
        name = str(info.get("name", key) or key).strip() or str(key)
        rel_nid = safe_node_id(f"rel:{name}")
        if rel_nid not in node_map:
            continue
        last = str(info.get("last_mentioned", "") or "").strip()
        if not last:
            continue
        date_part = last[:10]
        daily_nid = f"daily:{date_part}"
        if daily_nid in node_map:
            add_link(daily_nid, rel_nid, "mentioned")

    return {"nodes": list(node_map.values()), "links": links}

@app.get("/debug/persona-memory/joint-drift-report", dependencies=[Depends(verify_admin_token)])
async def get_persona_memory_joint_drift_report(window_days: int = 7, source: str = "persona_eval"):
    return _build_persona_memory_joint_drift_report(window_days=window_days, source=source)

@app.post("/debug/persona-memory/joint-drift-report/export", dependencies=[Depends(verify_admin_token)])
async def export_persona_memory_joint_drift_report(payload: Dict[str, Any]):
    window_days_raw = payload.get("window_days", 7)
    source = str(payload.get("source", "persona_eval")).strip() or "persona_eval"
    try:
        window_days = int(window_days_raw)
    except (TypeError, ValueError):
        window_days = 7
    report = _build_persona_memory_joint_drift_report(window_days=window_days, source=source)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str(payload.get("output_path", "")).strip(),
        default_filename=f"PERSONA_MEMORY_JOINT_DRIFT_{stamp}.md",
    )
    memory_current = report.get("memory", {}).get("current_window", {}) if isinstance(report.get("memory", {}), dict) else {}
    memory_drift = report.get("memory", {}).get("drift", {}) if isinstance(report.get("memory", {}), dict) else {}
    persona_trend = (
        report.get("persona", {}).get("trend", {}) if isinstance(report.get("persona", {}), dict) else {}
    )
    lines = [
        "# Persona + Memory Joint Drift Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_days: {report.get('window_days')}",
        f"- source: {report.get('source')}",
        f"- backend_dir: {report.get('backend_dir')}",
        "",
        "## Persona Trend",
        f"- direction: {persona_trend.get('direction', 'stable')}",
        f"- warning_delta: {persona_trend.get('warning_delta', 0)}",
        f"- critical_delta: {persona_trend.get('critical_delta', 0)}",
        f"- consistency_score_delta: {persona_trend.get('consistency_score_delta')}",
        "",
        "## Memory Drift",
        f"- event_total: {memory_current.get('event_total', 0)}",
        f"- extraction_total: {memory_current.get('extraction_total', 0)}",
        f"- yield_rate: {memory_current.get('yield_rate', 0)}",
        f"- churn_ratio: {memory_current.get('churn_ratio', 0)}",
        f"- duplicate_key_ratio: {memory_current.get('duplicate_key_ratio', 0)}",
        f"- drift_score: {memory_drift.get('score', 0)}",
        f"- drift_level: {memory_drift.get('level', 'healthy')}",
        "",
        "## Joint Assessment",
        f"- direction: {(report.get('joint', {}) or {}).get('direction', 'stable')}",
        f"- risk_level: {(report.get('joint', {}) or {}).get('risk_level', 'healthy')}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "report": report}

@app.get("/debug/memory/extraction-quality-report", dependencies=[Depends(verify_admin_token)])
async def get_memory_extraction_quality_report(window_days: int = 7):
    return _build_memory_extraction_quality_report(window_days=window_days)

@app.post("/debug/memory/extraction-quality-report/export", dependencies=[Depends(verify_admin_token)])
async def export_memory_extraction_quality_report(payload: Dict[str, Any]):
    window_days_raw = payload.get("window_days", 7)
    try:
        window_days = int(window_days_raw)
    except (TypeError, ValueError):
        window_days = 7
    report = _build_memory_extraction_quality_report(window_days=window_days)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str(payload.get("output_path", "")).strip(),
        default_filename=f"MEMORY_EXTRACTION_QUALITY_{stamp}.md",
    )
    current = report.get("current_window", {}) if isinstance(report.get("current_window"), dict) else {}
    trend = report.get("trend", {}) if isinstance(report.get("trend"), dict) else {}
    lines = [
        "# Memory Extraction Quality Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_days: {report.get('window_days')}",
        f"- backend_dir: {report.get('backend_dir')}",
        "",
        "## Current Window",
        f"- event_total: {current.get('event_total', 0)}",
        f"- high_value_attempts: {current.get('high_value_attempts', 0)}",
        f"- high_value_accepted: {current.get('high_value_accepted', 0)}",
        f"- high_value_enriched: {current.get('high_value_enriched', 0)}",
        f"- precision_proxy: {current.get('precision_proxy', 0)}",
        f"- recall_proxy: {current.get('recall_proxy', 0)}",
        f"- f1_proxy: {current.get('f1_proxy', 0)}",
        "",
        "## Trend",
        f"- direction: {trend.get('direction', 'stable')}",
        f"- quality_level: {trend.get('quality_level', 'healthy')}",
        f"- precision_proxy_delta: {trend.get('precision_proxy_delta', 0)}",
        f"- recall_proxy_delta: {trend.get('recall_proxy_delta', 0)}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "report": report}
