from __future__ import annotations

from cli.trajectory_cmd import _normalize_steps


def test_normalize_steps_builds_tool_step_with_result() -> None:
    payload = {
        "events": [
            {
                "stage": "act",
                "action": "tool_call",
                "payload": {
                    "tool": "read_file",
                    "tool_call_id": "tc1",
                    "args_preview": "{\"path\":\"a.txt\"}",
                    "args_hash": "h1",
                },
            },
            {
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "read_file",
                    "tool_call_id": "tc1",
                    "status": "error",
                    "error_code": "TOOL_EXECUTION_FAILED",
                    "trace_id": "trc_x",
                    "error_hint": "check",
                    "result_preview": "Error [TOOL_EXECUTION_FAILED]: boom",
                },
            },
        ]
    }
    steps = _normalize_steps(payload)
    assert len(steps) == 1
    step = steps[0]
    assert step["kind"] == "tool"
    assert step["tool"] == "read_file"
    assert step["tool_call_id"] == "tc1"
    assert step["status"] == "error"
    assert step["error_code"] == "TOOL_EXECUTION_FAILED"
    assert step["trace_id"] == "trc_x"
    assert step["error_hint"] == "check"

