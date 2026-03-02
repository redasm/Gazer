import agent.loop as loop_module
import runtime.config_manager as config_manager
from agent.loop import AgentLoop


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key_path: str, default=None):
        current = self.data
        for part in key_path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current


def test_get_tool_governance_limits_prefers_provider_agents_max_concurrent(monkeypatch):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "max_tool_calls_per_turn": 11,
                    "max_parallel_tool_calls": 3,
                }
            }
        ),
    )
    monkeypatch.setattr(
        loop_module.ModelRegistry,
        "resolve_model_ref",
        staticmethod(lambda _profile: ("gmn", "gpt-5.2")),
    )
    monkeypatch.setattr(
        loop_module.ModelRegistry,
        "get_provider_config",
        staticmethod(
            lambda _name: {
                "agents": {
                    "defaults": {
                        "maxConcurrent": 8,
                    }
                }
            }
        ),
    )
    loop = AgentLoop.__new__(AgentLoop)

    turn_limit, parallel_limit = loop._get_tool_governance_limits()

    assert turn_limit == 11
    assert parallel_limit == 8


def test_get_tool_governance_limits_ignores_invalid_provider_agents_value(monkeypatch):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "max_tool_calls_per_turn": 13,
                    "max_parallel_tool_calls": 4,
                }
            }
        ),
    )
    monkeypatch.setattr(
        loop_module.ModelRegistry,
        "resolve_model_ref",
        staticmethod(lambda _profile: ("gmn", "gpt-5.2")),
    )
    monkeypatch.setattr(
        loop_module.ModelRegistry,
        "get_provider_config",
        staticmethod(
            lambda _name: {
                "agents": {
                    "defaults": {
                        "maxConcurrent": "invalid",
                    }
                }
            }
        ),
    )
    loop = AgentLoop.__new__(AgentLoop)

    turn_limit, parallel_limit = loop._get_tool_governance_limits()

    assert turn_limit == 13
    assert parallel_limit == 4
