import asyncio
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
async def test_orchestrator_parallel_and_per_agent_quota(monkeypatch, tmp_path: Path):
    queue = CommandQueue()
    orch = AgentOrchestrator(
        command_queue=queue,
        provider=_DummyProvider(),
        max_parallel_tasks=2,
        max_parallel_per_agent=1,
        max_pending_tasks=32,
        default_timeout_seconds=2.0,
    )
    orch.register_agent(AgentConfig(id="a", name="A", workspace=tmp_path))
    orch.register_agent(AgentConfig(id="b", name="B", workspace=tmp_path))

    active = {"global": 0, "a": 0, "b": 0}
    peak = {"global": 0, "a": 0, "b": 0}

    async def _run(agent_id: str, msg) -> _Resp:
        active["global"] += 1
        active[agent_id] += 1
        peak["global"] = max(peak["global"], active["global"])
        peak[agent_id] = max(peak[agent_id], active[agent_id])
        await asyncio.sleep(0.05)
        active[agent_id] -= 1
        active["global"] -= 1
        return _Resp(f"{agent_id}:{msg.content}")

    loops = {
        "a": _LoopStub(lambda msg: _run("a", msg)),
        "b": _LoopStub(lambda msg: _run("b", msg)),
    }
    monkeypatch.setattr(orch, "_get_or_create_loop", lambda agent_id: loops[agent_id])

    try:
        tasks = [
            asyncio.create_task(orch.run_agent_turn("a", "task-a1")),
            asyncio.create_task(orch.run_agent_turn("a", "task-a2")),
            asyncio.create_task(orch.run_agent_turn("b", "task-b1")),
            asyncio.create_task(orch.run_agent_turn("b", "task-b2")),
        ]
        out = await asyncio.gather(*tasks)
    finally:
        orch.stop()

    assert len(out) == 4
    assert peak["global"] <= 2
    assert peak["a"] <= 1
    assert peak["b"] <= 1


@pytest.mark.asyncio
async def test_orchestrator_sla_priority_retry_and_cancel(monkeypatch, tmp_path: Path):
    queue = CommandQueue()
    orch = AgentOrchestrator(
        command_queue=queue,
        provider=_DummyProvider(),
        max_parallel_tasks=1,
        max_parallel_per_agent=1,
        max_pending_tasks=32,
        default_timeout_seconds=1.0,
    )
    orch.register_agent(AgentConfig(id="a", name="A", workspace=tmp_path))

    hold_event = asyncio.Event()
    hold_started = asyncio.Event()
    call_order = []
    flaky_attempt = {"n": 0}

    async def _handler(msg) -> _Resp:
        text = str(msg.content)
        call_order.append(text)
        if text == "hold":
            hold_started.set()
            await hold_event.wait()
            return _Resp("hold-ok")
        if text == "flaky":
            flaky_attempt["n"] += 1
            if flaky_attempt["n"] == 1:
                await asyncio.sleep(0.06)
            return _Resp(f"flaky-{flaky_attempt['n']}")
        if text == "long":
            await asyncio.sleep(1.0)
            return _Resp("long-ok")
        await asyncio.sleep(0.01)
        return _Resp(f"ok:{text}")

    monkeypatch.setattr(orch, "_get_or_create_loop", lambda _agent_id: _LoopStub(_handler))

    try:
        # Priority: high should execute before low once worker becomes available.
        hold_task = asyncio.create_task(orch.run_agent_turn("a", "hold", priority="normal"))
        await hold_started.wait()
        low_task = asyncio.create_task(orch.run_agent_turn("a", "low", priority="low"))
        high_task = asyncio.create_task(orch.run_agent_turn("a", "high", priority="high"))
        hold_event.set()
        await asyncio.gather(hold_task, high_task, low_task)
        assert call_order.index("high") < call_order.index("low")

        # Retry with timeout.
        retry_out = await orch.run_agent_turn(
            "a",
            "flaky",
            timeout_seconds=0.01,
            max_retries=1,
            retry_backoff_seconds=0.0,
        )
        assert retry_out == "flaky-2"
        assert flaky_attempt["n"] == 2

        # Cancel running task.
        task_id = await orch.submit_agent_turn("a", "long", timeout_seconds=2.0)
        waiter = asyncio.create_task(orch.wait_task(task_id))
        await asyncio.sleep(0.05)
        assert orch.cancel_task(task_id) is True
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        orch.stop()
