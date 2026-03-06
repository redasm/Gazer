"""Core data models for the Multi-Agent Collaboration System.

Pure dataclasses with no external dependencies. Defines the vocabulary
shared across TaskGraph, Workers, Planner, and communication layers.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolPolicy


# ------------------------------------------------------------------
# Task status & priority
# ------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_PLANNER = "waiting_planner"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


class TaskComplexity(str, Enum):
    """Used by Worker to decide fast-brain vs slow-brain routing."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class MultiAgentExecutionContext:
    """Per-session execution context propagated from the parent agent turn."""

    tool_policy: "ToolPolicy | None" = None
    sender_id: str = ""
    channel: str = ""
    model_provider: str = ""
    model_name: str = ""
    session_key: str = ""


# ------------------------------------------------------------------
# Task
# ------------------------------------------------------------------

def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Task:
    """A single unit of work in the task DAG."""

    task_id: str = field(default_factory=_short_uuid)
    name: str = ""
    description: str = ""

    # DAG structure
    depends_on: list[str] = field(default_factory=list)

    # Execution config
    priority: TaskPriority = TaskPriority.NORMAL
    max_retries: int = 2
    timeout_sec: float = 120.0
    instruction: str = ""
    required_skills: list[str] = field(default_factory=list)
    allow_subtask_spawn: bool = True

    # Planner delegation quality fields (Anthropic best-practice)
    objective: str = ""
    output_format: str = ""
    tool_guidance: str = ""
    boundaries: str = ""

    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: str | None = None
    retry_count: int = 0
    result: Any = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    result_ref: str = ""

    # Timestamps
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def is_ready(self, completed_task_ids: set[str]) -> bool:
        """True when all upstream dependencies are satisfied."""
        if self.status != TaskStatus.PENDING:
            return False
        return all(dep in completed_task_ids for dep in self.depends_on)

    def get_dependency_results(self, all_tasks: dict[str, "Task"]) -> dict[str, Any]:
        """Collect results from upstream tasks, keyed by task_id."""
        results: dict[str, Any] = {}
        for dep_id in self.depends_on:
            dep = all_tasks.get(dep_id)
            if dep is not None and dep.status == TaskStatus.DONE:
                results[dep_id] = dep.result
        return results


# ------------------------------------------------------------------
# Agent message (inter-agent communication)
# ------------------------------------------------------------------

class MessageType(str, Enum):
    ASK = "ask"
    INFORM = "inform"
    BROADCAST = "broadcast"
    REPLY = "reply"
    NEED_PLANNER = "need_planner"


@dataclass
class AgentMessage:
    """A single message exchanged between agents via AgentMessageBus."""

    msg_id: str = field(default_factory=_short_uuid)
    sender_id: str = ""
    target_id: str | None = None  # None = broadcast
    msg_type: MessageType = MessageType.INFORM
    content: Any = None
    reply_to: str | None = None
    ttl_sec: float = 30.0
    created_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_sec


# ------------------------------------------------------------------
# Worker output schema
# ------------------------------------------------------------------

@dataclass
class WorkerResult:
    """Structured output from a Worker after executing a task."""

    result: str = ""
    result_ref: str = ""
    spawn_subtasks: bool = False
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    need_planner: bool = False
    need_planner_reason: str = ""
