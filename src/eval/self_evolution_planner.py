from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from eval.self_evolution_world_model import CompressedState, MinimalWorldModel, TransitionPrediction





@dataclass
class ToolPolicyView:
    deny_tools: set[str]
    allow_owner_only: bool
    tool_owner_flags: Dict[str, bool]

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ToolPolicyView":
        raw_deny = payload.get("deny_tools", [])
        deny_tools = {str(item).strip() for item in raw_deny if str(item).strip()} if isinstance(raw_deny, list) else set()
        allow_owner_only = bool(payload.get("allow_owner_only", False))
        tool_owner_flags_raw = payload.get("tool_owner_flags", {})
        tool_owner_flags: Dict[str, bool] = {}
        if isinstance(tool_owner_flags_raw, dict):
            for key, value in tool_owner_flags_raw.items():
                k = str(key).strip()
                if not k:
                    continue
                tool_owner_flags[k] = bool(value)
        return cls(deny_tools=deny_tools, allow_owner_only=allow_owner_only, tool_owner_flags=tool_owner_flags)


def _action_allowed(action: Dict[str, Any], policy: ToolPolicyView) -> Tuple[bool, str]:
    tool_name = str(action.get("tool", action.get("name", ""))).strip()
    if tool_name in policy.deny_tools:
        return False, "tool_denied"
    is_owner_only = bool(action.get("owner_only", policy.tool_owner_flags.get(tool_name, False)))
    if is_owner_only and not policy.allow_owner_only:
        return False, "owner_only_exceeded"
    return True, "allowed"


def _prediction_score(prediction: TransitionPrediction, action: Dict[str, Any]) -> float:
    reward_bias = float(action.get("reward_bias", 0.0) or 0.0)
    return (
        (prediction.success_prob * 1.4)
        + (prediction.next_state.progress * 1.2)
        - (prediction.next_state.risk * 0.9)
        - (prediction.next_state.budget_pressure * 0.6)
        - (prediction.expected_cost * 0.25)
        + reward_bias
    )


def _simulate_rollout(
    model: MinimalWorldModel,
    start_state: CompressedState,
    actions: List[Dict[str, Any]],
    *,
    horizon: int,
    first_action: Dict[str, Any],
    rollout_index: int,
) -> float:
    if not actions:
        return 0.0
    state = start_state
    total = 0.0
    current = first_action
    for depth in range(max(1, horizon)):
        prediction = model.predict_transition(state, current)
        total += _prediction_score(prediction, current)
        state = prediction.next_state
        if state.progress >= 0.999 or state.remaining_steps <= 0:
            break
        pick = (rollout_index + depth) % len(actions)
        current = actions[pick]
    return total


def plan_light_action(
    model: MinimalWorldModel,
    state: CompressedState,
    candidate_actions: List[Dict[str, Any]],
    policy: ToolPolicyView,
    *,
    beam_width: int = 3,
    horizon: int = 2,
    mcts_rollouts: int = 6,
) -> Dict[str, Any]:
    allowed_actions: List[Dict[str, Any]] = []
    blocked_actions: List[Dict[str, Any]] = []
    for action in candidate_actions:
        if not isinstance(action, dict):
            continue
        allowed, reason = _action_allowed(action, policy)
        if allowed:
            allowed_actions.append(action)
        else:
            blocked_actions.append(
                {
                    "name": str(action.get("name", "")).strip(),
                    "tool": str(action.get("tool", action.get("name", ""))).strip(),
                    "reason": reason,
                }
            )

    if not allowed_actions:
        return {
            "selected_action": "",
            "ranked_actions": [],
            "blocked_actions": blocked_actions,
            "beam_width": max(1, int(beam_width)),
            "horizon": max(1, int(horizon)),
            "mcts_rollouts": max(1, int(mcts_rollouts)),
        }

    beam: List[Dict[str, Any]] = [{"state": state, "score": 0.0, "path": []}]
    for _ in range(max(1, int(horizon))):
        expanded: List[Dict[str, Any]] = []
        for branch in beam:
            branch_state = branch["state"]
            branch_score = float(branch["score"])
            branch_path = list(branch["path"])
            for action in allowed_actions:
                prediction = model.predict_transition(branch_state, action)
                expanded.append(
                    {
                        "state": prediction.next_state,
                        "score": branch_score + _prediction_score(prediction, action),
                        "path": branch_path + [str(action.get("name", "")).strip()],
                    }
                )
        expanded.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        beam = expanded[: max(1, int(beam_width))]

    first_action_scores: Dict[str, float] = {}
    for branch in beam:
        path = list(branch.get("path", []))
        if not path:
            continue
        first = str(path[0]).strip()
        score = float(branch.get("score", 0.0))
        first_action_scores[first] = max(first_action_scores.get(first, -10_000.0), score)

    ranked: List[Dict[str, Any]] = []
    for action in allowed_actions:
        action_name = str(action.get("name", "")).strip()
        beam_score = float(first_action_scores.get(action_name, -10_000.0))
        rollout_values = [
            _simulate_rollout(
                model,
                state,
                allowed_actions,
                horizon=max(1, int(horizon)),
                first_action=action,
                rollout_index=idx,
            )
            for idx in range(max(1, int(mcts_rollouts)))
        ]
        mcts_score = round(sum(rollout_values) / len(rollout_values), 4)
        combined = round((beam_score * 0.65) + (mcts_score * 0.35), 4)
        ranked.append(
            {
                "name": action_name,
                "tool": str(action.get("tool", action_name)).strip(),
                "beam_score": round(beam_score, 4),
                "mcts_score": mcts_score,
                "score": combined,
            }
        )

    ranked.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    selected = str(ranked[0].get("name", "")).strip() if ranked else ""
    return {
        "selected_action": selected,
        "ranked_actions": ranked,
        "blocked_actions": blocked_actions,
        "beam_width": max(1, int(beam_width)),
        "horizon": max(1, int(horizon)),
        "mcts_rollouts": max(1, int(mcts_rollouts)),
    }

