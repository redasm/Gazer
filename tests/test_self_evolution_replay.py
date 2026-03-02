from eval.self_evolution_planner import ToolPolicyView, plan_light_action
from eval.self_evolution_replay import build_default_replays, compare_planning_strategies
from eval.self_evolution_world_model import MinimalWorldModel


def test_world_model_compress_and_predict_transition() -> None:
    model = MinimalWorldModel()
    episodes = build_default_replays()
    model.fit(episodes)
    state = model.compress_state({"progress": 0.1, "risk": 0.25, "remaining_steps": 3, "budget_pressure": 0.1})
    action = episodes[0]["candidate_actions"][1]
    prediction = model.predict_transition(state, action)

    assert 0.0 <= prediction.success_prob <= 1.0
    assert prediction.expected_cost > 0.0
    assert prediction.next_state.progress >= state.progress
    assert prediction.next_state.remaining_steps == 2


def test_light_planner_respects_tool_policy() -> None:
    episodes = build_default_replays()
    model = MinimalWorldModel()
    model.fit(episodes)
    target = episodes[0]
    state = model.compress_state(target["initial_state"])
    policy = ToolPolicyView.from_payload({"deny_tools": ["memory_lookup"], "max_tier": "standard"})

    result = plan_light_action(
        model,
        state,
        target["candidate_actions"],
        policy,
        beam_width=3,
        horizon=2,
        mcts_rollouts=4,
    )
    blocked = {item.get("tool", "") for item in result["blocked_actions"]}
    assert "memory_lookup" in blocked
    assert result["selected_action"] != "gather_context"


def test_offline_replay_comparison_has_expected_metrics_shape() -> None:
    report = compare_planning_strategies(build_default_replays(), beam_width=3, horizon=2)

    assert report["dataset_size"] == 5
    assert "success_rate" in report["baseline"]
    assert "success_rate" in report["light_planning"]
    assert "avg_cost" in report["delta"]
    assert isinstance(report["delta"]["failure_type_shift"], dict)
    assert len(report["episodes"]) == 5

