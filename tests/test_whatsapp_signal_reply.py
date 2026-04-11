from types import SimpleNamespace

import pytest

from channels.signal_channel import SignalChannel
from channels.whatsapp import WhatsAppChannel


class _AsyncResponse:
    def __init__(self, status_code=200, *, payload=None, text="ok", content=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = content

    def json(self):
        return self._payload


class _AsyncHttpClient:
    def __init__(self):
        self.calls = []
        self.next_get = _AsyncResponse(status_code=200, payload=[])

    async def post(self, *args, **kwargs):
        self.calls.append(("post", args, kwargs))
        return _AsyncResponse(status_code=200, payload={"messages": [{"id": "mid"}]})

    async def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return self.next_get


@pytest.mark.asyncio
async def test_whatsapp_inbound_sets_generic_reply_to():
    captured = {}
    channel = object.__new__(WhatsAppChannel)

    async def _fake_publish(**kwargs):
        captured.update(kwargs)

    async def _fake_mark_read(*_args, **_kwargs):
        return None

    channel.publish = _fake_publish
    channel._mark_read = _fake_mark_read

    await channel._process_inbound(
        {"from": "user-1", "type": "text", "id": "wamid-123", "text": {"body": "hello"}},
        {"user-1": "User"},
    )

    assert captured["metadata"]["reply_to"] == "wamid-123"


@pytest.mark.asyncio
async def test_whatsapp_send_text_includes_context_message_id():
    channel = object.__new__(WhatsAppChannel)
    channel.phone_number_id = "123"
    channel.api_version = "v21.0"
    channel._http = _AsyncHttpClient()

    await channel._send_text("user-1", "reply", reply_to="wamid-123")

    payload = channel._http.calls[0][2]["json"]
    assert payload["context"]["message_id"] == "wamid-123"


@pytest.mark.asyncio
async def test_signal_receive_sets_reply_metadata():
    published = []
    channel = object.__new__(SignalChannel)
    channel.phone_number = "+200"
    channel.api_url = "http://signal.example"

    async def _fake_publish(**kwargs):
        published.append(kwargs)

    channel.publish = _fake_publish
    channel._download_attachment = lambda *_args, **_kwargs: None
    channel._http = _AsyncHttpClient()
    channel._http.next_get = _AsyncResponse(
        status_code=200,
        payload=[
            {
                "envelope": {
                    "sourceNumber": "+100",
                    "dataMessage": {
                        "message": "hello",
                        "timestamp": 1234567890,
                        "attachments": [],
                    },
                }
            }
        ],
    )

    await channel._receive_messages()

    assert published[0]["metadata"]["reply_to"] == "1234567890"
    assert published[0]["metadata"]["signal_quote_author"] == "+100"


@pytest.mark.asyncio
async def test_signal_send_text_includes_quote_fields():
    channel = object.__new__(SignalChannel)
    channel.phone_number = "+200"
    channel.api_url = "http://signal.example"
    channel._http = _AsyncHttpClient()

    await channel._send_text(
        "+100",
        "reply",
        quote_timestamp="1234567890",
        metadata={"signal_quote_author": "+100", "signal_quote_message": "hello"},
    )

    payload = channel._http.calls[0][2]["json"]
    assert payload["quote_timestamp"] == "1234567890"
    assert payload["quote_author"] == "+100"
    assert payload["quote_message"] == "hello"
