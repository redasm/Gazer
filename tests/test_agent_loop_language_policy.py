from pathlib import Path
from types import SimpleNamespace

import pytest

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.events import InboundMessage
from bus.queue import MessageBus
from llm.base import LLMResponse


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key_path: str, default=None):
        cur = self.data
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class _DummyContext:
    async def prepare_memory_context(self, _content: str):
        return None

    def build_messages(self, *, history, current_message, media=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message}]

    def add_assistant_message(self, messages, content, tool_calls):
        return [*messages, {"role": "assistant", "content": content, "tool_calls": tool_calls}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return [*messages, {"role": "tool", "content": result, "tool_call_id": tool_call_id, "name": tool_name}]


class _Provider:
    def __init__(self, response: LLMResponse):
        self._response = response

    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return self._response


def _make_loop(monkeypatch, tmp_path, response: LLMResponse) -> AgentLoop:
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_groups": {},
                    "llm_max_retries": 0,
                    "llm_retry_backoff_seconds": 0.0,
                }
            }
        ),
    )
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )
    return AgentLoop(
        bus=MessageBus(),
        provider=_Provider(response),
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )


@pytest.mark.asyncio
async def test_agent_loop_llm_error_uses_chinese_for_chinese_input(monkeypatch, tmp_path):
    loop = _make_loop(monkeypatch, tmp_path, LLMResponse(content="provider_down", tool_calls=[], error=True))
    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="你好，帮我看看")
    )
    assert out is not None
    assert out.content.startswith("抱歉，我暂时无法得到有效模型回复")


@pytest.mark.asyncio
async def test_agent_loop_llm_error_uses_english_for_english_input(monkeypatch, tmp_path):
    loop = _make_loop(monkeypatch, tmp_path, LLMResponse(content="provider_down", tool_calls=[], error=True))
    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="hello, please help")
    )
    assert out is not None
    assert out.content.startswith("Sorry, I couldn't get a valid model response right now.")


@pytest.mark.asyncio
async def test_agent_loop_persona_runtime_guard_can_rewrite(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_groups": {},
                    "llm_max_retries": 0,
                    "llm_retry_backoff_seconds": 0.0,
                },
                "personality": {
                    "runtime": {
                        "enabled": True,
                        "signals": {"enabled": True, "retain": 100},
                        "auto_correction": {
                            "enabled": True,
                            "strategy": "rewrite",
                            "trigger_levels": ["warning", "critical"],
                        },
                    }
                },
            }
        ),
    )
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    class _FakePersonaRuntime:
        def process_output(
            self,
            *,
            content: str,
            source: str,
            run_id: str,
            language: str,
            auto_correct_enabled: bool,
            strategy: str,
            trigger_levels,
            metadata,
            retain: int,
        ):
            return {
                "final_content": "我是 Gazer，你的 AI 伙伴。I am just a generic AI.",
                "signal": {
                    "level": "warning",
                    "violation_count": 1,
                    "violations": ["identity_drift"],
                    "correction_applied": True,
                    "correction_strategy": "rewrite",
                    "drift_score": 0.45,
                },
            }

    monkeypatch.setattr("soul.persona_runtime.get_persona_runtime_manager", lambda: _FakePersonaRuntime())

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_Provider(LLMResponse(content="I am just a generic AI.", tool_calls=[], error=False)),
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="hello")
    )
    assert out is not None
    assert "Gazer" in out.content


