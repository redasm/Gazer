import time
from types import SimpleNamespace
from pathlib import Path

from runtime import config_manager
from agent.loop import AgentLoop
from bus.queue import MessageBus
from llm.base import LLMResponse

class _FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        cur = self.data
        for part in str(key_path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

class _DummyContext:
    async def prepare_memory_context(self, _content): pass
    def get_agents_tool_policy_overlay(self): return {}
    def build_messages(self, **kwargs): return []
    def add_assistant_message(self, *a, **k): return []
    def add_tool_result(self, *a, **k): return []

class _Provider:
    def get_default_model(self): return "dummy-model"
    async def chat(self, *a, **k): return LLMResponse(content="ok", tool_calls=[], error=False)

def _build_runtime_cfg():
    return {
        "security": {},
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

config_manager.config = _FakeConfig(_build_runtime_cfg())

class _FakePersonaRuntime:
    def get_latest_signal(self, source=None):
        return {
            "level": "critical",
            "source": "persona_eval",
            "created_at": time.time(),
        }

import soul.persona_runtime as pr
import agent.loop as al
pr.get_persona_runtime_manager = lambda: _FakePersonaRuntime()
al.get_persona_runtime_manager = lambda: _FakePersonaRuntime()

loop = AgentLoop(
    bus=MessageBus(),
    provider=_Provider(),
    workspace=Path("/tmp/foo"),
    context_builder=_DummyContext(),
)

print(config_manager.config.get("personality.runtime"))
policy = loop._resolve_tool_policy()
print("Base policy returned from apply_tool_policy_pipeline_steps:")
print(policy)
print(loop._persona_tool_policy_linkage_status)
