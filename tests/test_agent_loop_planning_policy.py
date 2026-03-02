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


def test_should_plan_off_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig({"agents": {"defaults": {"planning": {"mode": "off"}}}}),
    )
    assert AgentLoop._should_plan("请帮我一步一步分析这个系统设计", history_len=20) is False


def test_should_plan_always_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig({"agents": {"defaults": {"planning": {"mode": "always"}}}}),
    )
    assert AgentLoop._should_plan("hi", history_len=0) is True


def test_should_plan_auto_mode_by_structured_text(monkeypatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "agents": {
                    "defaults": {
                        "planning": {
                            "mode": "auto",
                            "auto": {
                                "min_message_chars": 220,
                                "min_history_messages": 8,
                                "min_line_breaks": 2,
                                "min_list_lines": 2,
                            },
                        }
                    }
                }
            }
        ),
    )
    text = "1. first\n2. second\n3. third"
    assert AgentLoop._should_plan(text, history_len=0) is True


def test_should_plan_auto_mode_by_history_pressure(monkeypatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "agents": {
                    "defaults": {
                        "planning": {
                            "mode": "auto",
                            "auto": {
                                "min_message_chars": 999,
                                "min_history_messages": 4,
                                "min_line_breaks": 99,
                                "min_list_lines": 99,
                            },
                        }
                    }
                }
            }
        ),
    )
    assert AgentLoop._should_plan("短句", history_len=5) is True
    assert AgentLoop._should_plan("短句", history_len=1) is False
