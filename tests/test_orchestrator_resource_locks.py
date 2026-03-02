import asyncio
import time
from pathlib import Path

import pytest

from agent.orchestrator import AgentConfig, AgentOrchestrator
from bus.command_queue import CommandQueue


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


@pytest.mark.asyncio
async def test_orchestrator_shared_resource_lock_serializes_tasks(monkeypatch, tmp_path: Path):
    queue = CommandQueue()
    orch = AgentOrchestrator(
        command_queue=queue,
        provider=_DummyProvider(),
        max_parallel_tasks=2,
        max_parallel_per_agent=2,
        max_pending_tasks=32,
        default_timeout_seconds=2.0,
    )
    orch.register_agent(AgentConfig(id="a", name="A", workspace=tmp_path))

    started = {}
    ended = {}

    async def _handler(msg):
        key = str(msg.content)
        started[key] = time.monotonic()
        await asyncio.sleep(0.06)
        ended[key] = time.monotonic()
        return _Resp(key)

    monkeypatch.setattr(orch, "_get_or_create_loop", lambda _agent_id: _LoopStub(_handler))

    try:
        t1 = asyncio.create_task(
            orch.run_agent_turn("a", "first", resource_locks={"shared": ["report"]})
        )
        t2 = asyncio.create_task(
            orch.run_agent_turn("a", "second", resource_locks={"shared": ["report"]})
        )
        await asyncio.gather(t1, t2)
    finally:
        orch.stop()

    assert started["second"] >= ended["first"]


@pytest.mark.asyncio
async def test_orchestrator_directory_device_lock_timeout(monkeypatch, tmp_path: Path):
    queue = CommandQueue()
    orch = AgentOrchestrator(
        command_queue=queue,
        provider=_DummyProvider(),
        max_parallel_tasks=2,
        max_parallel_per_agent=2,
        max_pending_tasks=32,
        default_timeout_seconds=2.0,
    )
    orch.register_agent(AgentConfig(id="a", name="A", workspace=tmp_path))

    hold_event = asyncio.Event()
    hold_started = asyncio.Event()
    lock_path = tmp_path / "workspace"

    async def _handler(msg):
        text = str(msg.content)
        if text == "hold":
            hold_started.set()
            await hold_event.wait()
            return _Resp("hold-ok")
        await asyncio.sleep(0.01)
        return _Resp(text)

    monkeypatch.setattr(orch, "_get_or_create_loop", lambda _agent_id: _LoopStub(_handler))

    try:
        hold_task = asyncio.create_task(
            orch.run_agent_turn(
                "a",
                "hold",
                resource_locks={"directory": [str(lock_path)], "device": ["camera-main"]},
            )
        )
        await hold_started.wait()

        with pytest.raises(RuntimeError, match="ORCHESTRATOR_RESOURCE_LOCK_TIMEOUT"):
            await orch.run_agent_turn(
                "a",
                "blocked",
                resource_locks={"directory": [str(lock_path)], "device": ["camera-main"]},
                resource_lock_timeout_seconds=0.05,
            )

        failed = orch.list_task_runs(status="failed", limit=10)
        assert any("ORCHESTRATOR_RESOURCE_LOCK_TIMEOUT" in str(item.get("error", "")) for item in failed)
    finally:
        hold_event.set()
        await hold_task
        orch.stop()
