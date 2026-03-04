from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Request, WebSocket
from typing import Dict, Any, List, Optional
import time
import json
import collections
import contextvars
import logging
from tools.admin._shared import (
    config, TOOL_REGISTRY, CANVAS_STATE, _mcp_rate_counts, _mcp_audit_buffer, _mcp_request_ctx,
    LLM_ROUTER, TRAINING_JOB_MANAGER, EVAL_BENCHMARK_MANAGER,
    _mcp_actor, _mcp_rate_limit_check, _mcp_response_error, _mcp_response_ok, _mcp_text_resource,
    _summarize_training_output
)

def _get_memory_manager():
    from tools.admin.memory import _get_memory_manager as _impl
    return _impl()

def _get_eval_benchmark_manager():
    from tools.admin.observability import _get_eval_benchmark_manager as _impl
    return _impl()

def _get_training_job_manager():
    from tools.admin.observability import _get_training_job_manager as _impl
    return _impl()

def _build_workflow_observability_metrics(limit: int = 200):
    from tools.admin.system import _build_workflow_observability_metrics as _impl
    return _impl(limit=limit)
from tools.admin.auth import verify_admin_token
from tools.admin.auth import _verify_ws_auth, _extract_ws_token
from tools.registry import ToolSafetyTier
from urllib.parse import urlparse, parse_qs
from ._shared import _redact_config

app = APIRouter()
logger = logging.getLogger('mcp')

def _mcp_policy() -> Dict[str, Any]:
    raw = config.get("api.mcp", {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        max_requests = max(1, int(raw.get("rate_limit_requests", 120) or 120))
    except (TypeError, ValueError):
        max_requests = 120
    try:
        window_seconds = max(1, int(raw.get("rate_limit_window_seconds", 60) or 60))
    except (TypeError, ValueError):
        window_seconds = 60
    try:
        audit_retain = max(50, min(int(raw.get("audit_retain", 500) or 500), 5000))
    except (TypeError, ValueError):
        audit_retain = 500
    allowed_resource_prefixes = [
        str(item).strip()
        for item in (raw.get("allowed_resource_prefixes", []) or [])
        if str(item).strip()
    ]
    allowed_prompt_names = [
        str(item).strip().lower()
        for item in (raw.get("allowed_prompt_names", []) or [])
        if str(item).strip()
    ]
    return {
        "enabled": bool(raw.get("enabled", True)),
        "rate_limit_requests": max_requests,
        "rate_limit_window_seconds": window_seconds,
        "allow_tools": bool(raw.get("allow_tools", True)),
        "allow_resources": bool(raw.get("allow_resources", True)),
        "allow_prompts": bool(raw.get("allow_prompts", True)),
        "allowed_resource_prefixes": allowed_resource_prefixes,
        "allowed_prompt_names": allowed_prompt_names,
        "audit_retain": audit_retain,
    }

def _mcp_method_group(method: str) -> str:
    key = str(method or "").strip().lower()
    if key.startswith("tools/"):
        return "tools"
    if key.startswith("resources/"):
        return "resources"
    if key.startswith("prompts/"):
        return "prompts"
    return "core"

def _mcp_resource_allowed(uri: str, policy: Dict[str, Any]) -> bool:
    prefixes = list(policy.get("allowed_resource_prefixes", []) or [])
    if not prefixes:
        return True
    raw_uri = str(uri or "").strip()
    return any(raw_uri.startswith(prefix) for prefix in prefixes)

def _mcp_prompt_allowed(name: str, policy: Dict[str, Any]) -> bool:
    allow_names = list(policy.get("allowed_prompt_names", []) or [])
    if not allow_names:
        return True
    return str(name or "").strip().lower() in set(allow_names)

@app.post("/mcp", dependencies=[Depends(verify_admin_token)])
@app.post("/mcp/", dependencies=[Depends(verify_admin_token)])
async def mcp_jsonrpc(payload: Dict[str, Any], request: Request = None):
    """Minimal MCP-compatible JSON-RPC endpoint for tool discovery and invocation."""
    if not isinstance(payload, dict):
        return _mcp_response_error(None, -32600, "Invalid Request")

    method = str(payload.get("method", "")).strip()
    request_id = payload.get("id")
    params = payload.get("params", {}) if isinstance(payload.get("params"), dict) else {}
    method_group = _mcp_method_group(method)
    policy = _mcp_policy()
    actor = _mcp_actor(request)
    _mcp_request_ctx.set(
        {
            "started_at": time.perf_counter(),
            "actor": actor,
            "request_id": request_id,
            "method": method,
            "group": method_group,
            "tool": str(params.get("name", "")).strip() if method == "tools/call" else "",
            "resource_uri": str(params.get("uri", "")).strip() if method == "resources/read" else "",
            "prompt_name": str(params.get("name", "")).strip().lower() if method == "prompts/get" else "",
        }
    )

    if not method:
        return _mcp_response_error(request_id, -32600, "Invalid Request: missing method")

    if not bool(policy.get("enabled", True)):
        return _mcp_response_error(
            request_id,
            -32030,
            "MCP endpoint disabled by policy",
        )

    allowed, retry_after = _mcp_rate_limit_check(actor, policy)
    if not allowed:
        return _mcp_response_error(
            request_id,
            -32029,
            "MCP rate limit exceeded",
            {"retry_after_seconds": int(retry_after)},
        )

    if method_group == "tools" and not bool(policy.get("allow_tools", True)):
        return _mcp_response_error(request_id, -32010, "MCP access denied: tools are disabled")
    if method_group == "resources" and not bool(policy.get("allow_resources", True)):
        return _mcp_response_error(request_id, -32010, "MCP access denied: resources are disabled")
    if method_group == "prompts" and not bool(policy.get("allow_prompts", True)):
        return _mcp_response_error(request_id, -32010, "MCP access denied: prompts are disabled")

    if method == "initialize":
        return _mcp_response_ok(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "gazer-admin-mcp", "version": "0.1"},
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
            },
        )

    if method in {"ping", "notifications/initialized"}:
        return _mcp_response_ok(request_id, {})

    if method == "tools/list":
        if TOOL_REGISTRY is None:
            return _mcp_response_error(request_id, -32000, "Tool registry unavailable")
        definitions = TOOL_REGISTRY.get_definitions(max_tier=ToolSafetyTier.PRIVILEGED)
        tools: List[Dict[str, Any]] = []
        for item in definitions:
            fn = item.get("function", {}) if isinstance(item, dict) else {}
            tools.append(
                {
                    "name": str(fn.get("name", "")),
                    "description": str(fn.get("description", "")),
                    "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return _mcp_response_ok(request_id, {"tools": tools})

    if method == "tools/call":
        if TOOL_REGISTRY is None:
            return _mcp_response_error(request_id, -32000, "Tool registry unavailable")
        tool_name = str(params.get("name", "")).strip()
        arguments = params.get("arguments", {})
        if not tool_name:
            return _mcp_response_error(request_id, -32602, "Invalid params: missing tool name")
        if not isinstance(arguments, dict):
            return _mcp_response_error(request_id, -32602, "Invalid params: arguments must be object")
        result = await TOOL_REGISTRY.execute(
            tool_name,
            arguments,
            max_tier=ToolSafetyTier.PRIVILEGED,
        )
        is_error = str(result).startswith("Error")
        return _mcp_response_ok(
            request_id,
            {
                "content": [{"type": "text", "text": str(result)}],
                "isError": bool(is_error),
            },
        )

    if method == "resources/list":
        resources = [
            {
                "uri": "gazer://config/safe",
                "name": "safe_config",
                "description": "Redacted runtime config snapshot.",
                "mimeType": "application/json",
            },
            {
                "uri": "gazer://llm/router/status",
                "name": "llm_router_status",
                "description": "Current LLM router strategy and provider stats.",
                "mimeType": "application/json",
            },
            {
                "uri": "gazer://memory/recent?limit=20",
                "name": "recent_memory",
                "description": "Recent memory entries for context/debug.",
                "mimeType": "application/json",
            },
            {
                "uri": "gazer://eval/benchmark/latest?dataset_id=<id>&include_compare=true&baseline_index=1&include_workflow=true",
                "name": "eval_benchmark_latest",
                "description": "Latest benchmark run report. Supports compare and workflow observability summary.",
                "mimeType": "application/json",
            },
            {
                "uri": "gazer://eval/gate/status?include_streak=true&include_resolved_tasks=true&streak_limit=10&dataset_id=<id>&include_workflow=true",
                "name": "eval_gate_status",
                "description": "Current release gate status with streak/task summary and workflow observability.",
                "mimeType": "application/json",
            },
            {
                "uri": "gazer://eval/trainer/latest?status=completed&include_workflow=true",
                "name": "eval_trainer_latest",
                "description": "Latest training job summary with optional workflow observability summary.",
                "mimeType": "application/json",
            },
        ]
        resources = [
            item
            for item in resources
            if _mcp_resource_allowed(str((item or {}).get("uri", "")), policy)
        ]
        return _mcp_response_ok(request_id, {"resources": resources})

    if method == "resources/read":
        uri = str(params.get("uri", "")).strip()
        if not uri:
            return _mcp_response_error(request_id, -32602, "Invalid params: missing resource uri")
        if not _mcp_resource_allowed(uri, policy):
            return _mcp_response_error(
                request_id,
                -32010,
                "MCP access denied: resource uri not allowed",
                {"uri": uri},
            )

        parsed = urlparse(uri)
        if parsed.scheme != "gazer":
            return _mcp_response_error(request_id, -32002, "Unsupported resource uri", {"uri": uri})
        query_map = parse_qs(parsed.query or "")

        host = parsed.netloc.strip().lower()
        path = parsed.path.strip("/").lower()
        key = f"{host}/{path}".strip("/")

        if key == "config/safe":
            safe_config = _redact_config(getattr(config, "data", {}))
            return _mcp_response_ok(
                request_id,
                {"contents": [_mcp_text_resource(uri, "safe_config", safe_config)]},
            )

        if key == "llm/router/status":
            status = {"enabled": False, "strategy": "none", "providers": []}
            if LLM_ROUTER is not None and hasattr(LLM_ROUTER, "get_status"):
                status = dict(LLM_ROUTER.get_status() or {})
                status["enabled"] = True
            return _mcp_response_ok(
                request_id,
                {"contents": [_mcp_text_resource(uri, "llm_router_status", status)]},
            )

        if key == "memory/recent":
            query = params.get("query", {})
            query_limit = None
            if isinstance(query, dict) and "limit" in query:
                query_limit = query.get("limit")
            if query_limit is None and query_map.get("limit"):
                query_limit = query_map["limit"][0]
            if query_limit is None:
                query_limit = params.get("limit")
            try:
                limit = max(1, min(int(query_limit or 20), 200))
            except (TypeError, ValueError):
                limit = 20
            try:
                memory = _get_memory_manager().load_recent(limit=limit)
                payload_data = {
                    "count": len(memory.memories),
                    "entries": [
                        {
                            "sender": entry.sender,
                            "content": entry.content,
                            "timestamp": entry.timestamp.isoformat(),
                        }
                        for entry in memory.memories
                    ],
                }
            except Exception as exc:
                payload_data = {"count": 0, "entries": [], "error": str(exc)}
            return _mcp_response_ok(
                request_id,
                {"contents": [_mcp_text_resource(uri, "recent_memory", payload_data)]},
            )

        if key == "eval/benchmark/latest":
            dataset_id = str(params.get("dataset_id", "")).strip()
            if not dataset_id and query_map.get("dataset_id"):
                dataset_id = str(query_map["dataset_id"][0]).strip()
            include_compare_raw = params.get("include_compare")
            if include_compare_raw is None and query_map.get("include_compare"):
                include_compare_raw = query_map["include_compare"][0]
            include_compare = str(include_compare_raw).strip().lower() in {"1", "true", "yes", "on"}
            baseline_index_raw = params.get("baseline_index")
            if baseline_index_raw is None and query_map.get("baseline_index"):
                baseline_index_raw = query_map["baseline_index"][0]
            try:
                baseline_index = max(1, int(baseline_index_raw if baseline_index_raw is not None else 1))
            except (TypeError, ValueError):
                baseline_index = 1
            include_workflow_raw = params.get("include_workflow")
            if include_workflow_raw is None and query_map.get("include_workflow"):
                include_workflow_raw = query_map["include_workflow"][0]
            include_workflow = True if include_workflow_raw is None else str(include_workflow_raw).strip().lower() in {"1", "true", "yes", "on"}
            workflow_limit_raw = params.get("workflow_limit")
            if workflow_limit_raw is None and query_map.get("workflow_limit"):
                workflow_limit_raw = query_map["workflow_limit"][0]
            try:
                workflow_limit = max(1, min(int(workflow_limit_raw if workflow_limit_raw is not None else 100), 1000))
            except (TypeError, ValueError):
                workflow_limit = 100
            manager = _get_eval_benchmark_manager()
            selected_dataset = dataset_id
            if not selected_dataset:
                datasets = manager.list_datasets(limit=1)
                if datasets:
                    selected_dataset = str((datasets[0] or {}).get("id", "")).strip()
            if not selected_dataset:
                payload_data = {
                    "dataset_id": "",
                    "latest_run": None,
                    "compare": None,
                    "note": "No benchmark dataset found",
                }
            else:
                latest = manager.get_latest_run(selected_dataset)
                compare_payload = None
                if include_compare and hasattr(manager, "compare_with_baseline"):
                    try:
                        compare_payload = manager.compare_with_baseline(
                            selected_dataset,
                            baseline_index=baseline_index,
                        )
                    except Exception as exc:
                        compare_payload = {"error": str(exc)}
                payload_data = {
                    "dataset_id": selected_dataset,
                    "latest_run": latest,
                    "has_run": latest is not None,
                    "include_compare": include_compare,
                    "baseline_index": baseline_index,
                    "compare": compare_payload,
                }
            payload_data["include_workflow"] = include_workflow
            if include_workflow:
                payload_data["workflow_observability"] = _build_workflow_observability_metrics(limit=workflow_limit)
            return _mcp_response_ok(
                request_id,
                {"contents": [_mcp_text_resource(uri, "eval_benchmark_latest", payload_data)]},
            )

        if key == "eval/gate/status":
            manager = _get_eval_benchmark_manager()
            include_streak_raw = params.get("include_streak")
            if include_streak_raw is None and query_map.get("include_streak"):
                include_streak_raw = query_map["include_streak"][0]
            include_streak = str(include_streak_raw).strip().lower() in {"1", "true", "yes", "on"}
            include_resolved_raw = params.get("include_resolved_tasks")
            if include_resolved_raw is None and query_map.get("include_resolved_tasks"):
                include_resolved_raw = query_map["include_resolved_tasks"][0]
            include_resolved_tasks = str(include_resolved_raw).strip().lower() in {"1", "true", "yes", "on"}
            streak_limit_raw = params.get("streak_limit")
            if streak_limit_raw is None and query_map.get("streak_limit"):
                streak_limit_raw = query_map["streak_limit"][0]
            try:
                streak_limit = max(1, min(int(streak_limit_raw if streak_limit_raw is not None else 10), 50))
            except (TypeError, ValueError):
                streak_limit = 10
            dataset_id = str(params.get("dataset_id", "")).strip()
            if not dataset_id and query_map.get("dataset_id"):
                dataset_id = str(query_map["dataset_id"][0]).strip()
            include_workflow_raw = params.get("include_workflow")
            if include_workflow_raw is None and query_map.get("include_workflow"):
                include_workflow_raw = query_map["include_workflow"][0]
            include_workflow = True if include_workflow_raw is None else str(include_workflow_raw).strip().lower() in {"1", "true", "yes", "on"}
            workflow_limit_raw = params.get("workflow_limit")
            if workflow_limit_raw is None and query_map.get("workflow_limit"):
                workflow_limit_raw = query_map["workflow_limit"][0]
            try:
                workflow_limit = max(1, min(int(workflow_limit_raw if workflow_limit_raw is not None else 100), 1000))
            except (TypeError, ValueError):
                workflow_limit = 100

            payload_data = dict(manager.get_release_gate_status())
            payload_data["include_streak"] = include_streak
            payload_data["include_resolved_tasks"] = include_resolved_tasks
            if include_streak or include_resolved_tasks:
                streaks = []
                if include_streak and hasattr(manager, "get_gate_streaks"):
                    streaks = manager.get_gate_streaks(limit=streak_limit, dataset_id=dataset_id or None)
                open_tasks = manager.list_optimization_tasks(
                    limit=streak_limit,
                    status="open",
                    dataset_id=dataset_id or None,
                )
                resolved_tasks = []
                if include_resolved_tasks:
                    resolved_tasks = manager.list_optimization_tasks(
                        limit=streak_limit,
                        status="resolved",
                        dataset_id=dataset_id or None,
                    )
                payload_data["dataset_id_filter"] = dataset_id or None
                payload_data["streak_limit"] = streak_limit
                payload_data["gate_streaks"] = streaks
                payload_data["recent_open_optimization_tasks"] = open_tasks
                if include_resolved_tasks:
                    payload_data["recent_resolved_optimization_tasks"] = resolved_tasks
            payload_data["include_workflow"] = include_workflow
            if include_workflow:
                payload_data["workflow_observability"] = _build_workflow_observability_metrics(limit=workflow_limit)
            return _mcp_response_ok(
                request_id,
                {"contents": [_mcp_text_resource(uri, "eval_gate_status", payload_data)]},
            )

        if key == "eval/trainer/latest":
            manager = _get_training_job_manager()
            status = str(params.get("status", "")).strip()
            if not status and query_map.get("status"):
                status = str(query_map["status"][0]).strip()
            include_output_raw = params.get("include_output")
            if include_output_raw is None and query_map.get("include_output"):
                include_output_raw = query_map["include_output"][0]
            include_output = str(include_output_raw).strip().lower() in {"1", "true", "yes", "on"}
            include_workflow_raw = params.get("include_workflow")
            if include_workflow_raw is None and query_map.get("include_workflow"):
                include_workflow_raw = query_map["include_workflow"][0]
            include_workflow = True if include_workflow_raw is None else str(include_workflow_raw).strip().lower() in {"1", "true", "yes", "on"}
            workflow_limit_raw = params.get("workflow_limit")
            if workflow_limit_raw is None and query_map.get("workflow_limit"):
                workflow_limit_raw = query_map["workflow_limit"][0]
            try:
                workflow_limit = max(1, min(int(workflow_limit_raw if workflow_limit_raw is not None else 100), 1000))
            except (TypeError, ValueError):
                workflow_limit = 100
            jobs = manager.list_jobs(limit=1, status=status or None)
            latest_job = None
            if jobs:
                latest_id = str((jobs[0] or {}).get("job_id", "")).strip()
                if latest_id:
                    latest_job = manager.get_job(latest_id) or jobs[0]
            job_payload = None
            if isinstance(latest_job, dict):
                job_payload = dict(latest_job)
                job_payload["output_summary"] = _summarize_training_output(job_payload.get("output"))
                if not include_output and "output" in job_payload:
                    job_payload.pop("output", None)
            payload_data = {
                "status_filter": status or None,
                "include_output": include_output,
                "include_workflow": include_workflow,
                "latest_job": job_payload,
                "has_job": job_payload is not None,
            }
            if include_workflow:
                payload_data["workflow_observability"] = _build_workflow_observability_metrics(limit=workflow_limit)
            return _mcp_response_ok(
                request_id,
                {"contents": [_mcp_text_resource(uri, "eval_trainer_latest", payload_data)]},
            )

        return _mcp_response_error(request_id, -32002, "Resource not found", {"uri": uri})

    if method == "prompts/list":
        prompts = [
            {
                "name": "safety_review",
                "description": "Review a planned tool call for policy/safety risk.",
                "arguments": [{"name": "plan", "required": True}],
            },
            {
                "name": "benchmark_triage",
                "description": "Summarize benchmark failures and produce remediation actions.",
                "arguments": [{"name": "report_json", "required": True}],
            },
            {
                "name": "persona_consistency_eval",
                "description": "Evaluate persona consistency output quality against goals.",
                "arguments": [{"name": "conversation", "required": True}],
            },
        ]
        prompts = [
            item
            for item in prompts
            if _mcp_prompt_allowed(str((item or {}).get("name", "")), policy)
        ]
        return _mcp_response_ok(request_id, {"prompts": prompts})

    if method == "prompts/get":
        name = str(params.get("name", "")).strip().lower()
        args = params.get("arguments", {}) if isinstance(params.get("arguments"), dict) else {}
        if not name:
            return _mcp_response_error(request_id, -32602, "Invalid params: missing prompt name")
        if not _mcp_prompt_allowed(name, policy):
            return _mcp_response_error(
                request_id,
                -32010,
                "MCP access denied: prompt not allowed",
                {"name": name},
            )

        templates = {
            "safety_review": (
                "You are a strict safety reviewer. Analyze the plan below and return risk level, "
                "policy concerns, and a safer alternative.\n\nPlan:\n{plan}"
            ),
            "benchmark_triage": (
                "You are an eval engineer. Given benchmark report JSON, identify top 3 root causes, "
                "then provide prioritized remediation tasks.\n\nReport:\n{report_json}"
            ),
            "persona_consistency_eval": (
                "You are a persona auditor. Score consistency (0-1), style adherence, and safety "
                "compliance for the conversation below.\n\nConversation:\n{conversation}"
            ),
        }
        template = templates.get(name)
        if template is None:
            return _mcp_response_error(request_id, -32003, "Prompt not found", {"name": name})
        try:
            text = template.format(**{k: str(v) for k, v in args.items()})
        except Exception as exc:
            return _mcp_response_error(request_id, -32602, "Invalid prompt arguments", {"error": str(exc)})
        return _mcp_response_ok(
            request_id,
            {
                "description": name,
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
            },
        )

    return _mcp_response_error(request_id, -32601, f"Method not found: {method}")

@app.get("/mcp/audit", dependencies=[Depends(verify_admin_token)])
async def get_mcp_audit(
    limit: int = 100,
    method: Optional[str] = None,
    status: Optional[str] = None,
):
    entries = list(_mcp_audit_buffer)
    if method:
        m = str(method).strip().lower()
        entries = [item for item in entries if str(item.get("method", "")).strip().lower() == m]
    if status:
        s = str(status).strip().lower()
        entries = [item for item in entries if str(item.get("status", "")).strip().lower() == s]
    safe_limit = max(1, min(int(limit), 2000))
    return {"status": "ok", "items": entries[-safe_limit:], "total": len(entries)}

@app.delete("/mcp/audit", dependencies=[Depends(verify_admin_token)])
async def clear_mcp_audit():
    _mcp_audit_buffer.clear()
    return {"status": "success", "message": "MCP audit cleared"}

@app.get("/mcp/policy", dependencies=[Depends(verify_admin_token)])
async def get_mcp_policy():
    return {"status": "ok", "policy": _mcp_policy()}

@app.post("/mcp/policy/simulate", dependencies=[Depends(verify_admin_token)])
async def simulate_mcp_policy(payload: Dict[str, Any]):
    method = str(payload.get("method", "")).strip()
    params = payload.get("params", {}) if isinstance(payload.get("params"), dict) else {}
    if not method:
        raise HTTPException(status_code=400, detail="'method' is required")
    policy = _mcp_policy()
    group = _mcp_method_group(method)
    allowed = bool(policy.get("enabled", True))
    reason = "allowed"
    if not allowed:
        reason = "disabled"
    elif group == "tools" and not bool(policy.get("allow_tools", True)):
        allowed = False
        reason = "tools_disabled"
    elif group == "resources" and not bool(policy.get("allow_resources", True)):
        allowed = False
        reason = "resources_disabled"
    elif group == "prompts" and not bool(policy.get("allow_prompts", True)):
        allowed = False
        reason = "prompts_disabled"

    uri = str(params.get("uri", "")).strip()
    if allowed and method == "resources/read":
        allowed = _mcp_resource_allowed(uri, policy)
        reason = "resource_not_allowed" if not allowed else reason

    prompt_name = str(params.get("name", "")).strip().lower()
    if allowed and method == "prompts/get":
        allowed = _mcp_prompt_allowed(prompt_name, policy)
        reason = "prompt_not_allowed" if not allowed else reason

    return {
        "status": "ok",
        "simulation": {
            "method": method,
            "group": group,
            "allowed": bool(allowed),
            "reason": reason,
            "resource_uri": uri or None,
            "prompt_name": prompt_name or None,
            "policy": policy,
        },
    }
