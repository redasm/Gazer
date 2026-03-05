import re

with open('tests/test_workflow_graph_api.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace block of _shared monkeypatches
old_block = r'''    monkeypatch\.setattr\(_shared, "_WORKFLOW_GRAPH_DIR", graph_dir\)
    monkeypatch\.setattr\(_shared, "TOOL_REGISTRY", _FakeToolRegistry\(\)\)
    monkeypatch\.setattr\(_shared, "LLM_ROUTER", _FakeRouter\(\)\)'''

new_block = '''    monkeypatch.setattr("tools.admin.workflow_helpers._WORKFLOW_GRAPH_DIR", graph_dir)
    monkeypatch.setattr("tools.admin.workflows._WORKFLOW_GRAPH_DIR", graph_dir)
    fake_tr = _FakeToolRegistry()
    fake_router = _FakeRouter()
    monkeypatch.setattr("tools.admin.workflow_helpers.TOOL_REGISTRY", fake_tr)
    monkeypatch.setattr("tools.admin.workflow_helpers.LLM_ROUTER", fake_router)
    monkeypatch.setattr("tools.admin.workflows.TOOL_REGISTRY", fake_tr)
    monkeypatch.setattr("tools.admin.workflows.LLM_ROUTER", fake_router)'''

text = re.sub(old_block, new_block, text)

# _workflow_run_history replaces
text = text.replace(
    'monkeypatch.setattr(_shared, "_workflow_run_history", dq)',
    'monkeypatch.setattr("tools.admin.workflows._workflow_run_history", dq)\n    monkeypatch.setattr("tools.admin.strategy_helpers._workflow_run_history", dq)\n    monkeypatch.setattr("tools.admin.system._workflow_run_history", dq)\n    monkeypatch.setattr("tools.admin.observability._workflow_run_history", dq)'
)

text = text.replace(
    'monkeypatch.setattr(system, "_workflow_run_history", dq)',
    '# removed monkeypatch(system, history)'
)

text = text.replace(
    'monkeypatch.setattr(_shared, "LLM_ROUTER", None)',
    'monkeypatch.setattr("tools.admin.workflow_helpers.LLM_ROUTER", None)\n    monkeypatch.setattr("tools.admin.workflows.LLM_ROUTER", None)'
)

text = text.replace(
    'monkeypatch.setattr(observability, "get_llm_router", lambda: None)',
    'monkeypatch.setattr("tools.admin.observability.get_llm_router", lambda: None)'
)

with open('tests/test_workflow_graph_api.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("PATCH COMPLETED")
