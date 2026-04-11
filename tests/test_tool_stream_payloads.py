from agent.loop_mixins.tool_result_utils import ToolResultUtilsMixin
from llm.base import ToolCallRequest


class _DummyToolPayloads(ToolResultUtilsMixin):
    pass


def test_tool_stream_payloads_include_label_and_summary():
    tool_call = ToolCallRequest(
        id="tc_exec_1",
        name="exec",
        arguments={"command": "npm install", "workdir": "web"},
    )

    call_payload = _DummyToolPayloads._build_tool_call_payload(tool_call)
    result_payload = _DummyToolPayloads._build_tool_result_payload(tool_call, "added 42 packages in 3s")
    progress_payload = _DummyToolPayloads._build_tool_progress_payload(
        tool_call,
        stage="stdout",
        message="[stdout] installing dependencies",
        sequence=2,
    )

    assert call_payload["tool"] == "exec"
    assert call_payload["label"] == 'exec: "npm install"'
    assert result_payload["tool"] == "exec"
    assert result_payload["label"] == 'exec: "npm install"'
    assert result_payload["status"] == "ok"
    assert result_payload["result_summary"] == "added 42 packages in 3s"
    assert progress_payload["status"] == "running"
    assert progress_payload["progress_stage"] == "stdout"
    assert progress_payload["progress_message"] == "[stdout] installing dependencies"
    assert _DummyToolPayloads._format_tool_stream_text(event_type="call", payload=call_payload) == 'exec: "npm install"'
    assert _DummyToolPayloads._format_tool_stream_text(event_type="progress", payload=progress_payload) == 'exec: "npm install" -> [stdout] installing dependencies'
    assert _DummyToolPayloads._format_tool_stream_text(event_type="result", payload=result_payload) == 'exec: "npm install" -> added 42 packages in 3s'
