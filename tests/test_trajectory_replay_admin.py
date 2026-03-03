from tools.admin import api_facade as admin_api


def test_trajectory_replay_admin_module_smoke():
    payload = {
        "run_id": "traj_smoke",
        "events": [
            {
                "ts": 1.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {"tool": "screen_observe", "tool_call_id": "tc_smoke"},
            }
        ],
        "final": {"status": "ok", "final_content": "done"},
    }
    steps = admin_api._normalize_trajectory_steps(payload)
    assert len(steps) == 1
    assert steps[0]["tool"] == "screen_observe"
