"""Coding loop, benchmark, and edit operation helpers extracted from _shared.py."""

from __future__ import annotations
import copy
import json
import logging
import re
import shlex
import subprocess
import time
import uuid
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from fastapi import HTTPException
from runtime.config_manager import config
from tools.admin.state import (
    _coding_quality_history,
    _coding_benchmark_history,
    _coding_benchmark_scheduler_state,
    _gui_simple_benchmark_history,
    _PROJECT_ROOT,
)
from tools.admin.utils import _TOOL_ERROR_PATTERN, _is_subpath
from tools.admin.observability_helpers import _get_eval_benchmark_manager
from runtime.task_store import TaskExecutionStore

TASK_RUN_STORE = TaskExecutionStore()

if TYPE_CHECKING:
    from eval.benchmark import EvalBenchmarkManager

logger = logging.getLogger('GazerAdminAPI')


def _safe_task_path(rel_path: str) -> Path:
    candidate = Path(rel_path).expanduser()
    if candidate.is_absolute():
        raise ValueError("Absolute paths are not allowed")
    target = (_PROJECT_ROOT / candidate).resolve()
    if not _is_subpath(_PROJECT_ROOT, target):
        raise ValueError("Path traversal detected")
    return target


def _run_verify_command(cmd: str, cwd: Path, timeout_seconds: int = 120) -> Dict[str, Any]:
    blocked_metachars = [";", "&", "|", ">", "<", "`", "$(", "${"]
    for char in blocked_metachars:
        if char in cmd:
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": f"Error: Command contains blocked shell metacharacters: {char}",
            }
    first_word = cmd.strip().split()[0].lower() if cmd.strip() else ""
    blocked_execs = ["powershell", "powershell.exe", "cmd", "cmd.exe", "bash", "sh", "zsh"]
    if first_word in blocked_execs:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Error: blocked verify executable: {first_word}",
        }
    try:
        args = shlex.split(cmd, posix=True)
        if not args:
            return {"ok": False, "returncode": -1, "stdout": "", "stderr": "Error: verify command is empty"}
        res = subprocess.run(
            args, shell=False, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout_seconds,
        )
        return {
            "ok": True,
            "exit_code": res.returncode,
            "returncode": res.returncode,
            "logs": res.stdout + "\n" + res.stderr,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "exit_code": 124,
            "returncode": 124,
            "logs": f"Timeout after {timeout_seconds}s\n" + (e.stdout or "") + "\n" + (e.stderr or ""),
            "stdout": e.stdout or "",
            "stderr": f"Timeout after {timeout_seconds}s\n" + (e.stderr or ""),
        }
    except Exception as e:
        return {"ok": False, "exit_code": 1, "returncode": 1, "logs": str(e), "stdout": "", "stderr": str(e)}


def _record_coding_quality_event(event: Dict[str, Any]):
    import time
    event["ts"] = time.time()
    _coding_quality_history.append(event)

def _parse_tool_error_result(text: str) -> Dict[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return {"code": "UNKNOWN_ERROR", "message": ""}
    match = _TOOL_ERROR_PATTERN.match(raw)
    if not match:
        return {"code": "UNKNOWN_ERROR", "message": raw[:200]}
    return {
        "code": str(match.group(1) or "UNKNOWN_ERROR").strip().upper(),
        "message": str(match.group(2) or "").strip(),
    }

def _assess_coding_benchmark_health(window: int = 20) -> Dict[str, Any]:
    size = max(1, min(int(window), 200))
    items = list(_coding_benchmark_history)[-size:]
    threshold_cfg = config.get("security.coding_benchmark_gate", {}) or {}
    if not isinstance(threshold_cfg, dict):
        threshold_cfg = {}

    def _float_cfg(key: str, default: float) -> float:
        try:
            return float(threshold_cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _int_cfg(key: str, default: int) -> int:
        try:
            return int(threshold_cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    thresholds = {
        "warning_avg_score": _float_cfg("warning_avg_score", 0.8),
        "critical_avg_score": _float_cfg("critical_avg_score", 0.6),
        "warning_latest_score": _float_cfg("warning_latest_score", 0.75),
        "critical_latest_score": _float_cfg("critical_latest_score", 0.5),
        "warning_fail_runs": max(1, _int_cfg("warning_fail_runs", 2)),
        "critical_fail_runs": max(1, _int_cfg("critical_fail_runs", 3)),
    }

    if not items:
        return {
            "level": "unknown",
            "recommend_block_high_risk": False,
            "message": "coding_benchmark_no_data",
            "signals": {},
            "thresholds": thresholds,
            "window": size,
        }

    scores = [float(item.get("score", 0.0) or 0.0) for item in items]
    avg_score = sum(scores) / len(scores)
    latest_score = float(items[-1].get("score", 0.0) or 0.0)
    fail_runs = sum(1 for score in scores if score < thresholds["warning_avg_score"])
    signals = {
        "avg_score": round(avg_score, 4),
        "latest_score": round(latest_score, 4),
        "fail_runs": fail_runs,
        "total_runs": len(items),
    }

    critical = (
        avg_score < thresholds["critical_avg_score"]
        or latest_score < thresholds["critical_latest_score"]
        or fail_runs >= thresholds["critical_fail_runs"]
    )
    warning = (
        avg_score < thresholds["warning_avg_score"]
        or latest_score < thresholds["warning_latest_score"]
        or fail_runs >= thresholds["warning_fail_runs"]
    )

    if critical:
        return {
            "level": "critical",
            "recommend_block_high_risk": True,
            "message": "coding_benchmark_critical",
            "signals": signals,
            "thresholds": thresholds,
            "window": size,
        }
    if warning:
        return {
            "level": "warning",
            "recommend_block_high_risk": False,
            "message": "coding_benchmark_warning",
            "signals": signals,
            "thresholds": thresholds,
            "window": size,
        }
    return {
        "level": "healthy",
        "recommend_block_high_risk": False,
        "message": "coding_benchmark_healthy",
        "signals": signals,
        "thresholds": thresholds,
        "window": size,
    }

def _auto_link_release_gate_by_coding_benchmark(
    *,
    manager: EvalBenchmarkManager,
    gate: Dict[str, Any],
    health: Dict[str, Any],
) -> Dict[str, Any]:
    current_blocked = bool((gate or {}).get("blocked", False))
    recommend_block = bool((health or {}).get("recommend_block_high_risk", False))
    level = str((health or {}).get("level", "")).strip().lower()
    actions: Dict[str, Any] = {"changed_gate": False, "created_task": False, "resolved_tasks": 0}

    if recommend_block != current_blocked:
        manager.set_release_gate_status(
            blocked=recommend_block,
            reason=str((health or {}).get("message", "")).strip() or "coding_benchmark_auto_link",
            source="coding_benchmark_auto_link",
            metadata={"level": level, "signals": health.get("signals", {})},
        )
        actions["changed_gate"] = True
        actions["blocked"] = recommend_block

    fail_threshold_raw = config.get("security.optimization_fail_streak_threshold", 2)
    try:
        fail_threshold = max(1, int(fail_threshold_raw))
    except (TypeError, ValueError):
        fail_threshold = 2
    dataset_id = "coding_benchmark_auto"
    if recommend_block:
        synthetic_report = {
            "quality_gate": {
                "blocked": True,
                "reasons": [str((health or {}).get("message", "")).strip() or "coding_benchmark_degraded"],
            }
        }
        optimization = manager.register_gate_result(
            dataset_id=dataset_id,
            report=synthetic_report,
            fail_streak_threshold=fail_threshold,
        )
        actions["created_task"] = bool((optimization or {}).get("task_created", False))
        actions["fail_streak"] = int((optimization or {}).get("fail_streak", 0) or 0)
    else:
        open_items = manager.list_optimization_tasks(limit=200, status="open", dataset_id=dataset_id)
        resolved = 0
        for item in open_items:
            task_id = str(item.get("task_id", "")).strip()
            if not task_id:
                continue
            updated = manager.set_optimization_task_status(
                task_id=task_id,
                status="resolved",
                note="auto_resolved_by_coding_benchmark_recovery",
            )
            if updated is not None:
                resolved += 1
        actions["resolved_tasks"] = resolved
    return actions

def _apply_edit_operation(original: str, item: Dict[str, Any]) -> tuple[str, bool, str]:
    """Applies an edit operation to the original text based on operation types."""
    import re
    if not isinstance(item, dict):
        return original, False, "invalid_item"
    operation = str(item.get("operation", "replace") or "replace").lower()
    
    if operation == "replace":
        find_text = str(item.get("find", ""))
        replace_text = str(item.get("replace", ""))
        if not find_text:
            return original, False, "empty_find"
        if find_text in original:
            return original.replace(find_text, replace_text), True, "exact"
        return original, False, "not_found"
    
    elif operation == "insert_before":
        anchor = str(item.get("anchor", ""))
        replace_text = str(item.get("replace", ""))
        if not anchor:
            return original, False, "empty_anchor"
        if anchor in original:
            return original.replace(anchor, replace_text + anchor), True, "exact"
        return original, False, "not_found"
        
    elif operation == "insert_after":
        anchor = str(item.get("anchor", ""))
        replace_text = str(item.get("replace", ""))
        if not anchor:
            return original, False, "empty_anchor"
        if anchor in original:
            return original.replace(anchor, anchor + replace_text), True, "exact"
        return original, False, "not_found"
        
    elif operation == "delete":
        find_text = str(item.get("find", ""))
        if not find_text:
            return original, False, "empty_find"
        if find_text in original:
            return original.replace(find_text, ""), True, "exact"
        return original, False, "not_found"
        
    elif operation == "regex_replace":
        find_text = str(item.get("find", ""))
        replace_text = str(item.get("replace", ""))
        if not find_text:
            return original, False, "empty_find"
        try:
            matched = bool(re.search(find_text, original))
            if matched:
                return re.sub(find_text, replace_text, original), True, "regex"
            return original, False, "not_found"
        except re.error:
            return original, False, "regex_error"
            
    return original, False, "unsupported"

def _execute_deterministic_coding_loop(
    *,
    task_id: str,
    run_id: str,
    goal: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    started = time.time()
    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        raise HTTPException(status_code=400, detail="deterministic mode requires non-empty 'edits'")
    max_retries_raw = payload.get("max_retries", 1)
    try:
        max_retries = max(0, min(int(max_retries_raw), 3))
    except Exception:
        max_retries = 1
    timeout_raw = payload.get("test_timeout_seconds", 240)
    try:
        timeout_seconds = max(10, min(int(timeout_raw), 1200))
    except Exception:
        timeout_seconds = 240
    test_commands = payload.get("test_commands")
    if not isinstance(test_commands, list):
        test_commands = []
    test_commands = [str(item).strip() for item in test_commands if str(item).strip()]

    TASK_RUN_STORE.add_checkpoint(task_id, stage="discover", status="running", note="collect_input")
    search_pattern = str(payload.get("search_pattern", "") or "").strip()
    discover_hits: List[str] = []
    if search_pattern:
        for rec in edits:
            fp = str((rec or {}).get("file", "") or "").strip()
            if fp:
                discover_hits.append(fp)
    TASK_RUN_STORE.add_checkpoint(
        task_id,
        stage="discover",
        status="ok",
        note="input_ready",
        metadata={"search_pattern": search_pattern, "hits": discover_hits[:50]},
    )

    def apply_round(edit_items: List[Dict[str, Any]]) -> tuple[List[str], Dict[str, str], List[Dict[str, Any]]]:
        changed_files: List[str] = []
        backups: Dict[str, str] = {}
        applied: List[Dict[str, Any]] = []
        staged_updates: Dict[str, str] = {}
        for idx, item in enumerate(edit_items):
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail=f"edits[{idx}] must be an object")
            rel_file = str(item.get("file", "") or "").strip()
            if not rel_file:
                raise HTTPException(status_code=400, detail=f"edits[{idx}] requires file")
            path = _safe_task_path(rel_file)
            path_key = str(path)
            original = staged_updates[path_key] if path_key in staged_updates else path.read_text(encoding="utf-8")
            if path_key not in backups:
                backups[path_key] = path.read_text(encoding="utf-8")

            updated, ok, mode = _apply_edit_operation(original, item)
            if not ok:
                op = str(item.get("operation", "replace") or "replace")
                raise HTTPException(
                    status_code=400,
                    detail=f"edits[{idx}] operation={op} cannot apply in {rel_file} ({mode})",
                )
            staged_updates[path_key] = updated
            if updated != backups[path_key]:
                changed_files.append(rel_file)
            applied.append({"file": rel_file, "match_mode": mode, "operation": str(item.get("operation", "replace"))})

        # Atomic commit for this round: only write after all edits are validated.
        for p, txt in staged_updates.items():
            Path(p).write_text(txt, encoding="utf-8")
        return sorted(set(changed_files)), backups, applied

    retries = 0
    all_verify_runs: List[Dict[str, Any]] = []
    fallback_used = False
    recovery_count = 0
    final_changed: List[str] = []
    final_applied: List[Dict[str, Any]] = []
    while True:
        TASK_RUN_STORE.add_checkpoint(
            task_id, stage="patch", status="running", note=f"apply_round_{retries + 1}"
        )
        changed_files, backups, applied_info = apply_round(edits)
        final_changed = list(changed_files)
        final_applied = list(applied_info)
        TASK_RUN_STORE.add_checkpoint(
            task_id,
            stage="patch",
            status="ok",
            note="patch_applied",
            metadata={"files_changed": changed_files, "applied": applied_info},
        )

        verify_ok = True
        verify_runs: List[Dict[str, Any]] = []
        if test_commands:
            TASK_RUN_STORE.add_checkpoint(task_id, stage="verify", status="running", note="running_tests")
            for cmd in test_commands:
                one = _run_verify_command(cmd, _PROJECT_ROOT, timeout_seconds=timeout_seconds)
                verify_runs.append(one)
                if not one.get("ok", False):
                    verify_ok = False
            all_verify_runs.extend(verify_runs)
            TASK_RUN_STORE.add_checkpoint(
                task_id,
                stage="verify",
                status="ok" if verify_ok else "error",
                note="tests_completed",
                metadata={"results": verify_runs},
            )

        if verify_ok:
            break

        if retries >= max_retries:
            # Rollback to keep workspace clean on failed deterministic loop.
            for p, txt in backups.items():
                Path(p).write_text(txt, encoding="utf-8")
            TASK_RUN_STORE.add_checkpoint(
                task_id,
                stage="rollback",
                status="ok",
                note="verify_failed_restored_backup",
            )
            raise HTTPException(status_code=400, detail="Verification failed after retries; changes rolled back.")

        retries += 1
        fallback_edits = payload.get("fallback_edits")
        if isinstance(fallback_edits, list) and fallback_edits:
            edits = fallback_edits
            fallback_used = True
            recovery_count += 1
        else:
            # Retry same edits once with rollback first.
            for p, txt in backups.items():
                Path(p).write_text(txt, encoding="utf-8")
            recovery_count += 1

    duration_ms = round((time.time() - started) * 1000.0, 2)
    tests_total = len(test_commands)
    tests_passed = sum(1 for item in all_verify_runs if bool(item.get("ok", False)))
    output = {
        "mode": "deterministic",
        "goal": goal,
        "run_id": run_id,
        "files_changed": final_changed,
        "applied": final_applied,
        "verify": all_verify_runs,
        "verify_ok": tests_passed == tests_total if tests_total > 0 else True,
        "tests_total": tests_total,
        "tests_passed": tests_passed,
        "retries": retries,
        "fallback_used": fallback_used,
        "recovery_count": recovery_count,
        "duration_ms": duration_ms,
    }
    _record_coding_quality_event(
        {
            "task_id": task_id,
            "run_id": run_id,
            "kind": "coding_loop",
            "success": bool(output.get("verify_ok", False)),
            "duration_ms": duration_ms,
            "files_changed": len(final_changed),
            "tests_total": tests_total,
            "tests_passed": tests_passed,
            "retries": retries,
            "fallback_used": fallback_used,
            "recovery_count": recovery_count,
        }
    )
    return output

def _run_coding_benchmark_suite(payload: Dict[str, Any]) -> Dict[str, Any]:
    suite_name = str(payload.get("name", "default_suite") or "default_suite").strip()
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise HTTPException(status_code=400, detail="'cases' must be a non-empty array")
    timeout_raw = payload.get("test_timeout_seconds", 120)
    try:
        timeout_seconds = max(10, min(int(timeout_raw), 1200))
    except Exception:
        timeout_seconds = 120

    case_results: List[Dict[str, Any]] = []
    started = time.time()
    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            raise HTTPException(status_code=400, detail=f"cases[{idx}] must be an object")
        case_name = str(case.get("id", f"case_{idx+1}") or f"case_{idx+1}")
        files = case.get("files")
        edits = case.get("edits")
        if not isinstance(files, dict) or not isinstance(edits, list):
            raise HTTPException(status_code=400, detail=f"cases[{idx}] requires files(object) and edits(array)")
        verify_contains = case.get("verify_contains", {})
        if not isinstance(verify_contains, dict):
            verify_contains = {}
        test_commands = case.get("test_commands", [])
        if not isinstance(test_commands, list):
            test_commands = []

        with tempfile.TemporaryDirectory(prefix="gazer_coding_bench_") as tmpd:
            root = Path(tmpd)
            for rel, content in files.items():
                relp = str(rel or "").strip()
                if not relp:
                    continue
                p = (root / relp).resolve()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(content or ""), encoding="utf-8")

            local_payload = {
                "edits": edits,
                "max_retries": int(case.get("max_retries", 1) or 1),
                "test_commands": [str(cmd).strip() for cmd in test_commands if str(cmd).strip()],
                "test_timeout_seconds": timeout_seconds,
                "fallback_edits": case.get("fallback_edits", []),
            }

            import tools.admin.state as _state
            old_root = _state._PROJECT_ROOT
            try:
                _state._PROJECT_ROOT = root
                globals()["_PROJECT_ROOT"] = root
                out = _execute_deterministic_coding_loop(
                    task_id=f"bench_{suite_name}_{case_name}",
                    run_id=f"bench_{case_name}",
                    goal=str(case.get("goal", case_name)),
                    payload=local_payload,
                )
                verify_ok = bool(out.get("verify_ok", False))
                contains_ok = True
                contains_errors: List[str] = []
                for rel, needle in verify_contains.items():
                    fp = (root / str(rel)).resolve()
                    text = fp.read_text(encoding="utf-8") if fp.is_file() else ""
                    if str(needle) not in text:
                        contains_ok = False
                        contains_errors.append(f"{rel}:missing:{needle}")
                case_ok = bool(verify_ok and contains_ok)
                case_results.append(
                    {
                        "id": case_name,
                        "success": case_ok,
                        "verify_ok": verify_ok,
                        "contains_ok": contains_ok,
                        "contains_errors": contains_errors,
                        "duration_ms": out.get("duration_ms", 0.0),
                        "files_changed": len(out.get("files_changed", []) or []),
                        "retries": int(out.get("retries", 0) or 0),
                        "recovery_count": int(out.get("recovery_count", 0) or 0),
                    }
                )
            except HTTPException as exc:
                case_results.append(
                    {
                        "id": case_name,
                        "success": False,
                        "verify_ok": False,
                        "contains_ok": False,
                        "contains_errors": [str(exc.detail)],
                        "duration_ms": 0.0,
                        "files_changed": 0,
                        "retries": 0,
                        "recovery_count": 0,
                    }
                )
            finally:
                _state._PROJECT_ROOT = old_root
                globals()["_PROJECT_ROOT"] = old_root

    total = len(case_results)
    success = sum(1 for item in case_results if bool(item.get("success", False)))
    score = round(success / total, 4) if total > 0 else 0.0
    duration_ms = round((time.time() - started) * 1000.0, 2)
    summary = {
        "name": suite_name,
        "total_cases": total,
        "success_cases": success,
        "score": score,
        "duration_ms": duration_ms,
        "cases": case_results,
        "ts": time.time(),
    }
    _coding_benchmark_history.append(summary)
    return summary

def _maybe_run_scheduled_coding_benchmark(*, force: bool = False) -> Dict[str, Any]:
    """Run scheduled coding benchmark suite when due."""
    sched_cfg = config.get("security.coding_benchmark_scheduler", {}) or {}
    if not isinstance(sched_cfg, dict):
        sched_cfg = {}
    enabled = bool(sched_cfg.get("enabled", False))
    if not enabled and not force:
        return {"ran": False, "reason": "disabled"}

    payload = sched_cfg.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return {"ran": False, "reason": "empty_cases"}

    interval_raw = sched_cfg.get("interval_seconds", 1800)
    try:
        interval_seconds = max(30, min(int(interval_raw), 86400))
    except Exception:
        interval_seconds = 1800

    now = time.time()
    last_run = float(_coding_benchmark_scheduler_state.get("last_run_ts", 0.0) or 0.0)
    if not force and last_run > 0 and (now - last_run) < interval_seconds:
        return {"ran": False, "reason": "not_due", "next_in_seconds": round(interval_seconds - (now - last_run), 2)}

    summary = _run_coding_benchmark_suite(payload)
    _coding_benchmark_scheduler_state["last_run_ts"] = time.time()
    _coding_benchmark_scheduler_state["last_result"] = summary

    auto_link = bool(sched_cfg.get("auto_link_release_gate", True))
    auto_actions: Dict[str, Any] = {}
    gate: Optional[Dict[str, Any]] = None
    health: Optional[Dict[str, Any]] = None
    if auto_link:
        window_raw = sched_cfg.get("window", 20)
        try:
            window = max(1, min(int(window_raw), 200))
        except Exception:
            window = 20
        manager = _get_eval_benchmark_manager()
        gate = manager.get_release_gate_status()
        health = _assess_coding_benchmark_health(window=window)
        auto_actions = _auto_link_release_gate_by_coding_benchmark(manager=manager, gate=gate, health=health)
        gate = manager.get_release_gate_status()

    result = {
        "ran": True,
        "summary": summary,
        "auto_link": auto_actions,
        "health": health,
        "gate": gate,
    }
    _coding_benchmark_scheduler_state["last_result"] = result
    return result

