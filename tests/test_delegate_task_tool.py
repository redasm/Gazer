from __future__ import annotations

import asyncio

from agent.orchestrator import DelegateTaskTool


class _FakeOrchestrator:
    def __init__(self):
        self.calls = []

    def list_agents(self):
        return [
            {"id": "main", "name": "Main"},
            {"id": "coder", "name": "Coder"},
            {"id": "reviewer", "name": "Reviewer"},
        ]

    async def run_agent_turn(self, agent_id, message, *, session_key=None, lane=None):
        self.calls.append({"agent_id": agent_id, "message": message, "session_key": session_key})
        if agent_id == "coder":
            return "worker-output"
        if agent_id == "reviewer":
            return "review-output"
        return "main-output"


def test_delegate_task_execute_mode():
    orch = _FakeOrchestrator()
    tool = DelegateTaskTool(orch)

    out = asyncio.run(tool.execute(agent_id="coder", task="do it", mode="execute"))
    assert "[Sub-agent coder completed]" in out
    assert "worker-output" in out
    assert "[Reviewer" not in out
    assert len(orch.calls) == 1


def test_delegate_task_review_execute_mode():
    orch = _FakeOrchestrator()
    tool = DelegateTaskTool(orch)

    out = asyncio.run(
        tool.execute(
            agent_id="coder",
            task="do it",
            mode="review_execute",
            reviewer_agent_id="reviewer",
            review_instructions="focus on correctness",
            session_key="sess1",
        )
    )
    assert "[Sub-agent coder completed]" in out
    assert "[Reviewer reviewer]" in out
    assert "worker-output" in out
    assert "review-output" in out
    assert len(orch.calls) == 2
    assert orch.calls[0]["agent_id"] == "coder"
    assert orch.calls[1]["agent_id"] == "reviewer"
    assert orch.calls[1]["session_key"] == "sess1:review"


def test_delegate_task_unknown_agent_error_code():
    orch = _FakeOrchestrator()
    tool = DelegateTaskTool(orch)

    out = asyncio.run(tool.execute(agent_id="unknown", task="do it"))
    assert "DELEGATE_AGENT_UNKNOWN" in out

