import re
with open('tests/test_workflow_graph_api.py', 'r', encoding='utf-8') as f:
    text = f.read()

def replace_block(m):
    return '''    monkeypatch.setattr("tools.admin.workflow_helpers._WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr("tools.admin.workflows._WORKFLOW_GRAPH_DIR", graph_dir)
    fake_tr = _FakeToolRegistry()
    fake_router = _FakeRouter()
    monkeypatch.setattr("tools.admin.workflow_helpers.TOOL_REGISTRY", fake_tr)
    monkeypatch.setattr("tools.admin.workflow_helpers.LLM_ROUTER", fake_router)
    monkeypatch.setattr("tools.admin.workflows.TOOL_REGISTRY", fake_tr)
    monkeypatch.setattr("tools.admin.workflows.LLM_ROUTER", fake_router)'''

text = re.sub(
    r'    monkeypatch\.setattr\(\"tools\.admin\.workflow_helpers\._WORKFLOW_GRAPH_DIR\", graph_dir\)\s+monkeypatch\.setattr\(\"tools\.admin\.workflow_helpers\.TOOL_REGISTRY\", _FakeToolRegistry\(\)\)\s+monkeypatch\.setattr\(\"tools\.admin\.workflow_helpers\.LLM_ROUTER\", _FakeRouter\(\)\)',
    replace_block, text
)
text = text.replace('monkeypatch.setattr("tools.admin.workflow_helpers._workflow_run_history", dq)', 'monkeypatch.setattr("tools.admin.workflows._workflow_run_history", dq)\n    monkeypatch.setattr("tools.admin.strategy_helpers._workflow_run_history", dq)')
text = text.replace('monkeypatch.setattr("observability", "get_llm_router", lambda: None)', 'monkeypatch.setattr("tools.admin.observability.get_llm_router", lambda: None)')
text = text.replace('monkeypatch.setattr(observability, "get_llm_router", lambda: None)', 'monkeypatch.setattr("tools.admin.observability.get_llm_router", lambda: None)')

with open('tests/test_workflow_graph_api.py', 'w', encoding='utf-8') as f:
    f.write(text)
