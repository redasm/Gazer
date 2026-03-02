from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Response
from typing import Dict, Any, List, Optional
import logging
import os
import platform
import time
import json
import yaml
import psutil
import io
import csv
from tools.admin._shared import (
    config, _redact_config, _llm_history,
    TRAINING_JOB_MANAGER, TRAINING_BRIDGE_MANAGER,
    ONLINE_POLICY_LOOP_MANAGER, PERSONA_EVAL_MANAGER,
    PERSONA_RUNTIME_MANAGER, EVAL_BENCHMARK_MANAGER, TRAJECTORY_STORE,
    # Helpers used by debug routes (28 functions)
    _append_policy_audit, _assess_coding_benchmark_health,
    _auto_link_release_gate_by_coding_benchmark, _build_resume_payload,
    _build_rule_prompt_patch, _build_task_view, _build_training_publish_diff,
    _build_training_release_explanation, _capture_strategy_snapshot,
    _compare_replay_steps, _enqueue_chat_message,
    _evaluate_training_release_canary_guard, _execute_deterministic_coding_loop,
    _get_release_gate_health_thresholds, _is_subpath,
    _maybe_run_scheduled_coding_benchmark, _normalize_trajectory_steps,
    _persona_runtime_thresholds, _prepare_training_inputs,
    _require_orchestrator, _resolve_export_output_path,
    _resolve_online_policy_gate_thresholds, _resolve_online_policy_offpolicy_config,
    _resolve_training_publish_rollout, _resolve_training_release_approval,
    _run_coding_benchmark_suite, _score_training_job,
    _coding_benchmark_history, _coding_benchmark_scheduler_state,
)
from tools.admin.auth import verify_admin_token

# --- Lazy cross-module imports (avoids circular dependency via __init__.py) ---
# These functions are defined in sibling modules (system.py, config_routes.py, etc.)
# and cannot be imported at module load time because __init__.py imports all submodules.
_lazy_cache: dict = {}

def _lazy(name: str):
    """Lazily import a function from a sibling admin module."""
    if name not in _lazy_cache:
        _LAZY_MAP = {
            # system.py
            "_build_coding_benchmark_leaderboard": ("tools.admin.system", "_build_coding_benchmark_leaderboard"),
            "_build_coding_benchmark_observability": ("tools.admin.system", "_build_coding_benchmark_observability"),
            "_build_coding_quality_metrics": ("tools.admin.system", "_build_coding_quality_metrics"),
            "_build_persona_consistency_weekly_report": ("tools.admin.system", "_build_persona_consistency_weekly_report"),
            "_build_workflow_observability_metrics": ("tools.admin.system", "_build_workflow_observability_metrics"),
            "_latest_persona_consistency_signal": ("tools.admin.system", "_latest_persona_consistency_signal"),
            # config_routes.py
            "_policy_to_payload": ("tools.admin.config_routes", "_policy_to_payload"),
            "_resolve_agents_overlay_policy": ("tools.admin.config_routes", "_resolve_agents_overlay_policy"),
            "_resolve_global_policy": ("tools.admin.config_routes", "_resolve_global_policy"),
            # memory.py
            "_build_memory_extraction_quality_report": ("tools.admin.memory", "_build_memory_extraction_quality_report"),
            "_build_persona_memory_joint_drift_report": ("tools.admin.memory", "_build_persona_memory_joint_drift_report"),
            "_get_memory_manager": ("tools.admin.memory", "_get_memory_manager"),
            # observability.py
            "_append_alert": ("tools.admin.observability", "_append_alert"),
        }
        mod_path, attr = _LAZY_MAP[name]
        import importlib
        mod = importlib.import_module(mod_path)
        _lazy_cache[name] = getattr(mod, attr)
    return _lazy_cache[name]

# Convenience accessors so existing call sites don't need changes
def _build_coding_benchmark_leaderboard(*a, **kw): return _lazy("_build_coding_benchmark_leaderboard")(*a, **kw)
def _build_coding_benchmark_observability(*a, **kw): return _lazy("_build_coding_benchmark_observability")(*a, **kw)
def _build_coding_quality_metrics(*a, **kw): return _lazy("_build_coding_quality_metrics")(*a, **kw)
def _build_persona_consistency_weekly_report(*a, **kw): return _lazy("_build_persona_consistency_weekly_report")(*a, **kw)
def _build_workflow_observability_metrics(*a, **kw): return _lazy("_build_workflow_observability_metrics")(*a, **kw)
def _latest_persona_consistency_signal(*a, **kw): return _lazy("_latest_persona_consistency_signal")(*a, **kw)
def _policy_to_payload(*a, **kw): return _lazy("_policy_to_payload")(*a, **kw)
def _resolve_agents_overlay_policy(*a, **kw): return _lazy("_resolve_agents_overlay_policy")(*a, **kw)
def _resolve_global_policy(*a, **kw): return _lazy("_resolve_global_policy")(*a, **kw)
def _build_memory_extraction_quality_report(*a, **kw): return _lazy("_build_memory_extraction_quality_report")(*a, **kw)
def _build_persona_memory_joint_drift_report(*a, **kw): return _lazy("_build_persona_memory_joint_drift_report")(*a, **kw)
def _get_memory_manager(*a, **kw): return _lazy("_get_memory_manager")(*a, **kw)
def _append_alert(*a, **kw): return _lazy("_append_alert")(*a, **kw)



from io import StringIO
from multiprocessing import Process
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.trainer import TrainingJobManager
    from eval.training_bridge import TrainingBridgeManager
    from eval.benchmark import EvalBenchmarkManager
    from eval.online_policy_loop import OnlinePolicyLoopManager
    from eval.persona_consistency import PersonaConsistencyManager
    from soul.persona_runtime import PersonaRuntimeManager
    from llm.litellm_provider import LiteLLMProvider
    from tools.registry import ToolPolicy



app = APIRouter()
logger = logging.getLogger("GazerAdminAPI")

_training_job_manager = None
_training_bridge_manager = None
_online_policy_loop_manager = None
_persona_eval_manager = None
_persona_runtime_manager = None
_eval_benchmark_manager = None

def _get_eval_benchmark_manager() -> EvalBenchmarkManager:
    global EVAL_BENCHMARK_MANAGER
    if EVAL_BENCHMARK_MANAGER is None:
        from eval.benchmark import EvalBenchmarkManager
        EVAL_BENCHMARK_MANAGER = EvalBenchmarkManager()
    return EVAL_BENCHMARK_MANAGER

def _get_training_job_manager() -> TrainingJobManager:
    global TRAINING_JOB_MANAGER
    if TRAINING_JOB_MANAGER is None:
        from eval.trainer import TrainingJobManager
        TRAINING_JOB_MANAGER = TrainingJobManager()
    return TRAINING_JOB_MANAGER

def _get_training_bridge_manager() -> TrainingBridgeManager:
    global TRAINING_BRIDGE_MANAGER
    if TRAINING_BRIDGE_MANAGER is None:
        from eval.training_bridge import TrainingBridgeManager
        TRAINING_BRIDGE_MANAGER = TrainingBridgeManager()
    return TRAINING_BRIDGE_MANAGER

def _get_online_policy_loop_manager() -> OnlinePolicyLoopManager:
    global ONLINE_POLICY_LOOP_MANAGER
    if ONLINE_POLICY_LOOP_MANAGER is None:
        from eval.online_policy_loop import OnlinePolicyLoopManager
        ONLINE_POLICY_LOOP_MANAGER = OnlinePolicyLoopManager()
    return ONLINE_POLICY_LOOP_MANAGER

def _get_persona_eval_manager() -> PersonaConsistencyManager:
    global PERSONA_EVAL_MANAGER
    if PERSONA_EVAL_MANAGER is None:
        from eval.persona_consistency import PersonaConsistencyManager
        PERSONA_EVAL_MANAGER = PersonaConsistencyManager()
    return PERSONA_EVAL_MANAGER

def _get_persona_runtime_manager() -> PersonaRuntimeManager:
    global PERSONA_RUNTIME_MANAGER
    if PERSONA_RUNTIME_MANAGER is None:
        from soul.persona_runtime import PersonaRuntimeManager
        PERSONA_RUNTIME_MANAGER = PersonaRuntimeManager()
    return PERSONA_RUNTIME_MANAGER

def _assess_release_gate_workflow_health(
    gate_status: Dict[str, Any],
    workflow_metrics: Dict[str, Any],
    persona_metrics: Optional[Dict[str, Any]] = None,
    coding_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    thresholds = _get_release_gate_health_thresholds()
    gate_blocked = bool(gate_status.get("blocked", False))
    runs = int(workflow_metrics.get("total_runs", 0) or 0)
    failures = int(workflow_metrics.get("failures", 0) or 0)
    success_rate = float(workflow_metrics.get("success_rate", 1.0) or 1.0)
    p95_latency = float(workflow_metrics.get("p95_latency_ms", 0.0) or 0.0)
    persona = persona_metrics if isinstance(persona_metrics, dict) else {}
    has_persona_data = bool(persona.get("has_data", False))
    persona_score = float(persona.get("latest_score", 0.0) or 0.0)
    coding = coding_metrics if isinstance(coding_metrics, dict) else {}
    coding_runs = int(coding.get("total_runs", 0) or 0)
    coding_pass_rate = float(coding.get("pass_rate", 1.0) or 1.0)

    warning = runs > 0 and (
        success_rate < float(thresholds["warning_success_rate"])
        or failures >= int(thresholds["warning_failures"])
        or p95_latency > float(thresholds["warning_p95_latency_ms"])
    )
    if has_persona_data and persona_score < float(thresholds["warning_persona_consistency_score"]):
        warning = True
    if coding_runs > 0 and coding_pass_rate < 0.8:
        warning = True
    critical = runs > 0 and (
        success_rate < float(thresholds["critical_success_rate"])
        or failures >= int(thresholds["critical_failures"])
        or p95_latency > float(thresholds["critical_p95_latency_ms"])
    )
    if has_persona_data and persona_score < float(thresholds["critical_persona_consistency_score"]):
        critical = True
    if coding_runs > 0 and coding_pass_rate < 0.6:
        critical = True

    if gate_blocked:
        level = "critical"
        message = "release_gate_blocked"
    elif critical:
        level = "critical"
        message = "workflow_health_critical"
    elif warning:
        level = "warning"
        message = "workflow_health_warning"
    elif runs == 0:
        level = "unknown"
        message = "workflow_health_no_data"
    else:
        level = "healthy"
        message = "workflow_health_healthy"

    return {
        "level": level,
        "gate_blocked": gate_blocked,
        "warning": warning,
        "critical": critical or gate_blocked,
        "no_data": runs == 0,
        "recommend_block_high_risk": bool(gate_blocked or critical),
        "message": message,
        "signals": {
            "total_runs": runs,
            "failures": failures,
            "success_rate": round(success_rate, 4),
            "p95_latency_ms": round(p95_latency, 2),
            "persona_consistency_score": round(persona_score, 4) if has_persona_data else None,
            "persona_dataset_id": str(persona.get("dataset_id", "")) if has_persona_data else "",
            "coding_pass_rate": round(coding_pass_rate, 4) if coding_runs > 0 else None,
            "coding_total_runs": coding_runs,
        },
        "thresholds": thresholds,
    }

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

def _auto_link_release_gate(
    *,
    manager: EvalBenchmarkManager,
    gate: Dict[str, Any],
    health: Dict[str, Any],
) -> Dict[str, Any]:
    """Auto-link workflow/persona health signals to release gate and optimization tasks."""
    current_blocked = bool((gate or {}).get("blocked", False))
    recommend_block = bool((health or {}).get("recommend_block_high_risk", False))
    level = str((health or {}).get("level", "")).strip().lower()
    actions: Dict[str, Any] = {"changed_gate": False, "created_task": False, "resolved_tasks": 0}

    if recommend_block != current_blocked:
        reason = str((health or {}).get("message", "")).strip() or "auto_link_health_signal"
        manager.set_release_gate_status(
            blocked=recommend_block,
            reason=reason,
            source="workflow_health_auto_link",
            metadata={"level": level, "signals": health.get("signals", {})},
        )
        actions["changed_gate"] = True
        actions["blocked"] = recommend_block

    fail_threshold_raw = config.get("security.optimization_fail_streak_threshold", 2)
    try:
        fail_threshold = max(1, int(fail_threshold_raw))
    except (TypeError, ValueError):
        fail_threshold = 2
    if recommend_block:
        synthetic_report = {
            "quality_gate": {
                "blocked": True,
                "reasons": [str((health or {}).get("message", "")).strip() or "workflow_health_degraded"],
            }
        }
        optimization = manager.register_gate_result(
            dataset_id="workflow_health_auto",
            report=synthetic_report,
            fail_streak_threshold=fail_threshold,
        )
        actions["created_task"] = bool((optimization or {}).get("task_created", False))
        actions["fail_streak"] = int((optimization or {}).get("fail_streak", 0) or 0)
    else:
        open_items = manager.list_optimization_tasks(limit=200, status="open", dataset_id="workflow_health_auto")
        resolved = 0
        for item in open_items:
            task_id = str(item.get("task_id", "")).strip()
            if not task_id:
                continue
            updated = manager.set_optimization_task_status(
                task_id=task_id,
                status="resolved",
                note="auto_resolved_by_health_recovery",
            )
            if updated is not None:
                resolved += 1
        actions["resolved_tasks"] = resolved
    return actions

@app.get("/debug/agents-md/effective", dependencies=[Depends(verify_admin_token)])
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

@app.post("/debug/agents-md/effective", dependencies=[Depends(verify_admin_token)])
async def post_agents_md_effective(payload: Dict[str, Any]):
    target = str((payload or {}).get("agents_target_dir", "")).strip() or None
    return await get_agents_md_effective(target)

@app.post("/debug/agents-md/lint", dependencies=[Depends(verify_admin_token)])
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

@app.get("/debug/system", dependencies=[Depends(verify_admin_token)])
async def get_system_info():
    """Get system information for debugging."""
    try:
        memory = psutil.virtual_memory()
        # Use drive root on Windows, fallback to '/' on Unix
        if os.name == 'nt':
            disk_path = os.path.splitdrive(os.getcwd())[0] + os.sep
        else:
            disk_path = '/'
        disk = psutil.disk_usage(disk_path)
        
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": memory.percent,
            "memory_used_gb": memory.used / (1024 ** 3),
            "memory_total_gb": memory.total / (1024 ** 3),
            "disk_percent": disk.percent,
            "python_version": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            "processes": [
                {"name": "GazerBrain", "status": "running", "pid": os.getpid(), "memory_mb": psutil.Process().memory_info().rss // (1024 * 1024)},
                {"name": "AdminAPI", "status": "running", "pid": os.getpid(), "memory_mb": psutil.Process().memory_info().rss // (1024 * 1024)},
            ]
        }
    except Exception as e:
        logger.error(f"Failed to get system info: {e}")
        return {"error": str(e)}

@app.get("/debug/llm-history", dependencies=[Depends(verify_admin_token)])
async def get_llm_history(limit: int = 50):
    """Get recent LLM call history for debugging."""
    history = list(_llm_history)
    return {"calls": history[-limit:], "total": len(history)}

@app.get("/debug/trajectories", dependencies=[Depends(verify_admin_token)])
async def list_trajectories(limit: int = 50, session_key: Optional[str] = None):
    """List recent agent trajectories for replay/debugging."""
    if TRAJECTORY_STORE is None:
        return {"items": [], "total": 0, "note": "Trajectory store not injected yet."}
    items = TRAJECTORY_STORE.list_recent(limit=limit, session_key=session_key)
    return {"items": items, "total": len(items)}

@app.get("/debug/trajectories/{run_id}", dependencies=[Depends(verify_admin_token)])
async def get_trajectory(run_id: str):
    """Get one full trajectory for replay."""
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")
    payload = TRAJECTORY_STORE.get_trajectory(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")
    return payload

@app.get("/debug/trajectories/{run_id}/task-view", dependencies=[Depends(verify_admin_token)])
async def get_trajectory_task_view(run_id: str):
    """Get task-level observability summary for one trajectory."""
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")
    payload = TRAJECTORY_STORE.get_trajectory(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")
    return _build_task_view(payload)

@app.get("/debug/trajectories/{run_id}/replay-preview", dependencies=[Depends(verify_admin_token)])
async def get_trajectory_replay_preview(run_id: str, compare_run_id: Optional[str] = None):
    """Build normalized replay steps and optional diff against another run."""
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")
    payload = TRAJECTORY_STORE.get_trajectory(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")

    run_steps = _normalize_trajectory_steps(payload)
    replay: Dict[str, Any] = {
        "run_id": run_id,
        "step_count": len(run_steps),
        "tool_call_steps": sum(1 for step in run_steps if step.get("action") == "tool_call"),
        "tool_result_steps": sum(1 for step in run_steps if step.get("action") == "tool_result"),
        "error_steps": sum(
            1
            for step in run_steps
            if step.get("action") == "tool_result" and str(step.get("status", "")).lower() == "error"
        ),
        "steps": run_steps,
    }

    if not compare_run_id:
        return {"replay": replay}

    baseline = TRAJECTORY_STORE.get_trajectory(compare_run_id)
    if baseline is None:
        raise HTTPException(status_code=404, detail="Compare trajectory not found")
    baseline_steps = _normalize_trajectory_steps(baseline)
    comparison = _compare_replay_steps(run_steps, baseline_steps)
    comparison["compare_run_id"] = compare_run_id
    return {"replay": replay, "comparison": comparison}

@app.get("/debug/trajectories/{run_id}/resume", dependencies=[Depends(verify_admin_token)])
async def get_trajectory_resume(run_id: str):
    """Get resume draft payload for continuing a previous trajectory."""
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")
    payload = TRAJECTORY_STORE.get_trajectory(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")
    return _build_resume_payload(payload)

@app.post("/debug/trajectories/{run_id}/resume/send", dependencies=[Depends(verify_admin_token)])
async def send_trajectory_resume(run_id: str, data: Dict[str, Any]):
    """Enqueue trajectory resume draft into a chat session."""
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")

    payload = TRAJECTORY_STORE.get_trajectory(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")
    resume = _build_resume_payload(payload)

    session_id = str(data.get("session_id", "web-main") or "").strip() or "web-main"
    content = str(data.get("message", "") or "").strip() or str(resume.get("resume_message", "") or "").strip()
    task = TASK_RUN_STORE.create(
        kind="resume_send",
        run_id=run_id,
        session_id=session_id,
        payload={"can_resume": bool(resume.get("can_resume", False))},
    )
    TASK_RUN_STORE.add_checkpoint(task["task_id"], stage="enqueue", status="running", note="resume_send")
    _enqueue_chat_message(content=content, session_id=session_id, source="resume_send", sender_id="owner")
    TASK_RUN_STORE.add_checkpoint(task["task_id"], stage="enqueue", status="ok", note="queued_to_brain")
    TASK_RUN_STORE.update_status(
        task["task_id"],
        status="completed",
        output={"chat_id": session_id, "content_preview": content[:240]},
    )
    return {
        "status": "enqueued",
        "task_id": task["task_id"],
        "run_id": run_id,
        "chat_id": session_id,
        "can_resume": bool(resume.get("can_resume", False)),
        "content_preview": content[:240],
    }

@app.post("/debug/trajectories/{run_id}/resume/auto", dependencies=[Depends(verify_admin_token)])
async def auto_resume_trajectory(run_id: str, data: Dict[str, Any]):
    """Automatically continue a trajectory by enqueuing resume draft."""
    payload = dict(data or {})
    payload.setdefault("session_id", str(payload.get("session_id", "web-main") or "web-main"))
    payload.setdefault("message", "")
    result = await send_trajectory_resume(run_id, payload)
    result["mode"] = "auto"
    return result

@app.post("/debug/trajectories/{run_id}/replay-execute", dependencies=[Depends(verify_admin_token)])
async def replay_execute_trajectory(run_id: str, data: Dict[str, Any]):
    """Execute a replay prompt derived from trajectory tool timeline."""
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")
    payload = TRAJECTORY_STORE.get_trajectory(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")

    session_id = str(data.get("session_id", "web-main") or "").strip() or "web-main"
    compare_run_id = str(data.get("compare_run_id", "") or "").strip()
    replay = _normalize_trajectory_steps(payload)
    compare_summary = ""
    if compare_run_id:
        baseline = TRAJECTORY_STORE.get_trajectory(compare_run_id)
        if baseline is None:
            raise HTTPException(status_code=404, detail="Compare trajectory not found")
        cmp = _compare_replay_steps(replay, _normalize_trajectory_steps(baseline))
        compare_summary = (
            f"\n对比 run_id={compare_run_id}: overlap={cmp.get('overlap_ratio')} "
            f"shared={cmp.get('shared_steps')} missing={len(cmp.get('missing_from_run', []))} "
            f"added={len(cmp.get('added_in_run', []))}"
        )

    steps_preview = []
    for step in replay[:40]:
        steps_preview.append(
            {
                "action": step.get("action"),
                "tool": step.get("tool"),
                "tool_call_id": step.get("tool_call_id"),
                "status": step.get("status"),
                "error_code": step.get("error_code"),
                "args_hash": step.get("args_hash"),
            }
        )
    replay_prompt = (
        f"请基于轨迹 run_id={run_id} 执行一次可验证重放。\n"
        f"目标：重现关键工具调用路径并输出差异总结。{compare_summary}\n"
        f"步骤摘要(JSON):\n{json.dumps(steps_preview, ensure_ascii=False)}\n"
        "要求：不要盲目重复已失败动作；失败时给出替代方案并继续推进。"
    )
    task = TASK_RUN_STORE.create(
        kind="replay_execute",
        run_id=run_id,
        session_id=session_id,
        payload={"compare_run_id": compare_run_id or None, "step_count": len(replay)},
    )
    TASK_RUN_STORE.add_checkpoint(task["task_id"], stage="plan", status="ok", note="replay_prompt_built")
    _enqueue_chat_message(content=replay_prompt, session_id=session_id, source="replay_execute", sender_id="owner")
    TASK_RUN_STORE.add_checkpoint(task["task_id"], stage="enqueue", status="ok", note="queued_to_brain")
    TASK_RUN_STORE.update_status(
        task["task_id"],
        status="completed",
        output={"chat_id": session_id, "prompt_preview": replay_prompt[:240]},
    )
    return {"status": "enqueued", "task_id": task["task_id"], "run_id": run_id, "chat_id": session_id}

@app.get("/debug/task-runs", dependencies=[Depends(verify_admin_token)])
async def list_task_runs(limit: int = 50, status: Optional[str] = None, kind: Optional[str] = None):
    items = TASK_RUN_STORE.list(limit=max(1, min(limit, 500)), status=status, kind=kind)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/orchestrator/status", dependencies=[Depends(verify_admin_token)])
async def get_orchestrator_status():
    orchestrator = _require_orchestrator()
    return {"status": "ok", "orchestrator": orchestrator.get_status()}

@app.get("/debug/orchestrator/tasks", dependencies=[Depends(verify_admin_token)])
async def list_orchestrator_tasks(limit: int = 50, status: Optional[str] = None):
    orchestrator = _require_orchestrator()
    safe_limit = max(1, min(int(limit), 500))
    items = orchestrator.list_task_runs(limit=safe_limit, status=status)
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/orchestrator/tasks/{task_id}", dependencies=[Depends(verify_admin_token)])
async def get_orchestrator_task(task_id: str):
    orchestrator = _require_orchestrator()
    payload = orchestrator.get_task(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Orchestrator task not found")
    return {"status": "ok", "task": payload}

@app.post("/debug/orchestrator/tasks/{task_id}/sleep", dependencies=[Depends(verify_admin_token)])
async def sleep_orchestrator_task(task_id: str, data: Optional[Dict[str, Any]] = None):
    orchestrator = _require_orchestrator()
    payload = data if isinstance(data, dict) else {}
    delay_seconds = payload.get("delay_seconds")
    wake_events_raw = payload.get("wake_events")
    wake_events = wake_events_raw if isinstance(wake_events_raw, list) else None
    reason = str(payload.get("reason", "manual_sleep") or "manual_sleep")

    task = orchestrator.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Orchestrator task not found")

    ok = orchestrator.sleep_task(
        task_id,
        delay_seconds=delay_seconds,
        wake_events=wake_events,
        reason=reason,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Task cannot enter sleeping state")
    return {"status": "ok", "task": orchestrator.get_task(task_id)}

@app.post("/debug/orchestrator/tasks/{task_id}/wake", dependencies=[Depends(verify_admin_token)])
async def wake_orchestrator_task(task_id: str, data: Optional[Dict[str, Any]] = None):
    orchestrator = _require_orchestrator()
    payload = data if isinstance(data, dict) else {}
    reason = str(payload.get("reason", "manual_wake") or "manual_wake")

    task = orchestrator.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Orchestrator task not found")

    ok = orchestrator.wake_task(task_id, reason=reason)
    if not ok:
        raise HTTPException(status_code=409, detail="Task cannot be woken")
    return {"status": "ok", "task": orchestrator.get_task(task_id)}

@app.post("/debug/orchestrator/events/wake", dependencies=[Depends(verify_admin_token)])
async def wake_orchestrator_event(data: Optional[Dict[str, Any]] = None):
    orchestrator = _require_orchestrator()
    payload = data if isinstance(data, dict) else {}
    event_key = str(payload.get("event", "") or "").strip()
    if not event_key:
        raise HTTPException(status_code=400, detail="event is required")
    awakened = orchestrator.emit_wake_event(event_key)
    return {
        "status": "ok",
        "event": event_key,
        "awakened": int(awakened),
        "orchestrator": orchestrator.get_status(),
    }

@app.get("/debug/task-runs/{task_id}", dependencies=[Depends(verify_admin_token)])
async def get_task_run(task_id: str):
    payload = TASK_RUN_STORE.get(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    return {"status": "ok", "task": payload}

@app.post("/debug/task-runs/{task_id}/coding-loop", dependencies=[Depends(verify_admin_token)])
async def run_task_coding_loop(task_id: str, data: Dict[str, Any]):
    task = TASK_RUN_STORE.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    run_id = str(task.get("run_id", "") or "")
    session_id = str(data.get("session_id", task.get("session_id", "web-main")) or "").strip() or "web-main"
    goal = str(data.get("goal", "") or "").strip() or "修复问题并通过测试"
    mode = str(data.get("mode", "prompt") or "prompt").strip().lower()
    if mode in {"deterministic", "auto"} and isinstance(data.get("edits"), list) and data.get("edits"):
        TASK_RUN_STORE.add_checkpoint(task_id, stage="coding_loop", status="running", note="deterministic_start")
        try:
            output = _execute_deterministic_coding_loop(
                task_id=task_id,
                run_id=run_id,
                goal=goal,
                payload=data,
            )
        except HTTPException:
            TASK_RUN_STORE.update_status(task_id, status="failed", output={"mode": "deterministic", "goal": goal})
            raise
        TASK_RUN_STORE.update_status(task_id, status="completed", output=output)
        return {"status": "completed", "task_id": task_id, "mode": "deterministic", "output": output}

    loop_prompt = (
        f"你正在执行编码闭环任务 task_id={task_id}, run_id={run_id}。\n"
        f"目标：{goal}\n"
        "流程必须严格为：1) 检索定位 2) 修改代码 3) 运行相关测试 4) 若失败则回滚/重试 5) 给出最终结果与证据。\n"
        "输出中必须包含具体文件路径、命令、测试结果。"
    )
    TASK_RUN_STORE.add_checkpoint(task_id, stage="coding_loop", status="running", note="prompt_enqueued")
    _enqueue_chat_message(content=loop_prompt, session_id=session_id, source="coding_loop", sender_id="owner")
    TASK_RUN_STORE.add_checkpoint(task_id, stage="coding_loop", status="ok", note="queued_to_brain")
    TASK_RUN_STORE.update_status(task_id, status="completed", output={"chat_id": session_id, "goal": goal})
    return {"status": "enqueued", "task_id": task_id, "chat_id": session_id}

@app.get("/debug/coding-quality", dependencies=[Depends(verify_admin_token)])
async def get_coding_quality(window: int = 50, kind: Optional[str] = None):
    return {"status": "ok", "metrics": _build_coding_quality_metrics(window=window, kind=kind)}

@app.post("/debug/coding-benchmark/run", dependencies=[Depends(verify_admin_token)])
async def run_coding_benchmark(payload: Dict[str, Any]):
    summary = _run_coding_benchmark_suite(payload)
    auto_link_enabled = bool(payload.get("auto_link_release_gate", config.get("security.coding_benchmark_auto_link_on_run", False)))
    auto_actions: Dict[str, Any] = {}
    gate: Optional[Dict[str, Any]] = None
    health: Optional[Dict[str, Any]] = None
    if auto_link_enabled:
        manager = _get_eval_benchmark_manager()
        gate = manager.get_release_gate_status()
        health = _assess_coding_benchmark_health(window=int(payload.get("window", 20) or 20))
        auto_actions = _auto_link_release_gate_by_coding_benchmark(manager=manager, gate=gate, health=health)
        gate = manager.get_release_gate_status()
    return {
        "status": "ok",
        "summary": summary,
        "auto_link": auto_actions,
        "health": health,
        "gate": gate,
    }

@app.get("/debug/coding-benchmark/history", dependencies=[Depends(verify_admin_token)])
async def get_coding_benchmark_history(limit: int = 20):
    size = max(1, min(int(limit), 200))
    items = list(_coding_benchmark_history)[-size:]
    return {"status": "ok", "items": items, "total": len(items)}

@app.get("/debug/coding-benchmark/leaderboard", dependencies=[Depends(verify_admin_token)])
async def get_coding_benchmark_leaderboard(window: int = 20):
    return {"status": "ok", "leaderboard": _build_coding_benchmark_leaderboard(window=window)}

@app.get("/debug/coding-benchmark/observability", dependencies=[Depends(verify_admin_token)])
async def get_coding_benchmark_observability(window: int = 60):
    return {"status": "ok", "observability": _build_coding_benchmark_observability(window=window)}

@app.get("/debug/coding-benchmark/export.csv", dependencies=[Depends(verify_admin_token)])
async def export_coding_benchmark_csv(window: int = 60):
    size = max(1, min(int(window), 400))
    items = list(_coding_benchmark_history)[-size:]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ts", "name", "score", "success_cases", "total_cases", "duration_ms"])
    for rec in items:
        writer.writerow(
            [
                float(rec.get("ts", 0.0) or 0.0),
                str(rec.get("name", "") or ""),
                float(rec.get("score", 0.0) or 0.0),
                int(rec.get("success_cases", 0) or 0),
                int(rec.get("total_cases", 0) or 0),
                float(rec.get("duration_ms", 0.0) or 0.0),
            ]
        )
    return Response(content=buffer.getvalue(), media_type="text/csv")

@app.post("/debug/coding-benchmark/auto-link-release-gate", dependencies=[Depends(verify_admin_token)])
async def auto_link_coding_benchmark_release_gate(payload: Optional[Dict[str, Any]] = None):
    data = payload if isinstance(payload, dict) else {}
    window_raw = data.get("window", 20)
    try:
        window = max(1, min(int(window_raw), 200))
    except (TypeError, ValueError):
        window = 20
    manager = _get_eval_benchmark_manager()
    gate = manager.get_release_gate_status()
    health = _assess_coding_benchmark_health(window=window)
    actions = _auto_link_release_gate_by_coding_benchmark(manager=manager, gate=gate, health=health)
    return {
        "status": "ok",
        "actions": actions,
        "health": health,
        "gate": manager.get_release_gate_status(),
        "leaderboard": _build_coding_benchmark_leaderboard(window=window),
    }

@app.get("/debug/coding-benchmark/scheduler", dependencies=[Depends(verify_admin_token)])
async def get_coding_benchmark_scheduler_status():
    sched_cfg = config.get("security.coding_benchmark_scheduler", {}) or {}
    if not isinstance(sched_cfg, dict):
        sched_cfg = {}
    return {
        "status": "ok",
        "scheduler": sched_cfg,
        "state": dict(_coding_benchmark_scheduler_state),
    }

@app.post("/debug/coding-benchmark/scheduler/run-now", dependencies=[Depends(verify_admin_token)])
async def run_coding_benchmark_scheduler_now():
    result = _maybe_run_scheduled_coding_benchmark(force=True)
    return {"status": "ok", "result": result}

@app.get("/debug/eval-samples", dependencies=[Depends(verify_admin_token)])
async def get_eval_samples(limit: int = 100, label: Optional[str] = None):
    """Export feedback-linked regression samples from trajectories."""
    if TRAJECTORY_STORE is None:
        return {"samples": [], "total": 0, "note": "Trajectory store not injected yet."}
    samples = TRAJECTORY_STORE.list_feedback_samples(limit=limit, label=label)
    return {"samples": samples, "total": len(samples)}

@app.post("/debug/eval-benchmarks/build", dependencies=[Depends(verify_admin_token)])
async def build_eval_benchmark(payload: Dict[str, Any]):
    """Build a benchmark dataset from feedback-linked eval samples."""
    if TRAJECTORY_STORE is None:
        raise HTTPException(status_code=503, detail="Trajectory store not available")
    name = str(payload.get("name", "feedback_benchmark")).strip() or "feedback_benchmark"
    label = str(payload.get("label", "")).strip().lower() or None
    limit_raw = payload.get("limit", 200)
    try:
        limit = max(1, min(int(limit_raw), 1000))
    except (TypeError, ValueError):
        limit = 200

    samples = TRAJECTORY_STORE.list_feedback_samples(limit=limit, label=label)
    manager = _get_eval_benchmark_manager()
    dataset = manager.build_dataset(name=name, samples=samples, source="trajectory_feedback")
    return {"status": "ok", "dataset": dataset}

@app.get("/debug/eval-benchmarks", dependencies=[Depends(verify_admin_token)])
async def list_eval_benchmarks(limit: int = 50):
    manager = _get_eval_benchmark_manager()
    items = manager.list_datasets(limit=limit)
    return {"items": items, "total": len(items)}

@app.get("/debug/eval-benchmarks/{dataset_id}", dependencies=[Depends(verify_admin_token)])
async def get_eval_benchmark(dataset_id: str):
    manager = _get_eval_benchmark_manager()
    payload = manager.get_dataset(dataset_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Benchmark dataset not found")
    return payload

@app.post("/debug/eval-benchmarks/{dataset_id}/run", dependencies=[Depends(verify_admin_token)])
async def run_eval_benchmark(dataset_id: str, payload: Dict[str, Any]):
    """Run a benchmark dataset and return scoring metrics."""
    outputs_raw = payload.get("outputs", {})
    outputs: Dict[str, str] = {}
    if isinstance(outputs_raw, dict):
        outputs = {str(k): str(v) for k, v in outputs_raw.items()}
    gate_raw = payload.get("gate", {})
    gate: Dict[str, float] = {}
    if isinstance(gate_raw, dict):
        for key in ("min_composite_score", "min_pass_rate", "max_error_rate"):
            if key in gate_raw:
                try:
                    gate[key] = float(gate_raw[key])
                except (TypeError, ValueError):
                    continue

    manager = _get_eval_benchmark_manager()
    report = manager.run_dataset(dataset_id, outputs=outputs, gate=gate)
    if report is None:
        raise HTTPException(status_code=404, detail="Benchmark dataset not found")
    quality_gate = report.get("quality_gate", {})
    gate_blocked = bool(quality_gate.get("blocked", False))
    gate_reasons = quality_gate.get("reasons", [])
    gate_reason_text = ", ".join(str(item) for item in gate_reasons) if gate_reasons else ""
    gate_status = manager.set_release_gate_status(
        blocked=gate_blocked,
        reason=gate_reason_text or ("quality_gate_passed" if not gate_blocked else "quality_gate_blocked"),
        source=f"eval:{dataset_id}",
        metadata={
            "dataset_id": dataset_id,
            "composite_score": report.get("composite_score"),
            "pass_rate": report.get("pass_rate"),
            "error_rate": report.get("error_rate"),
        },
    )
    fail_threshold_raw = config.get("security.optimization_fail_streak_threshold", 2)
    try:
        fail_threshold = max(1, int(fail_threshold_raw))
    except (TypeError, ValueError):
        fail_threshold = 2
    optimization = manager.register_gate_result(
        dataset_id,
        report,
        fail_streak_threshold=fail_threshold,
    )
    _append_policy_audit(
        action="release.gate.updated",
        details={
            "blocked": gate_status.get("blocked", False),
            "source": gate_status.get("source", ""),
            "reason": gate_status.get("reason", ""),
        },
    )
    if optimization.get("task_created"):
        task = optimization.get("task", {}) if isinstance(optimization, dict) else {}
        optimization["rule_prompt_patch"] = _build_rule_prompt_patch(report)
        _append_policy_audit(
            action="optimization.task.created",
            details={
                "task_id": task.get("task_id"),
                "dataset_id": dataset_id,
                "fail_streak": optimization.get("fail_streak", 0),
            },
        )
        _append_policy_audit(
            action="optimization.rule_patch.created",
            details={
                "dataset_id": dataset_id,
                "rule_count": len((optimization.get("rule_prompt_patch") or {}).get("rules", [])),
            },
        )

    training_job_payload: Optional[Dict[str, Any]] = None
    trainer_enabled = bool(config.get("trainer.enabled", True))
    auto_trainer = bool(config.get("trainer.auto_run_on_gate_fail", True))
    if trainer_enabled and gate_blocked and optimization.get("task_created", False):
        max_samples_raw = config.get("trainer.max_samples_per_job", 200)
        try:
            max_samples = max(1, min(int(max_samples_raw), 1000))
        except (TypeError, ValueError):
            max_samples = 200
        train_inputs = _prepare_training_inputs(
            dataset_id=dataset_id,
            report=report,
            max_samples=max_samples,
        )
        training_mgr = _get_training_job_manager()
        created = training_mgr.create_job(
            dataset_id=dataset_id,
            trajectory_samples=train_inputs["trajectory_samples"],
            eval_samples=train_inputs["eval_samples"],
            source="auto_gate_fail",
            metadata={
                "optimization_task_id": ((optimization.get("task") or {}).get("task_id") if isinstance(optimization, dict) else None),
                "release_gate_reason": gate_status.get("reason", ""),
            },
        )
        if auto_trainer:
            executed = training_mgr.run_job(str(created.get("job_id", "")))
            training_job_payload = executed or created
        else:
            training_job_payload = created
        _append_policy_audit(
            action="trainer.job.created",
            details={
                "job_id": training_job_payload.get("job_id") if isinstance(training_job_payload, dict) else "",
                "dataset_id": dataset_id,
                "auto_run": auto_trainer,
            },
        )
    if gate_blocked:
        _append_alert(
            "critical",
            "quality_gate",
            "quality_gate_blocked",
            {
                "dataset_id": dataset_id,
                "reason": gate_status.get("reason", ""),
                "source": gate_status.get("source", ""),
            },
        )
    return {
        "status": "ok",
        "report": report,
        "release_gate": gate_status,
        "optimization": optimization,
        "training_job": training_job_payload,
    }

@app.get("/debug/eval-benchmarks/{dataset_id}/runs", dependencies=[Depends(verify_admin_token)])
async def list_eval_benchmark_runs(dataset_id: str, limit: int = 20):
    manager = _get_eval_benchmark_manager()
    dataset = manager.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Benchmark dataset not found")
    runs = manager.list_runs(dataset_id, limit=limit)
    return {"items": runs, "total": len(runs)}

@app.get("/debug/eval-benchmarks/{dataset_id}/latest", dependencies=[Depends(verify_admin_token)])
async def get_latest_eval_benchmark_run(dataset_id: str):
    manager = _get_eval_benchmark_manager()
    dataset = manager.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Benchmark dataset not found")
    latest = manager.get_latest_run(dataset_id)
    if latest is None:
        raise HTTPException(status_code=404, detail="No run report found for this dataset")
    return latest

@app.get("/debug/eval-benchmarks/{dataset_id}/compare", dependencies=[Depends(verify_admin_token)])
async def compare_eval_benchmark_runs(dataset_id: str, baseline_index: int = 1):
    manager = _get_eval_benchmark_manager()
    dataset = manager.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Benchmark dataset not found")
    result = manager.compare_with_baseline(dataset_id, baseline_index=baseline_index)
    if result is None:
        raise HTTPException(status_code=404, detail="Not enough run history to compare")
    return result

@app.post("/debug/eval-benchmarks/{dataset_id}/gate", dependencies=[Depends(verify_admin_token)])
async def evaluate_eval_benchmark_gate(dataset_id: str, payload: Dict[str, Any]):
    """Evaluate quality gate against historical run report."""
    manager = _get_eval_benchmark_manager()
    dataset = manager.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Benchmark dataset not found")
    gate_raw = payload.get("gate", {})
    if not isinstance(gate_raw, dict):
        raise HTTPException(status_code=400, detail="'gate' must be an object")
    gate: Dict[str, float] = {}
    for key in ("min_composite_score", "min_pass_rate", "max_error_rate"):
        if key in gate_raw:
            try:
                gate[key] = float(gate_raw[key])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"Invalid gate value for '{key}'")
    run_index_raw = payload.get("run_index", 0)
    try:
        run_index = int(run_index_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="'run_index' must be an integer")
    result = manager.evaluate_gate(dataset_id, gate=gate, run_index=run_index)
    if result is None:
        raise HTTPException(status_code=404, detail="Run report not found for requested index")
    return {"status": "ok", "evaluation": result}

@app.get("/debug/release-gate", dependencies=[Depends(verify_admin_token)])
async def get_release_gate_status():
    manager = _get_eval_benchmark_manager()
    gate = manager.get_release_gate_status()
    workflow = _build_workflow_observability_metrics(limit=200)
    persona = _latest_persona_consistency_signal()
    coding = _build_coding_quality_metrics(window=100)
    health = _assess_release_gate_workflow_health(
        gate_status=gate,
        workflow_metrics=workflow,
        persona_metrics=persona,
        coding_metrics=coding,
    )
    auto_link = bool(config.get("security.release_gate_auto_link_enabled", True)) and all(
        hasattr(manager, attr)
        for attr in ("set_release_gate_status", "register_gate_result", "list_optimization_tasks", "set_optimization_task_status")
    )
    auto_actions: Dict[str, Any] = {}
    if auto_link:
        auto_actions = _auto_link_release_gate(manager=manager, gate=gate, health=health)
        gate = manager.get_release_gate_status()
        health = _assess_release_gate_workflow_health(
            gate_status=gate,
            workflow_metrics=workflow,
            persona_metrics=persona,
            coding_metrics=coding,
        )
    if str(health.get("level", "")).lower() in {"warning", "critical"}:
        _append_alert(
            str(health.get("level", "warning")),
            "release_gate",
            str(health.get("message", "release_gate_signal")),
            {
                "gate_blocked": bool(gate.get("blocked", False)),
                "reason": str(gate.get("reason", "")),
                "signals": health.get("signals", {}),
            },
        )
    return {
        "status": "ok",
        "gate": gate,
        "workflow": workflow,
        "persona": persona,
        "coding": coding,
        "health": health,
        "thresholds": health.get("thresholds", {}),
        "auto_link": auto_actions,
    }

@app.post("/debug/release-gate/auto-link", dependencies=[Depends(verify_admin_token)])
async def auto_link_release_gate():
    manager = _get_eval_benchmark_manager()
    gate = manager.get_release_gate_status()
    workflow = _build_workflow_observability_metrics(limit=200)
    persona = _latest_persona_consistency_signal()
    coding = _build_coding_quality_metrics(window=100)
    health = _assess_release_gate_workflow_health(
        gate_status=gate,
        workflow_metrics=workflow,
        persona_metrics=persona,
        coding_metrics=coding,
    )
    actions = _auto_link_release_gate(manager=manager, gate=gate, health=health)
    return {
        "status": "ok",
        "actions": actions,
        "gate": manager.get_release_gate_status(),
        "health": health,
        "coding": coding,
    }

@app.post("/debug/release-gate/override", dependencies=[Depends(verify_admin_token)])
async def override_release_gate(payload: Dict[str, Any]):
    manager = _get_eval_benchmark_manager()
    blocked = bool(payload.get("blocked", False))
    reason = str(payload.get("reason", "")).strip() or "manual_override"
    source = str(payload.get("source", "manual")).strip() or "manual"
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    status = manager.set_release_gate_status(
        blocked=blocked,
        reason=reason,
        source=source,
        metadata=metadata,
    )
    _append_policy_audit(
        action="release.gate.override",
        details={
            "blocked": status.get("blocked", False),
            "source": status.get("source", ""),
            "reason": status.get("reason", ""),
        },
    )
    return {"status": "ok", "gate": status}

@app.get("/debug/optimization-tasks", dependencies=[Depends(verify_admin_token)])
async def list_optimization_tasks(limit: int = 50, status: Optional[str] = None, dataset_id: Optional[str] = None):
    manager = _get_eval_benchmark_manager()
    items = manager.list_optimization_tasks(limit=limit, status=status, dataset_id=dataset_id)
    return {"status": "ok", "items": items, "total": len(items)}

@app.post("/debug/optimization-tasks/{task_id}/status", dependencies=[Depends(verify_admin_token)])
async def update_optimization_task_status(task_id: str, payload: Dict[str, Any]):
    manager = _get_eval_benchmark_manager()
    status = str(payload.get("status", "")).strip().lower() or "open"
    note = str(payload.get("note", "")).strip()
    updated = manager.set_optimization_task_status(task_id=task_id, status=status, note=note)
    if updated is None:
        raise HTTPException(status_code=404, detail="Optimization task not found")
    _append_policy_audit(
        action="optimization.task.updated",
        details={"task_id": task_id, "status": status},
    )
    return {"status": "ok", "task": updated}

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
    return {"status": "ok", "candidate": OnlinePolicyLoopManager._compact(created)}

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
    return {"status": "ok", "candidate": OnlinePolicyLoopManager._compact(updated)}

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
    return {"status": "ok", "candidate": OnlinePolicyLoopManager._compact(updated)}

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
        "candidate": OnlinePolicyLoopManager._compact(published),
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

@app.get("/debug/config", dependencies=[Depends(verify_admin_token)])
async def get_debug_config():
    """Get current config (redacted) for the debug viewer."""
    return _redact_config(config.data)

@app.get("/debug/hardware/status", dependencies=[Depends(verify_admin_token)])
async def get_hardware_status():
    """Best-effort hardware status for camera/microphone/body bridge diagnostics."""
    payload: Dict[str, Any] = {
        "camera": {
            "configured_enabled": bool(config.get("perception.camera_enabled", False)),
            "configured_device_index": int(config.get("perception.camera_device_index", 0) or 0),
            "ok": False,
        },
        "microphone": {
            "configured_input_device": config.get("asr.input_device", None),
            "ok": False,
        },
        "body_bridge": {
            "configured_type": str(config.get("body.type", "none") or "none"),
            "ok": str(config.get("body.type", "none") or "none") != "none",
        },
    }
    try:
        import cv2 as _cv2

        idx = int(config.get("perception.camera_device_index", 0) or 0)
        cap = _cv2.VideoCapture(idx)
        payload["camera"]["ok"] = bool(cap.isOpened())
        cap.release()
    except Exception as exc:
        payload["camera"]["error"] = str(exc)

    try:
        from perception.ear import get_ear

        payload["microphone"] = get_ear().get_status()
    except Exception as exc:
        payload["microphone"]["error"] = str(exc)
    return {"status": "ok", "hardware": payload}

@app.post("/debug/test/{test_name}", dependencies=[Depends(verify_admin_token)])
async def run_diagnostic_test(test_name: str):
    """Run a diagnostic test."""
    import time as _time
    try:
        if test_name == "llm_connection":
            # Actually test the LLM provider by sending a minimal request
            from soul.models import ModelRegistry
            from llm.litellm_provider import LiteLLMProvider
            provider_name, _ = ModelRegistry.resolve_model_ref("slow_brain")
            provider_name = str(provider_name or "openai").strip() or "openai"
            provider_cfg = ModelRegistry.get_provider_config(provider_name) or {}
            api_mode = str(provider_cfg.get("api", "") or "").strip() or None
            headers = provider_cfg.get("headers")
            extra_headers = headers if isinstance(headers, dict) else None
            raw_auth_mode = str(provider_cfg.get("auth", "") or "").strip().lower()
            auth_mode = raw_auth_mode if raw_auth_mode in {"", "api-key", "bearer", "none"} else ""
            raw_auth_header = provider_cfg.get("authHeader")
            if raw_auth_header is None:
                raw_auth_header = provider_cfg.get("auth_header")
            if auth_mode in {"api-key", "bearer"}:
                auth_header = True
            elif auth_mode == "none":
                auth_header = False
            else:
                auth_header = bool(raw_auth_header) if isinstance(raw_auth_header, bool) else False
            raw_strict_api_mode = provider_cfg.get("strict_api_mode")
            if raw_strict_api_mode is None:
                raw_strict_api_mode = provider_cfg.get("strictApiMode")
            strict_api_mode = bool(raw_strict_api_mode) if isinstance(raw_strict_api_mode, bool) else True
            raw_reasoning_param = provider_cfg.get("reasoning_param")
            if raw_reasoning_param is None:
                raw_reasoning_param = provider_cfg.get("reasoningParam")
            reasoning_param = raw_reasoning_param if isinstance(raw_reasoning_param, bool) else None
            api_key, base_url, model_name, _headers = ModelRegistry.resolve_model("slow_brain")
            provider = LiteLLMProvider(
                api_key=api_key,
                api_base=base_url,
                default_model=model_name,
                api_mode=api_mode,
                extra_headers=extra_headers,
                auth_mode=auth_mode,
                auth_header=auth_header,
                strict_api_mode=strict_api_mode,
                reasoning_param=reasoning_param,
            )
            t0 = _time.monotonic()
            resp = await provider.chat(
                messages=[{"role": "user", "content": "Say OK"}],
                tools=[], model=model_name, max_tokens=8, temperature=0,
            )
            latency_ms = int((_time.monotonic() - t0) * 1000)
            if resp.error:
                return {"success": False, "message": f"LLM error: {resp.content}"}
            return {
                "success": True,
                "message": f"LLM OK — model={resp.model}, latency={latency_ms}ms, request_id={resp.request_id}",
            }
        
        elif test_name == "tts_synthesis":
            # Test TTS synthesis
            provider = config.get("voice.provider", "edge-tts")
            return {"success": True, "message": f"TTS provider ({provider}) is available"}
        
        elif test_name == "asr_recognition":
            status = await get_hardware_status()
            mic_ok = bool(((status or {}).get("hardware", {}).get("microphone", {}) or {}).get("ok", False))
            return {
                "success": mic_ok,
                "message": "ASR input device is available" if mic_ok else "ASR input device unavailable",
                "details": (status or {}).get("hardware", {}).get("microphone", {}),
            }
        
        elif test_name == "memory_index":
            # Test memory index
            _get_memory_manager().index.fts_search("test", limit=1)
            return {"success": True, "message": "OpenViking memory search adapter is healthy"}
        
        elif test_name == "hardware_bridge":
            status = await get_hardware_status()
            hw = (status or {}).get("hardware", {})
            bridge_ok = bool((hw.get("body_bridge") or {}).get("ok", False))
            return {
                "success": bridge_ok,
                "message": "Hardware bridge available" if bridge_ok else "Hardware bridge not configured",
                "details": hw,
            }
        
        else:
            return {"success": False, "message": f"Unknown test: {test_name}"}
    
    except Exception as e:
        return {"success": False, "message": str(e)}
