from __future__ import annotations

from collections import Counter
import time
from typing import Any, Dict, List, Tuple

from eval.self_evolution_planner import ToolPolicyView, plan_light_action
from eval.self_evolution_world_model import CompressedState, MinimalWorldModel



def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_policy(episode: Dict[str, Any]) -> ToolPolicyView:
    raw = episode.get("tool_policy")
    payload = raw if isinstance(raw, dict) else {}
    return ToolPolicyView.from_payload(payload)


def _allowed_actions(actions: List[Dict[str, Any]], policy: ToolPolicyView) -> List[Dict[str, Any]]:
    allowed: List[Dict[str, Any]] = []
    allow_owner_only = policy.allow_owner_only
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool_name = str(action.get("tool", action.get("name", ""))).strip()
        if tool_name in policy.deny_tools:
            continue
        is_owner_only = bool(action.get("owner_only", policy.tool_owner_flags.get(tool_name, False)))
        if is_owner_only and not allow_owner_only:
            continue
        allowed.append(action)
    return allowed


def _pick_baseline_action(actions: List[Dict[str, Any]], policy: ToolPolicyView) -> Dict[str, Any]:
    allowed = _allowed_actions(actions, policy)
    return allowed[0] if allowed else {}


def _simulate_action_step(
    state: CompressedState,
    action: Dict[str, Any],
    *,
    preferred_action: str,
) -> Tuple[CompressedState, bool, float, str]:
    base_success = _to_float(action.get("base_success"), 0.6)
    cost = max(0.0, _to_float(action.get("cost"), 1.0))
    delta_progress = _to_float(action.get("delta_progress"), 0.2)
    delta_risk = _to_float(action.get("delta_risk"), 0.05)
    is_preferred = str(action.get("name", "")).strip() == preferred_action
    success_prob = _clamp(
        base_success - (state.risk * 0.25) - (state.budget_pressure * 0.2) + (0.07 if is_preferred else 0.0),
        0.05,
        0.98,
    )
    success = success_prob >= 0.57
    if success:
        next_progress = _clamp(state.progress + max(0.0, delta_progress), 0.0, 1.0)
        next_risk = _clamp(state.risk + min(0.0, delta_risk) + 0.02, 0.0, 1.0)
        failure_type = ""
    else:
        next_progress = _clamp(state.progress + (max(0.0, delta_progress) * 0.25), 0.0, 1.0)
        next_risk = _clamp(state.risk + max(0.08, abs(delta_risk) * 0.8), 0.0, 1.0)
        failure_type = str(action.get("failure_type", "stalled")).strip() or "stalled"
    next_budget = _clamp(state.budget_pressure + (cost * 0.08), 0.0, 1.0)
    next_failure_bias = _clamp((state.failure_bias * 0.6) + ((0.0 if success else 1.0) * 0.4), 0.0, 1.0)
    next_state = CompressedState(
        progress=next_progress,
        risk=next_risk,
        remaining_steps=max(0, int(state.remaining_steps) - 1),
        budget_pressure=next_budget,
        failure_bias=next_failure_bias,
    )
    return next_state, success, cost, failure_type


def _run_episode(
    model: MinimalWorldModel,
    episode: Dict[str, Any],
    *,
    strategy: str,
    beam_width: int = 3,
    horizon: int = 2,
) -> Dict[str, Any]:
    actions = [item for item in list(episode.get("candidate_actions") or []) if isinstance(item, dict)]
    policy = _resolve_policy(episode)
    preferred_action = str(episode.get("preferred_action", "")).strip()
    initial_payload = episode.get("initial_state") if isinstance(episode.get("initial_state"), dict) else {}
    max_steps = int(episode.get("max_steps", 3) or 3)
    if "remaining_steps" not in initial_payload:
        initial_payload = dict(initial_payload)
        initial_payload["remaining_steps"] = max_steps
    state = model.compress_state(initial_payload)

    step_count = 0
    total_cost = 0.0
    last_failure_type = ""
    chosen_actions: List[str] = []
    while step_count < max_steps and state.remaining_steps > 0 and state.progress < 1.0:
        if strategy == "light_planning":
            plan = plan_light_action(
                model,
                state,
                actions,
                policy,
                beam_width=max(1, int(beam_width)),
                horizon=max(1, int(horizon)),
                mcts_rollouts=6,
            )
            selected_name = str(plan.get("selected_action", "")).strip()
            action = next((item for item in actions if str(item.get("name", "")).strip() == selected_name), {})
            if not action:
                action = _pick_baseline_action(actions, policy)
        else:
            action = _pick_baseline_action(actions, policy)
        if not action:
            last_failure_type = "policy_blocked"
            break
        chosen_actions.append(str(action.get("name", "")).strip())
        state, success, cost, failure_type = _simulate_action_step(state, action, preferred_action=preferred_action)
        step_count += 1
        total_cost += cost
        if success and state.progress >= 0.999:
            last_failure_type = ""
            break
        if not success and failure_type:
            last_failure_type = failure_type

    episode_success = state.progress >= 0.999
    if not episode_success and not last_failure_type:
        last_failure_type = "stalled"
    return {
        "episode_id": str(episode.get("episode_id", "")).strip(),
        "strategy": strategy,
        "success": episode_success,
        "steps": step_count,
        "cost": round(total_cost, 4),
        "failure_type": "" if episode_success else last_failure_type,
        "actions": chosen_actions,
    }


def _aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    success = sum(1 for item in results if bool(item.get("success", False)))
    avg_cost = (sum(float(item.get("cost", 0.0) or 0.0) for item in results) / total) if total else 0.0
    avg_steps = (sum(int(item.get("steps", 0) or 0) for item in results) / total) if total else 0.0
    failures = Counter(
        str(item.get("failure_type", "")).strip()
        for item in results
        if not bool(item.get("success", False)) and str(item.get("failure_type", "")).strip()
    )
    return {
        "episodes": total,
        "success_count": success,
        "success_rate": round((success / total), 4) if total else 0.0,
        "avg_cost": round(avg_cost, 4),
        "avg_steps": round(avg_steps, 4),
        "failure_types": dict(sorted(failures.items(), key=lambda kv: kv[0])),
    }


def compare_planning_strategies(
    replays: List[Dict[str, Any]],
    *,
    beam_width: int = 3,
    horizon: int = 2,
) -> Dict[str, Any]:
    model = MinimalWorldModel()
    model.fit(replays)

    baseline_results = [
        _run_episode(model, replay, strategy="no_planning", beam_width=beam_width, horizon=horizon)
        for replay in replays
    ]
    planned_results = [
        _run_episode(model, replay, strategy="light_planning", beam_width=beam_width, horizon=horizon)
        for replay in replays
    ]

    baseline = _aggregate(baseline_results)
    planned = _aggregate(planned_results)
    failure_keys = set(baseline["failure_types"]) | set(planned["failure_types"])
    failure_shift = {
        key: int(planned["failure_types"].get(key, 0)) - int(baseline["failure_types"].get(key, 0))
        for key in sorted(failure_keys)
    }
    return {
        "generated_at": time.time(),
        "dataset_size": len(replays),
        "baseline": baseline,
        "light_planning": planned,
        "delta": {
            "success_rate": round(planned["success_rate"] - baseline["success_rate"], 4),
            "avg_cost": round(planned["avg_cost"] - baseline["avg_cost"], 4),
            "avg_steps": round(planned["avg_steps"] - baseline["avg_steps"], 4),
            "failure_type_shift": failure_shift,
        },
        "episodes": [
            {
                "episode_id": b.get("episode_id", ""),
                "baseline": b,
                "light_planning": p,
            }
            for b, p in zip(baseline_results, planned_results)
        ],
    }


def build_default_replays() -> List[Dict[str, Any]]:
    return [
        {
            "episode_id": "offline_case_1",
            "max_steps": 3,
            "initial_state": {"progress": 0.0, "risk": 0.28, "budget_pressure": 0.1, "failure_bias": 0.2},
            "tool_policy": {"deny_tools": ["shell_exec"]},
            "preferred_action": "gather_context",
            "candidate_actions": [
                {"name": "shell_exec", "tool": "shell_exec", "owner_only": True, "base_success": 0.2, "delta_progress": 0.8, "delta_risk": 0.35, "cost": 2.8, "failure_type": "policy_blocked"},
                {"name": "gather_context", "tool": "memory_lookup", "owner_only": False, "base_success": 0.78, "delta_progress": 0.45, "delta_risk": -0.08, "cost": 0.8, "failure_type": "missing_context"},
                {"name": "direct_tool_call", "tool": "web_fetch", "owner_only": False, "base_success": 0.48, "delta_progress": 0.35, "delta_risk": 0.06, "cost": 1.2, "failure_type": "tool_timeout"},
            ],
        },
        {
            "episode_id": "offline_case_2",
            "max_steps": 3,
            "initial_state": {"progress": 0.0, "risk": 0.22, "budget_pressure": 0.05, "failure_bias": 0.15},
            "tool_policy": {"deny_tools": [], "allow_owner_only": False},
            "preferred_action": "small_batch_plan",
            "candidate_actions": [
                {"name": "direct_tool_call", "tool": "toolchain", "owner_only": False, "base_success": 0.46, "delta_progress": 0.36, "delta_risk": 0.07, "cost": 1.3, "failure_type": "tool_timeout"},
                {"name": "small_batch_plan", "tool": "llm_reason", "owner_only": False, "base_success": 0.75, "delta_progress": 0.43, "delta_risk": -0.04, "cost": 0.95, "failure_type": "reasoning_gap"},
                {"name": "retry_last", "tool": "toolchain", "owner_only": False, "base_success": 0.52, "delta_progress": 0.3, "delta_risk": 0.02, "cost": 1.0, "failure_type": "repeated_error"},
            ],
        },
        {
            "episode_id": "offline_case_3",
            "max_steps": 4,
            "initial_state": {"progress": 0.0, "risk": 0.32, "budget_pressure": 0.16, "failure_bias": 0.3},
            "tool_policy": {"deny_tools": ["bulk_write"], "allow_owner_only": False},
            "preferred_action": "policy_check",
            "candidate_actions": [
                {"name": "bulk_write", "tool": "bulk_write", "owner_only": True, "base_success": 0.25, "delta_progress": 0.7, "delta_risk": 0.28, "cost": 2.4, "failure_type": "policy_blocked"},
                {"name": "policy_check", "tool": "policy_guard", "owner_only": False, "base_success": 0.8, "delta_progress": 0.4, "delta_risk": -0.1, "cost": 0.7, "failure_type": "policy_mismatch"},
                {"name": "targeted_write", "tool": "single_write", "owner_only": False, "base_success": 0.58, "delta_progress": 0.34, "delta_risk": 0.04, "cost": 1.2, "failure_type": "write_conflict"},
            ],
        },
        {
            "episode_id": "offline_case_4",
            "max_steps": 3,
            "initial_state": {"progress": 0.0, "risk": 0.2, "budget_pressure": 0.08, "failure_bias": 0.1},
            "tool_policy": {"deny_tools": [], "allow_owner_only": False},
            "preferred_action": "retrieve_examples",
            "candidate_actions": [
                {"name": "direct_generate", "tool": "llm_generate", "owner_only": False, "base_success": 0.49, "delta_progress": 0.31, "delta_risk": 0.08, "cost": 1.0, "failure_type": "hallucination"},
                {"name": "retrieve_examples", "tool": "retriever", "owner_only": False, "base_success": 0.77, "delta_progress": 0.44, "delta_risk": -0.06, "cost": 0.85, "failure_type": "retrieval_miss"},
                {"name": "direct_tool_call", "tool": "workflow_tool", "owner_only": False, "base_success": 0.53, "delta_progress": 0.33, "delta_risk": 0.04, "cost": 1.2, "failure_type": "tool_timeout"},
            ],
        },
        {
            "episode_id": "offline_case_5",
            "max_steps": 4,
            "initial_state": {"progress": 0.0, "risk": 0.24, "budget_pressure": 0.12, "failure_bias": 0.2},
            "tool_policy": {"deny_tools": [], "allow_owner_only": False},
            "preferred_action": "split_subtasks",
            "candidate_actions": [
                {"name": "direct_tool_call", "tool": "toolchain", "owner_only": False, "base_success": 0.45, "delta_progress": 0.28, "delta_risk": 0.09, "cost": 1.35, "failure_type": "tool_timeout"},
                {"name": "split_subtasks", "tool": "planner", "owner_only": False, "base_success": 0.76, "delta_progress": 0.38, "delta_risk": -0.05, "cost": 0.9, "failure_type": "planning_gap"},
                {"name": "cache_and_retry", "tool": "cache", "owner_only": False, "base_success": 0.63, "delta_progress": 0.3, "delta_risk": 0.01, "cost": 0.7, "failure_type": "cache_miss"},
            ],
        },
    ]
