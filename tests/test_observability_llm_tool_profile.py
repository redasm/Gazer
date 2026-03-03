from tools.admin import api_facade as admin_api


class _FakeTrajectoryStore:
    def list_recent(self, limit=50, session_key=None):
        return [{"run_id": "r1"}, {"run_id": "r2"}]

    def get_trajectory(self, run_id):
        if run_id == "r1":
            return {
                "events": [
                    {"action": "llm_response", "payload": {"error": "timeout"}},
                    {"action": "tool_call", "payload": {"tool": "node_invoke"}},
                    {
                        "action": "tool_result",
                        "payload": {
                            "tool": "node_invoke",
                            "status": "error",
                            "error_code": "TOOL_NOT_PERMITTED",
                            "result_preview": "Error [TOOL_NOT_PERMITTED]",
                        },
                    },
                    {"action": "replan_hint", "payload": {"tool": "node_invoke"}},
                ]
            }
        return {
            "events": [
                {"action": "llm_response", "payload": {"error": ""}},
                {"action": "tool_call", "payload": {"tool": "read_file"}},
                {"action": "tool_result", "payload": {"tool": "read_file", "status": "ok"}},
            ]
        }


def test_build_llm_tool_failure_profile(monkeypatch):
    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", _FakeTrajectoryStore())
    profile = admin_api._build_llm_tool_failure_profile(limit=20)
    assert profile["llm"]["calls"] == 2
    assert profile["llm"]["failures"] == 1
    assert profile["tool"]["calls"] == 2
    assert profile["tool"]["failures"] == 1
    assert profile["tool"]["error_codes"]["tool_not_permitted"] == 1
    assert profile["replan_hints"] == 1

