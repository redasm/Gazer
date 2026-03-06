from __future__ import annotations

"""Tool-policy management router — explain, simulate, effective policy.

Routes:
  - POST /policy/explain
  - POST /policy/simulate
  - GET  /policy/effective
  - GET/POST /debug/agents-md/effective
  - POST /debug/agents-md/lint
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ._shared import (
    config, logger,
    _PROJECT_ROOT,
    _is_subpath,
    TOOL_REGISTRY,
)
from .auth import verify_admin_token
from .config_routes import (
    _resolve_global_policy,
    _policy_to_payload,
    _merge_policy_names,
    _detect_policy_conflicts,
    _resolve_agents_overlay_policy,
    _normalize_str_list,
)

router = APIRouter(tags=["policy"])

try:
    from tools.registry import ToolPolicy, normalize_tool_policy
except ImportError:
    ToolPolicy = None  # type: ignore
    normalize_tool_policy = None  # type: ignore

try:
    from tools.agents_overlay import lint_agents_overlay
except ImportError:
    lint_agents_overlay = None  # type: ignore


# ---------------------------------------------------------------------------
# Debug: agents-md overlay
# ---------------------------------------------------------------------------

@router.get("/debug/agents-md/effective", dependencies=[Depends(verify_admin_token)])
async def get_agents_md_effective(agents_target_dir: Optional[str] = None):
    overlay = _resolve_agents_overlay_policy(agents_target_dir, include_debug=True)
    return {
        "status": "ok",
        "target_dir": overlay.get("target_dir", "."),
        "files": overlay.get("files", []),
        "skill_priority": overlay.get("skill_priority", []),
        "allowed_tools": overlay.get("allowed_tools", []),
        "deny_tools": overlay.get("deny_tools", []),
        "routing_hints": overlay.get("routing_hints", []),
        "conflicts": overlay.get("conflicts", []),
        "combined_text": overlay.get("combined_text", ""),
        "debug": overlay.get("debug", []),
    }


@router.post("/debug/agents-md/effective", dependencies=[Depends(verify_admin_token)])
async def post_agents_md_effective(payload: Dict[str, Any]):
    target = str((payload or {}).get("agents_target_dir", "")).strip() or None
    return await get_agents_md_effective(target)


@router.post("/debug/agents-md/lint", dependencies=[Depends(verify_admin_token)])
async def run_agents_md_lint(payload: Dict[str, Any]):
    target_rel = str((payload or {}).get("agents_target_dir", "")).strip()
    target = _PROJECT_ROOT
    if target_rel:
        candidate = (_PROJECT_ROOT / target_rel).resolve()
        if not _is_subpath(_PROJECT_ROOT, candidate):
            raise HTTPException(status_code=400, detail="'agents_target_dir' must stay inside workspace")
        target = candidate
    report = lint_agents_overlay(_PROJECT_ROOT, target)
    return report


# ---------------------------------------------------------------------------
# Policy routes
# ---------------------------------------------------------------------------

@router.post("/policy/explain", dependencies=[Depends(verify_admin_token)])
async def explain_policy(payload: Dict[str, Any]):
    """Explain why a tool is allowed/blocked under current policy inputs."""
    if TOOL_REGISTRY is None:
        raise HTTPException(status_code=503, detail="Tool registry not available")

    tool_name = str(payload.get("tool_name", "")).strip()
    if not tool_name:
        raise HTTPException(status_code=400, detail="'tool_name' is required")

    model_provider = str(payload.get("model_provider", "")).strip().lower()
    model_name = str(payload.get("model_name", "")).strip().lower()
    groups = config.get("security.tool_groups", {})
    safe_groups = groups if isinstance(groups, dict) else {}

    request_policy_raw = payload.get("policy")
    resolved_policy_raw: Dict[str, Any] = request_policy_raw if isinstance(request_policy_raw, dict) else {}

    directory_overlay = _resolve_agents_overlay_policy(
        str(payload.get("agents_target_dir", "")).strip() or None
    )
    directory_policy_raw = {
        "allow_names": directory_overlay.get("allowed_tools", []),
        "deny_names": directory_overlay.get("deny_tools", []),
    }
    global_policy = normalize_tool_policy(_resolve_global_policy(), safe_groups)
    request_policy = normalize_tool_policy(resolved_policy_raw, safe_groups)
    directory_policy = normalize_tool_policy(directory_policy_raw, safe_groups)
    effective_base_policy = request_policy if isinstance(request_policy_raw, dict) else global_policy
    effective_policy = _merge_policy_names(
        effective_base_policy,
        allow_names=set(directory_policy.allow_names),
        deny_names=set(directory_policy.deny_names),
    )
    conflicts = _detect_policy_conflicts({
        "global": global_policy,
        "directory": directory_policy,
        "request": request_policy,
    })
    overlay_conflicts = directory_overlay.get("conflicts", [])
    if isinstance(overlay_conflicts, list) and overlay_conflicts:
        conflicts.extend(overlay_conflicts)

    result = TOOL_REGISTRY.evaluate_tool_access(
        tool_name,
        policy=effective_policy,
        model_provider=model_provider,
        model_name=model_name,
    )
    return {
        "status": "ok",
        "result": result,
        "explain": {
            "tool_name": tool_name,
            "model_context": {
                "provider": model_provider,
                "model": model_name,
                "available": bool(model_provider and model_name),
            },
            "layers": {
                "global": _policy_to_payload(global_policy),
                "directory": {
                    "target_dir": directory_overlay.get("target_dir", "."),
                    "policy": _policy_to_payload(directory_policy),
                    "routing_hints": directory_overlay.get("routing_hints", []),
                },
                "request": _policy_to_payload(request_policy),
                "effective": _policy_to_payload(effective_policy),
            },
            "conflicts": conflicts,
            "rule_chain": result.get("rule_chain", []),
        },
    }


@router.post("/policy/simulate", dependencies=[Depends(verify_admin_token)])
async def simulate_policy(payload: Dict[str, Any]):
    """Simulate access outcomes for all (or selected) tools under a policy."""
    if TOOL_REGISTRY is None:
        raise HTTPException(status_code=503, detail="Tool registry not available")

    model_provider = str(payload.get("model_provider", "")).strip().lower()
    model_name = str(payload.get("model_name", "")).strip().lower()
    policy_raw = payload.get("policy")
    groups = config.get("security.tool_groups", {})
    policy = normalize_tool_policy(policy_raw or {}, groups if isinstance(groups, dict) else {})
    names_raw = payload.get("tool_names")
    names = [str(item).strip() for item in names_raw] if isinstance(names_raw, list) else None
    results = TOOL_REGISTRY.simulate_access(
        policy=policy,
        names=names,
        model_provider=model_provider,
        model_name=model_name,
    )
    return {
        "status": "ok",
        "count": len(results),
        "results": results,
        "model_context": {
            "provider": model_provider,
            "model": model_name,
            "available": bool(model_provider and model_name),
        },
    }


@router.get("/policy/effective", dependencies=[Depends(verify_admin_token)])
async def get_effective_policy(
    agents_target_dir: Optional[str] = None,
    tool_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    model_name: Optional[str] = None,
):
    """Return current global policy with optional directory overlay preview."""
    groups = config.get("security.tool_groups", {})
    safe_groups = groups if isinstance(groups, dict) else {}
    global_policy = normalize_tool_policy(_resolve_global_policy(), safe_groups)
    directory_overlay = _resolve_agents_overlay_policy(agents_target_dir)
    directory_policy = normalize_tool_policy(
        {
            "allow_names": directory_overlay.get("allowed_tools", []),
            "deny_names": directory_overlay.get("deny_tools", []),
        },
        safe_groups,
    )

    result: Dict[str, Any] = {
        "status": "ok",
        "global": {
            "owner_only_tools": sum(1 for t in TOOL_REGISTRY._tools.values() if t.owner_only) if TOOL_REGISTRY else 0,
            "group_count": len(safe_groups),
            "groups": sorted(safe_groups.keys()),
            "policy": _policy_to_payload(global_policy),
        },
        "tool_policy_v3": {
            "enabled": True,
            "dimensions": [
                "tool_name", "tool_provider",
                "model_provider", "model_name", "model_selector",
            ],
        },
        "directory": {
            "target_dir": directory_overlay.get("target_dir", "."),
            "files": directory_overlay.get("files", []),
            "routing_hints": directory_overlay.get("routing_hints", []),
            "policy": _policy_to_payload(directory_policy),
            "overlay_conflicts": directory_overlay.get("conflicts", []),
        },
    }
    layers_for_conflicts: Dict[str, "ToolPolicy"] = {"global": global_policy, "directory": directory_policy}
    conflicts = _detect_policy_conflicts(layers_for_conflicts)
    overlay_conflicts = directory_overlay.get("conflicts", [])
    if isinstance(overlay_conflicts, list) and overlay_conflicts:
        conflicts.extend(overlay_conflicts)
    result["conflicts"] = conflicts
    if tool_name:
        merged_effective = _merge_policy_names(
            global_policy,
            allow_names=set(directory_policy.allow_names),
            deny_names=set(directory_policy.deny_names),
        )
        if TOOL_REGISTRY is not None:
            decision = TOOL_REGISTRY.evaluate_tool_access(
                str(tool_name),
                policy=merged_effective,
                model_provider=str(model_provider or "").strip().lower(),
                model_name=str(model_name or "").strip().lower(),
            )
            result["preview"] = {
                "tool_name": str(tool_name),
                "model_context": {
                    "provider": str(model_provider or "").strip().lower(),
                    "model": str(model_name or "").strip().lower(),
                    "available": bool(str(model_provider or "").strip() and str(model_name or "").strip()),
                },
                "effective_policy": _policy_to_payload(merged_effective),
                "decision": decision,
            }
    return result
