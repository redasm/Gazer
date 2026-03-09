"""Multi-agent routing, budget management, and auto-routing mixin."""

import asyncio
import logging
from typing import Optional

from bus.events import InboundMessage
from multi_agent.models import MultiAgentExecutionContext
from runtime.config_manager import config

logger = logging.getLogger("GazerAdapter")


class _MultiAgentWorkerBudget:
    """Process-local slot budget for concurrent multi-agent workers."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity or 1))
        self._available = self.capacity
        self._condition = asyncio.Condition()

    @property
    def in_use(self) -> int:
        return self.capacity - self._available

    async def acquire(self, slots: int) -> int:
        requested = max(1, min(int(slots or 1), self.capacity))
        async with self._condition:
            while self._available < requested:
                await self._condition.wait()
            self._available -= requested
            return requested

    async def release(self, slots: int) -> None:
        released = max(1, min(int(slots or 1), self.capacity))
        async with self._condition:
            self._available = min(self.capacity, self._available + released)
            self._condition.notify_all()


class MultiAgentMixin:
    """Mixin providing multi-agent routing for GazerAgent.

    The host class must provide:
      provider, _fast_provider, _fast_model, process_message(),
      _multi_agent_worker_budget attribute.
    """

    async def process_multi_agent(
        self,
        goal: str,
        max_workers: int | None = None,
        execution_context: MultiAgentExecutionContext | None = None,
    ) -> str:
        """Execute a goal using the multi-agent collaboration system."""
        from multi_agent.runtime import MultiAgentRuntime

        ma_cfg = self._get_multi_agent_config()
        if max_workers is None:
            max_workers = ma_cfg["max_workers"]
        requested_workers = max(1, min(int(max_workers or 1), ma_cfg["max_workers"]))
        budget = self._get_multi_agent_worker_budget(total_slots=ma_cfg["max_workers"])
        acquired_workers = await budget.acquire(requested_workers)
        runtime = MultiAgentRuntime(
            self,
            max_agents=acquired_workers,
            execution_context=execution_context,
        )
        try:
            logger.info(
                "Multi-agent budget acquired: workers=%d/%d",
                acquired_workers,
                ma_cfg["max_workers"],
            )
            return await runtime.execute(goal)
        finally:
            await budget.release(acquired_workers)
            logger.info(
                "Multi-agent budget released: workers=%d/%d",
                acquired_workers,
                ma_cfg["max_workers"],
            )

    def _get_multi_agent_worker_budget(self, total_slots: int) -> _MultiAgentWorkerBudget:
        desired_capacity = max(1, int(total_slots or 1))
        budget = getattr(self, "_multi_agent_worker_budget", None)
        if budget is None:
            budget = _MultiAgentWorkerBudget(desired_capacity)
            self._multi_agent_worker_budget = budget
            return budget
        if budget.capacity != desired_capacity and budget.in_use == 0:
            budget = _MultiAgentWorkerBudget(desired_capacity)
            self._multi_agent_worker_budget = budget
        return budget

    def _should_auto_route_inbound_message(self, msg: InboundMessage) -> bool:
        """Return True when an inbound bus message should use auto multi-agent routing."""
        if msg.channel == "gazer":
            return False
        ma_cfg = self._get_multi_agent_config()
        return bool(ma_cfg["allow_multi"] and self._fast_provider is not None)

    async def _assess_multi_agent_workers(self, content: str) -> int | None:
        """Return the suggested worker count for multi-agent execution, or None."""
        ma_cfg = self._get_multi_agent_config()
        if not ma_cfg["allow_multi"] or self._fast_provider is None:
            return None

        try:
            from multi_agent.brain_router import DualBrainRouter
            from multi_agent.assessor import TaskComplexityAssessor

            router = DualBrainRouter(
                slow_provider=self.provider,
                fast_provider=self._fast_provider,
                fast_model=self._fast_model,
            )
            assessor = TaskComplexityAssessor(
                router=router,
                max_workers_limit=ma_cfg["max_workers"],
            )
            result = await assessor.assess(content)
            if not result.use_multi_agent:
                return None
            return min(result.worker_hint, ma_cfg["max_workers"])
        except Exception:
            logger.debug("TaskComplexityAssessor failed, falling back to single agent", exc_info=True)
            return None

    async def _maybe_auto_route_inbound_message(
        self,
        msg: InboundMessage,
        execution_context: MultiAgentExecutionContext,
    ) -> str | None:
        """Auto-route external inbound messages to the multi-agent runtime when appropriate."""
        if not self._should_auto_route_inbound_message(msg):
            return None

        workers = await self._assess_multi_agent_workers(msg.content)
        if workers is None:
            return None

        logger.info(
            "Auto-route inbound: multi-agent (channel=%s, score_workers=%d)",
            msg.channel,
            workers,
        )
        return await self.process_multi_agent(
            msg.content,
            max_workers=workers,
            execution_context=execution_context,
        )

    def _get_multi_agent_config(self) -> dict:
        """Read multi_agent settings from config, with safe defaults."""
        return {
            "allow_multi": bool(config.get("multi_agent.allow_multi", False)),
            "max_workers": int(config.get("multi_agent.max_workers", 5) or 5),
        }

    async def process_auto(
        self,
        content: str,
        sender: str = "User",
        execution_context: MultiAgentExecutionContext | None = None,
    ) -> str:
        """Unified entry point with automatic single/multi-agent routing."""
        workers = await self._assess_multi_agent_workers(content)
        if workers is not None:
            logger.info("Auto-route: multi-agent (workers=%d)", workers)
            return await self.process_multi_agent(
                content,
                max_workers=workers,
                execution_context=execution_context,
            )

        return await self.process_message(content, sender=sender)
