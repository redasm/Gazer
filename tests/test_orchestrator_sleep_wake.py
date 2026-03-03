import asyncio
from pathlib import Path

import pytest

from agent.orchestrator import AgentConfig, AgentOrchestrator
from bus.command_queue import CommandQueue
from tools.admin import api_facade as admin_api


class _DummyProvider:
    pass


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _LoopStub:
    def __init__(self, handler):
        self._handler = handler

    async def _process_message(self, msg):
        return await self._handler(msg)


async def _wait_for_status(orch: AgentOrchestrator, task_id: str, status: str, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        payload = orch.get_task(task_id)
        if payload and payload.get("status") == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for task={task_id} status={status}")


@pytest.mark.asyncio
async def test_orchestrator_delayed_wake(monkeypatch, tmp_path: Path):
    queue = CommandQueue()
    orch = AgentOrchestrator(
        command_queue=queue,
        provider=_DummyProvider(),
        max_parallel_tasks=1,
        max_parallel_per_agent=1,
        sleep_poll_interval_seconds=0.02,
    )
    orch.register_agent(AgentConfig(id="a", name="A", workspace=tmp_path))

    async def _handler(msg):
        await asyncio.sleep(0.01)
        return _Resp(f"ok:{msg.content}")

    monkeypatch.setattr(orch, "_get_or_create_loop", lambda _agent_id: _LoopStub(_handler))

    try:
        task_id = await orch.submit_agent_turn("a", "timer-task", sleep_for_seconds=0.08)
        await _wait_for_status(orch, task_id, "sleeping", timeout=0.5)
        result = await orch.wait_task(task_id, timeout_seconds=1.2)
    finally:
        orch.stop()

    assert result == "ok:timer-task"
    task = orch.get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert float(task["started_at"]) - float(task["created_at"]) >= 0.06


@pytest.mark.asyncio
async def test_orchestrator_event_wake(monkeypatch, tmp_path: Path):
    queue = CommandQueue()
    orch = AgentOrchestrator(
        command_queue=queue,
        provider=_DummyProvider(),
        max_parallel_tasks=1,
        max_parallel_per_agent=1,
        sleep_poll_interval_seconds=0.02,
    )
    orch.register_agent(AgentConfig(id="a", name="A", workspace=tmp_path))

    async def _handler(msg):
        return _Resp(f"ok:{msg.content}")

    monkeypatch.setattr(orch, "_get_or_create_loop", lambda _agent_id: _LoopStub(_handler))

    try:
        task_id = await orch.submit_agent_turn("a", "event-task", wake_events=["resource:db-ready"])
        await _wait_for_status(orch, task_id, "sleeping", timeout=0.5)

        awakened = orch.emit_wake_event("resource:db-ready")
        assert awakened >= 1
        result = await orch.wait_task(task_id, timeout_seconds=1.2)
    finally:
        orch.stop()

    assert result == "ok:event-task"
    task = orch.get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert str(task["wake_reason"]).startswith("event:")


@pytest.mark.asyncio
async def test_admin_api_orchestrator_sleep_wake_endpoints(monkeypatch, tmp_path: Path):
    queue = CommandQueue()
    orch = AgentOrchestrator(
        command_queue=queue,
        provider=_DummyProvider(),
        max_parallel_tasks=1,
        max_parallel_per_agent=1,
        sleep_poll_interval_seconds=0.02,
    )
    orch.register_agent(AgentConfig(id="a", name="A", workspace=tmp_path))

    async def _handler(msg):
        return _Resp(f"ok:{msg.content}")

    monkeypatch.setattr(orch, "_get_or_create_loop", lambda _agent_id: _LoopStub(_handler))
    original = admin_api.ORCHESTRATOR
    admin_api.ORCHESTRATOR = orch

    try:
        task_id = await orch.submit_agent_turn("a", "api-task", wake_events=["channel:discord"])
        await _wait_for_status(orch, task_id, "sleeping", timeout=0.5)

        status_payload = await admin_api.get_orchestrator_status()
        assert status_payload["status"] == "ok"
        assert int(status_payload["orchestrator"]["counts"]["sleeping"]) >= 1

        tasks_payload = await admin_api.list_orchestrator_tasks(limit=20)
        assert any(item.get("task_id") == task_id for item in tasks_payload["items"])

        task_payload = await admin_api.get_orchestrator_task(task_id)
        assert task_payload["task"]["task_id"] == task_id

        sleep_payload = await admin_api.sleep_orchestrator_task(
            task_id,
            {"delay_seconds": 0.05, "wake_events": ["channel:discord"], "reason": "api_pause"},
        )
        assert sleep_payload["task"]["status"] == "sleeping"

        wake_event_payload = await admin_api.wake_orchestrator_event({"event": "channel:discord"})
        assert wake_event_payload["status"] == "ok"
        assert int(wake_event_payload["awakened"]) >= 1

        wake_payload = await admin_api.wake_orchestrator_task(task_id, {"reason": "api_manual"})
        assert wake_payload["status"] == "ok"
        result = await orch.wait_task(task_id, timeout_seconds=1.2)
    finally:
        admin_api.ORCHESTRATOR = original
        orch.stop()

    assert result == "ok:api-task"
