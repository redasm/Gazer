from __future__ import annotations

from types import SimpleNamespace

import asyncio

from tools.registry import ToolRegistry
from agent.loop import AgentLoop


def test_tool_registry_errors_include_trace_id_and_hint() -> None:
    registry = ToolRegistry()
    result = asyncio.run(registry.execute("missing_tool_name", {}))
    assert result.startswith("Error [TOOL_NOT_FOUND]:")
    assert "(trace_id=" in result
    assert "\nHint:" in result


def test_tool_result_payload_extracts_trace_and_hint() -> None:
    tc = SimpleNamespace(name="x", id="id1", arguments={"a": 1})
    result = "Error [TOOL_TIMEOUT]: timed out (trace_id=trc_test)\nHint: do x"
    payload = AgentLoop._build_tool_result_payload(tc, result)
    assert payload["status"] == "error"
    assert payload["error_code"] == "TOOL_TIMEOUT"
    assert payload["trace_id"] == "trc_test"
    assert payload["error_hint"] == "do x"

