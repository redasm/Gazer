"""Workflow graph validation, execution, and Flowise interop helpers extracted from _shared.py."""

from __future__ import annotations
import asyncio
import copy
import importlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from flow.flowise_interop import flowise_migration_suggestion
from runtime.config_manager import config
try:
    from plugins.loader import PluginLoader
    from security.threat_scan import scan_directory as threat_scan_directory
except ImportError:
    pass
from tools.admin.state import (
    _PROJECT_ROOT,
    _WORKFLOW_GRAPH_DIR,
    TOOL_REGISTRY,
    LLM_ROUTER,
    TRAJECTORY_STORE,
)
from tools.admin.observability_helpers import _get_eval_benchmark_manager

logger = logging.getLogger('GazerAdminAPI')


def _summarize_flowise_errors(errors: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {"total": 0, "node": 0, "edge": 0, "by_code": {}}
    if not isinstance(errors, list):
        return summary
    for item in errors:
        if not isinstance(item, dict):
            continue
        summary["total"] += 1
        level = str(item.get("level", "node")).strip().lower()
        if level not in {"node", "edge"}:
            level = "node"
        summary[level] += 1
        code = str(item.get("code") or item.get("reason") or "unknown_error").strip() or "unknown_error"
        by_code = summary["by_code"]
        by_code[code] = int(by_code.get(code, 0)) + 1
    return summary

def _flowise_migration_replacement(node_name: str) -> Dict[str, str]:
    return flowise_migration_suggestion(node_name)

def _classify_workflow_validation_error(detail: Any) -> str:
    text = str(detail or "").strip().lower()
    if "cycle" in text:
        return "dag_cycle"
    if "reachable output" in text:
        return "no_reachable_output"
    if "condition node" in text and ("tagged" in text or "duplicate" in text):
        return "condition_edges_invalid"
    return "workflow_invalid"

def _safe_task_path(rel_path: str) -> Path:
    target = (_PROJECT_ROOT / rel_path).resolve()
    if not str(target).startswith(str(_PROJECT_ROOT.resolve())):
        raise ValueError("Path traversal detected")
    return target

def _run_verify_command(cmd: str, cwd: Path, timeout_seconds: int = 120) -> Dict[str, Any]:
    import subprocess
    import re
    
    # 1. Block shell metacharacters (except simple hyphens/underscores/etc)
    # The original implementation blocked shell metacharacters
    blocked_metachars = [";", "&", "|", ">", "<", "`", "$(", "${"]
    for char in blocked_metachars:
        if char in cmd:
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": f"Error: Command contains blocked shell metacharacters: {char}"
            }
            
    # 2. Block certain executables like powershell, cmd, bash, sh based on tests
    first_word = cmd.strip().split()[0].lower() if cmd.strip() else ""
    blocked_execs = ["powershell", "powershell.exe", "cmd", "cmd.exe", "bash", "sh", "zsh"]
    if first_word in blocked_execs:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Error: blocked verify executable: {first_word}"
        }

    try:
        # Avoid shell=True for security, the tests mock shell=False
        args = cmd.split()
        res = subprocess.run(
            args, shell=False, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout_seconds
        )
        return {
            "ok": True,
            "exit_code": res.returncode,
            "returncode": res.returncode,
            "logs": res.stdout + "\n" + res.stderr,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "exit_code": 124,
            "returncode": 124,
            "logs": f"Timeout after {timeout_seconds}s\n" + (e.stdout or "") + "\n" + (e.stderr or ""),
            "stdout": e.stdout or "",
            "stderr": f"Timeout after {timeout_seconds}s\n" + (e.stderr or "")
        }
    except Exception as e:
        return {
            "ok": False,
            "exit_code": 1,
            "returncode": 1,
            "logs": str(e),
            "stdout": "",
            "stderr": str(e)
        }

def _render_workflow_template(template: Any, ctx: Dict[str, Any]) -> Any:
    if isinstance(template, str):
        result = template
        for k, v in ctx.items():
            if k == "node_outputs":
                continue
            result = result.replace(f"{{{{{k}}}}}", str(v))
        if "node_outputs" in ctx and isinstance(ctx["node_outputs"], dict):
            for node_id, out_val in ctx["node_outputs"].items():
                result = result.replace(f"{{{{node.{node_id}}}}}", str(out_val))
        return result
    elif isinstance(template, dict):
        return {k: _render_workflow_template(v, ctx) for k, v in template.items()}
    elif isinstance(template, list):
        return [_render_workflow_template(item, ctx) for item in template]
    return template

def _default_flowise_roundtrip_cases() -> List[Dict[str, Any]]:
    return [
        {
            "name": "chat_prompt_tool_output",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "p1", "type": "customNode", "data": {"name": "chatPromptTemplate", "inputs": {"template": "Q={{prev}}"}}},
                    {"id": "t1", "type": "customNode", "data": {"name": "tool", "inputs": {"toolName": "echo"}}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "{{prev}}"}}},
                ],
                "edges": [
                    {"source": "in1", "target": "p1"},
                    {"source": "p1", "target": "t1"},
                    {"source": "t1", "target": "out1"},
                ],
            },
        },
        {
            "name": "condition_branch",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "c1", "type": "customNode", "data": {"name": "ifElse", "inputs": {"operator": "contains", "value": "yes"}}},
                    {"id": "ot", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "TRUE:{{prev}}"}}},
                    {"id": "of", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "FALSE:{{prev}}"}}},
                ],
                "edges": [
                    {"source": "in1", "target": "c1"},
                    {"source": "c1", "target": "ot", "label": "true"},
                    {"source": "c1", "target": "of", "label": "false"},
                ],
            },
        },
        {
            "name": "memory_retriever_agent_toolchain",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "m1", "type": "customNode", "data": {"name": "bufferWindowMemory", "inputs": {"memoryPrompt": "M={{prev}}"}}},
                    {"id": "r1", "type": "customNode", "data": {"name": "vectorStoreRetriever"}},
                    {"id": "a1", "type": "customNode", "data": {"name": "conversationalAgent", "inputs": {"systemMessage": "Assistant"}}},
                    {"id": "tc1", "type": "customNode", "data": {"name": "toolChain", "inputs": {"toolName": "echo"}}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
                ],
                "edges": [
                    {"source": "in1", "target": "m1"},
                    {"source": "m1", "target": "r1"},
                    {"source": "r1", "target": "a1"},
                    {"source": "a1", "target": "tc1"},
                    {"source": "tc1", "target": "out1"},
                ],
            },
        },
        {
            "name": "retrieval_qa_chain",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "rq1", "type": "customNode", "data": {"name": "retrievalQAChain"}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "{{prev}}"}}},
                ],
                "edges": [
                    {"source": "in1", "target": "rq1"},
                    {"source": "rq1", "target": "out1"},
                ],
            },
        },
        {
            "name": "web_search_tool_path",
            "flowise": {
                "nodes": [
                    {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                    {"id": "w1", "type": "customNode", "data": {"name": "webSearch"}},
                    {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
                ],
                "edges": [
                    {"source": "in1", "target": "w1"},
                    {"source": "w1", "target": "out1"},
                ],
            },
        },
    ]

def _workflow_roundtrip_semantic_signature(workflow: Dict[str, Any]) -> Dict[str, Any]:
    nodes_raw = workflow.get("nodes", []) if isinstance(workflow.get("nodes"), list) else []
    edges_raw = workflow.get("edges", []) if isinstance(workflow.get("edges"), list) else []

    node_sig: List[Dict[str, Any]] = []
    for item in nodes_raw:
        if not isinstance(item, dict):
            continue
        node_type = str(item.get("type", "")).strip().lower()
        cfg_raw = item.get("config", {})
        cfg = dict(cfg_raw) if isinstance(cfg_raw, dict) else {}
        cfg.pop("_flowise", None)
        if node_type == "input":
            normalized = {"default": str(cfg.get("default", ""))}
        elif node_type == "prompt":
            normalized = {"prompt": str(cfg.get("prompt", "{{prev}}"))}
        elif node_type == "tool":
            normalized = {
                "tool_name": str(cfg.get("tool_name", "")).strip(),
                "args": cfg.get("args", {}) if isinstance(cfg.get("args"), dict) else {},
            }
        elif node_type == "condition":
            normalized = {
                "operator": str(cfg.get("operator", "contains")).strip().lower(),
                "value": str(cfg.get("value", "")),
            }
        elif node_type == "output":
            normalized = {"text": str(cfg.get("text", "{{prev}}"))}
        else:
            normalized = cfg
        node_sig.append(
            {
                "id": str(item.get("id", "")).strip(),
                "type": node_type,
                "config": normalized,
            }
        )
    node_sig.sort(key=lambda item: str(item.get("id", "")))

    edge_sig: List[Dict[str, Any]] = []
    for item in edges_raw:
        if not isinstance(item, dict):
            continue
        edge_sig.append(
            {
                "source": str(item.get("source", "")).strip(),
                "target": str(item.get("target", "")).strip(),
                "when": str(item.get("when", "")).strip().lower(),
            }
        )
    edge_sig.sort(key=lambda item: (item["source"], item["target"], item["when"]))
    return {"nodes": node_sig, "edges": edge_sig}

def _simulate_workflow_roundtrip_output(workflow: Dict[str, Any], input_text: str) -> str:
    nodes_raw = workflow.get("nodes", []) if isinstance(workflow.get("nodes"), list) else []
    edges_raw = workflow.get("edges", []) if isinstance(workflow.get("edges"), list) else []
    node_map: Dict[str, Dict[str, Any]] = {}
    for item in nodes_raw:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id", "")).strip()
        if not node_id:
            continue
        node_map[node_id] = item

    incoming_count: Dict[str, int] = {node_id: 0 for node_id in node_map.keys()}
    outgoing: Dict[str, List[Dict[str, Any]]] = {node_id: [] for node_id in node_map.keys()}
    for edge in edges_raw:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in node_map and target in node_map:
            outgoing[source].append(edge)
            incoming_count[target] = incoming_count.get(target, 0) + 1

    pending_predecessors = dict(incoming_count)
    incoming_values: Dict[str, List[str]] = {node_id: [] for node_id in node_map.keys()}
    queue: List[str] = [node_id for node_id, cnt in pending_predecessors.items() if cnt == 0]
    queued = set(queue)
    completed: set[str] = set()

    ctx: Dict[str, Any] = {"input": str(input_text or ""), "prev": str(input_text or ""), "node_outputs": {}}
    final_output = ""
    visited = 0
    max_steps = max(1, min(500, len(node_map) * 8 if node_map else 1))

    while queue and visited < max_steps:
        visited += 1
        current_id = queue.pop(0)
        queued.discard(current_id)
        if current_id in completed:
            continue
        node = node_map.get(current_id)
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", "")).strip().lower()
        node_inputs = incoming_values.get(current_id, [])
        node_prev = str(node_inputs[-1]) if node_inputs else str(ctx.get("prev", ""))
        config = node.get("config", {}) if isinstance(node.get("config"), dict) else {}
        condition_outcome: Optional[bool] = None

        if not bool(node.get("enabled", True)):
            result_text = node_prev
        elif incoming_count.get(current_id, 0) > 0 and not node_inputs:
            result_text = node_prev
        elif node_type == "input":
            default_text = str(config.get("default", "")).strip()
            result_text = str(input_text or "") if str(input_text or "") else default_text
        elif node_type == "prompt":
            prompt = str(config.get("prompt", "{{prev}}"))
            result_text = str(
                _render_workflow_template(
                    prompt,
                    {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
                )
            )
        elif node_type == "tool":
            tool_name = str(config.get("tool_name", "")).strip() or "echo"
            raw_args = config.get("args", {}) if isinstance(config.get("args"), dict) else {}
            rendered_args = _render_workflow_template(
                raw_args,
                {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
            )
            result_text = f"tool:{tool_name}:{json.dumps(rendered_args, ensure_ascii=False, sort_keys=True)}"
        elif node_type == "condition":
            operator = str(config.get("operator", "contains")).strip().lower()
            expected = str(
                _render_workflow_template(
                    config.get("value", ""),
                    {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
                )
            )
            if operator == "equals":
                condition_outcome = node_prev == expected
            elif operator == "not_contains":
                condition_outcome = expected not in node_prev
            else:
                condition_outcome = expected in node_prev
            result_text = "true" if condition_outcome else "false"
        elif node_type == "output":
            result_text = str(
                _render_workflow_template(
                    config.get("text", "{{prev}}"),
                    {"input": str(input_text or ""), "prev": node_prev, "node_outputs": ctx.get("node_outputs", {})},
                )
            )
            final_output = result_text
        else:
            result_text = node_prev

        ctx["node_outputs"][current_id] = result_text
        ctx["prev"] = result_text
        completed.add(current_id)

        outgoing_edges = outgoing.get(current_id, [])
        selected_targets: set[str] = set()
        if node_type == "condition" and condition_outcome is not None:
            tagged_edges = [edge for edge in outgoing_edges if str(edge.get("when", "")).strip().lower() in {"true", "false", "default"}]
            if tagged_edges:
                branch_key = "true" if condition_outcome else "false"
                matched = [
                    edge for edge in tagged_edges if str(edge.get("when", "")).strip().lower() == branch_key
                ]
                if not matched:
                    matched = [
                        edge
                        for edge in tagged_edges
                        if str(edge.get("when", "")).strip().lower() in {"default", ""}
                    ]
                selected_targets = {str(edge.get("target", "")).strip() for edge in matched}
            elif outgoing_edges:
                selected_targets = {str(outgoing_edges[0].get("target", "")).strip()}
        else:
            selected_targets = {str(edge.get("target", "")).strip() for edge in outgoing_edges}

        for edge in outgoing_edges:
            target = str(edge.get("target", "")).strip()
            if target not in pending_predecessors:
                continue
            if target in selected_targets:
                incoming_values[target].append(result_text)
            pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
            if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                queue.append(target)
                queued.add(target)

    return final_output or str(ctx.get("prev", ""))

def _plugin_loader() -> PluginLoader:
    return PluginLoader(workspace=Path.cwd())

def _plugin_install_base(global_install: bool = False) -> Path:
    if global_install:
        return Path.home() / ".gazer" / "extensions"
    return Path("extensions")

def _scan_plugin_source_for_threats(source: Path) -> Dict[str, Any]:
    scan_cfg = config.get("security.threat_scan", {}) or {}
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}
    return threat_scan_directory(source, scan_cfg)

def _plugin_market_snapshot() -> Dict[str, Any]:
    loader = _plugin_loader()
    manifests = loader.discover()
    enabled = {
        str(item).strip()
        for item in (config.get("plugins.enabled", []) or [])
        if str(item).strip()
    }
    disabled = {
        str(item).strip()
        for item in (config.get("plugins.disabled", []) or [])
        if str(item).strip()
    }
    items: List[Dict[str, Any]] = []
    for manifest in manifests.values():
        items.append(
            {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "slot": manifest.slot.value,
                "optional": bool(manifest.optional),
                "description": manifest.description,
                "base_dir": str(manifest.base_dir) if manifest.base_dir else "",
                "enabled": manifest.id in enabled and manifest.id not in disabled,
                "disabled": manifest.id in disabled,
                "integrity_ok": bool(manifest.integrity_ok),
                "signature_ok": bool(manifest.signature_ok),
                "verification_error": str(manifest.verification_error or ""),
            }
        )
    items.sort(key=lambda item: item["id"])
    return {
        "items": items,
        "total": len(items),
        "failed_ids": sorted(loader.failed_ids),
    }

def _memory_recall_regression_settings() -> Dict[str, Any]:
    raw = config.get("memory.recall_regression", {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    thresholds = raw.get("thresholds", {}) if isinstance(raw.get("thresholds", {}), dict) else {}
    gate = raw.get("gate", {}) if isinstance(raw.get("gate", {}), dict) else {}

    def _as_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(low, min(parsed, high))

    def _as_float(value: Any, default: float, low: float, high: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(low, min(parsed, high))

    mode = str(gate.get("mode", "warn")).strip().lower() or "warn"
    if mode not in {"warn", "block", "disabled"}:
        mode = "warn"

    return {
        "enabled": bool(raw.get("enabled", True)),
        "window_days": _as_int(raw.get("window_days", 7), 7, 1, 30),
        "query_set_path": str(raw.get("query_set_path", "")).strip(),
        "top_k": _as_int(raw.get("top_k", 5), 5, 1, 20),
        "min_match_score": _as_float(raw.get("min_match_score", 0.18), 0.18, 0.0, 1.0),
        "thresholds": {
            "min_precision_proxy": _as_float(
                thresholds.get("min_precision_proxy", 0.45), 0.45, 0.0, 1.0
            ),
            "min_recall_proxy": _as_float(
                thresholds.get("min_recall_proxy", 0.45), 0.45, 0.0, 1.0
            ),
            "warning_drop": _as_float(thresholds.get("warning_drop", 0.05), 0.05, 0.0, 1.0),
            "critical_drop": _as_float(thresholds.get("critical_drop", 0.12), 0.12, 0.0, 1.0),
        },
        "gate": {
            "link_release_gate": bool(gate.get("link_release_gate", True)),
            "mode": mode,
            "source": str(gate.get("source", "memory_recall_regression")).strip()
            or "memory_recall_regression",
            "reason_warning": str(
                gate.get("reason_warning", "memory_recall_regression_warning")
            ).strip()
            or "memory_recall_regression_warning",
            "reason_critical": str(
                gate.get("reason_critical", "memory_recall_regression_critical")
            ).strip()
            or "memory_recall_regression_critical",
        },
    }

def _apply_memory_recall_gate_linkage(
    *,
    report: Dict[str, Any],
    enabled: bool,
    apply_gate: bool,
    gate_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    manager = _get_eval_benchmark_manager()
    current_gate = manager.get_release_gate_status()
    level = str((report.get("gate", {}) or {}).get("level", "healthy")).strip().lower()
    mode = str(gate_cfg.get("mode", "warn")).strip().lower() or "warn"
    linkage: Dict[str, Any] = {
        "enabled": bool(enabled and gate_cfg.get("link_release_gate", True)),
        "applied": bool(apply_gate),
        "mode": mode,
        "level": level,
        "alert_only": mode != "block",
        "changed_gate": False,
        "gate": current_gate,
        "signal": {
            "active": level in {"warning", "critical"},
            "reason": str(
                gate_cfg.get("reason_critical", "memory_recall_regression_critical")
                if level == "critical"
                else gate_cfg.get("reason_warning", "memory_recall_regression_warning")
            ),
        },
    }
    if not linkage["enabled"] or not apply_gate or mode in {"disabled", ""}:
        return linkage

    if mode == "block":
        should_block = level == "critical"
        reason = str(
            gate_cfg.get("reason_critical", "memory_recall_regression_critical")
            if should_block
            else "memory_recall_regression_recovered"
        )
        source = str(gate_cfg.get("source", "memory_recall_regression")).strip() or "memory_recall_regression"
        current_blocked = bool(current_gate.get("blocked", False))
        current_source = str(current_gate.get("source", "")).strip()
        should_update = False
        if should_block:
            should_update = (not current_blocked) or (current_source != source)
        else:
            should_update = current_blocked and current_source == source
        if should_update:
            current_gate = manager.set_release_gate_status(
                blocked=should_block,
                reason=reason,
                source=source,
                metadata={
                    "report": "memory_recall_regression",
                    "level": level,
                    "quality_score": (report.get("current_window", {}).get("metrics", {}) or {}).get(
                        "quality_score", 0.0
                    ),
                },
            )
            linkage["changed_gate"] = True
            linkage["gate"] = current_gate
        return linkage

    # warn-mode linkage: signal only, keep gate block status untouched.
    return linkage

def _workflow_graph_path(workflow_id: str) -> Path:
    safe = str(workflow_id or "").strip().replace("/", "_").replace("\\", "_")
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid workflow id")
    _WORKFLOW_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    return _WORKFLOW_GRAPH_DIR / f"{safe}.json"

def _validate_workflow_graph(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Workflow payload must be an object")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="'name' is required")

    nodes_raw = payload.get("nodes", [])
    edges_raw = payload.get("edges", [])
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list):
        raise HTTPException(status_code=400, detail="'nodes' and 'edges' must be arrays")

    allowed_types = {"input", "prompt", "tool", "condition", "output"}
    nodes: List[Dict[str, Any]] = []
    node_ids = set()
    node_type_map: Dict[str, str] = {}
    for idx, item in enumerate(nodes_raw):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}]' must be an object")
        node_id = str(item.get("id", "")).strip()
        node_type = str(item.get("type", "")).strip().lower()
        if not node_id:
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}].id' is required")
        if node_id in node_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate node id '{node_id}'")
        if node_type not in allowed_types:
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}].type' must be one of {sorted(allowed_types)}")
        node_ids.add(node_id)
        node_type_map[node_id] = node_type
        cfg = item.get("config", {})
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=400, detail=f"'nodes[{idx}].config' must be an object")
        position = item.get("position", {})
        if position is None:
            position = {}
        if not isinstance(position, dict):
            position = {}
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "label": str(item.get("label", node_id)),
                "enabled": bool(item.get("enabled", True)),
                "locked": bool(item.get("locked", False)),
                "config": cfg,
                "position": {
                    "x": int(position.get("x", 40)),
                    "y": int(position.get("y", 40)),
                },
            }
        )

    edges: List[Dict[str, Any]] = []
    for idx, item in enumerate(edges_raw):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"'edges[{idx}]' must be an object")
        source = str(item.get("source", "")).strip()
        target = str(item.get("target", "")).strip()
        if source not in node_ids or target not in node_ids:
            raise HTTPException(status_code=400, detail=f"'edges[{idx}]' references unknown node")
        when_raw = item.get("when", None)
        when = str(when_raw).strip().lower() if when_raw is not None else ""
        if when not in {"", "true", "false", "default"}:
            raise HTTPException(status_code=400, detail=f"'edges[{idx}].when' must be one of ['', 'true', 'false', 'default']")
        source_type = node_type_map.get(source, "")
        if when and source_type != "condition":
            raise HTTPException(status_code=400, detail=f"'edges[{idx}].when' is only allowed when source node is 'condition'")
        edges.append(
            {
                "id": str(item.get("id", f"edge_{idx}")).strip() or f"edge_{idx}",
                "source": source,
                "target": target,
                "when": when,
            }
        )

    # DAG guardrail: workflow graph must be acyclic.
    graph_incoming: Dict[str, int] = {node_id: 0 for node_id in node_ids}
    graph_outgoing: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in graph_outgoing and target in graph_incoming:
            graph_outgoing[source].append(target)
            graph_incoming[target] += 1

    topo_queue: List[str] = [node_id for node_id, cnt in graph_incoming.items() if cnt == 0]
    visited_count = 0
    while topo_queue:
        current = topo_queue.pop(0)
        visited_count += 1
        for nxt in graph_outgoing.get(current, []):
            graph_incoming[nxt] = max(0, graph_incoming[nxt] - 1)
            if graph_incoming[nxt] == 0:
                topo_queue.append(nxt)
    if visited_count != len(node_ids):
        raise HTTPException(status_code=400, detail="Workflow graph contains a cycle; only DAG is supported")

    # Condition branch consistency checks.
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        if str(node.get("type", "")).strip().lower() != "condition":
            continue
        outgoing_edges = [edge for edge in edges if str(edge.get("source", "")).strip() == node_id]
        if not outgoing_edges:
            continue
        tagged = [edge for edge in outgoing_edges if str(edge.get("when", "")).strip() in {"true", "false", "default"}]
        untagged = [edge for edge in outgoing_edges if str(edge.get("when", "")).strip() == ""]
        if tagged and untagged:
            raise HTTPException(
                status_code=400,
                detail=f"Condition node '{node_id}' cannot mix tagged (true/false/default) and untagged edges",
            )
        if tagged:
            when_values = [str(edge.get("when", "")).strip() for edge in tagged]
            for label in ("true", "false", "default"):
                if when_values.count(label) > 1:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Condition node '{node_id}' has duplicate '{label}' edges",
                    )

    # Reachability guardrail: at least one output node must be reachable from a start node.
    start_nodes = [node_id for node_id, cnt in graph_incoming.items() if cnt == 0]
    input_nodes = [
        str(node.get("id", "")).strip()
        for node in nodes
        if str(node.get("type", "")).strip().lower() == "input"
    ]
    seed_nodes = [node_id for node_id in input_nodes if node_id in graph_outgoing] or start_nodes
    reachable = set(seed_nodes)
    bfs_queue = list(seed_nodes)
    while bfs_queue:
        current = bfs_queue.pop(0)
        for nxt in graph_outgoing.get(current, []):
            if nxt in reachable:
                continue
            reachable.add(nxt)
            bfs_queue.append(nxt)
    reachable_output = any(
        str(node.get("type", "")).strip().lower() == "output" and str(node.get("id", "")).strip() in reachable
        for node in nodes
    )
    if not reachable_output:
        raise HTTPException(status_code=400, detail="Workflow graph has no reachable output node")

    return {
        "id": str(payload.get("id", "")).strip(),
        "name": name,
        "description": str(payload.get("description", "")).strip(),
        "version": int(payload.get("version", 1) or 1),
        "created_at": float(payload.get("created_at", time.time())),
        "updated_at": float(time.time()),
        "nodes": nodes,
        "edges": edges,
    }

async def _execute_workflow_graph(graph: Dict[str, Any], *, input_text: str = "") -> Dict[str, Any]:
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    node_map = {str(item.get("id", "")): item for item in nodes if isinstance(item, dict)}
    incoming_count: Dict[str, int] = {node_id: 0 for node_id in node_map.keys()}
    outgoing: Dict[str, List[Dict[str, Any]]] = {node_id: [] for node_id in node_map.keys()}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in outgoing and target in incoming_count:
            outgoing[source].append(
                {
                    "source": source,
                    "target": target,
                    "when": str(edge.get("when", "")).strip().lower(),
                }
            )
            incoming_count[target] += 1

    start_candidates = [node_id for node_id, count in incoming_count.items() if count == 0]
    if not start_candidates:
        return {"status": "error", "error": "No start node found", "trace": []}
    queue: List[str] = sorted(start_candidates)
    queued = set(queue)
    completed = set()
    pending_predecessors = dict(incoming_count)
    incoming_values: Dict[str, List[str]] = {node_id: [] for node_id in node_map.keys()}
    visited = 0
    max_steps = 200
    trace: List[Dict[str, Any]] = []
    ctx: Dict[str, Any] = {"input": str(input_text or ""), "prev": str(input_text or ""), "node_outputs": {}}
    final_output = str(input_text or "")

    run_started_at = time.perf_counter()

    def _summarize_trace(trace_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        status_counts: Dict[str, int] = {"ok": 0, "warning": 0, "skipped": 0, "error": 0}
        node_duration_ms = 0
        for step in trace_items:
            step_status = str(step.get("status", "")).strip().lower()
            if step_status in status_counts:
                status_counts[step_status] += 1
            try:
                node_duration_ms += max(0, int(step.get("duration_ms", 0) or 0))
            except (TypeError, ValueError):
                continue
        return {
            "trace_nodes": len(trace_items),
            "ok_nodes": status_counts["ok"],
            "warning_nodes": status_counts["warning"],
            "skipped_nodes": status_counts["skipped"],
            "error_nodes": status_counts["error"],
            "node_duration_ms": node_duration_ms,
        }

    while queue and visited < max_steps:
        visited += 1
        current_id = queue.pop(0)
        queued.discard(current_id)
        if current_id in completed:
            continue
        node = node_map.get(current_id)
        if node is None:
            break
        node_type = str(node.get("type", "")).strip().lower()
        node_inputs = incoming_values.get(current_id, [])
        node_prev = str(node_inputs[-1]) if node_inputs else str(ctx.get("prev", ""))
        if not bool(node.get("enabled", True)):
            result_text = node_prev
            trace.append(
                {
                    "node_id": current_id,
                    "node_type": node_type,
                    "status": "skipped",
                    "output": result_text,
                    "reason": "node_disabled",
                    "duration_ms": 0,
                }
            )
            ctx["node_outputs"][current_id] = result_text
            ctx["prev"] = result_text
            completed.add(current_id)
            for edge in outgoing.get(current_id, []):
                target = edge.get("target", "")
                if target not in pending_predecessors:
                    continue
                pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
                if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                    queue.append(target)
                    queued.add(target)
            continue

        if incoming_count.get(current_id, 0) > 0 and not node_inputs:
            result_text = node_prev
            trace.append(
                {
                    "node_id": current_id,
                    "node_type": node_type,
                    "status": "skipped",
                    "output": result_text,
                    "reason": "no_active_input",
                    "duration_ms": 0,
                }
            )
            ctx["node_outputs"][current_id] = result_text
            ctx["prev"] = result_text
            completed.add(current_id)
            for edge in outgoing.get(current_id, []):
                target = edge.get("target", "")
                if target not in pending_predecessors:
                    continue
                pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
                if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                    queue.append(target)
                    queued.add(target)
            continue

        config = node.get("config", {}) if isinstance(node.get("config"), dict) else {}
        result_text = ""
        status = "ok"
        error_text = ""
        condition_outcome: Optional[bool] = None
        timeout_raw = config.get("timeout_ms", 0)
        retries_raw = config.get("retry_count", 0)
        on_error = str(config.get("on_error", "fail")).strip().lower()
        if on_error not in {"fail", "continue", "fallback"}:
            on_error = "fail"
        try:
            timeout_ms = max(0, min(int(timeout_raw), 120000))
        except (TypeError, ValueError):
            timeout_ms = 0
        try:
            retry_count = max(0, min(int(retries_raw), 5))
        except (TypeError, ValueError):
            retry_count = 0
        attempts_total = retry_count + 1
        attempts_used = 0
        node_started_at = time.perf_counter()

        async def _execute_node_once() -> tuple[str, Optional[bool]]:
            node_ctx: Dict[str, Any] = {
                "input": str(input_text or ""),
                "prev": node_prev,
                "node_outputs": ctx.get("node_outputs", {}),
            }
            if node_type == "input":
                default_text = str(config.get("default", "")).strip()
                return (node_ctx["input"] if node_ctx["input"] else default_text), None
            if node_type == "prompt":
                prompt = str(config.get("prompt", "{{prev}}"))
                rendered = str(_render_workflow_template(prompt, node_ctx))
                if LLM_ROUTER is not None and hasattr(LLM_ROUTER, "chat"):
                    resp = await LLM_ROUTER.chat(
                        messages=[{"role": "user", "content": rendered}],
                        tools=[],
                    )
                    text = str(getattr(resp, "content", "") or "")
                    if not text and getattr(resp, "error", None):
                        text = str(getattr(resp, "content", "") or "LLM error")
                    return text, None
                return rendered, None
            if node_type == "tool":
                if TOOL_REGISTRY is None:
                    raise RuntimeError("Tool registry unavailable")
                tool_name = str(config.get("tool_name", "")).strip()
                if not tool_name:
                    raise RuntimeError("tool_name is required")
                raw_args = config.get("args", {})
                if not isinstance(raw_args, dict):
                    raw_args = {}
                tool_args = _render_workflow_template(raw_args, node_ctx)
                text = await TOOL_REGISTRY.execute(
                    tool_name,
                    tool_args,
                )
                return str(text), None
            if node_type == "condition":
                operator = str(config.get("operator", "contains")).strip().lower()
                expected = str(_render_workflow_template(config.get("value", ""), node_ctx))
                current_text = str(node_ctx.get("prev", ""))
                if operator == "equals":
                    outcome = current_text == expected
                elif operator == "not_contains":
                    outcome = expected not in current_text
                else:
                    outcome = expected in current_text
                return ("true" if outcome else "false"), outcome
            if node_type == "output":
                return str(_render_workflow_template(config.get("text", "{{prev}}"), node_ctx)), None
            return str(node_ctx.get("prev", "")), None

        for attempt_index in range(attempts_total):
            attempts_used = attempt_index + 1
            try:
                if timeout_ms > 0:
                    result_text, condition_outcome = await asyncio.wait_for(
                        _execute_node_once(),
                        timeout=timeout_ms / 1000.0,
                    )
                else:
                    result_text, condition_outcome = await _execute_node_once()
                status = "ok"
                error_text = ""
                break
            except Exception as exc:
                status = "error"
                if isinstance(exc, asyncio.TimeoutError):
                    error_text = f"timeout after {timeout_ms}ms"
                else:
                    error_text = str(exc)
                result_text = f"Error: {error_text}"
                if attempt_index < attempts_total - 1:
                    continue

        if status == "error":
            if on_error == "continue":
                status = "warning"
                result_text = node_prev
            elif on_error == "fallback":
                status = "warning"
                fallback_template = str(config.get("fallback_output", "{{prev}}"))
                fallback_ctx: Dict[str, Any] = {
                    "input": str(input_text or ""),
                    "prev": node_prev,
                    "node_outputs": ctx.get("node_outputs", {}),
                    "error": error_text,
                }
                result_text = str(_render_workflow_template(fallback_template, fallback_ctx))
            else:
                node_duration_ms = max(0, int((time.perf_counter() - node_started_at) * 1000))
                trace.append(
                    {
                        "node_id": current_id,
                        "node_type": node_type,
                        "status": "error",
                        "output": result_text,
                        "error": error_text,
                        "attempts_used": attempts_used,
                        "attempts_total": attempts_total,
                        "timeout_ms": timeout_ms,
                        "on_error": on_error,
                        "duration_ms": node_duration_ms,
                    }
                )
                metrics = _summarize_trace(trace)
                metrics["total_duration_ms"] = max(0, int((time.perf_counter() - run_started_at) * 1000))
                return {
                    "status": "error",
                    "error": error_text or "node_execution_failed",
                    "failed_node_id": current_id,
                    "trace": trace,
                    "metrics": metrics,
                }

        if node_type == "output":
            final_output = result_text

        node_duration_ms = max(0, int((time.perf_counter() - node_started_at) * 1000))
        trace.append(
            {
                "node_id": current_id,
                "node_type": node_type,
                "status": status,
                "output": result_text,
                "error": error_text,
                "attempts_used": attempts_used,
                "attempts_total": attempts_total,
                "timeout_ms": timeout_ms,
                "on_error": on_error,
                "duration_ms": node_duration_ms,
            }
        )
        ctx["node_outputs"][current_id] = result_text
        ctx["prev"] = result_text
        completed.add(current_id)

        outgoing_edges = outgoing.get(current_id, [])
        selected_targets: set[str] = set()
        if status != "error":
            if node_type == "condition" and condition_outcome is not None:
                tagged_edges = [edge for edge in outgoing_edges if edge.get("when") in {"true", "false", "default"}]
                if tagged_edges:
                    branch_key = "true" if condition_outcome else "false"
                    matched = [edge for edge in tagged_edges if edge.get("when") == branch_key]
                    if not matched:
                        matched = [edge for edge in tagged_edges if edge.get("when") in {"default", ""}]
                    selected_targets = {str(edge.get("target", "")).strip() for edge in matched}
                else:
                    fallback = outgoing_edges[0:1]
                    selected_targets = {str(edge.get("target", "")).strip() for edge in fallback}
            else:
                selected_targets = {str(edge.get("target", "")).strip() for edge in outgoing_edges}

        for edge in outgoing_edges:
            target = str(edge.get("target", "")).strip()
            if target not in pending_predecessors:
                continue
            if target in selected_targets:
                incoming_values[target].append(result_text)
            pending_predecessors[target] = max(0, pending_predecessors[target] - 1)
            if pending_predecessors[target] == 0 and target not in completed and target not in queued:
                queue.append(target)
                queued.add(target)

    remaining_nodes = [node_id for node_id in node_map.keys() if node_id not in completed]
    if not final_output:
        final_output = str(ctx.get("prev", ""))
    metrics = _summarize_trace(trace)
    metrics["total_duration_ms"] = max(0, int((time.perf_counter() - run_started_at) * 1000))
    return {
        "status": "ok",
        "output": final_output,
        "trace": trace,
        "metrics": metrics,
        "truncated": visited >= max_steps or bool(remaining_nodes),
        "remaining_nodes": remaining_nodes,
    }

