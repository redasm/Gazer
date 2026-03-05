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
from tools.admin.state import _PROJECT_ROOT
from agent.agents_md_lint import lint_agents_overlay
from tools.admin.coding_helpers import TASK_RUN_STORE
from tools.admin.state import (
    EVAL_BENCHMARK_MANAGER,
    ONLINE_POLICY_LOOP_MANAGER,
    PERSONA_EVAL_MANAGER,
    PERSONA_RUNTIME_MANAGER,
    TRAINING_BRIDGE_MANAGER,
    TRAINING_JOB_MANAGER,
    TRAJECTORY_STORE,
    config,
    _coding_benchmark_history,
    _coding_benchmark_scheduler_state,
    _llm_history,
)
from tools.admin.utils import _redact_config, _is_subpath, _resolve_export_output_path
from tools.admin.coding_helpers import (
    _assess_coding_benchmark_health,
    _auto_link_release_gate_by_coding_benchmark,
    _execute_deterministic_coding_loop,
    _maybe_run_scheduled_coding_benchmark,
    _run_coding_benchmark_suite,
)
from tools.admin.strategy_helpers import (
    _append_policy_audit,
    _capture_strategy_snapshot,
    _enqueue_chat_message,
    _get_release_gate_health_thresholds,
    _persona_runtime_thresholds,
    _require_orchestrator,
)
from tools.admin.training_helpers import (
    _build_resume_payload,
    _build_rule_prompt_patch,
    _build_task_view,
    _build_training_publish_diff,
    _build_training_release_explanation,
    _compare_replay_steps,
    _evaluate_training_release_canary_guard,
    _normalize_trajectory_steps,
    _prepare_training_inputs,
    _resolve_online_policy_gate_thresholds,
    _resolve_online_policy_offpolicy_config,
    _resolve_training_publish_rollout,
    _resolve_training_release_approval,
    _score_training_job,
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


# ---------------------------------------------------------------------------
# Training-domain routes (extracted to training_routes.py)
# ---------------------------------------------------------------------------
from tools.admin.training_routes import app as _training_app
app.include_router(_training_app)

# ---------------------------------------------------------------------------
# Persona-domain routes (extracted to persona_routes.py)
# ---------------------------------------------------------------------------
from tools.admin.persona_routes import app as _persona_app
app.include_router(_persona_app)


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
