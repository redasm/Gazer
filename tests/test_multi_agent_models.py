"""Tests for multi_agent.models."""

import time

import pytest

from multi_agent.models import (
    AgentMessage,
    MessageType,
    Task,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
    WorkerResult,
)


class TestTaskStatus:
    def test_values(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.BLOCKED == "blocked"

    def test_all_statuses_exist(self):
        expected = {"pending", "ready", "running", "done", "failed", "blocked"}
        actual = {s.value for s in TaskStatus}
        assert actual == expected


class TestTaskPriority:
    def test_ordering(self):
        assert TaskPriority.CRITICAL < TaskPriority.HIGH < TaskPriority.NORMAL < TaskPriority.LOW

    def test_sortable(self):
        items = [TaskPriority.LOW, TaskPriority.CRITICAL, TaskPriority.NORMAL]
        assert sorted(items) == [TaskPriority.CRITICAL, TaskPriority.NORMAL, TaskPriority.LOW]


class TestTask:
    def test_defaults(self):
        t = Task(name="test")
        assert t.status == TaskStatus.PENDING
        assert t.priority == TaskPriority.NORMAL
        assert t.max_retries == 2
        assert t.depends_on == []
        assert t.task_id  # auto-generated

    def test_is_ready_no_deps(self):
        t = Task(name="root")
        assert t.is_ready(set()) is True

    def test_is_ready_with_deps(self):
        t = Task(name="child", depends_on=["a", "b"])
        assert t.is_ready({"a"}) is False
        assert t.is_ready({"a", "b"}) is True
        assert t.is_ready({"a", "b", "c"}) is True

    def test_is_ready_wrong_status(self):
        t = Task(name="running", status=TaskStatus.RUNNING)
        assert t.is_ready(set()) is False

    def test_get_dependency_results(self):
        parent = Task(task_id="p1", name="parent", status=TaskStatus.DONE, result="data")
        child = Task(name="child", depends_on=["p1"])
        results = child.get_dependency_results({"p1": parent})
        assert results == {"p1": "data"}

    def test_get_dependency_results_skips_non_done(self):
        parent = Task(task_id="p1", name="parent", status=TaskStatus.RUNNING, result="partial")
        child = Task(name="child", depends_on=["p1"])
        results = child.get_dependency_results({"p1": parent})
        assert results == {}

    def test_delegation_fields(self):
        t = Task(
            name="research",
            objective="find papers",
            output_format="JSON list",
            tool_guidance="use web_search",
            boundaries="do not search code repos",
        )
        assert t.objective == "find papers"
        assert t.boundaries == "do not search code repos"


class TestAgentMessage:
    def test_defaults(self):
        m = AgentMessage(sender_id="w1", content="hello")
        assert m.msg_type == MessageType.INFORM
        assert m.target_id is None
        assert m.ttl_sec == 30.0

    def test_is_expired(self):
        m = AgentMessage(sender_id="w1", content="old", ttl_sec=0.0, created_at=time.time() - 1)
        assert m.is_expired is True

    def test_not_expired(self):
        m = AgentMessage(sender_id="w1", content="fresh", ttl_sec=60.0)
        assert m.is_expired is False

    def test_broadcast_target_is_none(self):
        m = AgentMessage(sender_id="w1", msg_type=MessageType.BROADCAST)
        assert m.target_id is None


class TestWorkerResult:
    def test_defaults(self):
        r = WorkerResult()
        assert r.result == ""
        assert r.need_planner is False
        assert r.spawn_subtasks is False

    def test_with_escalation(self):
        r = WorkerResult(need_planner=True, need_planner_reason="too complex")
        assert r.need_planner is True


class TestTaskComplexity:
    def test_values(self):
        assert TaskComplexity.LOW == "low"
        assert TaskComplexity.HIGH == "high"
