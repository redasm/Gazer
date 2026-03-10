"""WorkerAgent — lightweight agent that claims and executes tasks.

Implements:
- Work Stealing: atomically claims READY tasks from the TaskGraph
- BrainHint-based routing (fast for normal, slow for error recovery)
- Interleaved thinking: evaluate tool results before declaring done
- need_planner escalation when task exceeds capability
- Adaptive error recovery: inject error context and let LLM self-correct
- Context window management: summarize & offload to Blackboard
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from multi_agent.brain_router import HINT_DEEP, HINT_DEFAULT, HINT_FAST, BrainHint
from multi_agent.communication import AgentMessageBus, Blackboard
from multi_agent.dual_brain import DualBrain
from multi_agent.monitor import monitor_hub, should_forward_monitor_events
from multi_agent.models import (
    AgentMessage,
    MessageType,
    MultiAgentExecutionContext,
    Task,
    TaskStatus,
    WorkerResult,
    _short_uuid,
)
from multi_agent.task_graph import TaskGraph

logger = logging.getLogger("multi_agent.Worker")

MAX_TOOL_ITERATIONS = 10
MAX_ERROR_RECOVERY_ROUNDS = 2
MAX_TOOL_CALLS_PER_TASK = 50
CONTEXT_TOKEN_WARNING_RATIO = 0.8


@dataclass
class WorkerConfig:
    skills: list[str] = field(default_factory=list)
    max_iterations: int = MAX_TOOL_ITERATIONS
    max_error_recovery: int = MAX_ERROR_RECOVERY_ROUNDS
    max_tool_calls: int = MAX_TOOL_CALLS_PER_TASK


class WorkerAgent:
    """A lightweight worker that claims and executes tasks from the DAG."""

    def __init__(
        self,
        agent_id: str,
        dual_brain: DualBrain,
        task_graph: TaskGraph,
        bus: AgentMessageBus,
        blackboard: Blackboard,
        tool_registry: Any = None,
        config: WorkerConfig | None = None,
        execution_context: MultiAgentExecutionContext | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._brain = dual_brain
        self._graph = task_graph
        self._bus = bus
        self._bb = blackboard
        self._tools = tool_registry
        self._config = config or WorkerConfig()
        self._execution_context = execution_context or MultiAgentExecutionContext()
        self._session_key = self._execution_context.session_key
        self._running = False
        self._current_task: Task | None = None
        self._idle = True
        self._task: asyncio.Task | None = None

    @property
    def is_idle(self) -> bool:
        return self._idle

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        await self._bus.register_agent(self.agent_id)
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Worker %s started", self.agent_id)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._bus.unregister_agent(self.agent_id)
        logger.info("Worker %s stopped", self.agent_id)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._process_inbox()

                task = await self._claim_task()
                if task is not None:
                    self._idle = False
                    self._current_task = task
                    try:
                        await self._execute_task(task)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.error("Worker %s task %s crashed: %s", self.agent_id, task.task_id, exc, exc_info=True)
                        await self._graph.mark_failed(task.task_id, str(exc))
                    finally:
                        self._current_task = None
                        self._idle = True
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("Worker %s loop error", self.agent_id, exc_info=True)
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Inbox
    # ------------------------------------------------------------------

    async def _process_inbox(self) -> None:
        messages = self._bus.drain_all(self.agent_id)
        for msg in messages:
            if msg.msg_type == MessageType.ASK:
                reply_text = await self._brain.generate(
                    prompt=f"Quick question from {msg.sender_id}: {msg.content}\nAnswer briefly.",
                    hint=HINT_FAST,
                )
                reply = AgentMessage(
                    sender_id=self.agent_id,
                    target_id=msg.sender_id,
                    msg_type=MessageType.REPLY,
                    content=reply_text,
                    reply_to=msg.msg_id,
                )
                await self._bus.send(reply)
            elif msg.msg_type == MessageType.BROADCAST:
                logger.debug("Worker %s received broadcast: %s", self.agent_id, str(msg.content)[:100])

    # ------------------------------------------------------------------
    # Work Stealing
    # ------------------------------------------------------------------

    async def _claim_task(self) -> Task | None:
        task = await self._graph.claim_ready_task(
            agent_id=self.agent_id,
            worker_skills=self._config.skills,
        )
        if task is None:
            return None
        logger.info("Worker %s claimed task %s (%s)", self.agent_id, task.task_id, task.name)
        await self._bus.send(AgentMessage(
            sender_id=self.agent_id,
            msg_type=MessageType.BROADCAST,
            content=f"Starting task: {task.name}",
        ))
        await self._emit_log("start", f"Starting task: {task.name}", task_id=task.task_id)
        return task

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_task(self, task: Task) -> None:
        if task.instruction == "__aggregate__":
            await self._execute_aggregation(task)
            return

        try:
            await asyncio.wait_for(
                self._execute_task_inner(task),
                timeout=task.timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Worker %s task %s timed out after %.0fs",
                self.agent_id, task.task_id, task.timeout_sec,
            )
            await self._graph.mark_failed(task.task_id, f"Task timed out after {task.timeout_sec:.0f}s")
            await self._emit_log("error", f"Task timed out after {task.timeout_sec:.0f}s", task_id=task.task_id)

    async def _execute_task_inner(self, task: Task) -> None:
        dep_results = self._load_dependency_context(task)
        hint = BrainHint(reasoning_depth=1)

        system_prompt = self._build_system_prompt(task)
        user_prompt = self._build_user_prompt(task, dep_results)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        tool_defs = self._get_tool_definitions()
        iteration = 0
        error_recovery_count = 0
        tool_call_count = 0

        while iteration < self._config.max_iterations:
            iteration += 1

            try:
                if tool_defs:
                    response = await self._brain.chat_with_tools(
                        messages=messages,
                        tools=tool_defs,
                        hint=hint,
                    )
                else:
                    text = await self._brain.generate(
                        prompt=user_prompt,
                        system=system_prompt,
                        hint=hint,
                    )
                    await self._complete_task(task, text)
                    return
            except Exception as exc:
                if error_recovery_count < self._config.max_error_recovery:
                    error_recovery_count += 1
                    hint = BrainHint(quality_critical=True, reasoning_depth=3)
                    messages.append({
                        "role": "user",
                        "content": (
                            f"An error occurred: {exc}\n"
                            "Please adapt your approach and try a different strategy."
                        ),
                    })
                    logger.warning(
                        "Worker %s error recovery round %d for task %s: %s",
                        self.agent_id, error_recovery_count, task.task_id, exc,
                    )
                    continue
                await self._graph.mark_failed(task.task_id, str(exc))
                await self._emit_log("error", f"Task failed: {exc}", task_id=task.task_id)
                return

            if response.has_tool_calls:
                tc_dicts = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in response.tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tc_dicts,
                })

                for tc in response.tool_calls:
                    tool_call_count += 1
                    if tool_call_count > self._config.max_tool_calls:
                        logger.warning(
                            "Worker %s task %s exceeded max tool calls (%d)",
                            self.agent_id, task.task_id, self._config.max_tool_calls,
                        )
                        await self._graph.mark_failed(
                            task.task_id,
                            f"Exceeded max tool calls ({self._config.max_tool_calls})",
                        )
                        await self._emit_log(
                            "error",
                            f"Task failed: exceeded max tool calls ({self._config.max_tool_calls})",
                            task_id=task.task_id,
                        )
                        return
                    await monitor_hub.task_tool_call(
                        session_key=self._session_key,
                        task_id=task.task_id,
                        agent_id=self.agent_id,
                        tool_name=tc.name,
                        tool_call_index=tool_call_count,
                        forward_ipc=should_forward_monitor_events(),
                    )
                    await self._emit_log("tool", f"{tc.name}()", task_id=task.task_id)
                    result = await self._execute_tool(tc.name, tc.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue

            content = response.content or ""

            if iteration < self._config.max_iterations - 1:
                should_continue = await self._evaluate_result_quality(task, content)
                if should_continue:
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": (
                            "The result may be incomplete. Please continue investigating "
                            "and provide a more thorough answer."
                        ),
                    })
                    continue

            await self._complete_task(task, content)
            return

        logger.warning("Worker %s exhausted iterations for task %s", self.agent_id, task.task_id)
        await self._graph.mark_failed(task.task_id, "Max iterations reached")
        await self._emit_log("error", "Task failed: max iterations reached", task_id=task.task_id)

    async def _execute_aggregation(self, task: Task) -> None:
        """Auto-complete aggregation nodes once all deps are done."""
        dep_results = task.get_dependency_results(self._graph.tasks)
        summary_parts = []
        for dep_id, result in dep_results.items():
            dep_task = self._graph.get_task(dep_id)
            name = dep_task.name if dep_task else dep_id
            summary_parts.append(f"- {name}: {str(result)[:200]}")
        summary = "\n".join(summary_parts) if summary_parts else "All subtasks completed."
        await self._graph.mark_done(task.task_id, summary)

    async def _complete_task(self, task: Task, raw_content: str) -> None:
        """Parse worker output and write results to blackboard + task graph."""
        worker_result = self._parse_worker_output(raw_content)

        if worker_result.need_planner:
            await self._bus.send(AgentMessage(
                sender_id=self.agent_id,
                target_id="planner",
                msg_type=MessageType.NEED_PLANNER,
                content={
                    "task_id": task.task_id,
                    "reason": worker_result.need_planner_reason,
                },
            ))
            await self._graph.mark_waiting_planner(
                task.task_id,
                reason=worker_result.need_planner_reason,
            )
            await self._emit_log(
                "system",
                f"Escalated to planner: {worker_result.need_planner_reason}",
                task_id=task.task_id,
            )
            return

        if worker_result.spawn_subtasks and task.allow_subtask_spawn and worker_result.subtasks:
            subtasks = []
            for st_dict in worker_result.subtasks:
                subtasks.append(Task(
                    name=st_dict.get("name", "subtask"),
                    description=st_dict.get("description", ""),
                    instruction=st_dict.get("instruction", ""),
                    priority=task.priority,
                ))
            await self._graph.add_subtasks(subtasks, task.task_id, replace_parent=True)
            for subtask in subtasks:
                await monitor_hub.task_created(
                    session_key=self._session_key,
                    task_id=subtask.task_id,
                    title=subtask.name,
                    description=subtask.description,
                    agent_id=self.agent_id,
                    depends=subtask.depends_on,
                    priority=subtask.priority.name.lower(),
                    forward_ipc=should_forward_monitor_events(),
                )
            await self._emit_log("system", f"Spawned {len(subtasks)} subtasks", task_id=task.task_id)
            return

        ref = await self._bb.write(
            key=task.task_id,
            value=worker_result.result,
            agent_id=self.agent_id,
        )
        await self._graph.mark_done(
            task.task_id,
            result=worker_result.result[:500] if worker_result.result else "",
            artifacts=worker_result.artifacts,
            result_ref=ref,
        )
        preview = (worker_result.result or "").strip()
        if len(preview) > 140:
            preview = f"{preview[:137]}..."
        await self._emit_log("complete", preview or "Task completed", task_id=task.task_id)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _tool_execution_kwargs(self) -> dict[str, Any]:
        return {
            "policy": self._execution_context.tool_policy,
            "sender_id": self._execution_context.sender_id,
            "channel": self._execution_context.channel,
            "model_provider": self._execution_context.model_provider,
            "model_name": self._execution_context.model_name,
        }

    def _get_tool_definitions(self) -> list[dict[str, Any]]:
        if self._tools is None:
            return []
        try:
            return self._tools.get_definitions(**self._tool_execution_kwargs())
        except Exception:
            return []

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if self._tools is None:
            return f"Error: no tool registry available (tried to call {name})"
        try:
            return await self._tools.execute(name, arguments, **self._tool_execution_kwargs())
        except Exception as exc:
            return f"Tool '{name}' failed: {exc}"

    async def _emit_log(self, log_type: str, message: str, *, task_id: str) -> None:
        if not self._session_key:
            return
        await monitor_hub.log_entry(
            session_key=self._session_key,
            task_id=task_id,
            agent_id=self.agent_id,
            type=log_type,
            message=message,
            forward_ipc=should_forward_monitor_events(),
        )

    # ------------------------------------------------------------------
    # Interleaved thinking: evaluate result quality
    # ------------------------------------------------------------------

    async def _evaluate_result_quality(self, task: Task, content: str) -> bool:
        """Ask the fast brain whether the result is sufficient.

        Returns True if more work is needed.
        """
        if not content.strip():
            return True

        eval_prompt = (
            f"Task: {task.name}\n"
            f"Objective: {task.objective or task.description}\n"
            f"Current result:\n{content[:2000]}\n\n"
            "Is this result complete and sufficient to satisfy the task objective? "
            "Answer ONLY 'yes' or 'no'."
        )
        verdict = await self._brain.generate(
            prompt=eval_prompt,
            hint=HINT_FAST,
            max_tokens=16,
        )
        return "no" in verdict.lower()

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_system_prompt(task: Task) -> str:
        parts = [
            "You are a focused worker agent. Execute the assigned task precisely.",
            "Return your result as JSON with this schema:",
            '{"result": "...", "spawn_subtasks": false, "subtasks": [], "artifacts": {}, "need_planner": false, "need_planner_reason": ""}',
        ]
        if task.tool_guidance:
            parts.append(f"\nTool guidance: {task.tool_guidance}")
        if task.boundaries:
            parts.append(f"\nBoundaries (do NOT do these): {task.boundaries}")
        return "\n".join(parts)

    @staticmethod
    def _build_user_prompt(task: Task, dep_results: dict[str, Any]) -> str:
        parts = [
            f"## Task: {task.name}",
            f"**Objective**: {task.objective or task.description}",
        ]
        if task.instruction:
            parts.append(f"**Instructions**: {task.instruction}")
        if task.output_format:
            parts.append(f"**Expected output format**: {task.output_format}")
        if dep_results:
            parts.append("\n## Dependency results:")
            for dep_id, result in dep_results.items():
                parts.append(f"- [{dep_id}]: {str(result)[:500]}")
        return "\n".join(parts)

    def _load_dependency_context(self, task: Task) -> dict[str, Any]:
        return task.get_dependency_results(self._graph.tasks)

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_worker_output(raw: str) -> WorkerResult:
        """Try to parse JSON from LLM output; fall back to plain text."""
        text = raw.strip()
        if "```" in text:
            for block in text.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                if block.startswith("{"):
                    try:
                        data = json.loads(block)
                        return WorkerResult(**{
                            k: v for k, v in data.items()
                            if k in WorkerResult.__dataclass_fields__
                        })
                    except (json.JSONDecodeError, TypeError):
                        pass

        if text.startswith("{"):
            try:
                data = json.loads(text)
                return WorkerResult(**{
                    k: v for k, v in data.items()
                    if k in WorkerResult.__dataclass_fields__
                })
            except (json.JSONDecodeError, TypeError):
                pass

        return WorkerResult(result=text)
