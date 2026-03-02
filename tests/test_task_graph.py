"""Tests for multi_agent.task_graph."""

import asyncio

import pytest

from multi_agent.models import Task, TaskPriority, TaskStatus
from multi_agent.task_graph import TaskGraph


@pytest.fixture
def graph():
    return TaskGraph()


@pytest.mark.asyncio
class TestTaskGraphBasic:
    async def test_add_and_retrieve(self, graph: TaskGraph):
        t = Task(task_id="t1", name="alpha")
        await graph.add_task(t)
        assert graph.get_task("t1") is t
        assert len(graph.tasks) == 1

    async def test_duplicate_task_id_raises(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="a"))
        with pytest.raises(ValueError, match="Duplicate"):
            await graph.add_task(Task(task_id="t1", name="b"))

    async def test_add_tasks_batch(self, graph: TaskGraph):
        tasks = [Task(task_id=f"t{i}", name=f"task-{i}") for i in range(5)]
        await graph.add_tasks(tasks)
        assert len(graph.tasks) == 5


@pytest.mark.asyncio
class TestReadyTasks:
    async def test_no_deps_immediately_ready(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="root"))
        ready = graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t1"

    async def test_deps_not_ready(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="root"))
        await graph.add_task(Task(task_id="t2", name="child", depends_on=["t1"]))
        ready = graph.get_ready_tasks()
        assert [t.task_id for t in ready] == ["t1"]

    async def test_deps_become_ready_after_done(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="root"))
        await graph.add_task(Task(task_id="t2", name="child", depends_on=["t1"]))
        await graph.mark_running("t1", "w1")
        await graph.mark_done("t1", "result")
        ready = graph.get_ready_tasks()
        assert [t.task_id for t in ready] == ["t2"]

    async def test_priority_ordering(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="low", name="low", priority=TaskPriority.LOW))
        await graph.add_task(Task(task_id="crit", name="crit", priority=TaskPriority.CRITICAL))
        await graph.add_task(Task(task_id="norm", name="norm", priority=TaskPriority.NORMAL))
        ready = graph.get_ready_tasks()
        assert [t.task_id for t in ready] == ["crit", "norm", "low"]


@pytest.mark.asyncio
class TestStateTransitions:
    async def test_mark_running(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="x"))
        await graph.mark_running("t1", "w1")
        assert graph.get_task("t1").status == TaskStatus.RUNNING
        assert graph.get_task("t1").assigned_to == "w1"

    async def test_mark_done(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="x"))
        await graph.mark_running("t1", "w1")
        await graph.mark_done("t1", "ok", artifacts={"file": "a.txt"})
        t = graph.get_task("t1")
        assert t.status == TaskStatus.DONE
        assert t.result == "ok"
        assert t.artifacts["file"] == "a.txt"

    async def test_mark_failed_with_retry(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="x", max_retries=2))
        await graph.mark_running("t1", "w1")
        will_retry = await graph.mark_failed("t1", "oops")
        assert will_retry is True
        assert graph.get_task("t1").status in (TaskStatus.PENDING, TaskStatus.READY)

    async def test_mark_failed_no_more_retries(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="x", max_retries=0))
        await graph.mark_running("t1", "w1")
        will_retry = await graph.mark_failed("t1", "fatal")
        assert will_retry is False
        assert graph.get_task("t1").status == TaskStatus.FAILED


@pytest.mark.asyncio
class TestCascadeBlock:
    async def test_downstream_blocked_on_failure(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="root", max_retries=0))
        await graph.add_task(Task(task_id="t2", name="child", depends_on=["t1"]))
        await graph.add_task(Task(task_id="t3", name="grandchild", depends_on=["t2"]))

        await graph.mark_running("t1", "w1")
        await graph.mark_failed("t1", "error")

        assert graph.get_task("t2").status == TaskStatus.BLOCKED
        assert graph.get_task("t3").status == TaskStatus.BLOCKED

    async def test_sibling_not_blocked(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="fail", max_retries=0))
        await graph.add_task(Task(task_id="t2", name="ok"))
        await graph.add_task(Task(task_id="t3", name="child_of_fail", depends_on=["t1"]))

        await graph.mark_running("t1", "w1")
        await graph.mark_failed("t1", "error")

        assert graph.get_task("t2").status == TaskStatus.READY
        assert graph.get_task("t3").status == TaskStatus.BLOCKED


@pytest.mark.asyncio
class TestSubtaskInjection:
    async def test_add_subtasks_replace_parent(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="parent", name="parent"))
        await graph.add_task(Task(task_id="downstream", name="downstream", depends_on=["parent"]))

        sub1 = Task(task_id="sub1", name="sub1")
        sub2 = Task(task_id="sub2", name="sub2")
        await graph.add_subtasks([sub1, sub2], "parent", replace_parent=True)

        agg = graph.get_task("agg_parent")
        assert agg is not None
        assert set(agg.depends_on) == {"sub1", "sub2"}

        ds = graph.get_task("downstream")
        assert "agg_parent" in ds.depends_on

        assert graph.get_task("parent").status == TaskStatus.DONE


@pytest.mark.asyncio
class TestCompletion:
    async def test_is_complete_all_done(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="a"))
        await graph.mark_running("t1", "w1")
        await graph.mark_done("t1", "ok")
        assert graph.is_complete() is True
        assert graph.is_successful() is True

    async def test_is_complete_with_failures(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="a", max_retries=0))
        await graph.mark_running("t1", "w1")
        await graph.mark_failed("t1", "error")
        assert graph.is_complete() is True
        assert graph.is_successful() is False

    async def test_not_complete_while_running(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="a"))
        await graph.mark_running("t1", "w1")
        assert graph.is_complete() is False


@pytest.mark.asyncio
class TestWatchers:
    async def test_watcher_called_on_done(self, graph: TaskGraph):
        calls = []

        async def on_change(task_id, old, new):
            calls.append((task_id, old.value, new.value))

        graph.add_watcher(on_change)
        await graph.add_task(Task(task_id="t1", name="a"))
        await graph.mark_running("t1", "w1")
        await graph.mark_done("t1", "ok")

        assert any(c[0] == "t1" and c[2] == "done" for c in calls)

    async def test_watcher_exception_does_not_crash(self, graph: TaskGraph):
        async def bad_watcher(task_id, old, new):
            raise RuntimeError("watcher boom")

        graph.add_watcher(bad_watcher)
        await graph.add_task(Task(task_id="t1", name="a"))
        await graph.mark_running("t1", "w1")
        await graph.mark_done("t1", "ok")  # Should not raise


@pytest.mark.asyncio
class TestFinalResults:
    async def test_leaf_tasks_collected(self, graph: TaskGraph):
        await graph.add_task(Task(task_id="t1", name="root"))
        await graph.add_task(Task(task_id="t2", name="leaf", depends_on=["t1"]))

        await graph.mark_running("t1", "w1")
        await graph.mark_done("t1", "root-result")
        await graph.mark_running("t2", "w2")
        await graph.mark_done("t2", "leaf-result", result_ref="bb://ref")

        results = graph.get_final_results()
        assert "t2" in results
        assert results["t2"]["result"] == "leaf-result"
