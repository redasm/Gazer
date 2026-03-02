from types import SimpleNamespace

import pytest

from bus.events import OutboundMessage, TypingEvent
from channels.feishu import FeishuChannel


class _FakeResponse:
    def __init__(self, ok: bool = True, message_id: str = "") -> None:
        self._ok = ok
        self.code = 0
        self.msg = "ok"
        self.data = SimpleNamespace(message_id=message_id)

    def success(self) -> bool:
        return self._ok


class _FakeMessageAPI:
    def __init__(self) -> None:
        self.create_calls = []
        self.delete_calls = []

    def create(self, request):
        self.create_calls.append(request)
        return _FakeResponse(ok=True, message_id="typing-mid-1")

    def delete(self, request):
        self.delete_calls.append(request)
        return _FakeResponse(ok=True)


class _FakeConfig:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def get(self, path: str, default=None):
        cur = self._payload
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def _build_channel(fake_message_api: _FakeMessageAPI) -> FeishuChannel:
    ch = object.__new__(FeishuChannel)
    ch.client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=fake_message_api)),
    )
    ch._typing_last_sent_at = {}
    ch._typing_status_message_ids = {}
    return ch


@pytest.mark.asyncio
async def test_feishu_typing_sends_status_and_records_message_id(monkeypatch):
    import channels.feishu as feishu_mod

    fake_cfg = _FakeConfig(
        {
            "feishu": {
                "simulated_typing": {
                    "enabled": True,
                    "text": "正在思考中...",
                    "min_interval_seconds": 1,
                    "auto_recall_on_reply": True,
                }
            }
        }
    )
    monkeypatch.setattr(feishu_mod, "config", fake_cfg)

    message_api = _FakeMessageAPI()
    ch = _build_channel(message_api)
    await ch._on_typing(TypingEvent(channel="feishu", chat_id="ou_xxx", is_typing=True))

    assert len(message_api.create_calls) == 1
    assert ch._typing_status_message_ids.get("ou_xxx") == "typing-mid-1"


@pytest.mark.asyncio
async def test_feishu_reply_auto_recalls_typing_status(monkeypatch):
    import channels.feishu as feishu_mod

    fake_cfg = _FakeConfig(
        {
            "feishu": {
                "simulated_typing": {
                    "enabled": True,
                    "text": "正在思考中...",
                    "min_interval_seconds": 1,
                    "auto_recall_on_reply": True,
                }
            }
        }
    )
    monkeypatch.setattr(feishu_mod, "config", fake_cfg)

    message_api = _FakeMessageAPI()
    ch = _build_channel(message_api)
    ch._typing_status_message_ids["ou_xxx"] = "typing-mid-1"

    await ch.send(OutboundMessage(channel="feishu", chat_id="ou_xxx", content="final answer"))

    assert len(message_api.create_calls) == 1
    assert len(message_api.delete_calls) == 1
    assert "ou_xxx" not in ch._typing_status_message_ids
