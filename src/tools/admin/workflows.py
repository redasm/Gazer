from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from typing import Dict, Any, List, Optional
import json
import uuid
import time
from tools.admin.auth import verify_admin_token
from flow.flowise_interop import flowise_to_gazer, gazer_to_flowise, flowise_migration_suggestion
from tools.admin.state import (
    get_llm_router,
    get_tool_registry,
    _WORKFLOW_GRAPH_DIR,
    _workflow_run_history,
)
from tools.admin.strategy_helpers import _append_policy_audit, _append_workflow_run_metric
from tools.admin.utils import _resolve_export_output_path
from tools.admin.workflow_helpers import (
    _classify_workflow_validation_error,
    _default_flowise_roundtrip_cases,
    _execute_workflow_graph,
    _flowise_migration_replacement,
    _simulate_workflow_roundtrip_output,
    _summarize_flowise_errors,
    _validate_workflow_graph,
    _workflow_graph_path,
    _workflow_roundtrip_semantic_signature,
)

app = APIRouter()

def _build_flowise_migration_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    source = payload.get("flowise") if isinstance(payload.get("flowise"), dict) else payload
    flowise_payload = source if isinstance(source, dict) else {}
    name = str(payload.get("name", flowise_payload.get("name", "flowise_migration"))).strip() or "flowise_migration"
    converted = flowise_to_gazer({"flowise": flowise_payload, "name": name})
    errors = list(converted.get("errors", []) or [])
    summary = _summarize_flowise_errors(errors)

    unsupported_nodes: List[Dict[str, Any]] = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("reason") or "").strip()
        if code != "unsupported_node_type":
            continue
        node_name = str(item.get("node_name", "")).strip()
        suggestion = _flowise_migration_replacement(node_name)
        unsupported_nodes.append(
            {
                "node_id": str(item.get("node_id", "")).strip(),
                "node_name": node_name,
                "reason": code,
                "replacement": suggestion["replacement"],
                "risk_rating": suggestion["risk_rating"],
                "migration_tier": suggestion.get("migration_tier", "manual_review"),
                "note": suggestion["note"],
            }
        )

    risk_breakdown = {"low": 0, "medium": 0, "high": 0}
    for item in unsupported_nodes:
        level = str(item.get("risk_rating", "high")).strip().lower()
        if level not in risk_breakdown:
            level = "high"
        risk_breakdown[level] += 1
    migration_tier_breakdown = {"auto_replace": 0, "manual_review": 0}
    for item in unsupported_nodes:
        tier = str(item.get("migration_tier", "manual_review")).strip().lower()
        if tier not in migration_tier_breakdown:
            tier = "manual_review"
        migration_tier_breakdown[tier] += 1

    workflow_payload = converted.get("workflow", {}) if isinstance(converted.get("workflow"), dict) else {}
    validation = {"ok": False, "code": "workflow_invalid", "message": "Workflow not validated"}
    try:
        normalized = _validate_workflow_graph(workflow_payload)
        validation = {
            "ok": True,
            "code": "ok",
            "message": "Workflow can be imported after unsupported nodes are handled",
            "node_count": len(list(normalized.get("nodes", []) or [])),
            "edge_count": len(list(normalized.get("edges", []) or [])),
        }
    except HTTPException as exc:
        validation = {
            "ok": False,
            "code": _classify_workflow_validation_error(exc.detail),
            "message": str(exc.detail),
        }

    return {
        "status": "ok",
        "generated_at": time.time(),
        "name": name,
        "summary": summary,
        "unsupported_nodes": unsupported_nodes,
        "risk_breakdown": risk_breakdown,
        "migration_tier_breakdown": migration_tier_breakdown,
        "validation": validation,
        "migration_steps": [
            "Apply auto_replace tier first, then manually review manual_review tier nodes.",
            "Replace unsupported nodes using the suggested replacement mapping.",
            "Re-run import in strict mode and fix remaining edge/node validation errors.",
            "Execute roundtrip report and verify structure/semantic/execution consistency.",
        ],
    }

def _build_flowise_roundtrip_report(cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    selected_cases = cases if isinstance(cases, list) and cases else _default_flowise_roundtrip_cases()
    rows: List[Dict[str, Any]] = []
    for idx, raw_case in enumerate(selected_cases):
        case = raw_case if isinstance(raw_case, dict) else {}
        name = str(case.get("name", f"case_{idx+1}")).strip() or f"case_{idx+1}"
        flowise_payload = case.get("flowise")
        if not isinstance(flowise_payload, dict):
            rows.append(
                {
                    "name": name,
                    "ok": False,
                    "error": "invalid_flowise_payload",
                    "input_nodes": 0,
                    "input_edges": 0,
                    "gazer_nodes": 0,
                    "gazer_edges": 0,
                    "reimport_nodes": 0,
                    "reimport_edges": 0,
                    "unsupported_count": 0,
                    "error_count": 0,
                    "structure_ok": False,
                    "semantic_ok": False,
                    "execution_ok": False,
                }
            )
            continue

        row: Dict[str, Any] = {
            "name": name,
            "ok": False,
            "error": "",
            "input_nodes": len(list(flowise_payload.get("nodes") or [])),
            "input_edges": len(list(flowise_payload.get("edges") or [])),
            "gazer_nodes": 0,
            "gazer_edges": 0,
            "reimport_nodes": 0,
            "reimport_edges": 0,
            "unsupported_count": 0,
            "error_count": 0,
            "structure_ok": False,
            "semantic_ok": False,
            "execution_ok": False,
            "execution_output": "",
            "reimport_output": "",
        }
        try:
            first = flowise_to_gazer({"flowise": flowise_payload, "name": name})
            first_errors = list(first.get("errors", []) or [])
            row["error_count"] = len(first_errors)
            if first_errors:
                row["error"] = "flowise_to_gazer_errors"
                rows.append(row)
                continue
            workflow = _validate_workflow_graph(first.get("workflow", {}))
            row["gazer_nodes"] = len(list(workflow.get("nodes", []) or []))
            row["gazer_edges"] = len(list(workflow.get("edges", []) or []))

            exported = gazer_to_flowise(workflow)
            row["unsupported_count"] = int(exported.get("unsupported_count", 0) or 0)
            if row["unsupported_count"] > 0:
                row["error"] = "gazer_to_flowise_unsupported_nodes"
                rows.append(row)
                continue

            second = flowise_to_gazer({"flowise": exported, "name": f"{name}_reimport"})
            second_errors = list(second.get("errors", []) or [])
            if second_errors:
                row["error"] = "reimport_flowise_to_gazer_errors"
                row["error_count"] = len(second_errors)
                rows.append(row)
                continue
            reimported = _validate_workflow_graph(second.get("workflow", {}))
            row["reimport_nodes"] = len(list(reimported.get("nodes", []) or []))
            row["reimport_edges"] = len(list(reimported.get("edges", []) or []))
            row["structure_ok"] = bool(
                row["gazer_nodes"] == row["reimport_nodes"] and row["gazer_edges"] == row["reimport_edges"]
            )
            semantic_a = _workflow_roundtrip_semantic_signature(workflow)
            semantic_b = _workflow_roundtrip_semantic_signature(reimported)
            row["semantic_ok"] = bool(semantic_a == semantic_b)
            sample_input = str(case.get("sample_input", "hello yes"))
            output_a = _simulate_workflow_roundtrip_output(workflow, sample_input)
            output_b = _simulate_workflow_roundtrip_output(reimported, sample_input)
            row["execution_output"] = output_a
            row["reimport_output"] = output_b
            row["execution_ok"] = bool(output_a == output_b)
            row["ok"] = bool(row["structure_ok"] and row["semantic_ok"] and row["execution_ok"])
            if not row["ok"] and not row["error"]:
                row["error"] = "roundtrip_consistency_mismatch"
            rows.append(row)
        except HTTPException as exc:
            row["error"] = f"http_{int(exc.status_code)}"
            rows.append(row)
        except Exception:
            row["error"] = "exception"
            rows.append(row)

    total_cases = len(rows)
    passed_cases = sum(1 for item in rows if bool(item.get("ok", False)))
    return {
        "generated_at": time.time(),
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "pass_rate": round(passed_cases / total_cases, 4) if total_cases else 1.0,
        "cases": rows,
    }

@app.get("/workflows/graphs", dependencies=[Depends(verify_admin_token)])
async def list_workflow_graphs(limit: int = 100):
    _WORKFLOW_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    items: List[Dict[str, Any]] = []
    paths = sorted(
        _WORKFLOW_GRAPH_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )
    safe_limit = max(1, min(int(limit), 500))
    for path in paths[:safe_limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        items.append(
            {
                "id": str(payload.get("id", path.stem)),
                "name": str(payload.get("name", path.stem)),
                "description": str(payload.get("description", "")),
                "updated_at": float(payload.get("updated_at", path.stat().st_mtime)),
                "node_count": len(payload.get("nodes", []) or []),
                "edge_count": len(payload.get("edges", []) or []),
            }
        )
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/workflows/graphs/{workflow_id}", dependencies=[Depends(verify_admin_token)])
async def get_workflow_graph(workflow_id: str):
    path = _workflow_graph_path(workflow_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Workflow graph not found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Workflow graph payload is corrupted")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Workflow graph payload is invalid")
    return {"status": "ok", "workflow": payload}

@app.post("/workflows/graphs", dependencies=[Depends(verify_admin_token)])
async def save_workflow_graph(payload: Dict[str, Any]):
    normalized = _validate_workflow_graph(payload)
    workflow_id = str(normalized.get("id", "")).strip() or f"wf_{uuid.uuid4().hex[:10]}"
    normalized["id"] = workflow_id
    path = _workflow_graph_path(workflow_id)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                normalized["created_at"] = float(existing.get("created_at", normalized["created_at"]))
                normalized["version"] = int(existing.get("version", 1)) + 1
        except Exception:
            pass
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    _append_policy_audit(
        action="workflow.graph.saved",
        details={"workflow_id": workflow_id, "name": normalized.get("name", ""), "version": normalized.get("version", 1)},
    )
    return {"status": "ok", "workflow": normalized}

@app.delete("/workflows/graphs/{workflow_id}", dependencies=[Depends(verify_admin_token)])
async def delete_workflow_graph(workflow_id: str):
    path = _workflow_graph_path(workflow_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Workflow graph not found")
    path.unlink(missing_ok=True)
    _append_policy_audit(action="workflow.graph.deleted", details={"workflow_id": workflow_id})
    return {"status": "ok", "workflow_id": workflow_id}

@app.post("/workflows/graphs/{workflow_id}/run", dependencies=[Depends(verify_admin_token)])
async def run_workflow_graph(workflow_id: str, payload: Dict[str, Any]):
    path = _workflow_graph_path(workflow_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Workflow graph not found")
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Workflow graph payload is corrupted")
    if not isinstance(graph, dict):
        raise HTTPException(status_code=500, detail="Workflow graph payload is invalid")
    input_text = str(payload.get("input", ""))
    result = await _execute_workflow_graph(graph, input_text=input_text)
    _append_workflow_run_metric(
        workflow_id=workflow_id,
        workflow_name=str(graph.get("name", "")).strip(),
        result=result if isinstance(result, dict) else {},
    )
    return {"status": "ok", "result": result}

@app.post("/workflows/flowise/import", dependencies=[Depends(verify_admin_token)])
async def import_flowise_workflow(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be an object")
    converted = flowise_to_gazer(payload)
    workflow_payload = converted.get("workflow", {}) if isinstance(converted.get("workflow"), dict) else {}
    errors = list(converted.get("errors", []) or [])
    error_summary = _summarize_flowise_errors(errors)
    strict = bool(payload.get("strict", True))
    if strict and errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Flowise import contains unsupported nodes/edges",
                "errors": errors,
                "summary": error_summary,
                "validation": {
                    "ok": False,
                    "code": "interop_errors",
                    "message": "Flowise interop conversion produced node/edge errors",
                },
            },
        )
    try:
        normalized = _validate_workflow_graph(workflow_payload)
    except HTTPException as exc:
        validation_error = {
            "ok": False,
            "code": _classify_workflow_validation_error(exc.detail),
            "message": str(exc.detail),
        }
        if errors:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": str(exc.detail),
                    "errors": errors,
                    "summary": error_summary,
                    "validation": validation_error,
                },
            )
        raise
    save = bool(payload.get("save", False))
    workflow = normalized
    if save:
        saved = await save_workflow_graph(normalized)
        workflow = saved.get("workflow", normalized) if isinstance(saved, dict) else normalized
    _append_policy_audit(
        action="workflow.flowise.import",
        details={
            "name": workflow.get("name", ""),
            "saved": save,
            "error_count": len(errors),
            "node_count": len(workflow.get("nodes", []) or []),
            "edge_count": len(workflow.get("edges", []) or []),
        },
    )
    return {
        "status": "ok",
        "saved": save,
        "workflow": workflow,
        "error_count": len(errors),
        "errors": errors,
        "summary": error_summary,
        "validation": {
            "ok": True,
            "checks": {
                "dag": True,
                "reachable_output": True,
                "condition_edges": True,
            },
        },
    }

@app.post("/workflows/flowise/export", dependencies=[Depends(verify_admin_token)])
async def export_flowise_workflow(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be an object")
    workflow: Dict[str, Any]
    workflow_id = str(payload.get("workflow_id", "")).strip()
    if workflow_id:
        path = _workflow_graph_path(workflow_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Workflow graph not found")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(status_code=500, detail="Workflow graph payload is corrupted")
        if not isinstance(loaded, dict):
            raise HTTPException(status_code=500, detail="Workflow graph payload is invalid")
        workflow = loaded
    else:
        workflow_raw = payload.get("workflow", {})
        if not isinstance(workflow_raw, dict):
            raise HTTPException(status_code=400, detail="'workflow' is required when 'workflow_id' is missing")
        workflow = workflow_raw
    normalized = _validate_workflow_graph(workflow)
    flowise_payload = gazer_to_flowise(normalized)
    _append_policy_audit(
        action="workflow.flowise.export",
        details={
            "workflow_id": normalized.get("id", workflow_id),
            "name": normalized.get("name", ""),
            "unsupported_count": flowise_payload.get("unsupported_count", 0),
        },
    )
    return {
        "status": "ok",
        "flowise": flowise_payload,
        "unsupported_count": flowise_payload.get("unsupported_count", 0),
        "unsupported_nodes": flowise_payload.get("unsupported_nodes", []),
    }

@app.post("/workflows/flowise/roundtrip-report", dependencies=[Depends(verify_admin_token)])
async def generate_flowise_roundtrip_report(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        payload = {}
    cases_raw = payload.get("cases", [])
    cases = cases_raw if isinstance(cases_raw, list) else []
    report = _build_flowise_roundtrip_report(cases=cases or None)
    return {"status": "ok", "report": report}

@app.post("/workflows/flowise/roundtrip-report/export", dependencies=[Depends(verify_admin_token)])
async def export_flowise_roundtrip_report(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        payload = {}
    cases_raw = payload.get("cases", [])
    cases = cases_raw if isinstance(cases_raw, list) else []
    report = _build_flowise_roundtrip_report(cases=cases or None)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str(payload.get("output_path", "")).strip(),
        default_filename=f"FLOWISE_ROUNDTRIP_REPORT_{stamp}.md",
    )

    lines = [
        "# Flowise Roundtrip Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- total_cases: {report.get('total_cases', 0)}",
        f"- passed_cases: {report.get('passed_cases', 0)}",
        f"- pass_rate: {report.get('pass_rate', 1.0)}",
        "",
        "## Cases",
    ]
    for item in list(report.get("cases", []) or []):
        lines.extend(
            [
                f"- name: {item.get('name', '')}",
                f"  - ok: {bool(item.get('ok', False))}",
                f"  - input_nodes: {int(item.get('input_nodes', 0) or 0)}",
                f"  - input_edges: {int(item.get('input_edges', 0) or 0)}",
                f"  - gazer_nodes: {int(item.get('gazer_nodes', 0) or 0)}",
                f"  - gazer_edges: {int(item.get('gazer_edges', 0) or 0)}",
                f"  - reimport_nodes: {int(item.get('reimport_nodes', 0) or 0)}",
                f"  - reimport_edges: {int(item.get('reimport_edges', 0) or 0)}",
                f"  - unsupported_count: {int(item.get('unsupported_count', 0) or 0)}",
                f"  - error_count: {int(item.get('error_count', 0) or 0)}",
                f"  - structure_ok: {bool(item.get('structure_ok', False))}",
                f"  - semantic_ok: {bool(item.get('semantic_ok', False))}",
                f"  - execution_ok: {bool(item.get('execution_ok', False))}",
                f"  - error: {item.get('error', '')}",
            ]
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "report": report}

@app.post("/workflows/flowise/migration-report", dependencies=[Depends(verify_admin_token)])
async def generate_flowise_migration_report(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        payload = {}
    report = _build_flowise_migration_report(payload)
    return {"status": "ok", "report": report}

@app.post("/workflows/flowise/migration-report/export", dependencies=[Depends(verify_admin_token)])
async def export_flowise_migration_report(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        payload = {}
    report = _build_flowise_migration_report(payload)
    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str(payload.get("output_path", "")).strip(),
        default_filename=f"FLOWISE_MIGRATION_REPORT_{stamp}.md",
    )
    lines = [
        "# Flowise Migration Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- name: {report.get('name')}",
        f"- total_errors: {(report.get('summary', {}) or {}).get('total', 0)}",
        f"- node_errors: {(report.get('summary', {}) or {}).get('node', 0)}",
        f"- edge_errors: {(report.get('summary', {}) or {}).get('edge', 0)}",
        f"- risk_low: {(report.get('risk_breakdown', {}) or {}).get('low', 0)}",
        f"- risk_medium: {(report.get('risk_breakdown', {}) or {}).get('medium', 0)}",
        f"- risk_high: {(report.get('risk_breakdown', {}) or {}).get('high', 0)}",
        f"- auto_replace: {(report.get('migration_tier_breakdown', {}) or {}).get('auto_replace', 0)}",
        f"- manual_review: {(report.get('migration_tier_breakdown', {}) or {}).get('manual_review', 0)}",
        "",
        "## Unsupported Nodes",
    ]
    unsupported_rows = list(report.get("unsupported_nodes", []) or [])
    if unsupported_rows:
        for item in unsupported_rows:
            lines.extend(
                [
                    f"- node_id: {item.get('node_id', '')}",
                    f"  - node_name: {item.get('node_name', '')}",
                    f"  - replacement: {item.get('replacement', '')}",
                    f"  - risk_rating: {item.get('risk_rating', 'high')}",
                    f"  - migration_tier: {item.get('migration_tier', 'manual_review')}",
                    f"  - note: {item.get('note', '')}",
                ]
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Validation",
            f"- ok: {(report.get('validation', {}) or {}).get('ok', False)}",
            f"- code: {(report.get('validation', {}) or {}).get('code', '')}",
            f"- message: {(report.get('validation', {}) or {}).get('message', '')}",
            "",
            "## Suggested Steps",
        ]
    )
    for step in list(report.get("migration_steps", []) or []):
        lines.append(f"- {step}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "report": report}
