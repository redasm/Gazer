import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("litellm")

import llm.litellm_provider as litellm_provider_module
import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.events import InboundMessage
from bus.queue import MessageBus
from devices.adapters.local_desktop import LocalDesktopNode
from devices.registry import DeviceRegistry
from llm.litellm_provider import LiteLLMProvider
from tools.device_tools import NodeInvokeTool
from tools.media_marker import MEDIA_MARKER


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


class _FakeCaptureManager:
    def get_observe_capability(self):
        return True, ""

    async def get_latest_observation(self, query: str):
        return f"observed: {query}"

    async def get_latest_observation_structured(self, query: str):
        return {"summary": f"observed: {query}", "query": query, "elements": []}


@pytest.mark.asyncio
async def test_e2e_openai_responses_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_ok_1",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="sk-test",
        api_base="https://gmn.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        strict_api_mode=True,
        max_retries=0,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "ping"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=32,
        temperature=0.0,
    )

    assert response.error is False
    assert response.content == "ok"
    assert captured.get("model") == "gpt-5.2"
    assert isinstance(captured.get("input"), list)


@pytest.mark.asyncio
async def test_e2e_html_502_error_surfaces_to_dialog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    html_502 = """
<!DOCTYPE html>
<html lang="en-US">
<head><title>chuangzuoli.com | 502: Bad gateway</title></head>
<body>
<div>Cloudflare Ray ID: <strong>9d2ce7832fdff7ab</strong></div>
</body>
</html>
"""

    async def _fake_aresponses(**_kwargs):
        raise RuntimeError(html_502)

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)
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
                },
                "agents": {
                    "defaults": {
                        "planning": {"mode": "off"},
                    }
                },
            }
        ),
    )
    monkeypatch.setattr(
        "agent.loop.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = LiteLLMProvider(
        api_key="sk-test",
        api_base="https://gmn.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        strict_api_mode=True,
        max_retries=0,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        max_iterations=2,
        context_builder=_DummyContext(),
    )

    out = await loop._process_message(
        InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="没法对话了")
    )
    assert out is not None
    assert "抱歉，我暂时无法得到有效模型回复" in out.content
    assert "Upstream host returned an HTML error page" in out.content
    assert "9d2ce7832fdff7ab" in out.content
    assert "<html" not in out.content.lower()


def test_e2e_screen_screenshot_then_file_send_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    screenshot_path = tmp_path / "screen_1.png"
    screenshot_path.write_bytes(b"png")

    registry = DeviceRegistry(default_target="local-desktop")
    node = LocalDesktopNode(capture_manager=_FakeCaptureManager(), action_enabled=False)
    monkeypatch.setattr(node, "_capture_screenshot_to_file", lambda: (True, str(screenshot_path)))
    registry.register(node)
    tool = NodeInvokeTool(registry)

    screenshot_result = asyncio.run(tool.execute(action="screen.screenshot", args={}))
    assert "Screenshot captured." in screenshot_result
    assert MEDIA_MARKER in screenshot_result
    captured_path = screenshot_result.split(MEDIA_MARKER, 1)[1].strip()
    assert captured_path == str(screenshot_path)

    send_result = asyncio.run(tool.execute(action="file.send", args={"path": captured_path}))
    assert "File prepared for sending." in send_result
    assert MEDIA_MARKER in send_result
    assert str(screenshot_path) in send_result
