"""Task DAG management for the Multi-Agent Collaboration System.

Maintains task dependency relationships and lifecycle states.
Supports dynamic subtask injection, cascading failure propagation,
and watcher callbacks for state-change notifications.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

from multi_agent.models import Task, TaskPriority, TaskStatus, _short_uuid

logger = logging.getLogger("multi_agent.TaskGraph")

WatcherCallback = Callable[[str, TaskStatus, TaskStatus], Coroutine[Any, Any, None]]


class TaskGraph:
    """Directed acyclic graph of tasks with thread-safe mutations."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self._watchers: list[WatcherCallback] = []
        self._change_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Read-only accessors (no lock needed for snapshot reads)
    # ------------------------------------------------------------------

    @property
    def tasks(self) -> dict[str, Task]:
        return dict(self._tasks)

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def _completed_ids(self) -> set[str]:
        return {tid for tid, t in self._tasks.items() if t.status == TaskStatus.DONE}

    def get_ready_tasks(self) -> list[Task]:
        """Return READY tasks sorted by priority (lowest numeric value first)."""
        completed = self._completed_ids()
        ready: list[Task] = []
        for t in self._tasks.values():
            if t.status == TaskStatus.READY:
                ready.append(t)
            elif t.status == TaskStatus.PENDING and t.is_ready(completed):
                ready.append(t)
        ready.sort(key=lambda t: (t.priority.value, t.created_at))
        return ready

    def is_complete(self) -> bool:
        """True when no task is PENDING, READY, or RUNNING."""
        return all(
            t.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.BLOCKED)
            for t in self._tasks.values()
        )

    def is_successful(self) -> bool:
        return self.is_complete() and all(
            t.status == TaskStatus.DONE for t in self._tasks.values()
        )

    def get_final_results(self) -> dict[str, Any]:
        """Collect results from leaf tasks (no downstream dependents)."""
        dependents: set[str] = set()
        for t in self._tasks.values():
            dependents.update(t.depends_on)

        results: dict[str, Any] = {}
        for tid, t in self._tasks.items():
            if tid not in dependents and t.status == TaskStatus.DONE:
                results[tid] = {
                    "name": t.name,
                    "result": t.result,
                    "result_ref": t.result_ref,
                    "artifacts": t.artifacts,
                }
        return results

    # ------------------------------------------------------------------
    # Watchers
    # ------------------------------------------------------------------

    def add_watcher(self, callback: WatcherCallback) -> None:
        self._watchers.append(callback)

    async def _notify_watchers(self, task_id: str, old: TaskStatus, new: TaskStatus) -> None:
        for cb in self._watchers:
            try:
                await cb(task_id, old, new)
            except Exception:
                logger.warning("Watcher callback failed for task %s", task_id, exc_info=True)

    # ------------------------------------------------------------------
    # Mutations (all guarded by _lock)
    # ------------------------------------------------------------------

    async def add_task(self, task: Task) -> None:
        async with self._lock:
            if task.task_id in self._tasks:
                raise ValueError(f"Duplicate task_id: {task.task_id}")
            self._tasks[task.task_id] = task
            self._promote_pending()
        logger.debug("Added task %s (%s)", task.task_id, task.name)

    async def add_tasks(self, tasks: list[Task]) -> None:
        async with self._lock:
            for task in tasks:
                if task.task_id in self._tasks:
                    raise ValueError(f"Duplicate task_id: {task.task_id}")
                self._tasks[task.task_id] = task
            self._promote_pending()

    async def add_subtasks(
        self,
        subtasks: list[Task],
        parent_task_id: str,
        replace_parent: bool = True,
    ) -> None:
        """Inject subtasks under an existing parent.

        If *replace_parent* is True the parent becomes a coordination node:
        an aggregation task is created that depends on all subtasks, and any
        downstream task that previously depended on the parent now depends on
        the aggregation task instead.
        """
        async with self._lock:
            parent = self._tasks.get(parent_task_id)
            if parent is None:
                raise ValueError(f"Unknown parent task: {parent_task_id}")

            for st in subtasks:
                st.priority = parent.priority
                if not st.depends_on:
                    st.depends_on = list(parent.depends_on)
                self._tasks[st.task_id] = st

            if replace_parent:
                agg_id = f"agg_{parent_task_id}"
                agg_task = Task(
                    task_id=agg_id,
                    name=f"Aggregate: {parent.name}",
                    description=f"Wait for all subtasks of {parent.name}",
                    depends_on=[st.task_id for st in subtasks],
                    priority=parent.priority,
                    instruction="__aggregate__",
                )
                self._tasks[agg_id] = agg_task

                for t in self._tasks.values():
                    if parent_task_id in t.depends_on and t.task_id != agg_id:
                        t.depends_on = [
                            agg_id if d == parent_task_id else d
                            for d in t.depends_on
                        ]

                old_status = parent.status
                parent.status = TaskStatus.DONE
                parent.result = f"Delegated to subtasks: {[st.task_id for st in subtasks]}"
                await self._notify_watchers(parent_task_id, old_status, TaskStatus.DONE)

            self._promote_pending()

        logger.info(
            "Added %d subtasks under %s (replace=%s)",
            len(subtasks), parent_task_id, replace_parent,
        )

    async def mark_running(self, task_id: str, agent_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")
            old = task.status
            task.status = TaskStatus.RUNNING
            task.assigned_to = agent_id
            task.started_at = time.time()
        self._change_event.set()
        await self._notify_watchers(task_id, old, TaskStatus.RUNNING)

    async def claim_ready_task(
        self,
        *,
        agent_id: str,
        worker_skills: list[str] | None = None,
    ) -> Task | None:
        """Atomically claim the highest-priority ready task for a worker."""
        worker_skills = worker_skills or []
        claimed: Task | None = None
        old_status: TaskStatus | None = None
        async with self._lock:
            completed = self._completed_ids()
            ready_tasks = [
                task
                for task in self._tasks.values()
                if task.status == TaskStatus.READY
                or (task.status == TaskStatus.PENDING and task.is_ready(completed))
            ]
            ready_tasks.sort(key=lambda task: (task.priority.value, task.created_at))
            for task in ready_tasks:
                if task.required_skills and not all(
                    skill in worker_skills for skill in task.required_skills
                ):
                    continue
                old_status = task.status
                task.status = TaskStatus.RUNNING
                task.assigned_to = agent_id
                task.started_at = time.time()
                claimed = task
                break
        if claimed is None or old_status is None:
            return None
        self._change_event.set()
        await self._notify_watchers(claimed.task_id, old_status, TaskStatus.RUNNING)
        return claimed

    async def mark_done(
        self,
        task_id: str,
        result: Any,
        artifacts: dict[str, Any] | None = None,
        result_ref: str = "",
    ) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")
            old = task.status
            task.status = TaskStatus.DONE
            task.result = result
            task.result_ref = result_ref
            if artifacts:
                task.artifacts.update(artifacts)
            task.finished_at = time.time()
            self._promote_pending()
        self._change_event.set()
        await self._notify_watchers(task_id, old, TaskStatus.DONE)
        logger.info("Task %s (%s) DONE", task_id, task.name)

    async def mark_waiting_planner(self, task_id: str, reason: str = "") -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")
            old = task.status
            task.status = TaskStatus.WAITING_PLANNER
            task.assigned_to = None
            task.finished_at = time.time()
            if reason:
                task.result = f"ESCALATED: {reason}"
        self._change_event.set()
        await self._notify_watchers(task_id, old, TaskStatus.WAITING_PLANNER)

    async def requeue_task(
        self,
        task_id: str,
        *,
        instruction: str | None = None,
        clear_retry_count: bool = False,
    ) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")
            old = task.status
            if instruction is not None:
                task.instruction = instruction
            task.status = TaskStatus.PENDING
            task.assigned_to = None
            task.started_at = None
            task.finished_at = None
            if clear_retry_count:
                task.retry_count = 0
            self._promote_pending()
            new_status = task.status
        self._change_event.set()
        await self._notify_watchers(task_id, old, new_status)

    async def mark_failed_terminal(self, task_id: str, error: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")
            old = task.status
            task.status = TaskStatus.FAILED
            task.assigned_to = None
            task.finished_at = time.time()
            task.result = f"FAILED: {error}"
            self._cascade_block(task_id)
        self._change_event.set()
        await self._notify_watchers(task_id, old, TaskStatus.FAILED)
        logger.error("Task %s permanently FAILED: %s", task_id, error)

    async def mark_failed(self, task_id: str, error: str) -> bool:
        """Mark a task as failed. Returns True if the task will be retried."""
        retry = False
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")

            task.retry_count += 1
            task.finished_at = time.time()

            if task.retry_count <= task.max_retries:
                old = task.status
                task.status = TaskStatus.PENDING
                task.assigned_to = None
                self._promote_pending()
                new_status = task.status  # may have been promoted to READY
                retry = True
            else:
                old = task.status
                task.status = TaskStatus.FAILED
                task.result = f"FAILED: {error}"
                self._cascade_block(task_id)
                new_status = TaskStatus.FAILED

        self._change_event.set()
        if retry:
            await self._notify_watchers(task_id, old, new_status)
            logger.warning(
                "Task %s failed (attempt %d/%d), will retry: %s",
                task_id, task.retry_count, task.max_retries, error,
            )
            return True
        await self._notify_watchers(task_id, old, TaskStatus.FAILED)
        logger.error("Task %s permanently FAILED: %s", task_id, error)
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _promote_pending(self) -> None:
        """Promote PENDING tasks to READY when all deps are satisfied."""
        completed = self._completed_ids()
        for t in self._tasks.values():
            if t.status == TaskStatus.PENDING and t.is_ready(completed):
                t.status = TaskStatus.READY

    def _cascade_block(self, failed_task_id: str) -> None:
        """Transitively block all downstream tasks when a task fails permanently."""
        blocked_ids = {failed_task_id}
        changed = True
        while changed:
            changed = False
            for t in self._tasks.values():
                if t.status in (TaskStatus.PENDING, TaskStatus.READY):
                    if any(dep in blocked_ids for dep in t.depends_on):
                        t.status = TaskStatus.BLOCKED
                        blocked_ids.add(t.task_id)
                        changed = True

    async def wait_until_complete(self, poll_interval: float = 0.5) -> None:
        """Block until all tasks reach a terminal status.

        Uses an internal event that is signalled on every state change,
        avoiding pure-polling overhead.
        """
        while not self.is_complete():
            self._change_event.clear()
            if self.is_complete():
                return
            try:
                await asyncio.wait_for(self._change_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass

    def get_summary(self) -> dict[str, int]:
        """Return counts by status for monitoring."""
        counts: dict[str, int] = {}
        for t in self._tasks.values():
            key = t.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts
