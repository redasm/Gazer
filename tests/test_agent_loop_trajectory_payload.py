from types import SimpleNamespace

from agent.loop import AgentLoop


def test_extract_error_code_from_standardized_error():
    code = AgentLoop._extract_error_code("Error [DEVICE_ACTION_UNSUPPORTED]: nope")
    assert code == "DEVICE_ACTION_UNSUPPORTED"


def test_build_tool_call_payload_contains_preview_and_hash():
    tc = SimpleNamespace(id="tc1", name="node_invoke", arguments={"action": "screen.observe"})
    payload = AgentLoop._build_tool_call_payload(tc)
    assert payload["tool"] == "node_invoke"
    assert payload["tool_call_id"] == "tc1"
    assert "screen.observe" in payload["args_preview"]
    assert len(payload["args_hash"]) == 16


def test_build_tool_result_payload_includes_error_code_and_media():
    tc = SimpleNamespace(id="tc2", name="node_invoke", arguments={})
    result = "Error [WEB_FETCH_FAILED]: boom __MEDIA__:C:/tmp/x.png"
    payload = AgentLoop._build_tool_result_payload(tc, result)
    assert payload["status"] == "error"
    assert payload["error_code"] == "WEB_FETCH_FAILED"
    assert payload["has_media"] is True
    assert payload["media_paths"] == ["C:/tmp/x.png"]


def test_build_replan_hint_for_known_error_code():
    hint = AgentLoop._build_replan_hint(
        tool_name="web_fetch",
        tool_result="Error [WEB_FETCH_FAILED]: failed to fetch",
    )
    assert "web_fetch" in hint
    assert "WEB_FETCH_FAILED" in hint
    assert "Validate URL" in hint
