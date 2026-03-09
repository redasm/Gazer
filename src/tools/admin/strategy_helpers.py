"""Policy audit, strategy snapshot, MCP, and various helper functions extracted from _shared.py."""

from __future__ import annotations
import collections
import copy
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Request, HTTPException
from runtime.config_manager import config
import tools.admin.state as _state
from tools.admin.state import (
    _policy_audit_buffer,
    _strategy_change_history,
    _workflow_run_history,
    _mcp_rate_counts,
    _mcp_audit_buffer,
    _POLICY_AUDIT_LOG_PATH,
    _STRATEGY_SNAPSHOT_LOG_PATH,
    get_llm_router,
)
from tools.admin.utils import _append_jsonl_record, _read_jsonl_tail

logger = logging.getLogger('GazerAdminAPI')

# Constants
_MAX_CHAT_MESSAGE_CHARS = int(config.get("api.max_chat_message_chars", 8000))

# MCP rate-limit events per actor
_mcp_rate_events = collections.defaultdict(collections.deque)


def _mcp_actor(request: Optional[Request]) -> str:
    if request is None:
        return "direct"
    host = "unknown"
    try:
        host = str(request.client.host if request.client else "unknown").strip() or "unknown"
    except Exception:
        host = "unknown"
    return f"ip:{host}"

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

def _is_strategy_rollback_key_allowed(key: str) -> bool:
    allowed_prefixes = ("models.router.", "security.", "personality.")
    return any(key.startswith(p) for p in allowed_prefixes)

def _save_config_if_supported() -> None:
    if hasattr(config, "save"):
        try:
            config.save()
        except Exception:
            logger.warning("Failed to save config during strategy rollback", exc_info=True)

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
    llm_router = get_llm_router()
    if llm_router is not None:
        strategy = filtered_values.get("models.router.strategy")
        if strategy is not None and hasattr(llm_router, "set_strategy"):
            try:
                llm_router.set_strategy(str(strategy))
                router_updated = True
            except Exception:
                logger.debug("Failed to apply router strategy rollback", exc_info=True)
        budget_policy = filtered_values.get("models.router.budget")
        if isinstance(budget_policy, dict) and hasattr(llm_router, "set_budget_policy"):
            try:
                llm_router.set_budget_policy(dict(budget_policy))
                router_updated = True
            except Exception:
                logger.debug("Failed to apply router budget rollback", exc_info=True)
        outlier_policy = filtered_values.get("models.router.outlier_ejection")
        if isinstance(outlier_policy, dict) and hasattr(llm_router, "set_outlier_policy"):
            try:
                llm_router.set_outlier_policy(dict(outlier_policy))
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

def _get_satellite_node_config(node_id: str) -> dict:
    """Read satellite node config from devices.satellite.<node_id>"""
    satellites = config.get("devices.satellite", {})
    if isinstance(satellites, dict):
        node_cfg = satellites.get(node_id, {})
        if isinstance(node_cfg, dict):
            return node_cfg
    return {}

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

def _get_tool_governance_snapshot(limit: int = 50) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    registry = _state.get_tool_registry()
    if registry is None:
        return {
            "available": False,
            "budget": {},
            "recent_rejections": [],
        }

    budget: Dict[str, Any] = {}
    recent_rejections: List[Dict[str, Any]] = []
    if hasattr(registry, "get_budget_runtime_status"):
        try:
            budget = registry.get_budget_runtime_status()
        except Exception:
            logger.debug("Failed to read tool budget runtime status", exc_info=True)
    if hasattr(registry, "get_recent_rejection_events"):
        try:
            recent_rejections = registry.get_recent_rejection_events(limit=safe_limit)
        except Exception:
            logger.debug("Failed to read tool rejection events", exc_info=True)

    return {
        "available": True,
        "budget": budget,
        "recent_rejections": recent_rejections,
    }

def _enqueue_chat_message(*, content: str, session_id: str, source: str, sender_id: str = "owner") -> None:
    if _state.API_QUEUES["input"] is None:
        raise HTTPException(status_code=503, detail="Brain disconnected")
    text = str(content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    if len(text) > _MAX_CHAT_MESSAGE_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Message too long (max {_MAX_CHAT_MESSAGE_CHARS} characters)",
        )
    _state.API_QUEUES["input"].put_nowait(
        {
            "type": "chat",
            "content": text,
            "source": source,
            "chat_id": str(session_id),
            "sender_id": str(sender_id),
        }
    )

