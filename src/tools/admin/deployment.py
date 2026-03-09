from __future__ import annotations

"""Deployment management router — model providers, deployment targets, orchestrator.

Routes:
  - /model-providers CRUD
  - /llm/deployment-targets CRUD + status + health
  - /providers/deployment-orchestrator/* (status, policy, apply, reconcile, failover, rollback)
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from tools.admin.state import (
    config, logger,
    get_provider_registry, get_deployment_orchestrator, get_llm_router,
)
from tools.admin.validation import _validate_provider_entry, _validate_deployment_target_entry
from .auth import verify_admin_token

router = APIRouter(tags=["deployment"])


# ---------------------------------------------------------------------------
# Model provider CRUD
# ---------------------------------------------------------------------------

@router.get("/model-providers", dependencies=[Depends(verify_admin_token)])
async def list_model_providers():
    registry = get_provider_registry()
    return {"status": "ok", "providers": registry.list_redacted_providers()}


@router.post("/model-providers", dependencies=[Depends(verify_admin_token)])
async def create_model_provider(payload: Dict[str, Any]):
    name = str(payload.get("name", "")).strip()
    provider = payload.get("provider", {})
    validated = _validate_provider_entry(name, provider)
    registry = get_provider_registry()
    if registry.get_provider(name):
        raise HTTPException(status_code=409, detail=f"Provider '{name}' already exists")
    registry.upsert_provider(name, validated)
    return {"status": "ok", "name": name}


@router.put("/model-providers/{provider_name}", dependencies=[Depends(verify_admin_token)])
async def update_model_provider(provider_name: str, payload: Dict[str, Any]):
    name = str(provider_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="provider_name is required")
    provider = payload.get("provider", payload)
    validated = _validate_provider_entry(name, provider)
    registry = get_provider_registry()
    if not registry.get_provider(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    registry.upsert_provider(name, validated)
    return {"status": "ok", "name": name}


@router.delete("/model-providers/{provider_name}", dependencies=[Depends(verify_admin_token)])
async def delete_model_provider(provider_name: str):
    name = str(provider_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="provider_name is required")
    registry = get_provider_registry()
    if not registry.delete_provider(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {"status": "ok", "name": name}


# ---------------------------------------------------------------------------
# Deployment targets
# ---------------------------------------------------------------------------

@router.get("/llm/deployment-targets", dependencies=[Depends(verify_admin_token)])
async def list_deployment_targets():
    registry = get_provider_registry()
    if hasattr(registry, "list_redacted_deployment_targets"):
        targets = registry.list_redacted_deployment_targets()
    else:
        targets = {}
    return {"status": "ok", "targets": targets}


@router.post("/llm/deployment-targets", dependencies=[Depends(verify_admin_token)])
async def create_deployment_target(payload: Dict[str, Any]):
    target_id = str(payload.get("target_id", "")).strip()
    target = payload.get("target", payload)
    validated = _validate_deployment_target_entry(target_id, target)
    registry = get_provider_registry()
    if not hasattr(registry, "upsert_deployment_target"):
        raise HTTPException(status_code=501, detail="deployment targets are not supported by current registry")
    if hasattr(registry, "get_deployment_target") and registry.get_deployment_target(target_id):
        raise HTTPException(status_code=409, detail=f"Deployment target '{target_id}' already exists")
    registry.upsert_deployment_target(target_id, validated)
    return {"status": "ok", "target_id": target_id}


@router.get("/llm/deployment-targets/status", dependencies=[Depends(verify_admin_token)])
async def get_deployment_targets_status():
    registry = get_provider_registry()
    if hasattr(registry, "list_redacted_deployment_targets"):
        targets = registry.list_redacted_deployment_targets()
    else:
        targets = {}
    enabled_count = 0
    if isinstance(targets, dict):
        enabled_count = sum(1 for item in targets.values() if bool((item or {}).get("enabled", True)))

    router_status: Dict[str, Any] = {}
    if get_llm_router() is not None and hasattr(get_llm_router(), "get_status"):
        try:
            router_status = get_llm_router().get_status()
        except Exception:
            logger.debug("Failed to read router status for deployment targets", exc_info=True)

    return {
        "status": "ok",
        "targets": targets,
        "enabled_targets": int(enabled_count),
        "router_enabled": bool(get_llm_router() is not None),
        "router": router_status,
    }


@router.get("/llm/deployment-targets/health", dependencies=[Depends(verify_admin_token)])
async def probe_deployment_targets(active: bool = False, timeout_seconds: float = 3.0):
    if get_llm_router() is None or not hasattr(get_llm_router(), "probe_routes"):
        return {"status": "ok", "active": bool(active), "probes": [], "note": "LLM router not enabled"}
    probes = await get_llm_router().probe_routes(active=bool(active), timeout_seconds=float(timeout_seconds))
    return {"status": "ok", "active": bool(active), "probes": probes}


@router.put("/llm/deployment-targets/{target_id}", dependencies=[Depends(verify_admin_token)])
async def update_deployment_target(target_id: str, payload: Dict[str, Any]):
    clean_target_id = str(target_id or "").strip()
    if not clean_target_id:
        raise HTTPException(status_code=400, detail="target_id is required")
    target = payload.get("target", payload)
    validated = _validate_deployment_target_entry(clean_target_id, target)
    registry = get_provider_registry()
    if not hasattr(registry, "upsert_deployment_target"):
        raise HTTPException(status_code=501, detail="deployment targets are not supported by current registry")
    if hasattr(registry, "get_deployment_target") and not registry.get_deployment_target(clean_target_id):
        raise HTTPException(status_code=404, detail=f"Deployment target '{clean_target_id}' not found")
    registry.upsert_deployment_target(clean_target_id, validated)
    return {"status": "ok", "target_id": clean_target_id}


@router.delete("/llm/deployment-targets/{target_id}", dependencies=[Depends(verify_admin_token)])
async def delete_deployment_target(target_id: str):
    clean_target_id = str(target_id or "").strip()
    if not clean_target_id:
        raise HTTPException(status_code=400, detail="target_id is required")
    registry = get_provider_registry()
    if not hasattr(registry, "delete_deployment_target"):
        raise HTTPException(status_code=501, detail="deployment targets are not supported by current registry")
    if not registry.delete_deployment_target(clean_target_id):
        raise HTTPException(status_code=404, detail=f"Deployment target '{clean_target_id}' not found")
    return {"status": "ok", "target_id": clean_target_id}


# ---------------------------------------------------------------------------
# Live router sync helper
# ---------------------------------------------------------------------------

def _sync_live_router_from_deployment_targets() -> None:
    """Best-effort live sync for LLM router route flags/weights."""
    if get_llm_router() is None:
        return
    routes = getattr(get_llm_router(), "_routes", None)
    if not isinstance(routes, list):
        return
    try:
        target_map = get_provider_registry().list_deployment_targets()
    except Exception:
        target_map = {}
    if not isinstance(target_map, dict):
        target_map = {}
    for route in routes:
        route_name = str(getattr(route, "name", "") or "").strip()
        if not route_name:
            continue
        target_cfg = target_map.get(route_name, {})
        if not isinstance(target_cfg, dict):
            continue
        setattr(route, "enabled", bool(target_cfg.get("enabled", True)))
        try:
            weight = float(target_cfg.get("traffic_weight", getattr(route, "traffic_weight", 1.0)) or 1.0)
        except (TypeError, ValueError):
            weight = 1.0
        setattr(route, "traffic_weight", max(0.01, weight))
    if hasattr(get_llm_router(), "set_strategy"):
        try:
            get_llm_router().set_strategy(str(config.get("models.router.strategy", "priority") or "priority"))
        except Exception:
            logger.debug("Failed to sync live router strategy from config", exc_info=True)


# ---------------------------------------------------------------------------
# Deployment orchestrator
# ---------------------------------------------------------------------------

@router.get("/providers/deployment-orchestrator/status", dependencies=[Depends(verify_admin_token)])
async def get_deployment_orchestrator_status():
    orchestrator = get_deployment_orchestrator()
    return {"status": "ok", **orchestrator.get_status()}


@router.post("/providers/deployment-orchestrator/policy", dependencies=[Depends(verify_admin_token)])
async def update_deployment_orchestrator_policy(payload: Dict[str, Any]):
    orchestrator = get_deployment_orchestrator()
    policy_patch = payload.get("policy", payload)
    if not isinstance(policy_patch, dict):
        raise HTTPException(status_code=400, detail="'policy' must be an object")
    try:
        updated = orchestrator.update_policy(policy_patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    out: Dict[str, Any] = {"status": "ok", "policy": updated}
    if bool(payload.get("apply", False)):
        out["applied"] = orchestrator.apply_policy(
            reason=str(payload.get("reason", "policy_apply")).strip() or "policy_apply"
        )
        _sync_live_router_from_deployment_targets()
    return out


@router.post("/providers/deployment-orchestrator/apply", dependencies=[Depends(verify_admin_token)])
async def apply_deployment_orchestrator_policy(payload: Dict[str, Any]):
    orchestrator = get_deployment_orchestrator()
    reason = str(payload.get("reason", "manual_apply")).strip() or "manual_apply"
    policy_patch = payload.get("policy")
    if isinstance(policy_patch, dict):
        orchestrator.update_policy(policy_patch)
    status_payload = orchestrator.apply_policy(reason=reason)
    _sync_live_router_from_deployment_targets()
    return {"status": "ok", **status_payload}


@router.post("/providers/deployment-orchestrator/reconcile", dependencies=[Depends(verify_admin_token)])
async def reconcile_deployment_orchestrator(payload: Dict[str, Any]):
    orchestrator = get_deployment_orchestrator()
    probes = payload.get("probes", None)
    if probes is None:
        if get_llm_router() is not None and hasattr(get_llm_router(), "probe_routes"):
            active = bool(payload.get("active", False))
            timeout_seconds = float(payload.get("timeout_seconds", 3.0) or 3.0)
            probes = await get_llm_router().probe_routes(active=active, timeout_seconds=timeout_seconds)
        else:
            probes = []
    if not isinstance(probes, list):
        raise HTTPException(status_code=400, detail="'probes' must be an array")
    status_payload = orchestrator.reconcile(probes)
    _sync_live_router_from_deployment_targets()
    return {"status": "ok", **status_payload}


@router.post("/providers/deployment-orchestrator/failover", dependencies=[Depends(verify_admin_token)])
async def deployment_orchestrator_failover(payload: Dict[str, Any]):
    orchestrator = get_deployment_orchestrator()
    target_id = str(payload.get("target_id", "")).strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="'target_id' is required")
    try:
        status_payload = orchestrator.failover(
            target_id=target_id,
            reason=str(payload.get("reason", "manual_failover")).strip() or "manual_failover",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _sync_live_router_from_deployment_targets()
    return {"status": "ok", **status_payload}


@router.post("/providers/deployment-orchestrator/rollback", dependencies=[Depends(verify_admin_token)])
async def deployment_orchestrator_rollback(payload: Dict[str, Any]):
    orchestrator = get_deployment_orchestrator()
    status_payload = orchestrator.rollback(
        reason=str(payload.get("reason", "manual_rollback")).strip() or "manual_rollback"
    )
    _sync_live_router_from_deployment_targets()
    return {"status": "ok", **status_payload}
