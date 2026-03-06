"""PlannerAgent — the orchestrating brain of the multi-agent system.

Responsible for:
1. Generating a task DAG from a user goal (slow brain)
2. Building the TaskGraph from the plan
3. Launching the AgentPool and monitoring progress
4. Handling need_planner escalation from Workers
5. Aggregating final results from the Blackboard
6. Persisting planning memory for self-evolution via OpenViking
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from multi_agent.agent_pool import AgentPool, PoolConfig
from multi_agent.brain_router import HINT_DEEP, HINT_FAST, BrainHint
from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.monitor import monitor_hub, should_forward_monitor_events
from multi_agent.models import (
    AgentMessage,
    MessageType,
    Task,
    TaskPriority,
    TaskStatus,
)
from multi_agent.task_graph import TaskGraph
from multi_agent.worker_agent import WorkerConfig

logger = logging.getLogger("multi_agent.Planner")

_MONITOR_INTERVAL = 2.0
_MESSAGE_POLL_INTERVAL = 0.5
_PLANNER_AGENT_ID = "planner"

_SIMPLE_TASK_THRESHOLD = 3
_MEDIUM_TASK_THRESHOLD = 10

PLANNING_SYSTEM_PROMPT = """\
You are a planning agent. Given a user goal, decompose it into a set of
independent or dependent tasks that can be executed in parallel by worker agents.

Scale effort to complexity:
- Simple goals: 1-2 tasks, 3-10 tool calls total
- Medium goals: 2-4 tasks, each 10-15 tool calls
- Complex goals: 5-10+ tasks with clear responsibility division

For EACH task, you MUST provide ALL of these fields to prevent workers from
duplicating work or missing key directions:
- name: short task name
- description: what this task is about
- instruction: detailed instructions for the worker
- objective: what the task should achieve
- output_format: expected structure of the result
- tool_guidance: which tools to prefer and strategy hints
- boundaries: what NOT to do, and how this task differs from others
- depends_on: list of task names this depends on (empty for root tasks)
- priority: "critical" | "high" | "normal" | "low"
- required_skills: list of required worker skills (usually empty)
- allow_subtask_spawn: true if the worker can further decompose

Return ONLY valid JSON with this schema:
{
  "summary": "one-sentence plan summary",
  "complexity": "simple" | "medium" | "complex",
  "tasks": [ ... ]
}
"""

AGGREGATION_SYSTEM_PROMPT = """\
You are a synthesis agent. Given the results from multiple worker agents,
produce a coherent, comprehensive final answer to the user's original goal.
Cite specific findings from each worker where relevant.
"""


class PlannerAgent:
    """Orchestrating agent that plans, monitors, and aggregates."""

    def __init__(
        self,
        dual_brain: DualBrain,
        task_graph: TaskGraph,
        pool: AgentPool,
        bus: AgentMessageBus,
        blackboard: Blackboard,
        memory_manager: Any = None,
        emotion_vector: dict[str, float] | None = None,
        session_key: str = "",
    ) -> None:
        self._brain = dual_brain
        self._graph = task_graph
        self._pool = pool
        self._bus = bus
        self._bb = blackboard
        self._memory = memory_manager
        self.emotion_vector = emotion_vector if emotion_vector is not None else {
            "excitement": 0.0,
            "frustration": 0.0,
            "confidence": 0.5,
        }
        self._user_goal = ""
        self._plan_summary = ""
        self._start_time = 0.0
        self._session_key = str(session_key or "").strip()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def execute(self, user_goal: str) -> str:
        """Run the full multi-agent pipeline and return the final result."""
        self._user_goal = user_goal
        self._start_time = time.time()

        logger.info("Planner starting for goal: %s", user_goal[:200])

        plan = await self._plan(user_goal)
        if plan is None:
            return "Failed to generate a plan for this goal."

        await self._build_task_graph(plan)
        task_count = len(self._graph.tasks)
        if task_count == 0:
            return "Plan produced no executable tasks."

        logger.info("Plan built: %d tasks, complexity=%s", task_count, plan.get("complexity", "unknown"))

        await self._bus.register_agent(_PLANNER_AGENT_ID)
        await self._pool.start()

        try:
            monitor = asyncio.create_task(self._monitor_loop())
            msg_loop = asyncio.create_task(self._message_loop())

            await monitor
            msg_loop.cancel()
            try:
                await asyncio.wait_for(msg_loop, timeout=_MESSAGE_POLL_INTERVAL + 1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                logger.debug("Planner message loop did not exit cleanly after cancellation", exc_info=True)
        finally:
            await self._pool.stop()
            await self._bus.unregister_agent(_PLANNER_AGENT_ID)

        result = await self._aggregate_results()
        await self._save_planning_memory(result)

        elapsed = time.time() - self._start_time
        logger.info("Planner completed in %.1fs", elapsed)
        return result

    # ------------------------------------------------------------------
    # Step 1: Plan generation (slow brain — quality_critical, depth=3)
    # ------------------------------------------------------------------

    async def _plan(self, user_goal: str) -> dict[str, Any] | None:
        history_context = await self._retrieve_planning_history(user_goal)

        prompt_parts = [f"## User Goal\n{user_goal}"]
        if history_context:
            prompt_parts.append(f"\n## Similar Past Plans (for reference)\n{history_context}")

        prompt = "\n".join(prompt_parts)

        raw = await self._brain.generate(
            prompt=prompt,
            system=PLANNING_SYSTEM_PROMPT,
            hint=HINT_DEEP,
            max_tokens=4096,
        )

        plan = self._parse_plan_json(raw)
        if plan is not None:
            self._plan_summary = plan.get("summary", "")
        return plan

    async def _retrieve_planning_history(self, goal: str) -> str:
        if self._memory is None:
            return ""
        try:
            backend = getattr(self._memory, "backend", None)
            if backend is None or not hasattr(backend, "hybrid_search"):
                return ""
            results = await backend.hybrid_search(
                query=f"planning: {goal}",
                limit=3,
            )
            if not results:
                return ""
            parts = []
            for r in results:
                content = str(r.get("content", ""))[:500]
                parts.append(f"- {content}")
            return "\n".join(parts)
        except Exception:
            logger.debug("Failed to retrieve planning history", exc_info=True)
            return ""

    @staticmethod
    def _parse_plan_json(raw: str) -> dict[str, Any] | None:
        text = raw.strip()

        if "```" in text:
            for block in text.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                if block.startswith("{"):
                    try:
                        return json.loads(block)
                    except json.JSONDecodeError:
                        pass

        start = text.find("{")
        if start >= 0:
            try:
                return json.loads(text[start:])
            except json.JSONDecodeError:
                pass

        logger.error("Failed to parse plan JSON from LLM output: %s", text[:300])
        return None

    # ------------------------------------------------------------------
    # Step 2: Build task graph
    # ------------------------------------------------------------------

    async def _build_task_graph(self, plan: dict[str, Any]) -> None:
        tasks_raw = plan.get("tasks", [])
        if not isinstance(tasks_raw, list):
            return

        await self._bb.write_context("plan_summary", plan.get("summary", ""))
        await self._bb.write_context("user_goal", self._user_goal)

        priority_map = {
            "critical": TaskPriority.CRITICAL,
            "high": TaskPriority.HIGH,
            "normal": TaskPriority.NORMAL,
            "low": TaskPriority.LOW,
        }

        name_to_id: dict[str, str] = {}
        tasks: list[Task] = []

        for raw in tasks_raw:
            if not isinstance(raw, dict):
                continue
            task = Task(
                name=raw.get("name", "unnamed"),
                description=raw.get("description", ""),
                instruction=raw.get("instruction", ""),
                objective=raw.get("objective", ""),
                output_format=raw.get("output_format", ""),
                tool_guidance=raw.get("tool_guidance", ""),
                boundaries=raw.get("boundaries", ""),
                priority=priority_map.get(
                    str(raw.get("priority", "normal")).lower(),
                    TaskPriority.NORMAL,
                ),
                required_skills=raw.get("required_skills", []) or [],
                allow_subtask_spawn=raw.get("allow_subtask_spawn", True),
            )
            name_to_id[task.name] = task.task_id
            tasks.append((task, raw.get("depends_on", []) or []))

        resolved: list[Task] = []
        for task, dep_names in tasks:
            task.depends_on = [
                name_to_id[name]
                for name in dep_names
                if name in name_to_id
            ]
            resolved.append(task)

        if resolved:
            await self._graph.add_tasks(resolved)
            if self._session_key:
                forward_ipc = should_forward_monitor_events()
                for task in resolved:
                    await monitor_hub.task_created(
                        session_key=self._session_key,
                        task_id=task.task_id,
                        title=task.name,
                        description=task.description,
                        agent_id="planner",
                        depends=task.depends_on,
                        priority=task.priority.name.lower(),
                        forward_ipc=forward_ipc,
                    )

    # ------------------------------------------------------------------
    # Step 4a: Monitor loop
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while not self._graph.is_complete():
            summary = self._graph.get_summary()
            total = sum(summary.values())
            done = summary.get("done", 0)
            failed = summary.get("failed", 0)

            if total > 0:
                self.emotion_vector["excitement"] = (done / total) * 0.5
                self.emotion_vector["frustration"] = min(1.0, failed * 0.2)
                self.emotion_vector["confidence"] = max(0.1, 1.0 - (failed / max(total, 1)))

            logger.debug(
                "Monitor: %s | emotion: excitement=%.2f frustration=%.2f",
                summary, self.emotion_vector["excitement"], self.emotion_vector["frustration"],
            )
            await self._graph.wait_until_complete(poll_interval=_MONITOR_INTERVAL)
        logger.info("Monitor: all tasks terminal — %s", self._graph.get_summary())

    # ------------------------------------------------------------------
    # Step 4b: Message loop (handle need_planner escalation)
    # ------------------------------------------------------------------

    async def _message_loop(self) -> None:
        while True:
            try:
                msg = await self._bus.receive(_PLANNER_AGENT_ID, timeout=_MESSAGE_POLL_INTERVAL)
                if msg is None:
                    continue

                if msg.msg_type == MessageType.NEED_PLANNER:
                    await self._handle_escalation(msg)
                elif msg.msg_type == MessageType.ASK:
                    await self._handle_worker_question(msg)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("Message loop error", exc_info=True)

    async def _handle_escalation(self, msg: AgentMessage) -> None:
        """Worker reported need_planner — re-plan or adjust the task."""
        content = msg.content if isinstance(msg.content, dict) else {}
        task_id = content.get("task_id", "")
        reason = content.get("reason", "unknown")

        logger.warning("Escalation from %s for task %s: %s", msg.sender_id, task_id, reason)

        task = self._graph.get_task(task_id)
        if task is None:
            return

        prompt = (
            f"A worker was unable to complete this task and escalated to you.\n"
            f"Task: {task.name}\n"
            f"Description: {task.description}\n"
            f"Worker's reason: {reason}\n\n"
            f"Options:\n"
            f"1. Provide revised instructions for the same task\n"
            f"2. Split into smaller subtasks\n"
            f"3. Mark as failed and continue\n\n"
            f"Return JSON: {{\"action\": \"revise|split|fail\", \"details\": ...}}"
        )
        raw = await self._brain.generate(
            prompt=prompt,
            hint=HINT_DEEP,
        )

        action_data = self._parse_plan_json(raw)
        if not isinstance(action_data, dict):
            action_data = {"action": "fail"}

        action = action_data.get("action", "fail")

        if action == "revise":
            await self._graph.requeue_task(
                task_id,
                instruction=str(action_data.get("details", task.instruction)),
                clear_retry_count=True,
            )
        elif action == "split":
            subtask_defs = action_data.get("details", [])
            if isinstance(subtask_defs, list) and subtask_defs:
                subtasks = [
                    Task(
                        name=sd.get("name", "subtask"),
                        description=sd.get("description", ""),
                        instruction=sd.get("instruction", ""),
                        priority=task.priority,
                    )
                    for sd in subtask_defs
                    if isinstance(sd, dict)
                ]
                if subtasks:
                    await self._graph.add_subtasks(subtasks, task_id, replace_parent=True)
                    if self._session_key:
                        forward_ipc = should_forward_monitor_events()
                        for subtask in subtasks:
                            await monitor_hub.task_created(
                                session_key=self._session_key,
                                task_id=subtask.task_id,
                                title=subtask.name,
                                description=subtask.description,
                                agent_id=_PLANNER_AGENT_ID,
                                depends=subtask.depends_on,
                                priority=subtask.priority.name.lower(),
                                forward_ipc=forward_ipc,
                            )
                    return
            await self._graph.mark_failed_terminal(task_id, "Planner returned no valid subtasks")
        else:
            await self._graph.mark_failed_terminal(task_id, f"Planner decided to fail: {reason}")

    async def _handle_worker_question(self, msg: AgentMessage) -> None:
        reply_text = await self._brain.generate(
            prompt=f"Worker {msg.sender_id} asks: {msg.content}\nProvide a brief, actionable answer.",
            hint=HINT_FAST,
        )
        await self._bus.send(AgentMessage(
            sender_id=_PLANNER_AGENT_ID,
            target_id=msg.sender_id,
            msg_type=MessageType.REPLY,
            content=reply_text,
            reply_to=msg.msg_id,
        ))

    # ------------------------------------------------------------------
    # Step 5: Aggregate results (slow brain — quality_critical)
    # ------------------------------------------------------------------

    async def _aggregate_results(self) -> str:
        final = self._graph.get_final_results()

        if not final:
            if self._graph.is_successful():
                return "All tasks completed but produced no results."
            summary = self._graph.get_summary()
            return f"Multi-agent execution finished with issues: {summary}"

        result_parts: list[str] = []
        for tid, info in final.items():
            ref = info.get("result_ref", "")
            full_result = await self._bb.read(tid, namespace="results")
            content = full_result if full_result else info.get("result", "")
            result_parts.append(
                f"### {info['name']}\n{content}"
            )

        results_text = "\n\n".join(result_parts)

        prompt = (
            f"## Original Goal\n{self._user_goal}\n\n"
            f"## Worker Results\n{results_text}\n\n"
            f"Synthesize a comprehensive final answer."
        )
        return await self._brain.generate(
            prompt=prompt,
            system=AGGREGATION_SYSTEM_PROMPT,
            hint=HINT_DEEP,
            max_tokens=4096,
        )

    # ------------------------------------------------------------------
    # Step 6: Planning memory (self-evolution)
    # ------------------------------------------------------------------

    async def _save_planning_memory(self, result: str) -> None:
        if self._memory is None:
            return
        try:
            elapsed = time.time() - self._start_time
            summary = self._graph.get_summary()
            entry_content = json.dumps({
                "goal": self._user_goal[:500],
                "plan_summary": self._plan_summary[:500],
                "success": self._graph.is_successful(),
                "task_count": sum(summary.values()),
                "done_count": summary.get("done", 0),
                "failed_count": summary.get("failed", 0),
                "duration_sec": round(elapsed, 1),
                "result_preview": result[:300],
            }, ensure_ascii=False)

            backend = getattr(self._memory, "backend", None)
            if backend is not None and hasattr(backend, "add_memory"):
                from datetime import datetime
                backend.add_memory(
                    content=f"[planning] {entry_content}",
                    sender="planner",
                    timestamp=datetime.now(),
                    metadata={"type": "planning_memory"},
                )
                logger.info("Saved planning memory (%.1fs, %s)", elapsed, "success" if self._graph.is_successful() else "partial")
        except Exception:
            logger.debug("Failed to save planning memory", exc_info=True)
