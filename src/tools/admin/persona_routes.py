"""Persona-domain debug routes.

Extracted from ``debug.py`` to reduce file size.  Covers:
- Persona mental process (CRUD, versions, diff, replay, rollback)
- Persona runtime signals
- Persona tool-policy linkage
- Persona consistency weekly report
- Persona-memory joint drift report
- Memory extraction quality report
- Persona runtime correction simulation
- Persona eval datasets and runs
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Response
from typing import Dict, Any, List, Optional
import logging
import json
import yaml
import time
import io
import csv

from tools.admin._shared import (
    config, _redact_config,
    _resolve_export_output_path,
    _append_policy_audit, _capture_strategy_snapshot,
    _persona_runtime_thresholds,
    PERSONA_EVAL_MANAGER, PERSONA_RUNTIME_MANAGER,
)
from tools.admin.auth import verify_admin_token

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from eval.persona_consistency import PersonaConsistencyManager
    from soul.persona_runtime import PersonaRuntimeManager

app = APIRouter()
logger = logging.getLogger("GazerAdminAPI")


# --- Manager accessors ---
def _get_persona_eval_manager():
    global PERSONA_EVAL_MANAGER
    if PERSONA_EVAL_MANAGER is None:
        from eval.persona_consistency import PersonaConsistencyManager
        PERSONA_EVAL_MANAGER = PersonaConsistencyManager()
    return PERSONA_EVAL_MANAGER

def _get_persona_runtime_manager():
    global PERSONA_RUNTIME_MANAGER
    if PERSONA_RUNTIME_MANAGER is None:
        from soul.persona_runtime import PersonaRuntimeManager
        PERSONA_RUNTIME_MANAGER = PersonaRuntimeManager()
    return PERSONA_RUNTIME_MANAGER

# Lazy cross-module imports
_lazy_cache: dict = {}

def _lazy(name: str):
    if name not in _lazy_cache:
        _LAZY_MAP = {
            "_build_persona_consistency_weekly_report": ("tools.admin.system", "_build_persona_consistency_weekly_report"),
            "_latest_persona_consistency_signal": ("tools.admin.system", "_latest_persona_consistency_signal"),
            "_build_memory_extraction_quality_report": ("tools.admin.memory", "_build_memory_extraction_quality_report"),
            "_build_persona_memory_joint_drift_report": ("tools.admin.memory", "_build_persona_memory_joint_drift_report"),
            "_get_memory_manager": ("tools.admin.memory", "_get_memory_manager"),
            "_append_alert": ("tools.admin.observability", "_append_alert"),
        }
        mod_path, attr = _LAZY_MAP[name]
        import importlib
        mod = importlib.import_module(mod_path)
        _lazy_cache[name] = getattr(mod, attr)
    return _lazy_cache[name]

def _build_persona_consistency_weekly_report(*a, **kw): return _lazy("_build_persona_consistency_weekly_report")(*a, **kw)
def _latest_persona_consistency_signal(*a, **kw): return _lazy("_latest_persona_consistency_signal")(*a, **kw)
def _build_memory_extraction_quality_report(*a, **kw): return _lazy("_build_memory_extraction_quality_report")(*a, **kw)
def _build_persona_memory_joint_drift_report(*a, **kw): return _lazy("_build_persona_memory_joint_drift_report")(*a, **kw)
def _get_memory_manager(*a, **kw): return _lazy("_get_memory_manager")(*a, **kw)
def _append_alert(*a, **kw): return _lazy("_append_alert")(*a, **kw)


@app.get("/debug/persona/mental-process", dependencies=[Depends(verify_admin_token)])
async def get_persona_mental_process():
    mental = config.get("personality.mental_process", {}) or {}
    if not isinstance(mental, dict):
        mental = {}
    runtime_mgr = _get_persona_runtime_manager()
    latest_version = runtime_mgr.list_mental_process_versions(limit=1)
    return {
        "status": "ok",
        "mental_process": mental,
        "yaml": yaml.safe_dump(mental, sort_keys=False, allow_unicode=True),
        "latest_version": latest_version[0] if latest_version else None,
    }

@app.post("/debug/persona/mental-process", dependencies=[Depends(verify_admin_token)])
async def update_persona_mental_process(payload: Dict[str, Any]):
    yaml_text = str(payload.get("yaml", "")).strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="'yaml' is required")
    try:
        parsed = yaml.safe_load(yaml_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Mental process YAML must be an object")
    states = parsed.get("states", [])
    if not isinstance(states, list) or not states:
        raise HTTPException(status_code=400, detail="'states' must be a non-empty array")

    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    runtime_mgr = _get_persona_runtime_manager()
    before = config.get("personality.mental_process", {}) or {}
    if not isinstance(before, dict):
        before = {}
    before_version = runtime_mgr.create_mental_process_version(
        mental_process=before,
        actor=actor,
        note="snapshot_before_update",
        source="snapshot_before_update",
    )
    config.set_many({"personality.mental_process": parsed})
    config.save()
    after_version = runtime_mgr.create_mental_process_version(
        mental_process=parsed,
        actor=actor,
        note=note or "manual_update",
        source="manual_update",
        related_version_id=str(before_version.get("version_id", "")),
    )
    _append_policy_audit(
        action="persona.mental_process.updated",
        details={
            "state_count": len(states),
            "actor": actor,
            "version_id": after_version.get("version_id", ""),
        },
    )
    _capture_strategy_snapshot(
        category="persona_mental_process",
        before={"personality.mental_process": before},
        after={"personality.mental_process": parsed},
        actor=actor,
        source="/debug/persona/mental-process",
        metadata={"version_id": after_version.get("version_id", "")},
    )
    return {
        "status": "ok",
        "mental_process": parsed,
        "version": after_version,
    }

@app.get("/debug/persona/mental-process/versions", dependencies=[Depends(verify_admin_token)])
async def list_persona_mental_process_versions(limit: int = 50):
    runtime_mgr = _get_persona_runtime_manager()
    items = runtime_mgr.list_mental_process_versions(limit=max(1, min(limit, 500)))
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/persona/mental-process/versions/diff", dependencies=[Depends(verify_admin_token)])
async def diff_persona_mental_process_versions(from_version_id: str, to_version_id: str):
    runtime_mgr = _get_persona_runtime_manager()
    payload = runtime_mgr.diff_mental_process_versions(
        from_version_id=str(from_version_id or "").strip(),
        to_version_id=str(to_version_id or "").strip(),
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Mental process version not found")
    return {"status": "ok", "diff": payload}

@app.get("/debug/persona/mental-process/versions/replay", dependencies=[Depends(verify_admin_token)])
async def replay_persona_mental_process_versions(limit: int = 50, start_version_id: Optional[str] = None):
    runtime_mgr = _get_persona_runtime_manager()
    items = runtime_mgr.replay_mental_process_versions(
        limit=max(1, min(limit, 500)),
        start_version_id=str(start_version_id or "").strip(),
    )
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/persona/mental-process/versions/{version_id}", dependencies=[Depends(verify_admin_token)])
async def get_persona_mental_process_version(version_id: str):
    runtime_mgr = _get_persona_runtime_manager()
    payload = runtime_mgr.get_mental_process_version(version_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Mental process version not found")
    return {"status": "ok", "version": payload}

@app.post("/debug/persona/mental-process/rollback", dependencies=[Depends(verify_admin_token)])
async def rollback_persona_mental_process(payload: Dict[str, Any]):
    version_id = str(payload.get("version_id", "")).strip()
    fast = bool(payload.get("fast", False))
    runtime_mgr = _get_persona_runtime_manager()
    if not version_id and not fast:
        raise HTTPException(status_code=400, detail="'version_id' is required when fast=false")
    target = runtime_mgr.get_mental_process_version(version_id) if version_id else None
    if target is None and fast:
        current = config.get("personality.mental_process", {}) or {}
        if not isinstance(current, dict):
            current = {}
        target = runtime_mgr.find_fast_rollback_target(current_mental_process=current)
        if target is not None:
            version_id = str(target.get("version_id", "")).strip()
    if target is None:
        raise HTTPException(status_code=404, detail="Mental process version not found")
    mental_process = target.get("mental_process", {})
    if not isinstance(mental_process, dict):
        raise HTTPException(status_code=400, detail="Version payload is invalid")
    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip() or f"rollback_to:{version_id}"
    config.set_many({"personality.mental_process": mental_process})
    config.save()
    created = runtime_mgr.create_mental_process_version(
        mental_process=mental_process,
        actor=actor,
        note=note,
        source="rollback",
        related_version_id=version_id,
    )
    _append_policy_audit(
        action="persona.mental_process.rollback",
        details={
            "target_version_id": version_id,
            "new_version_id": created.get("version_id", ""),
            "actor": actor,
            "fast": fast,
        },
    )
    return {
        "status": "ok",
        "mental_process": mental_process,
        "rolled_back_from": version_id,
        "fast_selected": fast,
        "version": created,
    }

@app.get("/debug/persona/runtime-signals", dependencies=[Depends(verify_admin_token)])
async def list_persona_runtime_signals(limit: int = 100, level: Optional[str] = None, source: Optional[str] = None):
    runtime_mgr = _get_persona_runtime_manager()
    items = runtime_mgr.list_signals(limit=max(1, min(limit, 500)), level=level, source=source)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/persona/runtime-signals/latest", dependencies=[Depends(verify_admin_token)])
async def get_latest_persona_runtime_signal(source: Optional[str] = None):
    runtime_mgr = _get_persona_runtime_manager()
    signal = runtime_mgr.get_latest_signal(source=source)
    if signal is None:
        return {"status": "ok", "signal": None, "note": "No runtime signal yet."}
    return {"status": "ok", "signal": signal}

@app.get("/debug/persona/tool-policy-linkage", dependencies=[Depends(verify_admin_token)])
async def get_persona_tool_policy_linkage_status(source: Optional[str] = None):
    runtime_cfg = config.get("personality.runtime", {}) or {}
    if not isinstance(runtime_cfg, dict):
        runtime_cfg = {}
    runtime_mgr = _get_persona_runtime_manager()
    signal = runtime_mgr.get_latest_signal(source=source)
    linkage = evaluate_persona_tool_policy_linkage(runtime_cfg=runtime_cfg, signal=signal)
    overlay = linkage.get("policy_overlay", {}) if isinstance(linkage.get("policy_overlay"), dict) else {}

    groups_raw = config.get("security.tool_groups", {})
    groups = groups_raw if isinstance(groups_raw, dict) else {}
    base = normalize_tool_policy(_resolve_global_policy(), groups)
    allow_names = set(base.allow_names)
    deny_names = set(base.deny_names) | {
        str(item).strip() for item in (overlay.get("deny_names", []) or []) if str(item).strip()
    }
    allow_providers = set(base.allow_providers)
    deny_providers = set(base.deny_providers) | {
        str(item).strip() for item in (overlay.get("deny_providers", []) or []) if str(item).strip()
    }
    allow_model_providers = set(base.allow_model_providers)
    deny_model_providers = set(base.deny_model_providers) | {
        str(item).strip().lower()
        for item in (overlay.get("deny_model_providers", []) or [])
        if str(item).strip()
    }
    allow_model_names = set(base.allow_model_names)
    deny_model_names = set(base.deny_model_names) | {
        str(item).strip().lower()
        for item in (overlay.get("deny_model_names", []) or [])
        if str(item).strip()
    }
    allow_model_selectors = set(base.allow_model_selectors)
    deny_model_selectors = set(base.deny_model_selectors) | {
        str(item).strip().lower()
        for item in (overlay.get("deny_model_selectors", []) or [])
        if str(item).strip()
    }
    incoming_allow_names = {
        str(item).strip() for item in (overlay.get("allow_names", []) or []) if str(item).strip()
    }
    if incoming_allow_names:
        allow_names = allow_names.intersection(incoming_allow_names) if allow_names else incoming_allow_names
    incoming_allow_providers = {
        str(item).strip() for item in (overlay.get("allow_providers", []) or []) if str(item).strip()
    }
    if incoming_allow_providers:
        allow_providers = (
            allow_providers.intersection(incoming_allow_providers) if allow_providers else incoming_allow_providers
        )
    incoming_allow_model_providers = {
        str(item).strip().lower()
        for item in (overlay.get("allow_model_providers", []) or [])
        if str(item).strip()
    }
    if incoming_allow_model_providers:
        allow_model_providers = (
            allow_model_providers.intersection(incoming_allow_model_providers)
            if allow_model_providers
            else incoming_allow_model_providers
        )
    incoming_allow_model_names = {
        str(item).strip().lower()
        for item in (overlay.get("allow_model_names", []) or [])
        if str(item).strip()
    }
    if incoming_allow_model_names:
        allow_model_names = (
            allow_model_names.intersection(incoming_allow_model_names)
            if allow_model_names
            else incoming_allow_model_names
        )
    incoming_allow_model_selectors = {
        str(item).strip().lower()
        for item in (overlay.get("allow_model_selectors", []) or [])
        if str(item).strip()
    }
    if incoming_allow_model_selectors:
        allow_model_selectors = (
            allow_model_selectors.intersection(incoming_allow_model_selectors)
            if allow_model_selectors
            else incoming_allow_model_selectors
        )
    if allow_names and deny_names:
        allow_names = {item for item in allow_names if item not in deny_names}
    if allow_providers and deny_providers:
        allow_providers = {item for item in allow_providers if item not in deny_providers}
    if allow_model_providers and deny_model_providers:
        allow_model_providers = {
            item for item in allow_model_providers if item not in deny_model_providers
        }
    if allow_model_names and deny_model_names:
        allow_model_names = {item for item in allow_model_names if item not in deny_model_names}
    if allow_model_selectors and deny_model_selectors:
        allow_model_selectors = {
            item for item in allow_model_selectors if item not in deny_model_selectors
        }

    effective = ToolPolicy(
        allow_names=allow_names,
        deny_names=deny_names,
        allow_providers=allow_providers,
        deny_providers=deny_providers,
        allow_model_providers=allow_model_providers,
        deny_model_providers=deny_model_providers,
        allow_model_names=allow_model_names,
        deny_model_names=deny_model_names,
        allow_model_selectors=allow_model_selectors,
        deny_model_selectors=deny_model_selectors,
    )
    return {
        "status": "ok",
        "linkage": linkage,
        "effective_policy": _policy_to_payload(effective),
        "overlay_counts": {
            "allow_names": len(list(overlay.get("allow_names", []) or [])),
            "deny_names": len(list(overlay.get("deny_names", []) or [])),
            "allow_providers": len(list(overlay.get("allow_providers", []) or [])),
            "deny_providers": len(list(overlay.get("deny_providers", []) or [])),
            "allow_model_providers": len(list(overlay.get("allow_model_providers", []) or [])),
            "deny_model_providers": len(list(overlay.get("deny_model_providers", []) or [])),
            "allow_model_names": len(list(overlay.get("allow_model_names", []) or [])),
            "deny_model_names": len(list(overlay.get("deny_model_names", []) or [])),
            "allow_model_selectors": len(list(overlay.get("allow_model_selectors", []) or [])),
            "deny_model_selectors": len(list(overlay.get("deny_model_selectors", []) or [])),
        },
    }

@app.get("/debug/persona/consistency/weekly-report", dependencies=[Depends(verify_admin_token)])
async def get_persona_consistency_weekly_report(window_days: int = 7, source: str = "persona_eval"):
    return _build_persona_consistency_weekly_report(window_days=window_days, source=source)

@app.post("/debug/persona/consistency/weekly-report/export", dependencies=[Depends(verify_admin_token)])
async def export_persona_consistency_weekly_report(payload: Dict[str, Any]):
    window_days_raw = payload.get("window_days", 7)
    source = str(payload.get("source", "persona_eval")).strip() or "persona_eval"
    try:
        window_days = int(window_days_raw)
    except (TypeError, ValueError):
        window_days = 7
    report = _build_persona_consistency_weekly_report(window_days=window_days, source=source)

    stamp = time.strftime("%Y-%m-%d")
    output_path = _resolve_export_output_path(
        output_raw=str(payload.get("output_path", "")).strip(),
        default_filename=f"PERSONA_CONSISTENCY_WEEKLY_{stamp}.md",
    )

    current = report.get("current_window", {}) if isinstance(report.get("current_window"), dict) else {}
    previous = report.get("previous_window", {}) if isinstance(report.get("previous_window"), dict) else {}
    trend = report.get("trend", {}) if isinstance(report.get("trend"), dict) else {}
    lines = [
        "# Persona Consistency Weekly Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_days: {report.get('window_days')}",
        f"- source: {report.get('source')}",
        "",
        "## Current Window",
        f"- signal_total: {current.get('signal_total', 0)}",
        f"- warning: {(current.get('levels', {}) or {}).get('warning', 0)}",
        f"- critical: {(current.get('levels', {}) or {}).get('critical', 0)}",
        f"- consistency_score_avg: {current.get('consistency_score_avg')}",
        "",
        "## Previous Window",
        f"- signal_total: {previous.get('signal_total', 0)}",
        f"- warning: {(previous.get('levels', {}) or {}).get('warning', 0)}",
        f"- critical: {(previous.get('levels', {}) or {}).get('critical', 0)}",
        f"- consistency_score_avg: {previous.get('consistency_score_avg')}",
        "",
        "## Trend",
        f"- direction: {trend.get('direction', 'stable')}",
        f"- warning_delta: {trend.get('warning_delta', 0)}",
        f"- critical_delta: {trend.get('critical_delta', 0)}",
        f"- consistency_score_delta: {trend.get('consistency_score_delta')}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "path": str(output_path), "report": report}

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

@app.post("/debug/persona/runtime-correction/simulate", dependencies=[Depends(verify_admin_token)])
async def simulate_persona_runtime_correction(payload: Dict[str, Any]):
    content = str(payload.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="'content' is required")
    language = str(payload.get("language", "zh")).strip() or "zh"
    strategy = str(payload.get("strategy", "rewrite")).strip().lower() or "rewrite"
    runtime_cfg = config.get("personality.runtime", {}) or {}
    auto_cfg = runtime_cfg.get("auto_correction", {}) if isinstance(runtime_cfg, dict) else {}
    if not isinstance(auto_cfg, dict):
        auto_cfg = {}
    trigger_levels = auto_cfg.get("trigger_levels", ["critical"])
    if not isinstance(trigger_levels, list):
        trigger_levels = ["critical"]
    runtime_mgr = _get_persona_runtime_manager()
    thresholds = _persona_runtime_thresholds()
    processed = runtime_mgr.process_output(
        content=content,
        source="simulate",
        run_id=str(payload.get("run_id", "")).strip(),
        language=language,
        auto_correct_enabled=True,
        strategy=strategy,
        trigger_levels=[str(item).strip().lower() for item in trigger_levels if str(item).strip()],
        metadata={"simulate": True},
        retain=int(thresholds.get("retain", 500)),
        ab_config=auto_cfg.get("ab", {}) if isinstance(auto_cfg.get("ab", {}), dict) else {},
        assignment_key=str(payload.get("ab_key", payload.get("run_id", "simulate"))).strip() or "simulate",
    )
    return {"status": "ok", "result": processed}

@app.post("/debug/persona-eval/datasets/build", dependencies=[Depends(verify_admin_token)])
async def build_persona_eval_dataset(payload: Dict[str, Any]):
    manager = _get_persona_eval_manager()
    name = str(payload.get("name", "default")).strip() or "default"
    system_prompt = str(config.get("personality.system_prompt", ""))
    dataset = manager.build_dataset(name=name, system_prompt=system_prompt)
    return {"status": "ok", "dataset": dataset}

@app.get("/debug/persona-eval/datasets", dependencies=[Depends(verify_admin_token)])
async def list_persona_eval_datasets(limit: int = 50):
    manager = _get_persona_eval_manager()
    items = manager.list_datasets(limit=limit)
    return {"status": "ok", "items": items, "total": len(items)}

@app.post("/debug/persona-eval/datasets/{dataset_id}/run", dependencies=[Depends(verify_admin_token)])
async def run_persona_eval_dataset(dataset_id: str, payload: Dict[str, Any]):
    manager = _get_persona_eval_manager()
    outputs_raw = payload.get("outputs", {})
    outputs = outputs_raw if isinstance(outputs_raw, dict) else {}
    if not outputs and bool(payload.get("auto_generate", False)):
        generated = manager.generate_outputs(
            dataset_id,
            system_prompt=str(config.get("personality.system_prompt", "")),
        )
        if generated is None:
            raise HTTPException(status_code=404, detail="Persona eval dataset not found")
        outputs = generated
    safe_outputs = {str(k): str(v) for k, v in outputs.items()}
    report = manager.run_dataset(dataset_id, outputs=safe_outputs)
    if report is None:
        raise HTTPException(status_code=404, detail="Persona eval dataset not found")
    runtime_cfg = _persona_runtime_thresholds()
    runtime_mgr = _get_persona_runtime_manager()
    runtime_signal = runtime_mgr.assess_eval_report(
        report=report,
        dataset_id=dataset_id,
        warning_score=float(runtime_cfg.get("warning_score", 0.82)),
        critical_score=float(runtime_cfg.get("critical_score", 0.70)),
    )
    if bool(runtime_cfg.get("enabled", True)) and bool(runtime_cfg.get("signals_enabled", True)):
        runtime_mgr.record_signal(runtime_signal, retain=int(runtime_cfg.get("retain", 500)))

    corrected_outputs: Optional[Dict[str, str]] = None
    correction_policy: Optional[Dict[str, Dict[str, Any]]] = None
    auto_correct = bool(payload.get("auto_correct", False))
    if auto_correct and str(runtime_signal.get("level", "healthy")).lower() in {"warning", "critical"}:
        correction_cfg = (config.get("personality.runtime.auto_correction", {}) or {})
        if not isinstance(correction_cfg, dict):
            correction_cfg = {}
        strategy = str(correction_cfg.get("strategy", payload.get("strategy", "rewrite"))).strip().lower() or "rewrite"
        language = str(payload.get("language", "zh")).strip() or "zh"
        corrected_outputs = {}
        correction_policy = {}
        ab_cfg = correction_cfg.get("ab", {}) if isinstance(correction_cfg.get("ab", {}), dict) else {}
        for key, text in safe_outputs.items():
            key_lc = str(key).strip().lower()
            preferred: List[str] = []
            if "identity" in key_lc:
                preferred.append("identity_consistency")
            if "safety" in key_lc:
                preferred.append("safety_consistency")
            per_output_violations: List[str] = list(preferred)
            for item in list(runtime_signal.get("violations", [])):
                violation = str(item).strip()
                if violation and violation not in per_output_violations:
                    per_output_violations.append(violation)
            ab_decision = runtime_mgr.resolve_ab_strategy(
                ab_config=ab_cfg,
                assignment_key=f"{dataset_id}:{key}",
                violations=per_output_violations,
                default_strategy=strategy,
            )
            applied_strategy = str(ab_decision.get("strategy", strategy)).strip().lower() or strategy
            corrected_outputs[key] = runtime_mgr.apply_correction(
                content=text,
                strategy=applied_strategy,
                language=language,
                violations=per_output_violations,
            )
            correction_policy[key] = {
                "strategy": applied_strategy,
                "ab_enabled": bool(ab_decision.get("enabled", False)),
                "ab_profile": str(ab_decision.get("profile", "")),
                "ab_reason": str(ab_decision.get("reason", "")),
            }

    _append_policy_audit(
        action="persona.eval.ran",
        details={
            "dataset_id": dataset_id,
            "consistency_score": report.get("consistency_score", 0.0),
            "auto_passed": report.get("auto_passed", False),
            "runtime_level": runtime_signal.get("level", "healthy"),
            "auto_correct": auto_correct,
        },
    )
    response: Dict[str, Any] = {
        "status": "ok",
        "report": report,
        "runtime_signal": runtime_signal,
    }
    if corrected_outputs is not None:
        response["corrected_outputs"] = corrected_outputs
    if correction_policy is not None:
        response["correction_policy"] = correction_policy
    return response

@app.get("/debug/persona-eval/datasets/{dataset_id}/runs", dependencies=[Depends(verify_admin_token)])
async def list_persona_eval_runs(dataset_id: str, limit: int = 20):
    manager = _get_persona_eval_manager()
    dataset = manager.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Persona eval dataset not found")
    items = manager.list_runs(dataset_id, limit=limit)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/persona-eval/datasets/{dataset_id}/latest", dependencies=[Depends(verify_admin_token)])
async def get_latest_persona_eval_run(dataset_id: str):
    manager = _get_persona_eval_manager()
    dataset = manager.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Persona eval dataset not found")
    latest = manager.get_latest_run(dataset_id)
    if latest is None:
        raise HTTPException(status_code=404, detail="No persona eval run found for this dataset")
    return {"status": "ok", "report": latest}
