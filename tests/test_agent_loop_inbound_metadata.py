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
    def __init__(self):
        self.last_messages = []

    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.last_messages = messages
        return LLMResponse(content="ok", tool_calls=[])


class _FakeTrajectoryStore:
    def __init__(self) -> None:
        self.events = []

    def start(self, **kwargs):
        return "traj_test_1"

    def add_event(self, run_id, *, stage, action, payload):
        self.events.append({"run_id": run_id, "stage": stage, "action": action, "payload": payload})

    def finalize(self, run_id, *, status, final_content, usage=None, metrics=None):
        return None


@pytest.mark.asyncio
async def test_agent_loop_injects_inbound_metadata_note_and_records_trajectory(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "tool_max_tier": "safe",
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

    provider = _Provider()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    store = _FakeTrajectoryStore()
    loop.trajectory_store = store

    msg = InboundMessage(
        channel="feishu",
        sender_id="ou_x",
        chat_id="ou_x",
        content="[User sent an audio clip]",
        metadata={
            "feishu_message_id": "om_xxx",
            "feishu_message_type": "audio",
            "feishu_media": [{"path": "data/media/feishu_audio_1.mp3", "message_type": "audio"}],
        },
    )
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "ok"
    system_notes = [m.get("content", "") for m in provider.last_messages if m.get("role") == "system"]
    assert any("Inbound Media Context" in str(note) for note in system_notes)
    assert any(event["action"] == "inbound_metadata" for event in store.events)
