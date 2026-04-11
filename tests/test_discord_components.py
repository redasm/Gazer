from types import SimpleNamespace

import pytest

from bus.events import OutboundMessage
from channels.discord import DiscordChannel


def test_discord_component_command_parsing_button_select_modal():
    channel = DiscordChannel(token="", allowed_guild_ids=[])

    button = SimpleNamespace(data={"custom_id": "gazer_btn::%2Frouter%20show"})
    assert channel._interaction_to_command(button) == "/router show"

    select = SimpleNamespace(data={"custom_id": "gazer_sel::main", "values": ["/router on"]})
    assert channel._interaction_to_command(select) == "/router on"

    modal = SimpleNamespace(
        data={
            "custom_id": "gazer_modal::%2Fmodel%20set%20slow",
            "components": [
                {"components": [{"value": "openai"}, {"value": "gpt-4o-mini"}]},
            ],
        }
    )
    assert channel._interaction_to_command(modal) == "/model set slow\nopenai\ngpt-4o-mini"


def test_discord_component_normalization_filters_unknown_types():
    payload = {
        "components": [
            {"type": "button", "label": "A", "command": "/router"},
            {"type": "select", "options": [{"label": "On", "value": "/router on"}]},
            {"type": "modal", "title": "ignored"},
            "bad",
        ]
    }
    out = DiscordChannel._normalize_components(payload)
    assert len(out) == 2
    assert out[0]["type"] == "button"
    assert out[1]["type"] == "select"


@pytest.mark.asyncio
async def test_discord_send_builds_view_from_components():
    class _FakeView:
        def __init__(self, timeout=None):
            self.timeout = timeout
            self.items = []

        def add_item(self, item):
            self.items.append(item)

    class _FakeButton:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeSelect:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeSelectOption:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeButtonStyle:
        primary = "primary"
        secondary = "secondary"
        success = "success"
        danger = "danger"

    class _FakeDiscord:
        ui = SimpleNamespace(View=_FakeView, Button=_FakeButton, Select=_FakeSelect)
        SelectOption = _FakeSelectOption
        ButtonStyle = _FakeButtonStyle

    class _FakeChannel:
        def __init__(self):
            self.calls = []
            self.partial_messages = []

        async def send(self, *args, **kwargs):
            self.calls.append((args, kwargs))

        def get_partial_message(self, message_id):
            self.partial_messages.append(message_id)
            return f"ref:{message_id}"

    class _FakeClient:
        def __init__(self, ch):
            self._ch = ch

        def get_channel(self, _channel_id):
            return self._ch

    fake_channel = _FakeChannel()
    channel = DiscordChannel(token="", allowed_guild_ids=[])
    channel._discord = _FakeDiscord()
    channel._client = _FakeClient(fake_channel)

    await channel.send(
        OutboundMessage(
            channel="discord",
            chat_id="42",
            content="请选择",
            metadata={
                "components": [
                    {"type": "button", "label": "Router", "command": "/router show", "style": "primary"},
                    {
                        "type": "select",
                        "id": "router",
                        "placeholder": "Choose",
                        "options": [{"label": "On", "value": "/router on"}],
                    },
                ]
            },
        )
    )

    assert len(fake_channel.calls) == 1
    _args, kwargs = fake_channel.calls[0]
    assert kwargs["content"] == "请选择"
    view = kwargs["view"]
    assert len(view.items) == 2


@pytest.mark.asyncio
async def test_discord_send_uses_reference_when_reply_to_present():
    class _FakeChannel:
        def __init__(self):
            self.calls = []
            self.partial_messages = []

        async def send(self, *args, **kwargs):
            self.calls.append((args, kwargs))

        def get_partial_message(self, message_id):
            self.partial_messages.append(message_id)
            return f"ref:{message_id}"

    class _FakeClient:
        def __init__(self, ch):
            self._ch = ch

        def get_channel(self, _channel_id):
            return self._ch

    fake_channel = _FakeChannel()
    channel = DiscordChannel(token="", allowed_guild_ids=[])
    channel._client = _FakeClient(fake_channel)

    await channel.send(
        OutboundMessage(channel="discord", chat_id="42", content="reply", reply_to="99")
    )

    assert fake_channel.partial_messages == [99]
    assert fake_channel.calls[0][1]["reference"] == "ref:99"
