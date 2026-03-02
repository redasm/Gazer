from bus.events import InboundMessage
from llm.base import LLMProvider, LLMResponse
from agent.adapter import GazerAgent


class _StubProvider(LLMProvider):
    def __init__(self, name: str):
        super().__init__()
        self._name = name

    def get_default_model(self) -> str:
        return f"{self._name}-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return LLMResponse(content=f"{self._name}:ok", finish_reason="stop", error=False)


class _OwnerManager:
    def __init__(self, owner_pairs):
        self._pairs = set(owner_pairs)

    def is_owner_sender(self, channel: str, sender_id: str) -> bool:
        return (channel, sender_id) in self._pairs


def _msg(channel: str, sender_id: str) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id="chat-main",
        content="hello",
    )


def test_router_rollout_disabled_allows_all(monkeypatch):
    agent = GazerAgent.__new__(GazerAgent)
    agent.router = object()
    agent._router_rollout = {"enabled": False}
    monkeypatch.setattr("agent.adapter.get_owner_manager", lambda: _OwnerManager(set()))

    assert agent._is_router_allowed_for_context(channel="discord", sender_id="user-a") is True
    assert agent._is_router_allowed_for_context(channel="", sender_id="") is True


def test_router_rollout_owner_only(monkeypatch):
    agent = GazerAgent.__new__(GazerAgent)
    agent.router = object()
    agent._router_rollout = {"enabled": True, "owner_only": True, "channels": []}
    monkeypatch.setattr(
        "agent.adapter.get_owner_manager",
        lambda: _OwnerManager({("feishu", "owner-1")}),
    )

    assert agent._is_router_allowed_for_context(channel="feishu", sender_id="owner-1") is True
    assert agent._is_router_allowed_for_context(channel="feishu", sender_id="user-2") is False


def test_router_rollout_channel_allowlist(monkeypatch):
    agent = GazerAgent.__new__(GazerAgent)
    agent.router = object()
    agent._router_rollout = {"enabled": True, "owner_only": False, "channels": ["web", "telegram"]}
    monkeypatch.setattr("agent.adapter.get_owner_manager", lambda: _OwnerManager(set()))

    assert agent._is_router_allowed_for_context(channel="web", sender_id="u1") is True
    assert agent._is_router_allowed_for_context(channel="discord", sender_id="u1") is False


def test_resolve_provider_uses_fallback_when_rollout_blocks(monkeypatch):
    router = _StubProvider("router")
    fallback = _StubProvider("fallback")
    agent = GazerAgent.__new__(GazerAgent)
    agent.router = router
    agent._router_fallback_provider = fallback
    agent._router_rollout = {"enabled": True, "owner_only": True, "channels": []}
    monkeypatch.setattr(
        "agent.adapter.get_owner_manager",
        lambda: _OwnerManager({("feishu", "owner-1")}),
    )

    blocked_msg = _msg("feishu", "user-2")
    owner_msg = _msg("feishu", "owner-1")

    assert agent._resolve_slow_provider_for_message(blocked_msg, router) is fallback
    assert agent._resolve_slow_provider_for_message(owner_msg, fallback) is router
