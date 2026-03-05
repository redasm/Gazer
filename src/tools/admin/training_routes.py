"""Training-domain debug routes.

Extracted from ``debug.py`` to reduce file size.  Covers:
- Training jobs (CRUD, run, publish, release lifecycle)
- Training bridge (export, compare, sample store)
- Online policy loop (candidates, off-policy eval, gate check, publish)
- Training experiments
- Bootstrap pipeline
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Response
from typing import Dict, Any, List, Optional
import logging
import json
import yaml
import time

from tools.admin.auth import verify_admin_token

from typing import TYPE_CHECKING
from tools.admin.coding_helpers import TASK_RUN_STORE
from tools.admin.state import (
    EVAL_BENCHMARK_MANAGER,
    ONLINE_POLICY_LOOP_MANAGER,
    TRAINING_BRIDGE_MANAGER,
    TRAINING_JOB_MANAGER,
    TRAJECTORY_STORE,
    config,
)
from tools.admin.strategy_helpers import _append_policy_audit, _capture_strategy_snapshot, _get_release_gate_health_thresholds
from tools.admin.training_helpers import (
    _build_training_publish_diff,
    _build_training_release_explanation,
    _evaluate_training_release_canary_guard,
    _prepare_training_inputs,
    _resolve_online_policy_gate_thresholds,
    _resolve_online_policy_offpolicy_config,
    _resolve_training_publish_rollout,
    _resolve_training_release_approval,
    _score_training_job,
)
from tools.admin.utils import _resolve_export_output_path
if TYPE_CHECKING:
    from eval.trainer import TrainingJobManager
    from eval.training_bridge import TrainingBridgeManager
    from eval.benchmark import EvalBenchmarkManager
    from eval.online_policy_loop import OnlinePolicyLoopManager

app = APIRouter()
logger = logging.getLogger("GazerAdminAPI")


# --- Manager accessors (mirrors debug.py lazy-init pattern) ---

def _get_eval_benchmark_manager():
    global EVAL_BENCHMARK_MANAGER
    if EVAL_BENCHMARK_MANAGER is None:
        from eval.benchmark import EvalBenchmarkManager
        EVAL_BENCHMARK_MANAGER = EvalBenchmarkManager()
    return EVAL_BENCHMARK_MANAGER

def _get_training_job_manager():
    global TRAINING_JOB_MANAGER
    if TRAINING_JOB_MANAGER is None:
        from eval.trainer import TrainingJobManager
        TRAINING_JOB_MANAGER = TrainingJobManager()
    return TRAINING_JOB_MANAGER

def _get_training_bridge_manager():
    global TRAINING_BRIDGE_MANAGER
    if TRAINING_BRIDGE_MANAGER is None:
        from eval.training_bridge import TrainingBridgeManager
        TRAINING_BRIDGE_MANAGER = TrainingBridgeManager()
    return TRAINING_BRIDGE_MANAGER

def _get_online_policy_loop_manager():
    global ONLINE_POLICY_LOOP_MANAGER
    if ONLINE_POLICY_LOOP_MANAGER is None:
        from eval.online_policy_loop import OnlinePolicyLoopManager
        ONLINE_POLICY_LOOP_MANAGER = OnlinePolicyLoopManager()
    return ONLINE_POLICY_LOOP_MANAGER

def _collect_training_bridge_trajectories(*, run_ids: List[str], limit: int) -> List[Dict[str, Any]]:
    if TRAJECTORY_STORE is None:
        return []
    selected_ids: List[str] = []
    if run_ids:
        for item in run_ids:
            rid = str(item).strip()
            if rid and rid not in selected_ids:
                selected_ids.append(rid)
    else:
        recent = TRAJECTORY_STORE.list_recent(limit=limit)
        for item in recent:
            rid = str((item or {}).get("run_id", "")).strip()
            if rid and rid not in selected_ids:
                selected_ids.append(rid)
    trajectories: List[Dict[str, Any]] = []
    for rid in selected_ids[:limit]:
        payload = TRAJECTORY_STORE.get_trajectory(rid)
        if isinstance(payload, dict):
            trajectories.append(payload)
    return trajectories

def _latest_eval_context_by_run(dataset_id: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"eval_by_run": {}, "release_gate": {}}
    target_dataset_id = str(dataset_id).strip()
    if not target_dataset_id:
        return result
    try:
        manager = _get_eval_benchmark_manager()
    except Exception:
        return result
    latest = manager.get_latest_run(target_dataset_id)
    if isinstance(latest, dict):
        eval_by_run: Dict[str, Dict[str, Any]] = {}
        for item in list(latest.get("results", []) or []):
            if not isinstance(item, dict):
                continue
            run_id = str(item.get("run_id", "")).strip()
            if run_id:
                eval_by_run[run_id] = item
        result["eval_by_run"] = eval_by_run
    gate = manager.get_release_gate_status()
    result["release_gate"] = gate if isinstance(gate, dict) else {}
    return result


@app.get("/debug/training-jobs", dependencies=[Depends(verify_admin_token)])
async def list_training_jobs(limit: int = 50, status: Optional[str] = None):
    manager = _get_training_job_manager()
    items = manager.list_jobs(limit=limit, status=status)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/training-bridge/exports", dependencies=[Depends(verify_admin_token)])
async def list_training_bridge_exports(limit: int = 50, dataset_id: Optional[str] = None):
    manager = _get_training_bridge_manager()
    items = manager.list_exports(limit=max(1, min(limit, 500)), dataset_id=dataset_id)
    return {"status": "ok", "items": items, "total": len(items)}

@app.post("/debug/training-bridge/exports", dependencies=[Depends(verify_admin_token)])
async def create_training_bridge_export(payload: Dict[str, Any]):
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")
    dataset_id = str(payload.get("dataset_id", "bridge_manual")).strip() or "bridge_manual"
    source = str(payload.get("source", "trajectory_export")).strip() or "trajectory_export"
    include_eval_raw = payload.get("include_eval_context", True)
    include_eval_context = str(include_eval_raw).strip().lower() not in {"0", "false", "no", "off"}
    limit_raw = payload.get("limit", 200)
    try:
        limit = max(1, min(int(limit_raw), 1000))
    except (TypeError, ValueError):
        limit = 200
    run_ids_raw = payload.get("run_ids", [])
    run_ids = [str(item).strip() for item in run_ids_raw if str(item).strip()] if isinstance(run_ids_raw, list) else []
    trajectories = _collect_training_bridge_trajectories(run_ids=run_ids, limit=limit)
    if not trajectories:
        raise HTTPException(status_code=404, detail="No trajectories found for export")

    eval_context = _latest_eval_context_by_run(dataset_id) if include_eval_context else {"eval_by_run": {}, "release_gate": {}}
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
    metadata.update(
        {
            "requested_run_ids": run_ids,
            "include_eval_context": include_eval_context,
            "resolved_trajectory_count": len(trajectories),
        }
    )
    manager = _get_training_bridge_manager()
    created = manager.create_export(
        dataset_id=dataset_id,
        trajectories=trajectories,
        source=source,
        metadata=metadata,
        eval_by_run=eval_context.get("eval_by_run", {}),
        release_gate=eval_context.get("release_gate", {}),
    )
    compact = manager.get_export(str(created.get("export_id", "")), include_samples=False) or created
    _append_policy_audit(
        action="trainer.bridge.export.created",
        details={
            "export_id": compact.get("export_id", ""),
            "dataset_id": dataset_id,
            "sample_count": compact.get("sample_count", 0),
            "source": source,
        },
    )
    return {"status": "ok", "export": compact}

@app.get("/debug/training-bridge/exports/{export_id}", dependencies=[Depends(verify_admin_token)])
async def get_training_bridge_export(export_id: str, include_samples: bool = False):
    manager = _get_training_bridge_manager()
    payload = manager.get_export(export_id, include_samples=include_samples)
    if payload is None:
        raise HTTPException(status_code=404, detail="Training bridge export not found")
    return {"status": "ok", "export": payload}

@app.get("/debug/training-bridge/exports/{export_id}/compare", dependencies=[Depends(verify_admin_token)])
async def compare_training_bridge_export(
    export_id: str,
    baseline_export_id: Optional[str] = None,
    baseline_index: int = 1,
):
    manager = _get_training_bridge_manager()
    current = manager.get_export(export_id, include_samples=False)
    if current is None:
        raise HTTPException(status_code=404, detail="Training bridge export not found")

    baseline_target = str(baseline_export_id or "").strip()
    if not baseline_target:
        try:
            offset = max(1, min(int(baseline_index), 50))
        except (TypeError, ValueError):
            offset = 1
        dataset_id = str(current.get("dataset_id", "")).strip()
        history = manager.list_exports(limit=500, dataset_id=dataset_id or None)
        history_ids = [str(item.get("export_id", "")) for item in history if str(item.get("export_id", ""))]
        if export_id not in history_ids:
            history_ids.insert(0, export_id)
        current_index = history_ids.index(export_id)
        target_index = current_index + offset
        if target_index >= len(history_ids):
            raise HTTPException(status_code=404, detail="Not enough export history to compare")
        baseline_target = history_ids[target_index]

    comparison = manager.compare_exports(candidate_export_id=export_id, baseline_export_id=baseline_target)
    if comparison is None:
        raise HTTPException(status_code=404, detail="Baseline export not found")
    return {"status": "ok", "comparison": comparison}

@app.get("/debug/training-bridge/compare/latest", dependencies=[Depends(verify_admin_token)])
async def compare_training_bridge_latest(dataset_id: str, baseline_index: int = 1):
    target_dataset_id = str(dataset_id).strip()
    if not target_dataset_id:
        raise HTTPException(status_code=400, detail="'dataset_id' is required")
    try:
        idx = max(1, min(int(baseline_index), 50))
    except (TypeError, ValueError):
        idx = 1
    manager = _get_training_bridge_manager()
    comparison = manager.compare_with_baseline(target_dataset_id, baseline_index=idx)
    if comparison is None:
        raise HTTPException(status_code=404, detail="Not enough export history to compare")
    return {"status": "ok", "comparison": comparison}

@app.get("/debug/training-bridge/exports/{export_id}/training-inputs", dependencies=[Depends(verify_admin_token)])
async def get_training_bridge_training_inputs(export_id: str):
    manager = _get_training_bridge_manager()
    inputs = manager.to_training_inputs(export_id)
    if inputs is None:
        raise HTTPException(status_code=404, detail="Training bridge export not found")
    return {
        "status": "ok",
        "inputs": inputs,
        "summary": {
            "trajectory_count": len(inputs.get("trajectory_samples", [])),
            "eval_count": len(inputs.get("eval_samples", [])),
        },
    }

@app.post("/debug/training-bridge/exports/{export_id}/to-sample-store", dependencies=[Depends(verify_admin_token)])
async def create_training_sample_store_from_bridge_export(export_id: str, payload: Dict[str, Any]):
    bridge_manager = _get_training_bridge_manager()
    adapted = bridge_manager.to_training_inputs(export_id)
    if adapted is None:
        raise HTTPException(status_code=404, detail="Training bridge export not found")
    dataset_id = str(payload.get("dataset_id", adapted.get("dataset_id", ""))).strip() or "bridge_export"
    source = str(payload.get("source", "training_bridge_export")).strip() or "training_bridge_export"
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
    metadata["export_id"] = export_id
    manager = _get_training_job_manager()
    created = manager.create_sample_store(
        dataset_id=dataset_id,
        trajectory_samples=list(adapted.get("trajectory_samples", [])),
        eval_samples=list(adapted.get("eval_samples", [])),
        source=source,
        metadata=metadata,
    )
    _append_policy_audit(
        action="trainer.bridge.export.adapted",
        details={
            "export_id": export_id,
            "sample_store_id": created.get("store_id", ""),
            "dataset_id": dataset_id,
            "source": source,
        },
    )
    return {"status": "ok", "sample_store": created}

@app.get("/debug/online-policy/candidates", dependencies=[Depends(verify_admin_token)])
async def list_online_policy_candidates(
    limit: int = 50,
    dataset_id: Optional[str] = None,
    status: Optional[str] = None,
):
    manager = _get_online_policy_loop_manager()
    items = manager.list_candidates(limit=max(1, min(limit, 500)), dataset_id=dataset_id, status=status)
    return {"status": "ok", "items": items, "total": len(items)}

@app.post("/debug/online-policy/candidates", dependencies=[Depends(verify_admin_token)])
async def create_online_policy_candidate(payload: Dict[str, Any]):
    loop_cfg = config.get("trainer.online_policy_loop", {}) or {}
    if isinstance(loop_cfg, dict) and not bool(loop_cfg.get("enabled", True)):
        raise HTTPException(status_code=400, detail="trainer.online_policy_loop is disabled")

    bridge_manager = _get_training_bridge_manager()
    training_manager = _get_training_job_manager()
    eval_manager = _get_eval_benchmark_manager()
    loop_manager = _get_online_policy_loop_manager()

    dataset_id = str(payload.get("dataset_id", "")).strip() or None
    export_id = str(payload.get("export_id", "")).strip() or None
    source = str(payload.get("source", "online_policy_loop")).strip() or "online_policy_loop"
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}

    try:
        created = loop_manager.create_candidate_from_bridge(
            bridge_manager=bridge_manager,
            training_manager=training_manager,
            eval_manager=eval_manager,
            dataset_id=dataset_id,
            export_id=export_id,
            source=source,
            metadata=metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    auto_gate_check = bool(payload.get("auto_gate_check", True))
    if auto_gate_check:
        thresholds = _resolve_online_policy_gate_thresholds(payload.get("gate"))
        created = loop_manager.run_gate_check(
            candidate_id=str(created.get("candidate_id", "")),
            gate_status=eval_manager.get_release_gate_status(),
            require_release_gate_open=bool(thresholds.get("require_release_gate_open", True)),
            min_eval_pass_rate=float(thresholds.get("min_eval_pass_rate", 0.55)),
            min_trajectory_success_rate=float(thresholds.get("min_trajectory_success_rate", 0.6)),
            max_terminal_error_rate=float(thresholds.get("max_terminal_error_rate", 0.4)),
        )

    offpolicy_cfg = _resolve_online_policy_offpolicy_config(payload.get("offpolicy"))
    auto_offpolicy_eval = bool(payload.get("auto_offpolicy_eval", offpolicy_cfg.get("auto_run_on_create", True)))
    if bool(offpolicy_cfg.get("enabled", True)) and auto_offpolicy_eval:
        created = loop_manager.run_offpolicy_eval(
            candidate_id=str(created.get("candidate_id", "")),
            bridge_manager=bridge_manager,
            baseline_export_id=str(payload.get("baseline_export_id", "")).strip() or None,
            baseline_index=int(offpolicy_cfg.get("baseline_index", 1)),
            bootstrap_rounds=int(offpolicy_cfg.get("bootstrap_rounds", 300)),
            min_reward_threshold=float(offpolicy_cfg.get("min_reward_threshold", 0.6)),
            min_samples_for_confidence=int(offpolicy_cfg.get("min_samples_for_confidence", 20)),
        )

    _append_policy_audit(
        action="trainer.online_policy.candidate.created",
        details={
            "candidate_id": created.get("candidate_id", ""),
            "dataset_id": created.get("dataset_id", ""),
            "export_id": created.get("export_id", ""),
            "job_id": created.get("job_id", ""),
        },
    )
    return {"status": "ok", "candidate": loop_manager._compact(created)}

@app.post("/debug/online-policy/candidates/{candidate_id}/offpolicy-eval", dependencies=[Depends(verify_admin_token)])
async def run_online_policy_offpolicy_eval(candidate_id: str, payload: Optional[Dict[str, Any]] = None):
    loop_cfg = config.get("trainer.online_policy_loop", {}) or {}
    if isinstance(loop_cfg, dict) and not bool(loop_cfg.get("enabled", True)):
        raise HTTPException(status_code=400, detail="trainer.online_policy_loop is disabled")
    offpolicy_cfg = _resolve_online_policy_offpolicy_config(
        payload.get("offpolicy") if isinstance(payload, dict) else None
    )
    if not bool(offpolicy_cfg.get("enabled", True)):
        raise HTTPException(status_code=400, detail="trainer.online_policy_loop.offpolicy is disabled")

    loop_manager = _get_online_policy_loop_manager()
    bridge_manager = _get_training_bridge_manager()
    baseline_export_id = (
        str(payload.get("baseline_export_id", "")).strip()
        if isinstance(payload, dict)
        else ""
    )
    try:
        updated = loop_manager.run_offpolicy_eval(
            candidate_id=candidate_id,
            bridge_manager=bridge_manager,
            baseline_export_id=baseline_export_id or None,
            baseline_index=int(offpolicy_cfg.get("baseline_index", 1)),
            bootstrap_rounds=int(offpolicy_cfg.get("bootstrap_rounds", 300)),
            min_reward_threshold=float(offpolicy_cfg.get("min_reward_threshold", 0.6)),
            min_samples_for_confidence=int(offpolicy_cfg.get("min_samples_for_confidence", 20)),
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if ("not found" in detail.lower() or "missing" in detail.lower()) else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    _append_policy_audit(
        action="trainer.online_policy.candidate.offpolicy_evaluated",
        details={
            "candidate_id": candidate_id,
            "dataset_id": updated.get("dataset_id", ""),
            "export_id": updated.get("export_id", ""),
            "method": ((updated.get("offpolicy_eval") or {}).get("method") if isinstance(updated, dict) else ""),
        },
    )
    return {"status": "ok", "candidate": updated}

@app.get("/debug/online-policy/candidates/{candidate_id}", dependencies=[Depends(verify_admin_token)])
async def get_online_policy_candidate(candidate_id: str):
    manager = _get_online_policy_loop_manager()
    payload = manager.get_candidate(candidate_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Online policy candidate not found")
    return {"status": "ok", "candidate": payload}

@app.post("/debug/online-policy/candidates/{candidate_id}/gate-check", dependencies=[Depends(verify_admin_token)])
async def run_online_policy_gate_check(candidate_id: str, payload: Optional[Dict[str, Any]] = None):
    manager = _get_online_policy_loop_manager()
    eval_manager = _get_eval_benchmark_manager()
    threshold_cfg = _resolve_online_policy_gate_thresholds(payload.get("gate") if isinstance(payload, dict) else None)
    gate_override = payload.get("release_gate", {}) if isinstance(payload, dict) else {}
    gate_status = gate_override if isinstance(gate_override, dict) and gate_override else eval_manager.get_release_gate_status()
    try:
        updated = manager.run_gate_check(
            candidate_id=candidate_id,
            gate_status=gate_status,
            require_release_gate_open=bool(threshold_cfg.get("require_release_gate_open", True)),
            min_eval_pass_rate=float(threshold_cfg.get("min_eval_pass_rate", 0.55)),
            min_trajectory_success_rate=float(threshold_cfg.get("min_trajectory_success_rate", 0.6)),
            max_terminal_error_rate=float(threshold_cfg.get("max_terminal_error_rate", 0.4)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "candidate": manager._compact(updated)}

@app.post("/debug/online-policy/candidates/{candidate_id}/review", dependencies=[Depends(verify_admin_token)])
async def review_online_policy_candidate(candidate_id: str, payload: Dict[str, Any]):
    manager = _get_online_policy_loop_manager()
    approved = bool(payload.get("approved", False))
    reviewer = str(payload.get("reviewer", payload.get("actor", "admin"))).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    try:
        updated = manager.review_candidate(
            candidate_id=candidate_id,
            approved=approved,
            reviewer=reviewer,
            note=note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _append_policy_audit(
        action="trainer.online_policy.candidate.reviewed",
        details={
            "candidate_id": candidate_id,
            "approved": approved,
            "reviewer": reviewer,
        },
    )
    return {"status": "ok", "candidate": manager._compact(updated)}

@app.post("/debug/online-policy/candidates/{candidate_id}/publish", dependencies=[Depends(verify_admin_token)])
async def publish_online_policy_candidate(candidate_id: str, payload: Dict[str, Any]):
    loop_cfg = config.get("trainer.online_policy_loop", {}) or {}
    if isinstance(loop_cfg, dict) and not bool(loop_cfg.get("enabled", True)):
        raise HTTPException(status_code=400, detail="trainer.online_policy_loop is disabled")
    require_review = True
    if isinstance(loop_cfg, dict):
        require_review = bool(loop_cfg.get("require_review", True))

    loop_manager = _get_online_policy_loop_manager()
    candidate = loop_manager.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Online policy candidate not found")
    if require_review and not bool((candidate.get("review") or {}).get("approved", False)):
        raise HTTPException(status_code=400, detail="Candidate must be reviewed and approved before publish")

    threshold_cfg = _resolve_online_policy_gate_thresholds(payload.get("gate"))
    eval_manager = _get_eval_benchmark_manager()
    candidate = loop_manager.run_gate_check(
        candidate_id=candidate_id,
        gate_status=eval_manager.get_release_gate_status(),
        require_release_gate_open=bool(threshold_cfg.get("require_release_gate_open", True)),
        min_eval_pass_rate=float(threshold_cfg.get("min_eval_pass_rate", 0.55)),
        min_trajectory_success_rate=float(threshold_cfg.get("min_trajectory_success_rate", 0.6)),
        max_terminal_error_rate=float(threshold_cfg.get("max_terminal_error_rate", 0.4)),
    )
    gate_check = candidate.get("gate_check", {}) if isinstance(candidate.get("gate_check"), dict) else {}
    if not bool(gate_check.get("passed", False)):
        raise HTTPException(
            status_code=400,
            detail=f"Candidate gate check failed: {', '.join([str(x) for x in gate_check.get('reasons', [])])}",
        )

    training_manager = _get_training_job_manager()
    job_id = str(candidate.get("job_id", "")).strip()
    job = training_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Linked training job not found")
    if str(job.get("status", "")).strip().lower() != "completed":
        raise HTTPException(status_code=400, detail="Linked training job must be completed before publish")

    dry_run = bool(payload.get("dry_run", False))
    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    rollout_raw = payload.get("rollout", {})
    rollout = _resolve_training_publish_rollout(rollout_raw if isinstance(rollout_raw, dict) else {})
    diff = _build_training_publish_diff(job)
    if not dry_run:
        config.set_many(diff["after"])
        config.save()

    release = training_manager.create_release(
        job_id=job_id,
        actor=actor,
        note=note,
        before=diff["before"],
        after=diff["after"],
        dry_run=dry_run,
        rollout=rollout,
        rollback_rule={"source": "online_policy_loop", "candidate_id": candidate_id},
        strategy_package=diff.get("strategy_package", {}),
        approval={"required": False, "state": "not_required", "approved": False},
    )
    try:
        published = loop_manager.mark_published(
            candidate_id=candidate_id,
            actor=actor,
            note=note,
            release_id=str(release.get("release_id", "")),
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _append_policy_audit(
        action="trainer.online_policy.candidate.published",
        details={
            "candidate_id": candidate_id,
            "job_id": job_id,
            "release_id": release.get("release_id", ""),
            "dry_run": dry_run,
            "actor": actor,
        },
    )
    return {
        "status": "ok",
        "candidate": loop_manager._compact(published),
        "release": release,
        "summary": diff.get("summary", {}),
    }

@app.get("/debug/training-sample-stores", dependencies=[Depends(verify_admin_token)])
async def list_training_sample_stores(limit: int = 50, dataset_id: Optional[str] = None):
    manager = _get_training_job_manager()
    items = manager.list_sample_stores(limit=limit, dataset_id=dataset_id)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/training-sample-stores/{store_id}", dependencies=[Depends(verify_admin_token)])
async def get_training_sample_store(store_id: str):
    manager = _get_training_job_manager()
    payload = manager.get_sample_store(store_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Sample store not found")
    return {"status": "ok", "sample_store": payload}

@app.post("/debug/training-sample-stores/from-benchmark", dependencies=[Depends(verify_admin_token)])
async def create_training_sample_store_from_benchmark(payload: Dict[str, Any]):
    dataset_id = str(payload.get("dataset_id", "")).strip()
    if not dataset_id:
        raise HTTPException(status_code=400, detail="'dataset_id' is required")
    max_samples_raw = payload.get("max_samples", config.get("trainer.max_samples_per_job", 200))
    try:
        max_samples = max(1, min(int(max_samples_raw), 1000))
    except (TypeError, ValueError):
        max_samples = 200

    eval_manager = _get_eval_benchmark_manager()
    latest = eval_manager.get_latest_run(dataset_id)
    if latest is None:
        raise HTTPException(status_code=404, detail="No benchmark run report found for dataset")
    train_inputs = _prepare_training_inputs(dataset_id=dataset_id, report=latest, max_samples=max_samples)
    manager = _get_training_job_manager()
    created = manager.create_sample_store(
        dataset_id=dataset_id,
        trajectory_samples=train_inputs["trajectory_samples"],
        eval_samples=train_inputs["eval_samples"],
        source=str(payload.get("source", "benchmark_latest")).strip() or "benchmark_latest",
        metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {},
    )
    return {"status": "ok", "sample_store": created}

@app.get("/debug/training-experiments", dependencies=[Depends(verify_admin_token)])
async def list_training_experiments(limit: int = 50, dataset_id: Optional[str] = None):
    manager = _get_training_job_manager()
    items = manager.list_experiments(limit=limit, dataset_id=dataset_id)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/training-experiments/{experiment_id}", dependencies=[Depends(verify_admin_token)])
async def get_training_experiment(experiment_id: str):
    manager = _get_training_job_manager()
    payload = manager.get_experiment(experiment_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return {"status": "ok", "experiment": payload}

@app.get("/debug/training-experiments/{experiment_id}/compare", dependencies=[Depends(verify_admin_token)])
async def compare_training_experiment(experiment_id: str):
    manager = _get_training_job_manager()
    compare = manager.compare_experiment_runs(experiment_id)
    if compare is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return {"status": "ok", "comparison": compare}

@app.post("/debug/training-experiments", dependencies=[Depends(verify_admin_token)])
async def create_training_experiment(payload: Dict[str, Any]):
    dataset_id = str(payload.get("dataset_id", "")).strip()
    if not dataset_id:
        raise HTTPException(status_code=400, detail="'dataset_id' is required")
    name = str(payload.get("name", "")).strip() or f"{dataset_id}_experiment"
    manager = _get_training_job_manager()
    experiment = manager.create_experiment(
        dataset_id=dataset_id,
        name=name,
        params=payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {},
        sample_store_id=str(payload.get("sample_store_id", "")).strip() or None,
        metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {},
    )
    return {"status": "ok", "experiment": experiment}

@app.post("/debug/training-experiments/{experiment_id}/run", dependencies=[Depends(verify_admin_token)])
async def run_training_experiment(experiment_id: str, payload: Dict[str, Any]):
    manager = _get_training_job_manager()
    experiment = manager.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")

    dataset_id = str(experiment.get("dataset_id", "")).strip()
    sample_store_id = str(payload.get("sample_store_id") or experiment.get("sample_store_id", "")).strip()
    sample_store = manager.get_sample_store(sample_store_id) if sample_store_id else None
    if sample_store is None:
        eval_manager = _get_eval_benchmark_manager()
        latest = eval_manager.get_latest_run(dataset_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="No benchmark run report found for experiment dataset")
        max_samples_raw = payload.get("max_samples", config.get("trainer.max_samples_per_job", 200))
        try:
            max_samples = max(1, min(int(max_samples_raw), 1000))
        except (TypeError, ValueError):
            max_samples = 200
        train_inputs = _prepare_training_inputs(dataset_id=dataset_id, report=latest, max_samples=max_samples)
        sample_store = manager.create_sample_store(
            dataset_id=dataset_id,
            trajectory_samples=train_inputs["trajectory_samples"],
            eval_samples=train_inputs["eval_samples"],
            source="experiment_auto",
            metadata={"experiment_id": experiment_id},
        )
        sample_store_id = str(sample_store.get("store_id", ""))

    created = manager.create_job(
        dataset_id=dataset_id,
        trajectory_samples=list(sample_store.get("trajectory_samples", [])),
        eval_samples=list(sample_store.get("eval_samples", [])),
        source="experiment",
        metadata={
            "experiment_id": experiment_id,
            "sample_store_id": sample_store_id,
            **(payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}),
        },
    )
    executed = manager.run_job(str(created.get("job_id", ""))) or created
    metrics = _score_training_job(executed if isinstance(executed, dict) else created)
    manager.append_experiment_run(
        experiment_id=experiment_id,
        job_id=str((executed or created).get("job_id", "")),
        metrics=metrics,
    )
    return {
        "status": "ok",
        "experiment_id": experiment_id,
        "sample_store_id": sample_store_id,
        "job": executed,
        "metrics": metrics,
    }

@app.get("/debug/training-jobs/{job_id}", dependencies=[Depends(verify_admin_token)])
async def get_training_job(job_id: str):
    manager = _get_training_job_manager()
    payload = manager.get_job(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Training job not found")
    return {"status": "ok", "job": payload}

@app.post("/debug/training-jobs", dependencies=[Depends(verify_admin_token)])
async def create_training_job(payload: Dict[str, Any]):
    dataset_id = str(payload.get("dataset_id", "")).strip()
    if not dataset_id:
        raise HTTPException(status_code=400, detail="'dataset_id' is required")

    max_samples_raw = payload.get("max_samples", config.get("trainer.max_samples_per_job", 200))
    try:
        max_samples = max(1, min(int(max_samples_raw), 1000))
    except (TypeError, ValueError):
        max_samples = 200

    manager = _get_training_job_manager()
    sample_store_id = str(payload.get("sample_store_id", "")).strip()
    sample_store = manager.get_sample_store(sample_store_id) if sample_store_id else None
    if sample_store is not None:
        trajectory_samples = list(sample_store.get("trajectory_samples", []))
        eval_samples = list(sample_store.get("eval_samples", []))
    else:
        eval_manager = _get_eval_benchmark_manager()
        latest = eval_manager.get_latest_run(dataset_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="No benchmark run report found for dataset")
        train_inputs = _prepare_training_inputs(
            dataset_id=dataset_id,
            report=latest,
            max_samples=max_samples,
        )
        trajectory_samples = train_inputs["trajectory_samples"]
        eval_samples = train_inputs["eval_samples"]

    metadata: Dict[str, Any] = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
    experiment_id = str(payload.get("experiment_id", "")).strip()
    if experiment_id:
        metadata["experiment_id"] = experiment_id
    if sample_store_id:
        metadata["sample_store_id"] = sample_store_id

    created = manager.create_job(
        dataset_id=dataset_id,
        trajectory_samples=trajectory_samples,
        eval_samples=eval_samples,
        source=str(payload.get("source", "manual")).strip() or "manual",
        metadata=metadata,
    )
    _append_policy_audit(
        action="trainer.job.created",
        details={"job_id": created.get("job_id"), "dataset_id": dataset_id, "auto_run": False},
    )
    return {"status": "ok", "job": created}

@app.post("/debug/training-jobs/{job_id}/run", dependencies=[Depends(verify_admin_token)])
async def run_training_job(job_id: str):
    manager = _get_training_job_manager()
    result = manager.run_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Training job not found")
    metadata = result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {}
    experiment_id = str(metadata.get("experiment_id", "")).strip()
    if experiment_id:
        manager.append_experiment_run(
            experiment_id=experiment_id,
            job_id=job_id,
            metrics=_score_training_job(result),
        )
    _append_policy_audit(
        action="trainer.job.completed",
        details={"job_id": job_id, "status": result.get("status", "completed")},
    )
    return {"status": "ok", "job": result}

@app.post("/debug/training-jobs/{job_id}/publish", dependencies=[Depends(verify_admin_token)])
async def publish_training_job(job_id: str, payload: Dict[str, Any]):
    manager = _get_training_job_manager()
    job = manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Training job not found")
    if str(job.get("status", "")).strip().lower() != "completed":
        raise HTTPException(status_code=400, detail="Training job must be completed before publish")
    if not isinstance(job.get("output"), dict):
        raise HTTPException(status_code=400, detail="Training job output is missing")

    diff = _build_training_publish_diff(job)
    dry_run = bool(payload.get("dry_run", False))
    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    rollout_raw = payload.get("rollout", {})
    rollout = _resolve_training_publish_rollout(rollout_raw if isinstance(rollout_raw, dict) else {})
    rollout_mode = str(rollout.get("mode", "direct")).strip().lower() or "direct"
    canary_health_raw = payload.get("canary_health", {})
    canary_health = canary_health_raw if isinstance(canary_health_raw, dict) else {}
    rollback_rule_raw = payload.get("rollback_rule", {})
    rollback_rule = rollback_rule_raw if isinstance(rollback_rule_raw, dict) else {}
    approval_raw = payload.get("approval", {})
    approval = _resolve_training_release_approval(
        actor=actor,
        dry_run=dry_run,
        rollout_mode=rollout_mode,
        approval_payload=approval_raw if isinstance(approval_raw, dict) else {},
    )
    pending_approval = bool(approval.get("required", False) and not approval.get("approved", False) and not dry_run)

    before = diff["before"]
    after = diff["after"]
    if not dry_run and not pending_approval:
        config.set_many(after)
        config.save()

    release = manager.create_release(
        job_id=job_id,
        actor=actor,
        note=note,
        before=before,
        after=after,
        dry_run=dry_run,
        rollout=rollout,
        rollback_rule=rollback_rule,
        strategy_package=diff.get("strategy_package", {}),
        status_override="pending_approval" if pending_approval else None,
        approval=approval,
    )
    rollout_mode = str((release.get("rollout", {}) or {}).get("mode", "direct")).strip().lower() or "direct"
    if pending_approval:
        _append_policy_audit(
            action="trainer.job.publish.pending_approval",
            details={
                "job_id": job_id,
                "release_id": release.get("release_id"),
                "rollout_mode": rollout_mode,
                "actor": actor,
            },
        )
        return {
            "status": "ok",
            "dry_run": dry_run,
            "summary": diff["summary"],
            "release": release,
            "strategy_package": diff.get("strategy_package", {}),
            "release_gate": {},
            "release_gate_health": {},
            "canary_health": canary_health if canary_health else {},
            "pending_approval": True,
        }

    auto_rollback = bool(
        rollback_rule.get("on_gate_blocked", config.get("trainer.canary.auto_rollback_on_gate_fail", True))
    )
    auto_rollback_on_canary_fail = bool(
        rollback_rule.get("on_canary_failed", config.get("trainer.canary.auto_rollback_on_canary_fail", True))
    )
    canary_guard = _evaluate_training_release_canary_guard(
        rollout_mode=rollout_mode,
        canary_health=canary_health,
    )
    release_gate_snapshot = canary_guard["release_gate"]
    release_gate_health = canary_guard["release_gate_health"]
    should_rollback_on_gate = bool(canary_guard["should_rollback_on_gate"])
    should_rollback_on_canary = bool(canary_guard["should_rollback_on_canary"])
    if not dry_run and rollout_mode == "canary" and (
        (auto_rollback and should_rollback_on_gate)
        or (auto_rollback_on_canary_fail and should_rollback_on_canary)
    ):
        rollback_note = "auto_rollback:gate_blocked_during_canary"
        if should_rollback_on_canary:
            rollback_note = (
                "auto_rollback:canary_failed:"
                + str(canary_health.get("reason", "unknown")).strip()
            )
        if auto_rollback and should_rollback_on_gate:
            rollback_note = (
                "auto_rollback:release_gate_high_risk:"
                + str(release_gate_health.get("message", "gate_blocked")).strip()
            )
        try:
            config.set_many(before)
            config.save()
            release = manager.mark_release_rolled_back(
                release_id=str(release.get("release_id", "")),
                actor=actor,
                note=rollback_note,
            ) or release
        except Exception:
            logger.debug("Failed to auto rollback canary training release", exc_info=True)
    _append_policy_audit(
        action="trainer.job.published",
        details={
            "job_id": job_id,
            "release_id": release.get("release_id"),
            "dry_run": dry_run,
            "rollout_mode": rollout_mode,
            "prompt_rules_added": diff["summary"].get("prompt_rules_added", 0),
            "denylist_added": diff["summary"].get("denylist_added", []),
            "router_strategy": diff["summary"].get("router_strategy", ""),
            "release_gate_blocked": bool(release_gate_snapshot.get("blocked", False)),
            "release_gate_health": str(release_gate_health.get("level", "")),
        },
    )
    return {
        "status": "ok",
        "dry_run": dry_run,
        "summary": diff["summary"],
        "release": release,
        "strategy_package": diff.get("strategy_package", {}),
        "release_gate": release_gate_snapshot,
        "release_gate_health": release_gate_health,
        "canary_health": canary_health if canary_health else {},
        "pending_approval": False,
    }

@app.get("/debug/training-releases", dependencies=[Depends(verify_admin_token)])
async def list_training_releases(limit: int = 50, status: Optional[str] = None):
    manager = _get_training_job_manager()
    items = manager.list_releases(limit=limit, status=status)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/training-releases/{release_id}", dependencies=[Depends(verify_admin_token)])
async def get_training_release(release_id: str):
    manager = _get_training_job_manager()
    release = manager.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Training release not found")
    return {"status": "ok", "release": release}

@app.get("/debug/training-releases/{release_id}/explain", dependencies=[Depends(verify_admin_token)])
async def explain_training_release(release_id: str):
    manager = _get_training_job_manager()
    release = manager.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Training release not found")
    job_id = str(release.get("job_id", "")).strip()
    job = manager.get_job(job_id) if job_id else None
    explanation = _build_training_release_explanation(release=release, job=job if isinstance(job, dict) else None)
    return {"status": "ok", "explanation": explanation}

@app.get("/debug/training-jobs/{job_id}/explain", dependencies=[Depends(verify_admin_token)])
async def explain_training_job(job_id: str):
    manager = _get_training_job_manager()
    job = manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Training job not found")
    releases = manager.list_releases(limit=200, status=None)
    linked_release: Optional[Dict[str, Any]] = None
    for item in releases:
        if not isinstance(item, dict):
            continue
        if str(item.get("job_id", "")).strip() == str(job_id).strip():
            linked_release = item
            break
    if linked_release is None:
        linked_release = {
            "release_id": "",
            "job_id": str(job_id),
            "status": "not_published",
            "actor": "",
            "created_at": None,
            "rollout": {"mode": "direct", "percent": 100},
            "approval": {"required": False, "approved": False, "state": "not_required"},
            "rollback_note": "",
            "rollback_actor": "",
        }
    explanation = _build_training_release_explanation(release=linked_release, job=job)
    return {"status": "ok", "explanation": explanation}

@app.post("/debug/training-releases/{release_id}/approve", dependencies=[Depends(verify_admin_token)])
async def approve_training_release(release_id: str, payload: Dict[str, Any]):
    manager = _get_training_job_manager()
    release = manager.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Training release not found")
    release_status = str(release.get("status", "")).strip().lower()
    if release_status != "pending_approval":
        raise HTTPException(status_code=400, detail="Release is not pending approval")

    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    approval_cfg = config.get("trainer.release_approval", {}) or {}
    if not isinstance(approval_cfg, dict):
        approval_cfg = {}
    if bool(approval_cfg.get("require_note", False)) and not note:
        raise HTTPException(status_code=400, detail="Approval note is required by trainer.release_approval.require_note")

    after = release.get("after")
    if not isinstance(after, dict):
        raise HTTPException(status_code=400, detail="Release snapshot is invalid")
    config.set_many(after)
    config.save()

    rollout_mode = str((release.get("rollout", {}) or {}).get("mode", "direct")).strip().lower() or "direct"
    updated = manager.mark_release_approved(
        release_id=release_id,
        actor=actor,
        note=note,
        status="canary" if rollout_mode == "canary" else "published",
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update release approval status")

    canary_health_raw = payload.get("canary_health", {})
    canary_health = canary_health_raw if isinstance(canary_health_raw, dict) else {}
    rollback_rule = release.get("rollback_rule", {}) if isinstance(release.get("rollback_rule"), dict) else {}
    auto_rollback = bool(
        rollback_rule.get("on_gate_blocked", config.get("trainer.canary.auto_rollback_on_gate_fail", True))
    )
    auto_rollback_on_canary_fail = bool(
        rollback_rule.get("on_canary_failed", config.get("trainer.canary.auto_rollback_on_canary_fail", True))
    )
    canary_guard = _evaluate_training_release_canary_guard(
        rollout_mode=rollout_mode,
        canary_health=canary_health,
    )
    release_gate_snapshot = canary_guard["release_gate"]
    release_gate_health = canary_guard["release_gate_health"]
    should_rollback_on_gate = bool(canary_guard["should_rollback_on_gate"])
    should_rollback_on_canary = bool(canary_guard["should_rollback_on_canary"])
    if rollout_mode == "canary" and (
        (auto_rollback and should_rollback_on_gate)
        or (auto_rollback_on_canary_fail and should_rollback_on_canary)
    ):
        rollback_note = "auto_rollback:gate_blocked_during_canary"
        if should_rollback_on_canary:
            rollback_note = (
                "auto_rollback:canary_failed:"
                + str(canary_health.get("reason", "unknown")).strip()
            )
        if auto_rollback and should_rollback_on_gate:
            rollback_note = (
                "auto_rollback:release_gate_high_risk:"
                + str(release_gate_health.get("message", "gate_blocked")).strip()
            )
        before = release.get("before", {})
        if not isinstance(before, dict):
            raise HTTPException(status_code=400, detail="Release rollback snapshot is invalid")
        config.set_many(before)
        config.save()
        updated = manager.mark_release_rolled_back(
            release_id=release_id,
            actor=actor,
            note=rollback_note,
        ) or updated

    _append_policy_audit(
        action="trainer.release.approved",
        details={
            "release_id": release_id,
            "job_id": updated.get("job_id", ""),
            "actor": actor,
            "rollout_mode": rollout_mode,
            "release_gate_health": str(release_gate_health.get("level", "")),
        },
    )
    return {
        "status": "ok",
        "release": updated,
        "release_gate": release_gate_snapshot,
        "release_gate_health": release_gate_health,
        "canary_health": canary_health if canary_health else {},
    }

@app.post("/debug/training-releases/{release_id}/promote", dependencies=[Depends(verify_admin_token)])
async def promote_training_release(release_id: str, payload: Dict[str, Any]):
    manager = _get_training_job_manager()
    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    updated = manager.mark_release_promoted(release_id=release_id, actor=actor, note=note)
    if updated is None:
        raise HTTPException(status_code=404, detail="Training release not found")
    _append_policy_audit(
        action="trainer.release.promoted",
        details={"release_id": release_id, "actor": actor},
    )
    return {"status": "ok", "release": updated}

@app.post("/debug/training-releases/{release_id}/rollback", dependencies=[Depends(verify_admin_token)])
async def rollback_training_release(release_id: str, payload: Dict[str, Any]):
    manager = _get_training_job_manager()
    release = manager.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Training release not found")
    if str(release.get("status", "")).strip().lower() == "rolled_back":
        return {"status": "ok", "release": release, "note": "Already rolled back"}

    before = release.get("before", {})
    if not isinstance(before, dict):
        raise HTTPException(status_code=400, detail="Release snapshot is invalid")
    config.set_many(before)
    config.save()

    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    updated = manager.mark_release_rolled_back(release_id=release_id, actor=actor, note=note)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update release rollback status")
    _append_policy_audit(
        action="trainer.release.rolled_back",
        details={"release_id": release_id, "job_id": updated.get("job_id", ""), "actor": actor},
    )
    return {"status": "ok", "release": updated}

@app.post("/debug/bootstrap-pipeline/run", dependencies=[Depends(verify_admin_token)])
async def run_bootstrap_pipeline(payload: Dict[str, Any]):
    """One-shot self-bootstrapping pipeline.

    change_set -> training job -> gate -> publish/canary -> optional rollback.
    """
    manager = _get_training_job_manager()
    dataset_id = str(payload.get("dataset_id", "bootstrap_manual")).strip() or "bootstrap_manual"
    change_set = payload.get("change_set", {})
    if not isinstance(change_set, dict):
        raise HTTPException(status_code=400, detail="change_set must be an object")
    trajectory_samples = payload.get("trajectory_samples", [])
    eval_samples = payload.get("eval_samples", [])
    if not isinstance(trajectory_samples, list) or not isinstance(eval_samples, list):
        raise HTTPException(status_code=400, detail="trajectory_samples/eval_samples must be arrays")
    if not trajectory_samples and not eval_samples:
        latest = _get_eval_benchmark_manager().get_latest_report(dataset_id=dataset_id)
        if not latest:
            raise HTTPException(
                status_code=400,
                detail="No benchmark report found; provide trajectory_samples/eval_samples explicitly.",
            )
        max_samples_raw = payload.get("max_samples", config.get("trainer.max_samples_per_job", 200))
        try:
            max_samples = max(20, min(500, int(max_samples_raw)))
        except (TypeError, ValueError):
            max_samples = 200
        prepared = _prepare_training_inputs(dataset_id=dataset_id, report=latest, max_samples=max_samples)
        trajectory_samples = list(prepared.get("trajectory_samples", []))
        eval_samples = list(prepared.get("eval_samples", []))

    actor = str(payload.get("actor", "admin")).strip() or "admin"
    note = str(payload.get("note", "")).strip()
    dry_run = bool(payload.get("dry_run", False))
    rollout = payload.get("rollout", {})
    gate = payload.get("gate", {})
    canary_health = payload.get("canary_health", {})

    try:
        pipeline = manager.run_bootstrap_pipeline(
            dataset_id=dataset_id,
            change_set=change_set,
            trajectory_samples=trajectory_samples,
            eval_samples=eval_samples,
            actor=actor,
            note=note,
            dry_run=dry_run,
            rollout=rollout if isinstance(rollout, dict) else {},
            gate=gate if isinstance(gate, dict) else {},
            canary_health=canary_health if isinstance(canary_health, dict) else {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _append_policy_audit(
        action="trainer.bootstrap.pipeline",
        details={
            "pipeline_id": pipeline.get("pipeline_id", ""),
            "dataset_id": dataset_id,
            "status": pipeline.get("status", ""),
            "job_id": pipeline.get("job_id", ""),
            "release_id": pipeline.get("release_id", ""),
            "actor": actor,
        },
    )
    return {"status": "ok", "pipeline": pipeline}

@app.get("/debug/bootstrap-pipeline/runs", dependencies=[Depends(verify_admin_token)])
async def list_bootstrap_pipeline_runs(limit: int = 50, status: Optional[str] = None):
    manager = _get_training_job_manager()
    items = manager.list_bootstrap_runs(limit=max(1, min(limit, 500)), status=status)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/bootstrap-pipeline/runs/{pipeline_id}", dependencies=[Depends(verify_admin_token)])
async def get_bootstrap_pipeline_run(pipeline_id: str):
    manager = _get_training_job_manager()
    payload = manager.get_bootstrap_run(pipeline_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Bootstrap pipeline not found")
    return {"status": "ok", "pipeline": payload}
