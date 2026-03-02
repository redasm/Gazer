from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List, Optional
import time
from tools.admin._shared import _log_buffer, _policy_audit_buffer, _strategy_change_history, _mcp_audit_buffer, _dedupe_dict_rows, _read_jsonl_tail, _POLICY_AUDIT_LOG_PATH, _STRATEGY_SNAPSHOT_LOG_PATH, config
from tools.admin.auth import verify_admin_token
from tools.admin._shared import _find_strategy_snapshot, _apply_strategy_snapshot, _append_policy_audit

app = APIRouter()



@app.get("/logs", dependencies=[Depends(verify_admin_token)])
async def get_logs(limit: int = 200, level: Optional[str] = None, source: Optional[str] = None):
    """Get recent logs from the in-memory buffer."""
    logs = list(_log_buffer)
    if level:
        logs = [l for l in logs if l["level"].upper() == level.upper()]
    if source:
        logs = [l for l in logs if source.lower() in l["source"].lower()]
    return {"logs": logs[-limit:], "total": len(logs)}

@app.delete("/logs", dependencies=[Depends(verify_admin_token)])
async def clear_logs():
    """Clear the log buffer."""
    _log_buffer.clear()
    return {"status": "success", "message": "Logs cleared"}

@app.get("/policy/audit", dependencies=[Depends(verify_admin_token)])
async def get_policy_audit(limit: int = 100, action: Optional[str] = None):
    """Get recent policy/router audit events."""
    safe_limit = max(1, min(int(limit), 2000))
    entries = _dedupe_dict_rows(
        _read_jsonl_tail(_POLICY_AUDIT_LOG_PATH, limit=safe_limit * 5) + list(_policy_audit_buffer),
    )
    if action:
        normalized = action.strip().lower()
        entries = [item for item in entries if str(item.get("action", "")).lower() == normalized]
    return {"entries": entries[-safe_limit:], "total": len(entries)}

@app.delete("/policy/audit", dependencies=[Depends(verify_admin_token)])
async def clear_policy_audit():
    """Clear policy/router audit events."""
    if not bool(config.get("api.allow_audit_buffer_clear", False)):
        raise HTTPException(status_code=403, detail="Audit clear is disabled; use retention/archive policy")
    _policy_audit_buffer.clear()
    return {"status": "success", "message": "Policy audit cleared"}

@app.get("/governance/strategy/snapshots", dependencies=[Depends(verify_admin_token)])
async def list_strategy_change_snapshots(limit: int = 50, category: Optional[str] = None):
    safe_limit = max(1, min(int(limit), 500))
    items = _dedupe_dict_rows(
        _read_jsonl_tail(_STRATEGY_SNAPSHOT_LOG_PATH, limit=safe_limit * 4) + list(_strategy_change_history),
        id_keys=["snapshot_id"],
    )
    if category:
        marker = str(category).strip().lower()
        items = [item for item in items if str(item.get("category", "")).strip().lower() == marker]
    return {"status": "ok", "items": items[-safe_limit:], "total": len(items)}

@app.post("/governance/strategy/rollback", dependencies=[Depends(verify_admin_token)])
async def rollback_strategy_change(payload: Dict[str, Any]):
    snapshot_id = str(payload.get("snapshot_id", "")).strip()
    latest = bool(payload.get("latest", not snapshot_id))
    mode = str(payload.get("mode", "rollback")).strip().lower() or "rollback"
    if mode not in {"rollback", "apply"}:
        raise HTTPException(status_code=400, detail="'mode' must be 'rollback' or 'apply'")
    actor = str(payload.get("actor", "admin")).strip() or "admin"

    snapshot: Optional[Dict[str, Any]] = None
    if snapshot_id:
        snapshot = _find_strategy_snapshot(snapshot_id)
    elif latest:
        history = _dedupe_dict_rows(
            _read_jsonl_tail(_STRATEGY_SNAPSHOT_LOG_PATH, limit=2000) + list(_strategy_change_history),
            id_keys=["snapshot_id"],
        )
        if history:
            snapshot = dict(history[-1])
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Strategy snapshot not found")

    try:
        result = _apply_strategy_snapshot(snapshot, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _append_policy_audit(
        action="strategy.rollback.applied",
        details={
            "snapshot_id": snapshot.get("snapshot_id", ""),
            "category": snapshot.get("category", ""),
            "mode": mode,
            "actor": actor,
            "applied_keys": result.get("applied_keys", []),
        },
    )
    return {"status": "ok", "snapshot": snapshot, "result": result}

