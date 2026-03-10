"""MultiAgentRuntime — unified entry point for the multi-agent system.

Wires together all components (TaskGraph, AgentMessageBus, Blackboard,
AgentPool, PlannerAgent) and exposes a single ``execute()`` method.

Usage::

    from multi_agent.runtime import MultiAgentRuntime

    runtime = MultiAgentRuntime(agent_core, max_agents=5)
    result = await runtime.execute("Research quantum computing advances")
"""

from __future__ import annotations

import logging
import uuid
from enum import Enum
from typing import Any, Optional

from multi_agent.agent_pool import AgentPool, PoolConfig
from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.models import MultiAgentExecutionContext, TaskStatus
from multi_agent.monitor import monitor_hub, should_forward_monitor_events
from multi_agent.planner import PlannerAgent
from multi_agent.task_graph import TaskGraph

logger = logging.getLogger("multi_agent.Runtime")


class AgentMode(str, Enum):
    """Execution mode for the Gazer agent."""
    SINGLE = "single"
    MULTI = "multi"


class MultiAgentRuntime:
    """Unified entry point that assembles and runs the multi-agent pipeline.

    Parameters
    ----------
    agent_core:
        The existing GazerAgent instance.  Used to extract providers,
        tools, and memory — but never modified.
    max_agents:
        Maximum number of concurrent worker agents.
    """

    def __init__(
        self,
        agent_core: Any,
        max_agents: int = 5,
        execution_context: MultiAgentExecutionContext | None = None,
    ) -> None:
        session_id = uuid.uuid4().hex[:8]
        self._execution_context = execution_context or MultiAgentExecutionContext()
        if not self._execution_context.session_key:
            self._execution_context.session_key = f"ma-{session_id}"
        self._session_key = self._execution_context.session_key

        # Extract providers from agent_core
        slow_provider = getattr(agent_core, "provider", None)
        fast_provider = getattr(agent_core, "_fast_provider", None)
        fast_model = getattr(agent_core, "_fast_model", None)
        memory_manager = getattr(agent_core, "memory_manager", None)
        tool_registry = getattr(getattr(agent_core, "loop", None), "tools", None)

        if slow_provider is None:
            raise RuntimeError("agent_core must have a .provider (slow brain)")

        self._brain = DualBrain(
            slow_provider=slow_provider,
            fast_provider=fast_provider,
            fast_model=fast_model,
        )

        self._bus = AgentMessageBus()
        self._bb = Blackboard(
            session_id=session_id,
            memory_manager=memory_manager,
        )
        self._graph = TaskGraph()
        self._pool = AgentPool(
            dual_brain=self._brain,
            bus=self._bus,
            blackboard=self._bb,
            task_graph=self._graph,
            config=PoolConfig(max_agents=max_agents),
            tool_registry=tool_registry,
            execution_context=self._execution_context,
        )
        self._planner = PlannerAgent(
            dual_brain=self._brain,
            task_graph=self._graph,
            pool=self._pool,
            bus=self._bus,
            blackboard=self._bb,
            memory_manager=memory_manager,
            session_key=self._session_key,
        )
        self._graph.add_watcher(self._handle_graph_status_change)

        logger.info("MultiAgentRuntime initialized (session=%s, max_agents=%d)", self._session_key, max_agents)

    async def execute(self, user_goal: str) -> str:
        """Execute a multi-agent task and return the final result."""
        await monitor_hub.begin_session(
            self._session_key,
            user_goal,
            forward_ipc=should_forward_monitor_events(),
        )
        return await self._planner.execute(user_goal)

    async def set_max_agents(self, n: int) -> None:
        """Dynamically adjust the maximum number of worker agents."""
        await self._pool.set_max_agents(n)

    async def _handle_graph_status_change(
        self,
        task_id: str,
        old: TaskStatus,
        new: TaskStatus,
    ) -> None:
        task = self._graph.get_task(task_id)
        if task is None:
            return
        if task.instruction == "__aggregate__" or task_id.startswith("agg_"):
            return

        forward_ipc = should_forward_monitor_events()
        if new == TaskStatus.RUNNING:
            await monitor_hub.task_status(
                session_key=self._session_key,
                task_id=task_id,
                status="running",
                agent_id=task.assigned_to,
                started_at=task.started_at,
                ended_at=None,
                forward_ipc=forward_ipc,
            )
            return
        if new == TaskStatus.WAITING_PLANNER:
            await monitor_hub.task_status(
                session_key=self._session_key,
                task_id=task_id,
                status="sleeping",
                agent_id=task.assigned_to,
                started_at=task.started_at,
                ended_at=task.finished_at,
                forward_ipc=forward_ipc,
            )
            return
        if new in {TaskStatus.PENDING, TaskStatus.READY}:
            await monitor_hub.task_status(
                session_key=self._session_key,
                task_id=task_id,
                status="queued",
                agent_id=task.assigned_to,
                current_tool=None,
                started_at=task.started_at,
                ended_at=task.finished_at,
                forward_ipc=forward_ipc,
            )
            return
        if new == TaskStatus.DONE:
            await monitor_hub.task_completed(
                session_key=self._session_key,
                task_id=task_id,
                result_summary=str(task.result or "").strip(),
                started_at=task.started_at,
                ended_at=task.finished_at,
                forward_ipc=forward_ipc,
            )
            return
        if new in {TaskStatus.FAILED, TaskStatus.BLOCKED}:
            error = str(task.result or "").strip()
            if error.startswith("FAILED:"):
                error = error.split(":", 1)[1].strip()
            elif not error and new == TaskStatus.BLOCKED:
                error = "Blocked by dependency failure"
            await monitor_hub.task_failed(
                session_key=self._session_key,
                task_id=task_id,
                error=error,
                ended_at=task.finished_at,
                forward_ipc=forward_ipc,
            )
