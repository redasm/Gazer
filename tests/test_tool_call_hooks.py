import runtime.config_manager as config_manager
import agent.tool_call_hooks as tool_call_hooks_module
from agent.tool_call_hooks import ToolCallHookManager


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


def test_before_tool_call_blocks_repeated_identical_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_call_hooks": {
                        "enabled": True,
                        "loop_detection_enabled": True,
                        "loop_max_repeats": 1,
                        "loop_window_seconds": 120,
                        "session_max_events": 32,
                    }
                }
            }
        ),
    )
    manager = ToolCallHookManager()

    first = manager.before_tool_call(session_key="s1", tool_name="web_search", params={"q": "gazer"})
    second = manager.before_tool_call(session_key="s1", tool_name="web_search", params={"q": "gazer"})

    assert first is None
    assert isinstance(second, dict)
    assert second.get("code") == "TOOL_LOOP_BLOCKED"


def test_before_tool_call_no_block_when_hook_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_call_hooks": {
                        "enabled": False,
                        "loop_detection_enabled": True,
                        "loop_max_repeats": 1,
                    }
                }
            }
        ),
    )
    manager = ToolCallHookManager()

    assert manager.before_tool_call(session_key="s2", tool_name="web_search", params={"q": "gazer"}) is None
    assert manager.before_tool_call(session_key="s2", tool_name="web_search", params={"q": "gazer"}) is None


def test_before_tool_call_evicts_stale_sessions(monkeypatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_call_hooks": {
                        "enabled": True,
                        "loop_detection_enabled": True,
                        "loop_max_repeats": 3,
                        "loop_window_seconds": 1,
                        "session_max_events": 32,
                    }
                }
            }
        ),
    )
    now = [0.0]
    monkeypatch.setattr(tool_call_hooks_module.time, "time", lambda: now[0])
    manager = ToolCallHookManager()

    assert manager.before_tool_call(session_key="s1", tool_name="web_search", params={"q": "a"}) is None
    now[0] = 3.0
    assert manager.before_tool_call(session_key="s2", tool_name="web_search", params={"q": "b"}) is None

    status = manager.get_status()
    assert status["active_sessions"] == 1
