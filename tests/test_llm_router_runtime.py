import pytest

from llm.router import list_router_strategy_templates, resolve_router_strategy_template
from tools.admin import api_facade as admin_api


class _FakeConfig:
    def __init__(self):
        self.set_many_calls = []

    def set_many(self, updates: dict) -> None:
        self.set_many_calls.append(dict(updates))

    def set(self, key_path: str, value):
        self.set_many_calls.append({key_path: value})


def test_router_strategy_templates_are_available():
    templates = list_router_strategy_templates()
    assert "cost_first" in templates
    assert "latency_first" in templates
    assert "availability_first" in templates

    cost_first = resolve_router_strategy_template("cost_first")
    assert cost_first["strategy"] == "priority"
    assert cost_first["budget"]["enabled"] is True


def test_router_strategy_template_rejects_unknown_name():
    with pytest.raises(ValueError):
        resolve_router_strategy_template("unknown_template")


@pytest.mark.asyncio
async def test_admin_router_strategy_supports_template(monkeypatch):
    class _Router:
        def __init__(self):
            self.strategy = "priority"
            self.budget = {}
            self.outlier = {}

        def set_strategy(self, strategy: str):
            self.strategy = strategy

        def set_budget_policy(self, policy: dict):
            self.budget = dict(policy)

        def set_outlier_policy(self, policy: dict):
            self.outlier = dict(policy)

        def get_status(self):
            return {
                "strategy": self.strategy,
                "budget": self.budget,
                "outlier_ejection": self.outlier,
            }

    fake_cfg = _FakeConfig()
    monkeypatch.setattr("tools.admin.state.LLM_ROUTER", _Router())
    monkeypatch.setattr("tools.admin.state.get_app_context", lambda: None)
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    result = await admin_api.set_llm_router_strategy({"template": "latency_first"})
    assert result["status"] == "ok"
    assert result["strategy"] == "latency"
    assert result["template"] == "latency_first"

    updates = fake_cfg.set_many_calls[-1]
    assert updates["models.router.strategy"] == "latency"
    assert updates["models.router.strategy_template"] == "latency_first"
    assert "models.router.budget" in updates
    assert "models.router.outlier_ejection" in updates
