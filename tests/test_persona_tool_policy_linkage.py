import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.queue import MessageBus
from llm.base import LLMResponse
from tools.admin import api_facade as admin_api


class _FakeConfig:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def get(self, key_path: str, default: Any = None):
        cur: Any = self.data
        for part in str(key_path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set_many(self, patch: Dict[str, Any]) -> None:
        for path, value in patch.items():
            node = self.data
            keys = str(path).split(".")
            for key in keys[:-1]:
                child = node.get(key)
                if not isinstance(child, dict):
                    child = {}
                    node[key] = child
                node = child
            node[keys[-1]] = value

    def save(self) -> None:
        return None


class _DummyContext:
    async def prepare_memory_context(self, _content: str):
        return None

    def get_agents_tool_policy_overlay(self):
        return {}

    def build_messages(self, *, history, current_message, media=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message}]

    def add_assistant_message(self, messages, content, tool_calls):
        return [*messages, {"role": "assistant", "content": content, "tool_calls": tool_calls}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return [*messages, {"role": "tool", "content": result, "tool_call_id": tool_call_id, "name": tool_name}]


class _Provider:
    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return LLMResponse(content="ok", tool_calls=[], error=False)


def _build_runtime_cfg() -> Dict[str, Any]:
    return {
        "security": {
            "tool_groups": {},
            "tool_allowlist": [],
            "tool_denylist": [],
            "tool_allow_providers": [],
            "tool_deny_providers": [],
        },
        "personality": {
            "runtime": {
                "tool_policy_linkage": {
                    "enabled": True,
                    "trigger_levels": ["warning", "critical"],
                    "high_risk_levels": ["critical"],
                    "window_seconds": 1800,
                    "sources": ["persona_eval"],
                    "deny_names_by_level": {
                        "critical": ["exec", "node_invoke", "delegate_task"],
                    },
                    "deny_providers_by_level": {
                        "critical": ["devices", "runtime"],
                    },
                }
            }
        },
    }


def test_persona_tool_policy_linkage_applies_dynamic_deny(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config_manager, "config", _FakeConfig(_build_runtime_cfg()))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    class _FakePersonaRuntime:
        def get_latest_signal(self, source=None):
            return {
                "level": "critical",
                "source": "persona_eval",
                "created_at": time.time(),
            }

    monkeypatch.setattr("soul.persona_runtime.get_persona_runtime_manager", lambda: _FakePersonaRuntime())
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    policy = loop._resolve_tool_policy()
    assert "exec" in policy.deny_names
    assert "node_invoke" in policy.deny_names
    assert "devices" in policy.deny_providers
    status = loop.get_persona_tool_policy_linkage_status()
    assert status["active"] is True
    assert status["reason"] == "active"


def test_persona_tool_policy_linkage_ignores_stale_signal(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config_manager, "config", _FakeConfig(_build_runtime_cfg()))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    class _FakePersonaRuntime:
        def get_latest_signal(self, source=None):
            return {
                "level": "critical",
                "source": "persona_eval",
                "created_at": time.time() - 7200,
            }

    monkeypatch.setattr("soul.persona_runtime.get_persona_runtime_manager", lambda: _FakePersonaRuntime())
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    policy = loop._resolve_tool_policy()
    assert "exec" not in policy.deny_names
    status = loop.get_persona_tool_policy_linkage_status()
    assert status["active"] is False
    assert status["reason"] == "signal_stale"


@pytest.mark.asyncio
async def test_admin_persona_tool_policy_linkage_status_endpoint(monkeypatch):
    fake_cfg = _FakeConfig(_build_runtime_cfg())
    fake_cfg.set_many({"security.tool_denylist": ["web_fetch"]})
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    class _FakePersonaRuntime:
        def get_latest_signal(self, source=None):
            return {
                "level": "critical",
                "source": "persona_eval",
                "created_at": time.time(),
            }

    monkeypatch.setattr(admin_api, "_get_persona_runtime_manager", lambda: _FakePersonaRuntime())
    payload = await admin_api.get_persona_tool_policy_linkage_status(source="persona_eval")
    assert payload["status"] == "ok"
    assert payload["linkage"]["active"] is True
    assert "exec" in payload["linkage"]["policy_overlay"]["deny_names"]
    assert "web_fetch" in payload["effective_policy"]["deny_names"]
