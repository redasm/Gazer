from llm.base import ToolCallRequest
from tools.batching import ToolBatchPlanner
from tools.planner import ToolPlanner


def test_planner_builds_dependency_levels_and_batches():
    planner = ToolPlanner(enabled=True, compact_results=False)
    batch_planner = ToolBatchPlanner(enabled=True, max_batch_size=4, dedupe_enabled=True)
    calls = [
        ToolCallRequest(id="1", name="fetch_a", arguments={"q": "a"}),
        ToolCallRequest(id="2", name="fetch_b", arguments={"depends_on": "1"}),
        ToolCallRequest(id="3", name="fetch_c", arguments={"q": "c"}),
        ToolCallRequest(id="4", name="merge", arguments={"depends_on": ["2", "3"]}),
    ]

    plan = planner.plan(
        calls,
        lane_resolver=lambda _name: "default",
        max_parallel_calls=4,
        batch_planner=batch_planner,
    )

    assert plan.used_dependency_scheduler is True
    assert plan.dependency_edges == 3
    assert plan.cycle_detected is False
    assert plan.dependency_levels == [["1", "3"], ["2"], ["4"]]
    assert [[tc.id for tc in batch] for batch in plan.batch_plan.batches] == [["1", "3"], ["2"], ["4"]]
    assert plan.batch_plan.requested_calls == 4
    assert plan.batch_plan.unique_calls == 4
    assert plan.batch_plan.deduped_calls == 0


def test_planner_marks_cycle_and_falls_back_to_remaining_order():
    planner = ToolPlanner(enabled=True, compact_results=False)
    batch_planner = ToolBatchPlanner(enabled=True, max_batch_size=4, dedupe_enabled=False)
    calls = [
        ToolCallRequest(id="1", name="a", arguments={"depends_on": "2"}),
        ToolCallRequest(id="2", name="b", arguments={"depends_on": "1"}),
    ]

    plan = planner.plan(
        calls,
        lane_resolver=lambda _name: "default",
        max_parallel_calls=4,
        batch_planner=batch_planner,
    )

    assert plan.used_dependency_scheduler is True
    assert plan.cycle_detected is True
    assert [[tc.id for tc in batch] for batch in plan.batch_plan.batches] == [["1", "2"]]


def test_planner_compacts_large_results():
    planner = ToolPlanner(
        enabled=True,
        compact_results=True,
        max_result_chars=120,
        error_max_result_chars=200,
        head_chars=40,
        tail_chars=30,
    )
    content = "x" * 260
    compacted = planner.compact_tool_result(tool_name="web_fetch", result=content)
    assert "[planner_compacted tool=web_fetch" in compacted
    assert len(compacted) < len(content)
