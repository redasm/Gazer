"""Lane-based command queue for isolated concurrent task execution.

Inspired by OpenClaw's lane queue architecture. Each lane has its own
asyncio queue and configurable concurrency limit, so cron jobs don't
block the main chat, sub-agents don't starve user interactions, etc.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

from tools.base import CancellationToken

logger = logging.getLogger("CommandQueue")


class CommandLane(str, Enum):
    """Isolated execution lanes."""

    MAIN = "main"           # Primary chat workflow
    CRON = "cron"           # Scheduled jobs
    SUBAGENT = "subagent"   # Child agent spawning
    NESTED = "nested"       # Nested tool calls


# Default concurrency limits per lane
DEFAULT_LANE_LIMITS: Dict[CommandLane, int] = {
    CommandLane.MAIN: 1,
    CommandLane.CRON: 2,
    CommandLane.SUBAGENT: 3,
    CommandLane.NESTED: 2,
}


@dataclass
class CommandEntry:
    """A unit of work submitted to the queue."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    lane: CommandLane = CommandLane.MAIN
    callback: Optional[Callable[[], Awaitable[Any]]] = None
    cancel_token: Optional[CancellationToken] = None
    future: Optional[asyncio.Future] = None


class _LaneState:
    """Internal state for a single lane."""

    __slots__ = ("queue", "active", "max_concurrent", "name")

    def __init__(self, name: str, max_concurrent: int) -> None:
        self.name = name
        self.queue: asyncio.Queue[CommandEntry] = asyncio.Queue()
        self.active: int = 0
        self.max_concurrent: int = max_concurrent


class CommandQueue:
    """Multi-lane async task queue.

    Usage::

        cq = CommandQueue()
        asyncio.create_task(cq.run())

        future = await cq.enqueue(CommandLane.MAIN, my_coroutine_func)
        result = await future
    """

    def __init__(
        self,
        lane_limits: Optional[Dict[CommandLane, int]] = None,
    ) -> None:
        limits = {**DEFAULT_LANE_LIMITS, **(lane_limits or {})}
        self._lanes: Dict[CommandLane, _LaneState] = {
            lane: _LaneState(lane.value, limit)
            for lane, limit in limits.items()
        }
        self._running = False
        self._drain_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        lane: CommandLane,
        callback: Callable[[], Awaitable[Any]],
        cancel_token: Optional[CancellationToken] = None,
    ) -> asyncio.Future:
        """Submit a task to a lane and return a Future for the result."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        entry = CommandEntry(
            lane=lane,
            callback=callback,
            cancel_token=cancel_token,
            future=future,
        )
        state = self._lanes.get(lane)
        if state is None:
            future.set_exception(ValueError(f"Unknown lane: {lane}"))
            return future
        await state.queue.put(entry)
        logger.debug(f"Enqueued task {entry.id} in lane {lane.value}")
        return future

    async def run(self) -> None:
        """Start drain loops for every lane.  Call once at startup."""
        self._running = True
        for lane, state in self._lanes.items():
            task = asyncio.create_task(self._drain_lane(state))
            self._drain_tasks.append(task)
        logger.info(
            "CommandQueue started with lanes: "
            + ", ".join(f"{l.value}(max={s.max_concurrent})" for l, s in self._lanes.items())
        )
        # Keep alive until stopped
        while self._running:
            await asyncio.sleep(1)

    def stop(self) -> None:
        """Stop all drain loops."""
        self._running = False
        for t in self._drain_tasks:
            t.cancel()
        self._drain_tasks.clear()
        logger.info("CommandQueue stopped")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def pending(self, lane: CommandLane) -> int:
        """Number of pending tasks in a lane."""
        state = self._lanes.get(lane)
        return state.queue.qsize() if state else 0

    def active(self, lane: CommandLane) -> int:
        """Number of actively executing tasks in a lane."""
        state = self._lanes.get(lane)
        return state.active if state else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _drain_lane(self, state: _LaneState) -> None:
        """Continuously drain tasks from a single lane."""
        while self._running:
            # Wait until we have capacity
            if state.active >= state.max_concurrent:
                await asyncio.sleep(0.05)
                continue

            try:
                entry = await asyncio.wait_for(state.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            # Fire-and-forget the execution (respecting concurrency)
            state.active += 1
            asyncio.create_task(self._execute_entry(state, entry))

    async def _execute_entry(self, state: _LaneState, entry: CommandEntry) -> None:
        """Execute a single command entry and resolve its future."""
        try:
            if entry.cancel_token and entry.cancel_token.is_cancelled:
                if entry.future and not entry.future.done():
                    entry.future.set_exception(asyncio.CancelledError("Cancelled before execution"))
                return

            result = await entry.callback()

            if entry.future and not entry.future.done():
                entry.future.set_result(result)
        except Exception as exc:
            logger.error(f"Task {entry.id} in lane {state.name} failed: {exc}", exc_info=True)
            if entry.future and not entry.future.done():
                entry.future.set_exception(exc)
        finally:
            state.active -= 1
