"""Tests for bus.command_queue -- CommandQueue, CommandLane."""

import asyncio
import pytest
from bus.command_queue import CommandQueue, CommandLane, CommandEntry
from tools.base import CancellationToken


class TestCommandQueue:
    @pytest.mark.asyncio
    async def test_enqueue_and_execute(self):
        cq = CommandQueue()
        task = asyncio.create_task(cq.run())

        async def job():
            return 42

        future = await cq.enqueue(CommandLane.MAIN, job)
        result = await asyncio.wait_for(future, timeout=3)
        assert result == 42

        cq.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_cancelled_task(self):
        cq = CommandQueue()
        task = asyncio.create_task(cq.run())

        token = CancellationToken()
        token.cancel()

        async def job():
            return "should not run"

        future = await cq.enqueue(CommandLane.MAIN, job, cancel_token=token)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(future, timeout=3)

        cq.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def test_pending_and_active(self):
        cq = CommandQueue()
        assert cq.pending(CommandLane.MAIN) == 0
        assert cq.active(CommandLane.MAIN) == 0

    @pytest.mark.asyncio
    async def test_exception_propagation(self):
        cq = CommandQueue()
        task = asyncio.create_task(cq.run())

        async def failing_job():
            raise RuntimeError("boom")

        future = await cq.enqueue(CommandLane.CRON, failing_job)
        with pytest.raises(RuntimeError, match="boom"):
            await asyncio.wait_for(future, timeout=3)

        cq.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestCommandLane:
    def test_lane_values(self):
        assert CommandLane.MAIN.value == "main"
        assert CommandLane.CRON.value == "cron"
        assert CommandLane.SUBAGENT.value == "subagent"
        assert CommandLane.NESTED.value == "nested"
