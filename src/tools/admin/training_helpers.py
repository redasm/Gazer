"""Training pipeline, online policy, and release management helpers extracted from _shared.py."""

from __future__ import annotations
import copy
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from runtime.config_manager import config
from tools.admin.state import (
    TRAJECTORY_STORE,
    _mcp_audit_buffer,
    _mcp_request_ctx,
)

logger = logging.getLogger('GazerAdminAPI')


# ---------------------------------------------------------------------------
# Lazy import proxies (avoid circular imports at module load time)
# ---------------------------------------------------------------------------

def _get_eval_benchmark_manager():
    from tools.admin.observability_helpers import _get_eval_benchmark_manager as _impl
    return _impl()

def _assess_release_gate_workflow_health(*args, **kwargs):
    from tools.admin.debug import _assess_release_gate_workflow_health as _impl
    return _impl(*args, **kwargs)

def _build_workflow_observability_metrics(*args, **kwargs):
    from tools.admin.system import _build_workflow_observability_metrics as _impl
    return _impl(*args, **kwargs)

def _latest_persona_consistency_signal(*args, **kwargs):
    from tools.admin.system import _latest_persona_consistency_signal as _impl
    return _impl(*args, **kwargs)

def _build_coding_quality_metrics(*args, **kwargs):
    from tools.admin.system import _build_coding_quality_metrics as _impl
    return _impl(*args, **kwargs)


def _prepare_training_inputs(
    *,
    dataset_id: str,
    report: Dict[str, Any],
    max_samples: int,
) -> Dict[str, Any]:
    """Build trainer inputs from trajectory and eval report context."""
    def _quality_tier(score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.45:
            return "medium"
        return "low"

    def _is_negative_feedback(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        return any(token in normalized for token in {"unsafe", "wrong", "bad", "fail", "error"})

    def _score_sample(eval_item: Dict[str, Any], traj_item: Optional[Dict[str, Any]]) -> float:
        score = 0.0
        passed = eval_item.get("passed")
        if passed is True:
            score += 0.35
        elif passed is False:
            score += 0.08
        else:
            score += 0.18

        try:
            eval_score = float(eval_item.get("composite_score", eval_item.get("score")))
        except (TypeError, ValueError):
            eval_score = None
        if eval_score is not None:
            normalized = max(0.0, min(1.0, eval_score if eval_score <= 1.0 else eval_score / 100.0))
            score += 0.35 * normalized
        else:
            score += 0.2

        final_status = ""
        feedback_text = ""
        if isinstance(traj_item, dict):
            final = traj_item.get("final") if isinstance(traj_item.get("final"), dict) else {}
            final_status = str(final.get("status", "")).strip().lower()
            feedback_items = traj_item.get("feedback") if isinstance(traj_item.get("feedback"), list) else []
            if feedback_items:
                feedback_text = str((feedback_items[-1] or {}).get("feedback", ""))
        if final_status in {"done", "ok", "success", "completed"}:
            score += 0.15
        elif final_status in {"error", "failed", "llm_error", "incomplete"}:
            score += 0.0
        else:
            score += 0.08

        score += 0.0 if _is_negative_feedback(feedback_text) else 0.15
        return round(max(0.0, min(1.0, score)), 4)

    def _bucket_name(eval_item: Dict[str, Any], traj_item: Optional[Dict[str, Any]]) -> str:
        passed = eval_item.get("passed")
        final_status = ""
        if isinstance(traj_item, dict):
            final = traj_item.get("final") if isinstance(traj_item.get("final"), dict) else {}
            final_status = str(final.get("status", "")).strip().lower()
        has_error = final_status in {"error", "failed", "llm_error", "incomplete"}
        if passed is True:
            return "pass_error" if has_error else "pass_clean"
        if passed is False:
            return "fail_error" if has_error else "fail_clean"
        return "unknown"

    def _select_stratified(
        candidates: List[Dict[str, Any]],
        *,
        limit: int,
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        if len(candidates) <= limit:
            counts: Dict[str, int] = {}
            for item in candidates:
                bucket = str(item.get("_bucket", "unknown"))
                counts[bucket] = counts.get(bucket, 0) + 1
            return list(candidates), counts

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in candidates:
            bucket = str(item.get("_bucket", "unknown"))
            grouped.setdefault(bucket, []).append(item)
        for items in grouped.values():
            items.sort(
                key=lambda value: (
                    -float(value.get("_quality_score", 0.0)),
                    str((value.get("eval") or {}).get("run_id", "")),
                )
            )

        selected: List[Dict[str, Any]] = []
        for bucket in sorted(grouped.keys()):
            if len(selected) >= limit:
                break
            if grouped[bucket]:
                selected.append(grouped[bucket].pop(0))

        if len(selected) < limit:
            remaining: List[Dict[str, Any]] = []
            for items in grouped.values():
                remaining.extend(items)
            remaining.sort(
                key=lambda value: (
                    -float(value.get("_quality_score", 0.0)),
                    str((value.get("eval") or {}).get("run_id", "")),
                )
            )
            selected.extend(remaining[: max(0, limit - len(selected))])

        bucket_counts: Dict[str, int] = {}
        for item in selected:
            bucket = str(item.get("_bucket", "unknown"))
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        selected.sort(
            key=lambda value: (
                str((value.get("eval") or {}).get("run_id", "")),
            )
        )
        return selected, bucket_counts

    eval_results = list(report.get("results", []) or [])
    candidates: List[Dict[str, Any]] = []
    for item in eval_results:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id", "")).strip()
        if not run_id:
            continue
        traj = TRAJECTORY_STORE.get_trajectory(run_id) if TRAJECTORY_STORE is not None else None
        quality_score = _score_sample(item, traj if isinstance(traj, dict) else None)
        bucket = _bucket_name(item, traj if isinstance(traj, dict) else None)
        candidates.append(
            {
                "eval": item,
                "traj": traj if isinstance(traj, dict) else None,
                "_quality_score": quality_score,
                "_bucket": bucket,
            }
        )

    selected, bucket_counts = _select_stratified(candidates, limit=max(1, max_samples))
    selected_run_ids = [
        str((entry.get("eval") or {}).get("run_id", "")).strip()
        for entry in selected
        if str((entry.get("eval") or {}).get("run_id", "")).strip()
    ]
    selected_set = set(selected_run_ids)

    eval_samples: List[Dict[str, Any]] = []
    trajectory_samples: List[Dict[str, Any]] = []
    quality_scores: List[float] = []
    for entry in selected:
        eval_item = (entry.get("eval") or {}) if isinstance(entry.get("eval"), dict) else {}
        traj = entry.get("traj") if isinstance(entry.get("traj"), dict) else None
        quality_score = float(entry.get("_quality_score", 0.0))
        quality_scores.append(quality_score)
        eval_samples.append(
            {
                **eval_item,
                "quality_score": quality_score,
                "quality_tier": _quality_tier(quality_score),
                "sampling_bucket": str(entry.get("_bucket", "unknown")),
            }
        )

        run_id = str(eval_item.get("run_id", "")).strip()
        if not run_id or not traj:
            continue
        meta = traj.get("meta") if isinstance(traj.get("meta"), dict) else {}
        final = traj.get("final") if isinstance(traj.get("final"), dict) else {}
        feedback_items = traj.get("feedback") if isinstance(traj.get("feedback"), list) else []
        feedback_text = ""
        if feedback_items:
            feedback_text = str((feedback_items[-1] or {}).get("feedback", ""))
        trajectory_samples.append(
            {
                "run_id": run_id,
                "user_content": meta.get("user_content", ""),
                "assistant_output": final.get("final_content", ""),
                "status": final.get("status", ""),
                "feedback": feedback_text,
                "quality_score": quality_score,
                "quality_tier": _quality_tier(quality_score),
                "sampling_bucket": str(entry.get("_bucket", "unknown")),
            }
        )

    return {
        "dataset_id": dataset_id,
        "trajectory_samples": trajectory_samples,
        "eval_samples": eval_samples,
        "sampling": {
            "strategy": "quality_stratified_v1",
            "requested_max_samples": max(1, max_samples),
            "selected_count": len(selected),
            "available_count": len(candidates),
            "selected_run_ids": selected_run_ids,
            "selected_coverage": round(len(selected_set) / max(1, len(candidates)), 4),
            "bucket_counts": bucket_counts,
            "quality": {
                "avg": round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else None,
                "min": round(min(quality_scores), 4) if quality_scores else None,
                "max": round(max(quality_scores), 4) if quality_scores else None,
            },
        },
    }

def _build_rule_prompt_patch(report: Dict[str, Any]) -> Dict[str, Any]:
    quality_gate = report.get("quality_gate", {}) if isinstance(report, dict) else {}
    reasons = [str(item) for item in (quality_gate.get("reasons", []) or [])]
    rules: List[str] = []
    if "composite_score_below_threshold" in reasons:
        rules.append("Prioritize deterministic and concise responses for benchmark-critical prompts.")
    if "pass_rate_below_threshold" in reasons:
        rules.append("When confidence is low, ask one clarification question before invoking tools.")
    if "error_rate_above_threshold" in reasons:
        rules.append("Avoid repeating failed actions; return explicit fallback and recovery guidance.")
    if not rules:
        rules.append("Maintain persona consistency while minimizing avoidable tool errors.")
    return {
        "stage": "rule_prompt_optimization",
        "rules": rules,
        "source": "benchmark_gate_failure",
    }

def _normalize_trajectory_steps(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build normalized replay steps from trajectory events."""
    events = list((payload or {}).get("events") or [])
    steps: List[Dict[str, Any]] = []
    for idx, evt in enumerate(events):
        action = str((evt or {}).get("action", "") or "").strip().lower()
        if action not in {"tool_call", "tool_result"}:
            continue
        raw_payload = (evt or {}).get("payload") or {}
        step = {
            "index": idx,
            "ts": evt.get("ts"),
            "stage": str((evt or {}).get("stage", "") or ""),
            "action": action,
            "tool": str(raw_payload.get("tool", "") or ""),
            "tool_call_id": str(raw_payload.get("tool_call_id", "") or ""),
            "status": str(raw_payload.get("status", "") or ""),
            "error_code": str(raw_payload.get("error_code", "") or ""),
            "args_hash": str(raw_payload.get("args_hash", "") or ""),
            "args_preview": str(raw_payload.get("args_preview", "") or ""),
            "result_preview": str(raw_payload.get("result_preview", "") or ""),
            "has_media": bool(raw_payload.get("has_media", False)),
            "media_paths": list(raw_payload.get("media_paths", []) or []),
        }
        step["signature"] = "|".join(
            [
                step["action"],
                step["tool"],
                step["tool_call_id"],
                step["status"],
                step["error_code"],
                step["args_hash"],
            ]
        )
        steps.append(step)
    return steps

def _build_task_view(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize one trajectory into task-level observability metrics."""
    events = list((payload or {}).get("events") or [])
    stage_counts: Dict[str, int] = {}
    action_counts: Dict[str, int] = {}
    stage_first_ts: Dict[str, float] = {}
    stage_last_ts: Dict[str, float] = {}
    error_count = 0
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None

    for evt in events:
        stage = str((evt or {}).get("stage", "") or "unknown")
        action = str((evt or {}).get("action", "") or "unknown")
        ts = evt.get("ts")
        if isinstance(ts, (int, float)):
            tsv = float(ts)
            if first_ts is None or tsv < first_ts:
                first_ts = tsv
            if last_ts is None or tsv > last_ts:
                last_ts = tsv
            if stage not in stage_first_ts or tsv < stage_first_ts[stage]:
                stage_first_ts[stage] = tsv
            if stage not in stage_last_ts or tsv > stage_last_ts[stage]:
                stage_last_ts[stage] = tsv

        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        action_counts[action] = action_counts.get(action, 0) + 1
        raw_payload = (evt or {}).get("payload") or {}
        if action == "tool_result" and str(raw_payload.get("status", "")).lower() == "error":
            error_count += 1

    stages: List[Dict[str, Any]] = []
    for stage, count in stage_counts.items():
        s0 = stage_first_ts.get(stage)
        s1 = stage_last_ts.get(stage)
        duration_ms: Optional[float] = None
        if s0 is not None and s1 is not None:
            duration_ms = round(max(0.0, (s1 - s0) * 1000.0), 2)
        stages.append({"stage": stage, "count": count, "duration_ms": duration_ms})
    stages.sort(key=lambda x: x["stage"])

    total_duration_ms: Optional[float] = None
    if first_ts is not None and last_ts is not None:
        total_duration_ms = round(max(0.0, (last_ts - first_ts) * 1000.0), 2)

    final = (payload or {}).get("final") or {}
    return {
        "run_id": (payload or {}).get("run_id"),
        "status": str(final.get("status", "") or "running"),
        "event_count": len(events),
        "error_count": error_count,
        "stage_counts": stage_counts,
        "action_counts": action_counts,
        "stages": stages,
        "duration_ms": total_duration_ms,
        "turn_latency_ms": ((final.get("metrics") or {}).get("turn_latency_ms")),
    }

def _compare_replay_steps(
    run_steps: List[Dict[str, Any]],
    baseline_steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    run_signatures = [str(item.get("signature", "")) for item in run_steps]
    baseline_signatures = [str(item.get("signature", "")) for item in baseline_steps]
    shared = set(run_signatures) & set(baseline_signatures)
    missing_from_run = [sig for sig in baseline_signatures if sig not in shared]
    added_in_run = [sig for sig in run_signatures if sig not in shared]
    max_len = max(len(run_signatures), len(baseline_signatures), 1)
    overlap_ratio = round(len(shared) / max_len, 4)
    return {
        "run_steps": len(run_signatures),
        "baseline_steps": len(baseline_signatures),
        "shared_steps": len(shared),
        "overlap_ratio": overlap_ratio,
        "missing_from_run": missing_from_run[:20],
        "added_in_run": added_in_run[:20],
    }

def _build_resume_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a resume draft for interrupted/failed trajectories."""
    meta = (payload or {}).get("meta") or {}
    final = (payload or {}).get("final") or {}
    events = list((payload or {}).get("events") or [])

    last_error: Dict[str, Any] = {}
    for evt in reversed(events):
        if str((evt or {}).get("action", "")).lower() != "tool_result":
            continue
        p = (evt or {}).get("payload") or {}
        if str(p.get("status", "")).lower() == "error":
            last_error = {
                "tool": str(p.get("tool", "") or ""),
                "tool_call_id": str(p.get("tool_call_id", "") or ""),
                "error_code": str(p.get("error_code", "") or ""),
                "result_preview": str(p.get("result_preview", "") or ""),
            }
            break

    status = str(final.get("status", "") or "running")
    can_resume = status in {"running", "error", "incomplete", "llm_error"}
    user_content = str(meta.get("user_content", "") or "")
    final_preview = str(final.get("final_content", "") or "")[:240]
    error_line = ""
    if last_error:
        error_line = (
            f"最近失败工具: {last_error.get('tool')}, "
            f"error_code={last_error.get('error_code')}, "
            f"result={last_error.get('result_preview')[:120]}"
        )

    resume_message = (
        f"继续上次任务（run_id={payload.get('run_id', '')}）。\n"
        f"原始用户目标: {user_content}\n"
        f"上次结束状态: {status}\n"
        f"{error_line}\n"
        f"上次最终输出预览: {final_preview}\n"
        "要求：基于上述上下文继续执行，避免重复失败调用；如无法继续，请给出可执行替代方案。"
    ).strip()

    return {
        "run_id": payload.get("run_id"),
        "status": status,
        "can_resume": can_resume,
        "resume_message": resume_message,
        "last_error": last_error,
    }

def _unique_str_list(items: Any) -> list[str]:
    if not isinstance(items, (list, tuple, set)):
        return []
    result = []
    seen = set()
    for x in items:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return result

def _normalize_router_strategy(strategy: Any, fallback: str = "priority") -> str:
    s = str(strategy).strip().lower()
    if s in {"priority", "cost", "speed", "quality", "load"}:
        return s
    return fallback

def _apply_trainer_prompt_patch(base: str, rules: list[str], job_id: str) -> str:
    if not rules:
        return base
    lines = [str(base).rstrip()]
    if lines and not lines[0].endswith("\n"):
        lines.append("")
    lines.append(f"## <trainer_patch> (Applied from training job {job_id})")
    for r in rules:
        lines.append(f"- {r}")
    return "\n".join(lines)

def _build_training_publish_diff(job: Dict[str, Any]) -> Dict[str, Any]:
    output = (job.get("output") or {}) if isinstance(job, dict) else {}
    prompt_patch = output.get("prompt_patch") if isinstance(output, dict) else {}
    policy_patch = output.get("policy_patch") if isinstance(output, dict) else {}
    router_patch = output.get("router_patch") if isinstance(output, dict) else {}
    prompt_rules = _unique_str_list((prompt_patch or {}).get("rules", []))
    deny_add = _unique_str_list((policy_patch or {}).get("security.tool_denylist.add", []))

    before_prompt = str(config.get("personality.system_prompt", ""))
    before_deny = _unique_str_list(config.get("security.tool_denylist", []) or [])

    before_router_strategy = str(config.get("models.router.strategy", "priority")).strip().lower() or "priority"
    before_router_strategy = _normalize_router_strategy(before_router_strategy, fallback="priority")
    before_router_template = str(config.get("models.router.strategy_template", "")).strip()
    before_router_budget = config.get("models.router.budget", {})
    if not isinstance(before_router_budget, dict):
        before_router_budget = {}
    before_router_outlier = config.get("models.router.outlier_ejection", {})
    if not isinstance(before_router_outlier, dict):
        before_router_outlier = {}

    after_prompt = _apply_trainer_prompt_patch(
        before_prompt,
        rules=prompt_rules,
        job_id=str(job.get("job_id", "")),
    )
    after_deny = sorted(set(before_deny + deny_add))

    after_router_strategy = _normalize_router_strategy(
        (router_patch or {}).get("strategy", (router_patch or {}).get("models.router.strategy", before_router_strategy)),
        fallback=before_router_strategy,
    )
    after_router_template = str(
        (router_patch or {}).get(
            "strategy_template",
            (router_patch or {}).get("models.router.strategy_template", before_router_template),
        )
        or before_router_template
    ).strip()
    after_router_budget = (router_patch or {}).get("budget", (router_patch or {}).get("models.router.budget", before_router_budget))
    if not isinstance(after_router_budget, dict):
        after_router_budget = dict(before_router_budget)
    after_router_outlier = (router_patch or {}).get(
        "outlier_ejection",
        (router_patch or {}).get("models.router.outlier_ejection", before_router_outlier),
    )
    if not isinstance(after_router_outlier, dict):
        after_router_outlier = dict(before_router_outlier)

    before = {
        "personality.system_prompt": before_prompt,
        "security.tool_denylist": before_deny,
        "models.router.strategy": before_router_strategy,
        "models.router.strategy_template": before_router_template,
        "models.router.budget": dict(before_router_budget),
        "models.router.outlier_ejection": dict(before_router_outlier),
    }
    after = {
        "personality.system_prompt": after_prompt,
        "security.tool_denylist": after_deny,
        "models.router.strategy": after_router_strategy,
        "models.router.strategy_template": after_router_template,
        "models.router.budget": dict(after_router_budget),
        "models.router.outlier_ejection": dict(after_router_outlier),
    }
    strategy_package = {
        "version": "training_strategy_package_v1",
        "job_id": str(job.get("job_id", "")),
        "components": {
            "prompt": {
                "kind": "prompt",
                "patch": dict(prompt_patch) if isinstance(prompt_patch, dict) else {},
                "before": before_prompt,
                "after": after_prompt,
                "changed": before_prompt != after_prompt,
            },
            "policy": {
                "kind": "policy",
                "patch": dict(policy_patch) if isinstance(policy_patch, dict) else {},
                "before": {
                    "security.tool_denylist": before_deny,
                },
                "after": {
                    "security.tool_denylist": after_deny,
                },
                "changed": before_deny != after_deny,
            },
            "router": {
                "kind": "router",
                "patch": dict(router_patch) if isinstance(router_patch, dict) else {},
                "before": {
                    "models.router.strategy": before_router_strategy,
                    "models.router.strategy_template": before_router_template,
                    "models.router.budget": dict(before_router_budget),
                    "models.router.outlier_ejection": dict(before_router_outlier),
                },
                "after": {
                    "models.router.strategy": after_router_strategy,
                    "models.router.strategy_template": after_router_template,
                    "models.router.budget": dict(after_router_budget),
                    "models.router.outlier_ejection": dict(after_router_outlier),
                },
                "changed": (
                    before_router_strategy != after_router_strategy
                    or before_router_template != after_router_template
                    or dict(before_router_budget) != dict(after_router_budget)
                    or dict(before_router_outlier) != dict(after_router_outlier)
                ),
            },
        },
        "rollback_snapshot": dict(before),
        "apply_snapshot": dict(after),
    }
    return {
        "before": before,
        "after": after,
        "strategy_package": strategy_package,
        "summary": {
            "prompt_rules_added": len(prompt_rules),
            "denylist_added": sorted(set(after_deny) - set(before_deny)),

            "router_strategy_changed": before_router_strategy != after_router_strategy,
            "router_strategy": after_router_strategy,
        },
    }

def _score_training_job(job: Dict[str, Any]) -> Dict[str, Any]:
    output = (job.get("output") or {}) if isinstance(job, dict) else {}
    summary = output.get("training_summary") if isinstance(output, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    fail_count = int(summary.get("fail_count", 0) or 0)
    trajectory_count = int(summary.get("trajectory_count", 0) or 0)
    eval_count = int(summary.get("eval_count", 0) or 0)
    rule_count = len(_unique_str_list(((output.get("prompt_patch") or {}).get("rules", []))))
    baseline = max(1, trajectory_count + eval_count)
    score = max(0.0, min(1.0, 1.0 - (fail_count / baseline)))
    return {
        "score": round(score, 4),
        "fail_count": fail_count,
        "rule_count": rule_count,
        "trajectory_count": trajectory_count,
        "eval_count": eval_count,
    }

def _classify_training_failure_label(error_code: str) -> str:
    code = str(error_code or "").strip().lower()
    if code in {"invalid_parameter", "invalid_arguments", "tool_invalid_params", "schema_validation_failed"}:
        return "tool_parameter_error"
    if code in {"tool_not_permitted", "forbidden", "unauthorized", "permission_denied", "tool_tier_blocked"}:
        return "permission_error"
    if code in {"timeout", "network_timeout", "service_unavailable", "dependency_error"}:
        return "environment_error"
    return "strategy_error"

def _build_training_release_explanation(
    *,
    release: Dict[str, Any],
    job: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    release_status = str(release.get("status", "")).strip().lower()
    rollout_mode = str((release.get("rollout", {}) or {}).get("mode", "direct")).strip().lower() or "direct"
    approval = release.get("approval", {}) if isinstance(release.get("approval"), dict) else {}
    rollback_note = str(release.get("rollback_note", "")).strip()
    rollback_actor = str(release.get("rollback_actor", "")).strip()

    job_payload = job if isinstance(job, dict) else {}
    output = job_payload.get("output", {}) if isinstance(job_payload.get("output"), dict) else {}
    summary = output.get("training_summary", {}) if isinstance(output.get("training_summary"), dict) else {}
    prompt_patch = output.get("prompt_patch", {}) if isinstance(output.get("prompt_patch"), dict) else {}
    policy_patch = output.get("policy_patch", {}) if isinstance(output.get("policy_patch"), dict) else {}
    router_patch = output.get("router_patch", {}) if isinstance(output.get("router_patch"), dict) else {}

    fail_count = int(summary.get("fail_count", 0) or 0)
    trajectory_count = int(summary.get("trajectory_count", 0) or 0)
    eval_count = int(summary.get("eval_count", 0) or 0)
    rule_count = len(_unique_str_list(prompt_patch.get("rules", [])))
    deny_add = _unique_str_list(policy_patch.get("security.tool_denylist.add", []))
    router_strategy = str(router_patch.get("strategy", "")).strip().lower() or None

    reasons_effective: List[str] = []
    reasons_failed: List[str] = []
    decision_trace: List[str] = []

    if release_status == "pending_approval":
        outcome = "pending"
        reasons_failed.append("release pending manual approval; strategy package not applied yet")
        decision_trace.append("state:pending_approval")
    elif release_status == "rolled_back":
        outcome = "failed"
        reasons_failed.append("release rolled back after canary or gate checks")
        decision_trace.append("state:rolled_back")
    else:
        outcome = "effective"
        reasons_effective.append("release applied to runtime config")
        decision_trace.append(f"state:{release_status or 'published'}")

    if rollout_mode == "canary":
        decision_trace.append("rollout:canary")
        reasons_effective.append("canary rollout limits blast radius before full promotion")
    else:
        decision_trace.append("rollout:direct")

    if bool(approval.get("required", False)):
        if bool(approval.get("approved", False)):
            reasons_effective.append(
                f"manual approval passed by {str(approval.get('approved_by', 'admin')).strip() or 'admin'}"
            )
            decision_trace.append("approval:approved")
        else:
            reasons_failed.append("manual approval required but not approved")
            decision_trace.append("approval:pending")
    else:
        decision_trace.append("approval:not_required")

    if rollback_note:
        reasons_failed.append(f"rollback note: {rollback_note}")
        decision_trace.append(f"rollback_note:{rollback_note}")
    if rollback_actor:
        decision_trace.append(f"rollback_actor:{rollback_actor}")

    if fail_count > 0:
        reasons_failed.append(
            f"trainer summary shows failures ({fail_count}/{max(1, trajectory_count + eval_count)})"
        )
    else:
        reasons_effective.append("trainer summary shows no hard failures in sampled data")

    if rule_count > 0:
        reasons_effective.append(f"prompt patch added {rule_count} rule(s)")
    if deny_add:
        reasons_effective.append(f"policy patch extended denylist by {len(deny_add)} item(s)")
    if router_strategy:
        reasons_effective.append(f"router strategy proposed: {router_strategy}")

    tool_error_code_count = (
        summary.get("tool_error_code_count", {})
        if isinstance(summary.get("tool_error_code_count"), dict)
        else {}
    )
    label_counts: Dict[str, int] = {
        "tool_parameter_error": 0,
        "permission_error": 0,
        "environment_error": 0,
        "strategy_error": 0,
    }
    top_errors = sorted(
        ((str(code), int(count)) for code, count in tool_error_code_count.items()),
        key=lambda item: (-item[1], item[0]),
    )[:10]
    for code, count in top_errors:
        label = _classify_training_failure_label(code)
        label_counts[label] = label_counts.get(label, 0) + int(count)
    if top_errors:
        reasons_failed.append("top tool error codes observed in trainer summary")
        decision_trace.append("trainer:tool_error_code_count_present")

    return {
        "release_id": str(release.get("release_id", "")),
        "job_id": str(release.get("job_id", "")),
        "outcome": outcome,
        "release": {
            "status": release_status,
            "rollout_mode": rollout_mode,
            "actor": str(release.get("actor", "")),
            "created_at": release.get("created_at"),
        },
        "training_summary": {
            "trajectory_count": trajectory_count,
            "eval_count": eval_count,
            "fail_count": fail_count,
            "rule_count": rule_count,
            "denylist_add_count": len(deny_add),
            "router_strategy": router_strategy,
            "top_tool_error_codes": [{"error_code": code, "count": count} for code, count in top_errors],
        },
        "failure_attribution": {
            "by_label": label_counts,
        },
        "why_effective": reasons_effective[:12],
        "why_failed": reasons_failed[:12],
        "decision_trace": decision_trace[:20],
    }

def _resolve_training_publish_rollout(rollout_payload: Dict[str, Any]) -> Dict[str, Any]:
    rollout = dict(rollout_payload) if isinstance(rollout_payload, dict) else {}
    mode = str(rollout.get("mode", "")).strip().lower()
    if not mode:
        mode = "canary" if bool(config.get("trainer.canary.auto_rollout_on_publish", False)) else "direct"
    if mode not in {"direct", "canary"}:
        mode = "direct"
    rollout["mode"] = mode
    try:
        default_percent = int(config.get("trainer.canary.default_percent", 10) or 10)
    except (TypeError, ValueError):
        default_percent = 10
    default_percent = max(1, min(100, default_percent))
    percent_raw = rollout.get("percent", default_percent if mode == "canary" else 100)
    try:
        percent = int(percent_raw)
    except (TypeError, ValueError):
        percent = default_percent if mode == "canary" else 100
    rollout["percent"] = max(1, min(100, percent))
    return rollout

def _resolve_training_release_approval(
    *,
    actor: str,
    dry_run: bool,
    rollout_mode: str,
    approval_payload: Dict[str, Any],
) -> Dict[str, Any]:
    approval_cfg = config.get("trainer.release_approval", {}) or {}
    if not isinstance(approval_cfg, dict):
        approval_cfg = {}
    required_modes_raw = approval_cfg.get("required_modes", ["canary"])
    required_modes = (
        [str(item).strip().lower() for item in required_modes_raw if str(item).strip()]
        if isinstance(required_modes_raw, list)
        else ["canary"]
    )
    if not required_modes:
        required_modes = ["canary"]
    approval_input = approval_payload if isinstance(approval_payload, dict) else {}
    required_override = "required" in approval_input
    required = bool(approval_input.get("required", False)) if required_override else (
        bool(approval_cfg.get("enabled", False)) and rollout_mode in set(required_modes)
    )
    if dry_run:
        required = False
    approved = bool(approval_input.get("approved", False))
    approved_by = str(approval_input.get("approved_by", "")).strip()
    note = str(approval_input.get("note", "")).strip()
    if approved and not approved_by:
        approved_by = actor
    if required and approved and bool(approval_cfg.get("require_note", False)) and not note:
        raise HTTPException(status_code=400, detail="Approval note is required by trainer.release_approval.require_note")
    if required and not approved:
        state = "pending"
    elif required and approved:
        state = "approved"
    else:
        state = "not_required"
    return {
        "required": required,
        "state": state,
        "approved": approved if required else False,
        "approved_by": approved_by if required else "",
        "approved_at": time.time() if required and approved else None,
        "note": note,
    }

def _evaluate_training_release_canary_guard(
    *,
    rollout_mode: str,
    canary_health: Dict[str, Any],
) -> Dict[str, Any]:
    release_gate_snapshot: Dict[str, Any] = {}
    release_gate_health: Dict[str, Any] = {}
    if rollout_mode == "canary":
        gate = _get_eval_benchmark_manager().get_release_gate_status()
        release_gate_snapshot = dict(gate) if isinstance(gate, dict) else {}
        release_gate_health = _assess_release_gate_workflow_health(
            gate_status=release_gate_snapshot,
            workflow_metrics=_build_workflow_observability_metrics(limit=200),
            persona_metrics=_latest_persona_consistency_signal(),
            coding_metrics=_build_coding_quality_metrics(window=100),
        )
    should_rollback_on_gate = bool(
        release_gate_snapshot.get("blocked", False)
        or release_gate_health.get("recommend_block_high_risk", False)
    )
    should_rollback_on_canary = bool(canary_health) and not bool(canary_health.get("passed", True))
    return {
        "release_gate": release_gate_snapshot,
        "release_gate_health": release_gate_health,
        "should_rollback_on_gate": should_rollback_on_gate,
        "should_rollback_on_canary": should_rollback_on_canary,
    }

def _audit_mcp_response(status: str, code: Optional[int] = None, message: Optional[str] = None) -> None:
    ctx = _mcp_request_ctx.get(None)
    if not ctx:
        return
    entry = dict(ctx)
    entry["status"] = status
    started = entry.pop("started_at", time.perf_counter())
    entry["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if code is not None:
        entry["error_code"] = code
    if message is not None:
        entry["error_message"] = message
    _mcp_audit_buffer.append(entry)

def _mcp_response_ok(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    _audit_mcp_response(status="ok")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}

def _mcp_response_error(request_id: Any, code: int, message: str, data: Optional[Any] = None) -> Dict[str, Any]:
    _audit_mcp_response(status="error", code=code, message=message)
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload

def _mcp_text_resource(uri: str, name: str, data: Any) -> Dict[str, Any]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    return {
        "uri": uri,
        "name": name,
        "mimeType": "application/json",
        "text": text,
    }

def _summarize_training_output(output: Any) -> Dict[str, Any]:
    payload = output if isinstance(output, dict) else {}
    prompt_patch = payload.get("prompt_patch") if isinstance(payload.get("prompt_patch"), dict) else {}
    policy_patch = payload.get("policy_patch") if isinstance(payload.get("policy_patch"), dict) else {}
    router_patch = payload.get("router_patch") if isinstance(payload.get("router_patch"), dict) else {}
    rules = prompt_patch.get("rules") if isinstance(prompt_patch.get("rules"), list) else []
    denylist_add = policy_patch.get("security.tool_denylist.add")
    denylist = denylist_add if isinstance(denylist_add, list) else []
    return {
        "has_prompt_patch": bool(prompt_patch),
        "prompt_rule_count": len(rules),
        "has_policy_patch": bool(policy_patch),
        "denylist_add_count": len(denylist),

        "has_router_patch": bool(router_patch),
        "router_strategy": str(router_patch.get("strategy", "")).strip() or None,
    }

def _resolve_online_policy_gate_thresholds(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config.get("trainer.online_policy_loop.gate", {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    patch = payload if isinstance(payload, dict) else {}

    def _get_bool(key: str, default: bool) -> bool:
        if key not in patch:
            return bool(cfg.get(key, default))
        return bool(patch.get(key))

    def _get_float(key: str, default: float) -> float:
        raw = patch.get(key, cfg.get(key, default))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    return {
        "require_release_gate_open": _get_bool("require_release_gate_open", True),
        "min_eval_pass_rate": _get_float("min_eval_pass_rate", 0.55),
        "min_trajectory_success_rate": _get_float("min_trajectory_success_rate", 0.6),
        "max_terminal_error_rate": _get_float("max_terminal_error_rate", 0.4),
    }

def _resolve_online_policy_offpolicy_config(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    loop_cfg = config.get("trainer.online_policy_loop", {}) or {}
    if not isinstance(loop_cfg, dict):
        loop_cfg = {}
    cfg = loop_cfg.get("offpolicy", {})
    if not isinstance(cfg, dict):
        cfg = {}
    patch = payload if isinstance(payload, dict) else {}

    def _get_bool(key: str, default: bool) -> bool:
        if key not in patch:
            return bool(cfg.get(key, default))
        return bool(patch.get(key))

    def _get_int(key: str, default: int, minimum: int = 1) -> int:
        raw = patch.get(key, cfg.get(key, default))
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = int(default)
        return max(minimum, parsed)

    def _get_float(key: str, default: float) -> float:
        raw = patch.get(key, cfg.get(key, default))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    return {
        "enabled": _get_bool("enabled", True),
        "auto_run_on_create": _get_bool("auto_run_on_create", True),
        "baseline_index": _get_int("baseline_index", 1, minimum=1),
        "bootstrap_rounds": _get_int("bootstrap_rounds", 300, minimum=20),
        "min_reward_threshold": max(0.0, min(1.0, _get_float("min_reward_threshold", 0.6))),
        "min_samples_for_confidence": _get_int("min_samples_for_confidence", 20, minimum=1),
    }

