"""Multi-Agent Orchestrator -- manages multiple isolated agent instances.

Inspired by OpenClaw's multi-agent routing architecture.  Each agent has
its own workspace, session store, model override, and tool policy.  A
*principal* agent can delegate tasks to specialist sub-agents via
``DelegateTaskTool``.
"""

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from bus.command_queue import CommandLane, CommandQueue
from bus.events import InboundMessage
from bus.queue import MessageBus
from llm.base import LLMProvider
from tools.base import CancellationToken, Tool

logger = logging.getLogger("AgentOrchestrator")


# ------------------------------------------------------------------
# Agent configuration
# ------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Configuration for a single agent instance."""

    id: str
    name: str
    workspace: Path
    model: Optional[str] = None
    tool_policy: Optional[Dict[str, Any]] = None
    system_prompt_file: Optional[str] = None  # e.g. "SOUL.md"
    is_default: bool = False


@dataclass
class AgentBinding:
    """Routes inbound messages to a specific agent.

    Matching is most-specific-wins: channel+chat_id > channel > default.
    """

    agent_id: str
    channel: Optional[str] = None
    chat_id: Optional[str] = None
    sender_id: Optional[str] = None


@dataclass
class _TaskRecord:
    """Internal queued/running task record."""

    task_id: str
    agent_id: str
    message: str
    session_key: Optional[str]
    lane: CommandLane
    priority: str
    priority_rank: int
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    resource_lock_timeout_seconds: float
    resource_locks: Dict[str, List[str]]
    cancel_token: CancellationToken
    future: asyncio.Future
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    status: str = "queued"  # queued | running | sleeping | waking | completed | failed | cancelled
    attempts: int = 0
    error: str = ""
    conflicts: List[str] = field(default_factory=list)
    running_task: Optional[asyncio.Task] = None
    sleep_until: Optional[float] = None
    wake_events: List[str] = field(default_factory=list)
    sleep_reason: str = ""
    wake_reason: str = ""
    next_wake_at: Optional[float] = None

    def to_public(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "lane": self.lane.value,
            "priority": self.priority,
            "status": self.status,
            "attempts": int(self.attempts),
            "timeout_seconds": float(self.timeout_seconds),
            "max_retries": int(self.max_retries),
            "retry_backoff_seconds": float(self.retry_backoff_seconds),
            "resource_locks": dict(self.resource_locks),
            "resource_lock_timeout_seconds": float(self.resource_lock_timeout_seconds),
            "created_at": float(self.created_at),
            "started_at": float(self.started_at) if self.started_at is not None else None,
            "ended_at": float(self.ended_at) if self.ended_at is not None else None,
            "error": self.error,
            "conflicts": list(self.conflicts),
            "sleep_until": float(self.sleep_until) if self.sleep_until is not None else None,
            "wake_events": list(self.wake_events),
            "sleep_reason": self.sleep_reason,
            "wake_reason": self.wake_reason,
            "next_wake_at": float(self.next_wake_at) if self.next_wake_at is not None else None,
        }


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------

class AgentOrchestrator:
    """Manages multiple agent instances and routes messages between them."""

    _PRIORITY_RANK = {
        "high": 0,
        "normal": 1,
        "low": 2,
    }

    def __init__(
        self,
        command_queue: CommandQueue,
        provider: LLMProvider,
        bus: Optional[MessageBus] = None,
        *,
        max_parallel_tasks: int = 3,
        max_parallel_per_agent: int = 2,
        max_pending_tasks: int = 64,
        default_timeout_seconds: float = 120.0,
        default_max_retries: int = 0,
        default_retry_backoff_seconds: float = 0.0,
        default_priority: str = "normal",
        default_resource_lock_timeout_seconds: float = 30.0,
        sleep_poll_interval_seconds: float = 1.0,
        max_sleep_seconds: float = 3600.0,
    ) -> None:
        self._agents: Dict[str, AgentConfig] = {}
        self._bindings: List[AgentBinding] = []
        self._command_queue = command_queue
        self._provider = provider
        self._bus = bus
        self._default_agent_id: Optional[str] = None

        # Lazy-init agent loops (import here to avoid circular deps)
        self._loops: Dict[str, Any] = {}  # agent_id -> AgentLoop

        # Task scheduler / quota / SLA controls
        self._max_parallel_tasks = max(1, int(max_parallel_tasks or 1))
        self._max_parallel_per_agent = max(1, int(max_parallel_per_agent or 1))
        self._max_pending_tasks = max(1, int(max_pending_tasks or 1))
        self._default_timeout_seconds = max(0.1, float(default_timeout_seconds or 120.0))
        self._default_max_retries = max(0, int(default_max_retries or 0))
        self._default_retry_backoff_seconds = max(0.0, float(default_retry_backoff_seconds or 0.0))
        self._default_priority = self._normalize_priority(default_priority)
        self._default_resource_lock_timeout_seconds = max(
            0.1, float(default_resource_lock_timeout_seconds or 30.0)
        )
        self._sleep_poll_interval_seconds = max(0.05, float(sleep_poll_interval_seconds or 1.0))
        self._max_sleep_seconds = max(
            self._sleep_poll_interval_seconds,
            float(max_sleep_seconds or 3600.0),
        )

        self._task_queue: asyncio.PriorityQueue[tuple[int, int, str]] = asyncio.PriorityQueue()
        self._task_records: Dict[str, _TaskRecord] = {}
        self._task_seq: int = 0
        self._scheduler_started = False
        self._scheduler_lock = asyncio.Lock()
        self._worker_tasks: List[asyncio.Task] = []
        self._agent_semaphores: Dict[str, asyncio.Semaphore] = {}

        # Cross-task resource locks
        self._resource_locks: Dict[str, asyncio.Lock] = {}
        self._resource_lock_owners: Dict[str, str] = {}
        self._wake_events_seen: Dict[str, float] = {}
        self._sleep_waiters_by_event: Dict[str, Set[str]] = {}
        self._scheduled_requeues: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_agent(self, cfg: AgentConfig) -> None:
        """Register an agent configuration."""
        self._agents[cfg.id] = cfg
        if cfg.is_default:
            self._default_agent_id = cfg.id
        self._agent_semaphores.setdefault(cfg.id, asyncio.Semaphore(self._max_parallel_per_agent))
        logger.info("Registered agent: %s (%s)", cfg.id, cfg.name)

    def unregister_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        self._loops.pop(agent_id, None)
        self._agent_semaphores.pop(agent_id, None)

    def add_binding(self, binding: AgentBinding) -> None:
        self._bindings.append(binding)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def resolve_agent(self, msg: InboundMessage) -> str:
        """Determine which agent should handle an inbound message.

        Most-specific binding wins:
        1. channel + chat_id + sender_id
        2. channel + chat_id
        3. channel + sender_id
        4. channel only
        5. default agent
        """
        best_score = -1
        best_agent = self._default_agent_id or "main"

        for b in self._bindings:
            score = 0
            if b.channel and b.channel != msg.channel:
                continue
            if b.channel:
                score += 1
            if b.chat_id:
                if b.chat_id != msg.chat_id:
                    continue
                score += 10
            if b.sender_id:
                if b.sender_id != msg.sender_id:
                    continue
                score += 10
            if score > best_score:
                best_score = score
                best_agent = b.agent_id

        return best_agent

    # ------------------------------------------------------------------
    # Agent execution internals
    # ------------------------------------------------------------------

    def _get_or_create_loop(self, agent_id: str) -> Any:
        """Get or lazily create an AgentLoop for the given agent.

        Each sub-agent gets its own AgentLoop with:
        - Isolated session namespace (session_key prefix)
        - Per-agent MemoryManager (data stored under agent_id subdir)
        - Independent tool_policy and model override
        - Shared: LLM provider, MessageBus, base infrastructure
        """
        if agent_id in self._loops:
            return self._loops[agent_id]

        from agent.loop import AgentLoop
        from agent.context import ContextBuilder

        cfg = self._agents.get(agent_id)
        if not cfg:
            raise ValueError(f"Unknown agent: {agent_id}")

        bus = self._bus or MessageBus()
        context = ContextBuilder(cfg.workspace)

        # Per-agent memory namespace — isolated from main agent
        memory_manager = None
        try:
            from memory import MemoryManager
            from runtime.config_manager import config as _cfg
            base_dir = str(_cfg.get("memory.context_backend.data_dir", "data/openviking") or "data/openviking")
            agent_memory_dir = os.path.join(base_dir, "agents", agent_id)
            os.makedirs(agent_memory_dir, exist_ok=True)
            memory_manager = MemoryManager(base_path=agent_memory_dir)
        except Exception as exc:
            logger.warning("Failed to create per-agent MemoryManager for %s: %s", agent_id, exc)

        loop = AgentLoop(
            bus=bus,
            provider=self._provider,
            workspace=cfg.workspace,
            model=cfg.model,
            context_builder=context,
            tool_policy=cfg.tool_policy,
            memory_manager=memory_manager,
        )
        self._loops[agent_id] = loop
        return loop

    async def _execute_agent_turn_once(self, record: _TaskRecord) -> str:
        loop = self._get_or_create_loop(record.agent_id)

        async def _turn() -> str:
            record.cancel_token.raise_if_cancelled()
            msg = InboundMessage(
                channel="orchestrator",
                chat_id=record.session_key or f"sub:{record.agent_id}",
                sender_id="system",
                content=record.message,
            )
            response = await loop._process_message(msg)
            record.cancel_token.raise_if_cancelled()
            return response.content if response else ""

        queue_running = bool(getattr(self._command_queue, "_running", False))
        if queue_running:
            future = await self._command_queue.enqueue(
                record.lane,
                _turn,
                cancel_token=record.cancel_token,
            )
            return await future
        return await _turn()

    # ------------------------------------------------------------------
    # Scheduler / quota / SLA
    # ------------------------------------------------------------------

    async def _ensure_scheduler_started(self) -> None:
        if self._scheduler_started:
            return
        async with self._scheduler_lock:
            if self._scheduler_started:
                return
            self._worker_tasks = [
                asyncio.create_task(self._worker_loop(index))
                for index in range(self._max_parallel_tasks)
            ]
            self._scheduler_started = True
            logger.info(
                "AgentOrchestrator scheduler started: workers=%d per_agent=%d pending_limit=%d",
                self._max_parallel_tasks,
                self._max_parallel_per_agent,
                self._max_pending_tasks,
            )

    def stop(self) -> None:
        """Stop orchestrator workers and cancel unfinished tasks."""
        for worker in list(self._worker_tasks):
            worker.cancel()
        self._worker_tasks.clear()
        self._scheduler_started = False
        for sleeper in list(self._scheduled_requeues.values()):
            sleeper.cancel()
        self._scheduled_requeues.clear()

        for record in self._task_records.values():
            if record.status in {"queued", "running"}:
                record.cancel_token.cancel()
                if record.running_task and not record.running_task.done():
                    record.running_task.cancel()
                if not record.future.done():
                    record.future.set_exception(asyncio.CancelledError("Orchestrator stopped."))
                record.status = "cancelled"
                record.error = "Orchestrator stopped."
                record.ended_at = record.ended_at or time.time()
                self._clear_sleep_waiters(record.task_id)

    def _normalize_priority(self, priority: Any) -> str:
        marker = str(priority or "").strip().lower()
        if marker in self._PRIORITY_RANK:
            return marker
        return "normal"

    def _priority_rank(self, priority: Any) -> int:
        marker = self._normalize_priority(priority)
        return int(self._PRIORITY_RANK.get(marker, 1))

    def _coerce_id_list(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            return [part for part in parts if part]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _normalize_wake_events(self, wake_events: Optional[List[str]]) -> List[str]:
        if wake_events is None:
            return []
        normalized: List[str] = []
        for marker in wake_events:
            event_key = str(marker or "").strip()
            if event_key:
                normalized.append(event_key)
        return sorted(set(normalized))

    def _normalize_sleep_for_seconds(self, sleep_for_seconds: Optional[float]) -> Optional[float]:
        if sleep_for_seconds is None:
            return None
        try:
            seconds = float(sleep_for_seconds)
        except (TypeError, ValueError):
            return None
        seconds = max(0.0, min(seconds, self._max_sleep_seconds))
        if seconds <= 0.0:
            return None
        return time.time() + seconds

    def _normalize_resource_locks(self, resource_locks: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
        payload: Dict[str, Any] = resource_locks if isinstance(resource_locks, dict) else {}
        shared = self._coerce_id_list(payload.get("shared", []))
        directory_raw = self._coerce_id_list(payload.get("directory", []))
        device = self._coerce_id_list(payload.get("device", []))

        directories: List[str] = []
        for item in directory_raw:
            try:
                resolved = str(Path(item).expanduser().resolve())
            except Exception:
                resolved = os.path.abspath(str(item))
            directories.append(os.path.normcase(os.path.normpath(resolved)))

        return {
            "shared": sorted(set(shared)),
            "directory": sorted(set(directories)),
            "device": sorted(set(device)),
        }

    def _clear_sleep_waiters(self, task_id: str) -> None:
        for event_key in list(self._sleep_waiters_by_event.keys()):
            waiters = self._sleep_waiters_by_event.get(event_key)
            if not waiters:
                self._sleep_waiters_by_event.pop(event_key, None)
                continue
            waiters.discard(task_id)
            if not waiters:
                self._sleep_waiters_by_event.pop(event_key, None)

    def _register_sleep_waiters(self, record: _TaskRecord) -> None:
        self._clear_sleep_waiters(record.task_id)
        pending_events = [
            event_key
            for event_key in record.wake_events
            if event_key not in self._wake_events_seen
        ]
        for event_key in pending_events:
            self._sleep_waiters_by_event.setdefault(event_key, set()).add(record.task_id)

    def _cancel_scheduled_requeue(self, task_id: str) -> None:
        sleeper = self._scheduled_requeues.pop(task_id, None)
        if sleeper and not sleeper.done():
            sleeper.cancel()

    def _schedule_requeue(self, record: _TaskRecord, delay_seconds: float) -> None:
        if record.status in {"completed", "failed", "cancelled"}:
            return
        self._cancel_scheduled_requeue(record.task_id)
        safe_delay = max(0.0, float(delay_seconds or 0.0))

        async def _requeue() -> None:
            try:
                if safe_delay > 0:
                    await asyncio.sleep(safe_delay)
                current = self._task_records.get(record.task_id)
                if current is None:
                    return
                if current.status in {"completed", "failed", "cancelled"}:
                    return
                self._task_seq += 1
                await self._task_queue.put((current.priority_rank, self._task_seq, current.task_id))
            except asyncio.CancelledError:
                return

        self._scheduled_requeues[record.task_id] = asyncio.create_task(_requeue())

    def _evaluate_sleep_state(self, record: _TaskRecord) -> Tuple[bool, str, float]:
        now_ts = time.time()
        timer_wait_seconds: Optional[float] = None
        if record.sleep_until is not None and now_ts < float(record.sleep_until):
            timer_wait_seconds = max(0.0, float(record.sleep_until) - now_ts)

        pending_events = [
            event_key
            for event_key in record.wake_events
            if event_key not in self._wake_events_seen
        ]

        if timer_wait_seconds is None and not pending_events:
            return False, "ready", 0.0
        if timer_wait_seconds is not None and pending_events:
            return (
                True,
                f"timer+event:{pending_events[0]}",
                max(0.05, min(timer_wait_seconds, self._sleep_poll_interval_seconds)),
            )
        if timer_wait_seconds is not None:
            return True, "timer", max(0.05, timer_wait_seconds)
        return True, f"event:{pending_events[0]}", max(0.05, self._sleep_poll_interval_seconds)

    def _set_sleeping(self, record: _TaskRecord, reason: str, delay_seconds: float) -> None:
        record.status = "sleeping"
        record.sleep_reason = str(reason or "sleep")
        record.next_wake_at = time.time() + max(0.0, float(delay_seconds or 0.0))
        self._register_sleep_waiters(record)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        record = self._task_records.get(task_id)
        if record is None:
            return None
        return record.to_public()

    def get_status(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {
            "queued": 0,
            "running": 0,
            "sleeping": 0,
            "waking": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for record in self._task_records.values():
            marker = str(record.status or "").strip().lower()
            if marker in counts:
                counts[marker] += 1
        return {
            "scheduler_started": bool(self._scheduler_started),
            "workers": int(self._max_parallel_tasks),
            "per_agent_limit": int(self._max_parallel_per_agent),
            "max_pending_tasks": int(self._max_pending_tasks),
            "sleep_poll_interval_seconds": float(self._sleep_poll_interval_seconds),
            "pending_wake_events": len(self._sleep_waiters_by_event),
            "known_wake_events": sorted(self._wake_events_seen.keys()),
            "counts": counts,
        }

    def sleep_task(
        self,
        task_id: str,
        *,
        delay_seconds: Optional[float] = None,
        wake_events: Optional[List[str]] = None,
        reason: str = "manual_sleep",
    ) -> bool:
        record = self._task_records.get(task_id)
        if record is None:
            return False
        if record.status in {"completed", "failed", "cancelled", "running"}:
            return False

        sleep_until = self._normalize_sleep_for_seconds(delay_seconds)
        if sleep_until is not None:
            record.sleep_until = sleep_until
        if wake_events is not None:
            record.wake_events = self._normalize_wake_events(wake_events)
        if record.sleep_until is None and not record.wake_events:
            return False

        record.sleep_reason = str(reason or "manual_sleep")
        record.wake_reason = ""
        self._set_sleeping(record, record.sleep_reason, self._sleep_poll_interval_seconds)
        self._schedule_requeue(record, 0.0)
        return True

    def wake_task(self, task_id: str, *, reason: str = "manual_wake") -> bool:
        record = self._task_records.get(task_id)
        if record is None:
            return False
        if record.status in {"completed", "failed", "cancelled"}:
            return False

        record.sleep_until = None
        record.wake_events = []
        self._clear_sleep_waiters(task_id)
        record.next_wake_at = None
        record.status = "waking"
        record.wake_reason = str(reason or "manual_wake")
        self._schedule_requeue(record, 0.0)
        return True

    def emit_wake_event(self, event_key: str) -> int:
        marker = str(event_key or "").strip()
        if not marker:
            return 0

        self._wake_events_seen[marker] = time.time()
        waiting = list(self._sleep_waiters_by_event.pop(marker, set()))
        awakened = 0
        for task_id in waiting:
            record = self._task_records.get(task_id)
            if record is None:
                continue
            if record.status in {"completed", "failed", "cancelled"}:
                continue
            record.status = "waking"
            record.wake_reason = f"event:{marker}"
            record.next_wake_at = None
            self._clear_sleep_waiters(task_id)
            self._schedule_requeue(record, 0.0)
            awakened += 1
        return awakened

    def notify_inbound_message(self, msg: InboundMessage) -> int:
        event_keys = {
            f"channel:{msg.channel}",
            f"channel:{msg.channel}:chat:{msg.chat_id}",
            f"sender:{msg.sender_id}",
            f"session:{msg.session_key}",
        }
        awakened = 0
        for event_key in event_keys:
            awakened += self.emit_wake_event(event_key)
        return awakened

    def _resource_lock_keys(self, resource_locks: Dict[str, List[str]]) -> List[str]:
        keys: List[str] = []
        for item in resource_locks.get("shared", []):
            keys.append(f"shared:{item}")
        for item in resource_locks.get("directory", []):
            keys.append(f"directory:{item}")
        for item in resource_locks.get("device", []):
            keys.append(f"device:{item}")
        return sorted(set(keys))

    async def _acquire_resource_locks(
        self,
        task_id: str,
        lock_keys: List[str],
        timeout_seconds: float,
        conflicts: List[str],
    ) -> List[str]:
        acquired: List[str] = []
        for key in lock_keys:
            lock = self._resource_locks.setdefault(key, asyncio.Lock())
            owner = self._resource_lock_owners.get(key)
            if lock.locked() and owner and owner != task_id:
                conflicts.append(f"{key}->{owner}")
            try:
                await asyncio.wait_for(lock.acquire(), timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                owner_now = self._resource_lock_owners.get(key)
                holder = f" holder={owner_now}" if owner_now else ""
                raise RuntimeError(
                    f"Error [ORCHESTRATOR_RESOURCE_LOCK_TIMEOUT]: failed to acquire '{key}'{holder}."
                ) from exc
            self._resource_lock_owners[key] = task_id
            acquired.append(key)
        return acquired

    def _release_resource_locks(self, task_id: str, lock_keys: List[str]) -> None:
        for key in reversed(lock_keys):
            lock = self._resource_locks.get(key)
            if lock is None:
                continue
            owner = self._resource_lock_owners.get(key)
            if owner != task_id:
                continue
            if lock.locked():
                lock.release()
            self._resource_lock_owners.pop(key, None)

    async def _run_with_sla(self, record: _TaskRecord) -> str:
        attempts_total = max(1, int(record.max_retries) + 1)
        last_error: Optional[Exception] = None

        for attempt_index in range(attempts_total):
            if record.cancel_token.is_cancelled:
                raise asyncio.CancelledError("Task cancelled before execution")

            record.attempts = attempt_index + 1
            lock_keys = self._resource_lock_keys(record.resource_locks)
            acquired: List[str] = []
            try:
                acquired = await self._acquire_resource_locks(
                    record.task_id,
                    lock_keys,
                    record.resource_lock_timeout_seconds,
                    record.conflicts,
                )
                if record.timeout_seconds > 0:
                    return await asyncio.wait_for(
                        self._execute_agent_turn_once(record),
                        timeout=record.timeout_seconds,
                    )
                return await self._execute_agent_turn_once(record)
            except asyncio.TimeoutError as exc:
                last_error = RuntimeError(
                    f"Error [ORCHESTRATOR_TIMEOUT]: task '{record.task_id}' timed out "
                    f"after {record.timeout_seconds:.2f}s."
                )
                if attempt_index >= (attempts_total - 1):
                    raise last_error from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt_index >= (attempts_total - 1):
                    raise
            finally:
                self._release_resource_locks(record.task_id, acquired)

            backoff = max(0.0, float(record.retry_backoff_seconds))
            if backoff > 0:
                await asyncio.sleep(backoff * float(attempt_index + 1))

        if last_error is not None:
            raise last_error
        raise RuntimeError("Error [ORCHESTRATOR_EXECUTION_FAILED]: unknown orchestrator execution error.")

    async def _worker_loop(self, worker_index: int) -> None:
        while True:
            try:
                _, _, task_id = await self._task_queue.get()
            except asyncio.CancelledError:
                return

            record = self._task_records.get(task_id)
            try:
                if record is None:
                    continue
                if record.status not in {"queued", "sleeping", "waking"}:
                    continue
                if record.cancel_token.is_cancelled:
                    self._mark_cancelled(record, "Cancelled before execution")
                    continue

                should_sleep, sleep_reason, sleep_delay = self._evaluate_sleep_state(record)
                if should_sleep:
                    self._set_sleeping(record, sleep_reason, sleep_delay)
                    self._schedule_requeue(record, sleep_delay)
                    continue

                self._clear_sleep_waiters(record.task_id)
                self._cancel_scheduled_requeue(record.task_id)
                record.next_wake_at = None
                if record.status in {"sleeping", "waking"}:
                    record.status = "waking"
                    if not record.wake_reason:
                        record.wake_reason = "timer_elapsed"

                semaphore = self._agent_semaphores.setdefault(
                    record.agent_id,
                    asyncio.Semaphore(self._max_parallel_per_agent),
                )

                record.status = "running"
                record.started_at = time.time()
                record.running_task = asyncio.current_task()

                async with semaphore:
                    try:
                        result = await self._run_with_sla(record)
                        if not record.future.done():
                            record.future.set_result(result)
                        record.status = "completed"
                    except asyncio.CancelledError:
                        self._mark_cancelled(record, "Task cancelled")
                    except Exception as exc:
                        record.status = "failed"
                        record.error = str(exc)
                        if not record.future.done():
                            record.future.set_exception(exc)
            except Exception as exc:
                logger.error("Orchestrator worker %d failed: %s", worker_index, exc, exc_info=True)
            finally:
                if record is not None:
                    record.ended_at = time.time()
                    record.running_task = None
                self._task_queue.task_done()

    def _mark_cancelled(self, record: _TaskRecord, reason: str) -> None:
        record.status = "cancelled"
        record.error = str(reason)
        record.ended_at = record.ended_at or time.time()
        self._cancel_scheduled_requeue(record.task_id)
        self._clear_sleep_waiters(record.task_id)
        if not record.future.done():
            record.future.set_exception(asyncio.CancelledError(reason))

    def _active_task_count(self) -> int:
        return sum(
            1
            for rec in self._task_records.values()
            if rec.status in {"queued", "running", "sleeping", "waking"}
        )

    async def submit_agent_turn(
        self,
        agent_id: str,
        message: str,
        *,
        session_key: Optional[str] = None,
        lane: CommandLane = CommandLane.SUBAGENT,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_backoff_seconds: Optional[float] = None,
        priority: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
        resource_locks: Optional[Dict[str, Any]] = None,
        resource_lock_timeout_seconds: Optional[float] = None,
        sleep_for_seconds: Optional[float] = None,
        wake_events: Optional[List[str]] = None,
    ) -> str:
        """Submit a delegated task and return task_id."""
        if agent_id not in self._agents:
            raise ValueError(f"Unknown agent: {agent_id}")

        await self._ensure_scheduler_started()

        if self._active_task_count() >= self._max_pending_tasks:
            raise RuntimeError(
                "Error [ORCHESTRATOR_QUEUE_FULL]: too many pending/running tasks. "
                "Retry later or raise agents.orchestrator.max_pending_tasks."
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        task_id = uuid.uuid4().hex[:12]
        priority_text = self._normalize_priority(priority or self._default_priority)
        effective_timeout = (
            self._default_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        )
        if effective_timeout <= 0:
            effective_timeout = self._default_timeout_seconds
        effective_lock_timeout = (
            self._default_resource_lock_timeout_seconds
            if resource_lock_timeout_seconds is None
            else float(resource_lock_timeout_seconds)
        )
        if effective_lock_timeout <= 0:
            effective_lock_timeout = self._default_resource_lock_timeout_seconds
        sleep_until = self._normalize_sleep_for_seconds(sleep_for_seconds)
        normalized_wake_events = self._normalize_wake_events(wake_events)

        record = _TaskRecord(
            task_id=task_id,
            agent_id=agent_id,
            message=str(message),
            session_key=session_key,
            lane=lane,
            priority=priority_text,
            priority_rank=self._priority_rank(priority_text),
            timeout_seconds=max(0.001, effective_timeout),
            max_retries=max(0, int(max_retries if max_retries is not None else self._default_max_retries)),
            retry_backoff_seconds=max(
                0.0,
                float(
                    retry_backoff_seconds
                    if retry_backoff_seconds is not None
                    else self._default_retry_backoff_seconds
                ),
            ),
            resource_lock_timeout_seconds=max(0.001, effective_lock_timeout),
            resource_locks=self._normalize_resource_locks(resource_locks),
            cancel_token=cancel_token or CancellationToken(),
            future=future,
            sleep_until=sleep_until,
            wake_events=normalized_wake_events,
        )
        if record.sleep_until is not None or record.wake_events:
            reason_parts: List[str] = []
            if record.sleep_until is not None:
                reason_parts.append("timer")
            if record.wake_events:
                reason_parts.append("event")
            record.status = "sleeping"
            record.sleep_reason = "+".join(reason_parts) or "scheduled"
            self._register_sleep_waiters(record)

        self._task_records[task_id] = record
        self._task_seq += 1
        await self._task_queue.put((record.priority_rank, self._task_seq, task_id))
        return task_id

    async def run_agent_turn(
        self,
        agent_id: str,
        message: str,
        *,
        session_key: Optional[str] = None,
        lane: CommandLane = CommandLane.SUBAGENT,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_backoff_seconds: Optional[float] = None,
        priority: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
        resource_locks: Optional[Dict[str, Any]] = None,
        resource_lock_timeout_seconds: Optional[float] = None,
        sleep_for_seconds: Optional[float] = None,
        wake_events: Optional[List[str]] = None,
    ) -> str:
        """Run a single agent turn with orchestrator scheduling and SLA controls."""
        task_id = await self.submit_agent_turn(
            agent_id,
            message,
            session_key=session_key,
            lane=lane,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            priority=priority,
            cancel_token=cancel_token,
            resource_locks=resource_locks,
            resource_lock_timeout_seconds=resource_lock_timeout_seconds,
            sleep_for_seconds=sleep_for_seconds,
            wake_events=wake_events,
        )
        return await self.wait_task(task_id)

    async def wait_task(self, task_id: str, *, timeout_seconds: Optional[float] = None) -> Any:
        record = self._task_records.get(task_id)
        if record is None:
            raise ValueError(f"Unknown orchestrator task: {task_id}")
        if timeout_seconds is None:
            return await record.future
        return await asyncio.wait_for(record.future, timeout=max(0.1, float(timeout_seconds)))

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a queued or running task."""
        record = self._task_records.get(task_id)
        if record is None:
            return False

        record.cancel_token.cancel()
        self._cancel_scheduled_requeue(task_id)
        self._clear_sleep_waiters(task_id)
        if record.running_task and not record.running_task.done():
            record.running_task.cancel()
        if record.status in {"queued", "sleeping", "waking"}:
            self._mark_cancelled(record, "Cancelled while queued")
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_agents(self) -> List[Dict[str, Any]]:
        return [
            {"id": c.id, "name": c.name, "model": c.model, "default": c.is_default}
            for c in self._agents.values()
        ]

    def list_task_runs(self, *, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._task_records.values())
        if status:
            marker = str(status).strip().lower()
            items = [item for item in items if str(item.status).strip().lower() == marker]
        items.sort(key=lambda item: float(item.created_at), reverse=True)
        safe_limit = max(1, min(int(limit or 50), 500))
        return [item.to_public() for item in items[:safe_limit]]


# ------------------------------------------------------------------
# DelegateTaskTool -- allows the principal agent to delegate work
# ------------------------------------------------------------------

class DelegateTaskTool(Tool):
    """Delegate a task to a specialist sub-agent.

    The principal agent uses this tool to spawn a sub-agent turn with a
    specific prompt.  The sub-agent runs in the SUBAGENT lane and the
    result is returned to the principal.
    """

    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        self._orch = orchestrator

    @property
    def name(self) -> str:
        return "delegate_task"

    @property
    def description(self) -> str:
        agents_info = ", ".join(
            f"{a['id']}({a['name']})" for a in self._orch.list_agents()
        )
        return (
            f"Delegate a task to a specialist sub-agent (execute or review_execute mode). "
            f"Available agents: {agents_info or 'none registered'}. "
            f"Provide the agent_id and a detailed task description."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID of the sub-agent to delegate to.",
                },
                "task": {
                    "type": "string",
                    "description": "Detailed task description for the sub-agent.",
                },
                "session_key": {
                    "type": "string",
                    "description": "Optional session key for context isolation.",
                },
                "mode": {
                    "type": "string",
                    "description": "Execution mode: execute | review_execute. Defaults to execute.",
                },
                "reviewer_agent_id": {
                    "type": "string",
                    "description": "Optional reviewer agent for review_execute mode.",
                },
                "review_instructions": {
                    "type": "string",
                    "description": "Optional extra review constraints for reviewer agent.",
                },
            },
            "required": ["agent_id", "task"],
        }

    async def execute(
        self,
        agent_id: str = "",
        task: str = "",
        session_key: str = "",
        mode: str = "execute",
        reviewer_agent_id: str = "",
        review_instructions: str = "",
        **kwargs: Any,
    ) -> str:
        if not agent_id or not task:
            return "Error [DELEGATE_ARGS_REQUIRED]: both agent_id and task are required."

        mode = str(mode or "execute").strip().lower()
        if mode not in {"execute", "review_execute"}:
            return "Error [DELEGATE_MODE_INVALID]: mode must be one of execute|review_execute."

        agents = {a["id"] for a in self._orch.list_agents()}
        if agent_id not in agents:
            return (
                f"Error [DELEGATE_AGENT_UNKNOWN]: unknown agent '{agent_id}'. "
                f"Available: {', '.join(sorted(agents))}"
            )

        try:
            worker_result = await self._orch.run_agent_turn(
                agent_id, task,
                session_key=session_key or None,
                lane=CommandLane.SUBAGENT,
            )
            if mode == "execute":
                return f"[Sub-agent {agent_id} completed]\n{worker_result}"

            reviewer = str(reviewer_agent_id or "").strip()
            if not reviewer:
                for candidate in sorted(agents):
                    if candidate != agent_id:
                        reviewer = candidate
                        break
            if not reviewer:
                return (
                    "Error [DELEGATE_REVIEWER_MISSING]: review_execute mode requires a second available agent."
                )
            if reviewer not in agents:
                return (
                    f"Error [DELEGATE_REVIEWER_UNKNOWN]: unknown reviewer '{reviewer}'. "
                    f"Available: {', '.join(sorted(agents))}"
                )

            review_prompt = (
                "You are the reviewer agent. Review delegated execution output.\n"
                "Return concise markdown with sections:\n"
                "## Verdict\n## Issues\n## Improved Answer\n## Next Actions\n\n"
                f"Original Task:\n{task}\n\n"
                f"Worker Output ({agent_id}):\n{worker_result}\n"
            )
            if review_instructions:
                review_prompt += f"\nAdditional Review Instructions:\n{review_instructions}\n"

            review_result = await self._orch.run_agent_turn(
                reviewer,
                review_prompt,
                session_key=f"{session_key}:review" if session_key else None,
                lane=CommandLane.SUBAGENT,
            )
            return (
                f"[Sub-agent {agent_id} completed]\n{worker_result}\n\n"
                f"[Reviewer {reviewer}]\n{review_result}"
            )
        except Exception as exc:
            return f"Error [DELEGATE_EXECUTION_FAILED]: Error delegating to {agent_id}: {exc}"
