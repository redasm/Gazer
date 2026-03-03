from tools.admin import observability
from tools.admin import _shared
import asyncio
import collections
from pathlib import Path

import pytest

from tools.admin import api_facade as admin_api


class _FakeToolRegistry:
    def __init__(self):
        self._flaky_count = 0

    async def execute(self, name, params, **kwargs):
        if name == "flaky":
            self._flaky_count += 1
            if self._flaky_count == 1:
                raise RuntimeError("flaky failure")
            return "tool:flaky:ok"
        if name == "slow":
            await asyncio.sleep(0.05)
            return "tool:slow:done"
        return f"tool:{name}:{params.get('text', '')}"


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content
        self.error = None


class _FakeRouter:
    async def chat(self, messages, tools=None, **kwargs):
        text = messages[0]["content"] if messages else ""
        return _FakeLLMResponse(f"llm:{text}")


@pytest.mark.asyncio
async def test_workflow_graph_crud_and_run(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "demo_flow",
        "description": "demo",
        "nodes": [
            {"id": "in1", "type": "input", "label": "Input", "config": {"default": ""}, "position": {"x": 10, "y": 20}},
            {"id": "llm1", "type": "prompt", "label": "Prompt", "config": {"prompt": "Q={{prev}}"}},
            {"id": "tool1", "type": "tool", "label": "Tool", "config": {"tool_name": "echo", "args": {"text": "{{prev}}"}}},
            {"id": "out1", "type": "output", "label": "Output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"id": "e1", "source": "in1", "target": "llm1"},
            {"id": "e2", "source": "llm1", "target": "tool1"},
            {"id": "e3", "source": "tool1", "target": "out1"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    assert saved["status"] == "ok"
    workflow_id = saved["workflow"]["id"]
    assert workflow_id

    listed = await admin_api.list_workflow_graphs(limit=20)
    assert listed["status"] == "ok"
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == workflow_id

    loaded = await admin_api.get_workflow_graph(workflow_id)
    assert loaded["status"] == "ok"
    assert loaded["workflow"]["name"] == "demo_flow"

    run_res = await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    assert run_res["status"] == "ok"
    result = run_res["result"]
    assert result["status"] == "ok"
    assert "tool:echo:" in result["output"]
    assert len(result["trace"]) >= 3
    assert isinstance(result.get("metrics"), dict)
    assert result["metrics"]["trace_nodes"] == len(result["trace"])
    assert "total_duration_ms" in result["metrics"]
    assert all("duration_ms" in step for step in result["trace"])

    deleted = await admin_api.delete_workflow_graph(workflow_id)
    assert deleted["status"] == "ok"

    listed_after = await admin_api.list_workflow_graphs(limit=20)
    assert listed_after["total"] == 0


@pytest.mark.asyncio
async def test_workflow_graph_node_enabled_locked_persist_and_skip(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "flags_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}, "enabled": True, "locked": False},
            {"id": "prompt1", "type": "prompt", "config": {"prompt": "Q={{prev}}"}, "enabled": False, "locked": True},
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}, "enabled": True, "locked": False},
        ],
        "edges": [
            {"source": "in1", "target": "prompt1"},
            {"source": "prompt1", "target": "out1"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]
    assert workflow_id
    nodes_by_id = {item["id"]: item for item in saved["workflow"]["nodes"]}
    assert nodes_by_id["prompt1"]["enabled"] is False
    assert nodes_by_id["prompt1"]["locked"] is True

    run_res = await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    assert run_res["status"] == "ok"
    result = run_res["result"]
    assert result["status"] == "ok"
    assert any(step.get("status") == "skipped" and step.get("node_id") == "prompt1" for step in result["trace"])


@pytest.mark.asyncio
async def test_workflow_graph_dag_fanout_merge(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "dag_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "p1", "type": "prompt", "config": {"prompt": "A={{prev}}"}},
            {"id": "p2", "type": "prompt", "config": {"prompt": "B={{prev}}"}},
            {"id": "out1", "type": "output", "config": {"text": "{{node.p1}}|{{node.p2}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "p1"},
            {"source": "in1", "target": "p2"},
            {"source": "p1", "target": "out1"},
            {"source": "p2", "target": "out1"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]

    run_res = await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    result = run_res["result"]
    assert result["status"] == "ok"
    assert result["output"] == "llm:A=hello|llm:B=hello"
    trace_ids = [step.get("node_id") for step in result["trace"]]
    assert "p1" in trace_ids and "p2" in trace_ids


@pytest.mark.asyncio
async def test_workflow_graph_condition_branch_by_edge_when(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "branch_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "cond1", "type": "condition", "config": {"operator": "contains", "value": "yes"}},
            {"id": "out_true", "type": "output", "config": {"text": "TRUE:{{prev}}"}},
            {"id": "out_false", "type": "output", "config": {"text": "FALSE:{{prev}}"}},
            {"id": "final", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "cond1"},
            {"source": "cond1", "target": "out_true", "when": "true"},
            {"source": "cond1", "target": "out_false", "when": "false"},
            {"source": "out_true", "target": "final"},
            {"source": "out_false", "target": "final"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]

    run_yes = await admin_api.run_workflow_graph(workflow_id, {"input": "yes please"})
    result_yes = run_yes["result"]
    assert result_yes["status"] == "ok"
    assert result_yes["output"] == "TRUE:true"

    run_no = await admin_api.run_workflow_graph(workflow_id, {"input": "nope"})
    result_no = run_no["result"]
    assert result_no["status"] == "ok"
    assert result_no["output"] == "FALSE:false"


@pytest.mark.asyncio
async def test_workflow_graph_reject_cycle(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "cycle_flow",
        "nodes": [
            {"id": "a", "type": "input", "config": {"default": ""}},
            {"id": "b", "type": "prompt", "config": {"prompt": "{{prev}}"}},
        ],
        "edges": [
            {"source": "a", "target": "b"},
            {"source": "b", "target": "a"},
        ],
    }

    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.save_workflow_graph(payload)
    assert exc.value.status_code == 400
    assert "cycle" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_workflow_graph_retry_success(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "retry_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "tool1", "type": "tool", "config": {"tool_name": "flaky", "args": {"text": "{{prev}}"}, "retry_count": 1}},
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "tool1"},
            {"source": "tool1", "target": "out1"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]
    run_res = await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    result = run_res["result"]
    assert result["status"] == "ok"
    assert result["output"] == "tool:flaky:ok"
    tool_step = next(step for step in result["trace"] if step.get("node_id") == "tool1")
    assert tool_step["status"] == "ok"
    assert tool_step["attempts_used"] == 2
    assert tool_step["attempts_total"] == 2


@pytest.mark.asyncio
async def test_workflow_graph_timeout_continue(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "timeout_continue_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {
                "id": "tool1",
                "type": "tool",
                "config": {
                    "tool_name": "slow",
                    "args": {"text": "{{prev}}"},
                    "timeout_ms": 1,
                    "retry_count": 0,
                    "on_error": "continue",
                },
            },
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "tool1"},
            {"source": "tool1", "target": "out1"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]
    run_res = await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    result = run_res["result"]
    assert result["status"] == "ok"
    assert result["output"] == "hello"
    tool_step = next(step for step in result["trace"] if step.get("node_id") == "tool1")
    assert tool_step["status"] == "warning"
    assert "timeout" in str(tool_step.get("error", "")).lower()


@pytest.mark.asyncio
async def test_workflow_graph_timeout_fail_fast(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "timeout_fail_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {
                "id": "tool1",
                "type": "tool",
                "config": {
                    "tool_name": "slow",
                    "args": {"text": "{{prev}}"},
                    "timeout_ms": 1,
                    "retry_count": 0,
                    "on_error": "fail",
                },
            },
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "tool1"},
            {"source": "tool1", "target": "out1"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]
    run_res = await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    result = run_res["result"]
    assert result["status"] == "error"
    assert result["failed_node_id"] == "tool1"
    assert isinstance(result.get("metrics"), dict)
    assert result["metrics"]["error_nodes"] >= 1


@pytest.mark.asyncio
async def test_workflow_graph_timeout_fallback_output(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "timeout_fallback_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {
                "id": "tool1",
                "type": "tool",
                "config": {
                    "tool_name": "slow",
                    "args": {"text": "{{prev}}"},
                    "timeout_ms": 1,
                    "retry_count": 0,
                    "on_error": "fallback",
                    "fallback_output": "FALLBACK:{{input}}:{{error}}",
                },
            },
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "tool1"},
            {"source": "tool1", "target": "out1"},
        ],
    }

    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]
    run_res = await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    result = run_res["result"]
    assert result["status"] == "ok"
    assert "FALLBACK:hello:timeout" in result["output"]
    tool_step = next(step for step in result["trace"] if step.get("node_id") == "tool1")
    assert tool_step["status"] == "warning"


@pytest.mark.asyncio
async def test_workflow_graph_reject_when_on_non_condition(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "invalid_when_non_condition",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "out1", "when": "true"},
        ],
    }

    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.save_workflow_graph(payload)
    assert exc.value.status_code == 400
    assert "only allowed" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_workflow_graph_reject_condition_mixed_tagged_and_untagged(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "invalid_condition_mixed",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "cond1", "type": "condition", "config": {"operator": "contains", "value": "yes"}},
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
            {"id": "out2", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "cond1"},
            {"source": "cond1", "target": "out1", "when": "true"},
            {"source": "cond1", "target": "out2"},
        ],
    }

    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.save_workflow_graph(payload)
    assert exc.value.status_code == 400
    assert "cannot mix tagged" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_workflow_graph_reject_no_reachable_output(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "invalid_no_reachable_output",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "p1", "type": "prompt", "config": {"prompt": "{{prev}}"}},
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "p1"},
        ],
    }

    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.save_workflow_graph(payload)
    assert exc.value.status_code == 400
    assert "no reachable output" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_workflow_graph_observability_metrics_include_workflow(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())
    dq = collections.deque(maxlen=300)
    monkeypatch.setattr(_shared, "_workflow_run_history", dq)
    from tools.admin import system
    monkeypatch.setattr(system, "_workflow_run_history", dq)

    payload = {
        "name": "obs_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "tool1", "type": "tool", "config": {"tool_name": "echo", "args": {"text": "{{prev}}"}}},
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "tool1"},
            {"source": "tool1", "target": "out1"},
        ],
    }
    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]
    await admin_api.run_workflow_graph(workflow_id, {"input": "hello"})
    await admin_api.run_workflow_graph(workflow_id, {"input": "world"})
    monkeypatch.setattr(_shared, "LLM_ROUTER", None)

    metrics = await observability.get_observability_metrics(limit=20)
    assert metrics["status"] == "ok"
    workflow_metrics = metrics.get("workflow", {})
    assert workflow_metrics.get("total_runs", 0) >= 2
    assert "p95_latency_ms" in workflow_metrics
    items = workflow_metrics.get("workflows", [])
    assert any(item.get("workflow_id") == workflow_id for item in items)


@pytest.mark.asyncio
async def test_flowise_import_three_common_flows_and_run(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    cases = [
        {
            "name": "flowise_prompt_output",
            "flowise": {
                "nodes": [
                    {"id": "n_in", "type": "customNode", "data": {"name": "chatInput", "label": "Input"}, "position": {"x": 0, "y": 0}},
                    {
                        "id": "n_prompt",
                        "type": "customNode",
                        "data": {"name": "chatPromptTemplate", "label": "Prompt", "inputs": {"template": "Q={{prev}}"}},
                        "position": {"x": 120, "y": 0},
                    },
                    {"id": "n_out", "type": "customNode", "data": {"name": "chatOutput", "label": "Output", "inputs": {"text": "{{prev}}"}}, "position": {"x": 240, "y": 0}},
                ],
                "edges": [{"source": "n_in", "target": "n_prompt"}, {"source": "n_prompt", "target": "n_out"}],
            },
            "expected": "llm:Q=hello",
        },
        {
            "name": "flowise_prompt_tool_output",
            "flowise": {
                "nodes": [
                    {"id": "n_in", "type": "customNode", "data": {"name": "chatInput", "label": "Input"}, "position": {"x": 0, "y": 0}},
                    {
                        "id": "n_prompt",
                        "type": "customNode",
                        "data": {"name": "chatPromptTemplate", "label": "Prompt", "inputs": {"template": "Ask={{prev}}"}},
                        "position": {"x": 120, "y": 0},
                    },
                    {
                        "id": "n_tool",
                        "type": "customNode",
                        "data": {"name": "tool", "label": "Tool", "inputs": {"toolName": "echo"}},
                        "position": {"x": 240, "y": 0},
                    },
                    {"id": "n_out", "type": "customNode", "data": {"name": "chatOutput", "label": "Output", "inputs": {"text": "{{prev}}"}}, "position": {"x": 360, "y": 0}},
                ],
                "edges": [
                    {"source": "n_in", "target": "n_prompt"},
                    {"source": "n_prompt", "target": "n_tool"},
                    {"source": "n_tool", "target": "n_out"},
                ],
            },
            "expected": "tool:echo:",
        },
        {
            "name": "flowise_condition_branch",
            "flowise": {
                "nodes": [
                    {"id": "n_in", "type": "customNode", "data": {"name": "chatInput", "label": "Input"}, "position": {"x": 0, "y": 0}},
                    {
                        "id": "n_cond",
                        "type": "customNode",
                        "data": {"name": "ifElse", "label": "Cond", "inputs": {"operator": "contains", "value": "yes"}},
                        "position": {"x": 120, "y": 0},
                    },
                    {"id": "n_t", "type": "customNode", "data": {"name": "chatOutput", "label": "T", "inputs": {"text": "TRUE:{{prev}}"}}, "position": {"x": 240, "y": -40}},
                    {"id": "n_f", "type": "customNode", "data": {"name": "chatOutput", "label": "F", "inputs": {"text": "FALSE:{{prev}}"}}, "position": {"x": 240, "y": 40}},
                ],
                "edges": [
                    {"source": "n_in", "target": "n_cond"},
                    {"source": "n_cond", "target": "n_t", "label": "true"},
                    {"source": "n_cond", "target": "n_f", "label": "false"},
                ],
            },
            "expected": "TRUE:true",
        },
    ]

    for case in cases:
        imported = await admin_api.import_flowise_workflow({"flowise": case["flowise"], "name": case["name"], "strict": True})
        assert imported["status"] == "ok"
        assert imported["error_count"] == 0
        saved = await admin_api.save_workflow_graph(imported["workflow"])
        workflow_id = saved["workflow"]["id"]
        run = await admin_api.run_workflow_graph(workflow_id, {"input": "hello yes"})
        assert run["status"] == "ok"
        assert run["result"]["status"] == "ok"
        assert case["expected"] in run["result"]["output"]


@pytest.mark.asyncio
async def test_flowise_export_and_import_roundtrip(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    payload = {
        "name": "roundtrip_flow",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {"id": "p1", "type": "prompt", "config": {"prompt": "Q={{prev}}"}},
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [{"source": "in1", "target": "p1"}, {"source": "p1", "target": "out1"}],
    }
    saved = await admin_api.save_workflow_graph(payload)
    workflow_id = saved["workflow"]["id"]

    exported = await admin_api.export_flowise_workflow({"workflow_id": workflow_id})
    assert exported["status"] == "ok"
    assert exported["unsupported_count"] == 0

    imported = await admin_api.import_flowise_workflow({"flowise": exported["flowise"], "strict": True})
    assert imported["status"] == "ok"
    assert imported["error_count"] == 0
    run = await admin_api.run_workflow_graph((await admin_api.save_workflow_graph(imported["workflow"]))["workflow"]["id"], {"input": "hello"})
    assert run["result"]["status"] == "ok"
    assert "llm:Q=hello" in run["result"]["output"]


@pytest.mark.asyncio
async def test_flowise_roundtrip_report_default_and_export(tmp_path: Path):
    generated = await admin_api.generate_flowise_roundtrip_report({})
    assert generated["status"] == "ok"
    report = generated["report"]
    assert report["total_cases"] >= 5
    assert report["passed_cases"] >= 5
    assert report["pass_rate"] >= 0.99
    for case in list(report.get("cases", []) or []):
        assert "structure_ok" in case
        assert "semantic_ok" in case
        assert "execution_ok" in case
        assert case["structure_ok"] is True
        assert case["semantic_ok"] is True
        assert case["execution_ok"] is True

    report_path = tmp_path / "flowise_roundtrip_report.md"
    exported = await admin_api.export_flowise_roundtrip_report({"output_path": str(report_path)})
    assert exported["status"] == "ok"
    assert report_path.is_file()
    content = report_path.read_text(encoding="utf-8")
    assert "Flowise Roundtrip Report" in content
    assert "memory_retriever_agent_toolchain" in content
    assert "structure_ok" in content
    assert "semantic_ok" in content
    assert "execution_ok" in content


@pytest.mark.asyncio
async def test_flowise_migration_report_template_and_export(tmp_path: Path):
    payload = {
        "name": "migration_case",
        "flowise": {
            "nodes": [
                {"id": "n_in", "type": "customNode", "data": {"name": "chatInput"}},
                {"id": "n_bad", "type": "customNode", "data": {"name": "unsupportedMysteryNode"}},
                {"id": "n_router", "type": "customNode", "data": {"name": "llmRouterChain", "inputs": {"route": "image"}}},
                {"id": "n_out", "type": "customNode", "data": {"name": "chatOutput"}},
            ],
            "edges": [
                {"source": "n_in", "target": "n_bad"},
                {"source": "n_bad", "target": "n_router"},
                {"source": "n_router", "target": "n_out", "label": "true"},
            ],
        },
    }
    generated = await admin_api.generate_flowise_migration_report(payload)
    assert generated["status"] == "ok"
    report = generated["report"]
    assert report["summary"]["total"] >= 1
    assert isinstance(report["unsupported_nodes"], list)
    assert len(report["unsupported_nodes"]) >= 1
    assert "replacement" in report["unsupported_nodes"][0]
    assert report["unsupported_nodes"][0]["risk_rating"] in {"low", "medium", "high"}
    assert report["unsupported_nodes"][0]["migration_tier"] in {"auto_replace", "manual_review"}
    assert "risk_breakdown" in report
    assert "migration_tier_breakdown" in report

    report_path = tmp_path / "flowise_migration_report.md"
    exported = await admin_api.export_flowise_migration_report(
        {**payload, "output_path": str(report_path)}
    )
    assert exported["status"] == "ok"
    assert report_path.is_file()
    content = report_path.read_text(encoding="utf-8")
    assert "Flowise Migration Report" in content
    assert "risk_rating" in content
    assert "replacement" in content
    assert "migration_tier" in content


@pytest.mark.asyncio
async def test_flowise_import_reports_node_level_errors(monkeypatch, tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    monkeypatch.setattr(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr(_shared, "TOOL_REGISTRY", _FakeToolRegistry())
    monkeypatch.setattr(_shared, "LLM_ROUTER", _FakeRouter())

    flowise_payload = {
        "nodes": [
            {"id": "n_in", "type": "customNode", "data": {"name": "chatInput"}},
            {"id": "n_bad", "type": "customNode", "data": {"name": "unsupportedMysteryNode"}},
            {"id": "n_out", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "{{prev}}"}}},
        ],
        "edges": [
            {"source": "n_in", "target": "n_bad"},
            {"source": "n_bad", "target": "n_out"},
            {"source": "n_in", "target": "n_out"},
        ],
    }

    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.import_flowise_workflow({"flowise": flowise_payload, "strict": True})
    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert "errors" in detail
    assert "summary" in detail
    assert detail["summary"]["total"] >= 1
    assert detail["summary"]["node"] >= 1
    assert "validation" in detail
    assert detail["validation"]["ok"] is False
    assert detail["validation"]["code"] in {"interop_errors", "workflow_invalid", "no_reachable_output", "dag_cycle", "condition_edges_invalid"}
    assert any(item.get("node_id") == "n_bad" for item in detail["errors"])
    assert all("level" in item and "code" in item for item in detail["errors"])

    non_strict = await admin_api.import_flowise_workflow({"flowise": flowise_payload, "strict": False})
    assert non_strict["status"] == "ok"
    assert non_strict["error_count"] >= 1
    assert non_strict["summary"]["total"] >= 1
    assert non_strict["summary"]["node"] >= 1
    assert non_strict["validation"]["ok"] is True
    assert non_strict["validation"]["checks"]["dag"] is True
