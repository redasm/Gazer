"""AgentPool — dynamic worker scaling for the multi-agent system.

Manages a pool of WorkerAgent instances with automatic scale-up/scale-down
based on task queue pressure.  All workers share the same DualBrain instance
to conserve LLM resources.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.models import MultiAgentExecutionContext
from multi_agent.task_graph import TaskGraph
from multi_agent.worker_agent import WorkerAgent, WorkerConfig

logger = logging.getLogger("multi_agent.AgentPool")

_SCALE_CHECK_INTERVAL = 2.0


@dataclass
class PoolConfig:
    max_agents: int = 5
    min_agents: int = 1
    scale_up_ratio: float = 0.8
    idle_timeout: float = 60.0


class AgentPool:
    """Dynamic pool of WorkerAgent instances."""

    def __init__(
        self,
        dual_brain: DualBrain,
        bus: AgentMessageBus,
        blackboard: Blackboard,
        task_graph: TaskGraph,
        config: PoolConfig | None = None,
        tool_registry: Any = None,
        worker_config: WorkerConfig | None = None,
        execution_context: MultiAgentExecutionContext | None = None,
    ) -> None:
        self._brain = dual_brain
        self._bus = bus
        self._bb = blackboard
        self._graph = task_graph
        self._config = config or PoolConfig()
        self._tools = tool_registry
        self._worker_config = worker_config or WorkerConfig()
        self._execution_context = execution_context or MultiAgentExecutionContext()

        self._workers: dict[str, WorkerAgent] = {}
        self._worker_seq = 0
        self._scale_task: asyncio.Task | None = None
        self._running = False
        self._idle_since: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        for _ in range(self._config.min_agents):
            await self._spawn_worker()
        self._scale_task = asyncio.create_task(self._auto_scale_loop())
        logger.info(
            "AgentPool started: %d workers (min=%d, max=%d)",
            len(self._workers), self._config.min_agents, self._config.max_agents,
        )

    async def stop(self) -> None:
        self._running = False
        if self._scale_task and not self._scale_task.done():
            self._scale_task.cancel()
            try:
                await self._scale_task
            except asyncio.CancelledError:
                pass

        stop_tasks = [w.stop() for w in self._workers.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        self._workers.clear()
        logger.info("AgentPool stopped")

    async def set_max_agents(self, n: int) -> None:
        self._config.max_agents = max(self._config.min_agents, n)
        logger.info("Max agents updated to %d", self._config.max_agents)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        total = len(self._workers)
        busy = sum(1 for w in self._workers.values() if not w.is_idle)
        idle = total - busy
        return {
            "total": total,
            "busy": busy,
            "idle": idle,
            "max": self._config.max_agents,
            "min": self._config.min_agents,
            "tasks_summary": self._graph.get_summary(),
        }

    # ------------------------------------------------------------------
    # Scaling
    # ------------------------------------------------------------------

    async def _auto_scale_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(_SCALE_CHECK_INTERVAL)
                await self._evaluate_scaling()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("Scale loop error", exc_info=True)

    async def _evaluate_scaling(self) -> None:
        ready_count = len(self._graph.get_ready_tasks())
        total = len(self._workers)
        idle_workers = [w for w in self._workers.values() if w.is_idle]
        idle_count = len(idle_workers)
        busy_count = total - idle_count

        # Scale up: more ready tasks than idle workers, and room to grow
        if ready_count > idle_count and total < self._config.max_agents:
            needed = min(
                ready_count - idle_count,
                self._config.max_agents - total,
            )
            for _ in range(needed):
                await self._spawn_worker()
            logger.info("Scaled up: spawned %d workers (total=%d)", needed, len(self._workers))

        # Scale down: excess idle workers and no ready tasks
        elif ready_count == 0 and idle_count > self._config.min_agents:
            now = __import__("time").time()
            for worker in idle_workers:
                if len(self._workers) <= self._config.min_agents:
                    break
                idle_start = self._idle_since.get(worker.agent_id, now)
                if now - idle_start >= self._config.idle_timeout:
                    await self._remove_worker(worker.agent_id)
                    logger.info("Scaled down: removed idle worker %s", worker.agent_id)

        # Track idle start times
        now = __import__("time").time()
        for w in self._workers.values():
            if w.is_idle:
                self._idle_since.setdefault(w.agent_id, now)
            else:
                self._idle_since.pop(w.agent_id, None)

    async def _spawn_worker(self) -> WorkerAgent:
        self._worker_seq += 1
        agent_id = f"worker-{self._worker_seq:03d}"
        worker = WorkerAgent(
            agent_id=agent_id,
            dual_brain=self._brain,
            task_graph=self._graph,
            bus=self._bus,
            blackboard=self._bb,
            tool_registry=self._tools,
            config=self._worker_config,
            execution_context=self._execution_context,
        )
        self._workers[agent_id] = worker
        await worker.start()
        return worker

    async def _remove_worker(self, agent_id: str) -> None:
        worker = self._workers.pop(agent_id, None)
        if worker is not None:
            await worker.stop()
        self._idle_since.pop(agent_id, None)
