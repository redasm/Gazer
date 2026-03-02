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
    ) -> None:
        session_id = uuid.uuid4().hex[:8]

        # Extract providers from agent_core
        slow_provider = getattr(agent_core, "provider", None)
        fast_provider = getattr(agent_core, "_fast_provider", None)
        fast_model = getattr(agent_core, "_fast_model", None)
        memory_manager = getattr(agent_core, "memory_manager", None)
        tool_registry = getattr(getattr(agent_core, "loop", None), "tools", None)

        assert slow_provider is not None, "agent_core must have a .provider (slow brain)"

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
        )
        self._planner = PlannerAgent(
            dual_brain=self._brain,
            task_graph=self._graph,
            pool=self._pool,
            bus=self._bus,
            blackboard=self._bb,
            memory_manager=memory_manager,
        )

        logger.info("MultiAgentRuntime initialized (session=%s, max_agents=%d)", session_id, max_agents)

    async def execute(self, user_goal: str) -> str:
        """Execute a multi-agent task and return the final result."""
        return await self._planner.execute(user_goal)

    async def set_max_agents(self, n: int) -> None:
        """Dynamically adjust the maximum number of worker agents."""
        await self._pool.set_max_agents(n)
