from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from tools.admin import gateway as gateway_module
from tools.admin import websockets as websockets_module


class _DisconnectingWebSocket:
    def __init__(self, *, session_id: str = "web-main") -> None:
        self.query_params = {"session_id": session_id}
        self.headers = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.state = SimpleNamespace(is_owner=True)
        self.sent_messages = []

    async def receive_text(self) -> str:
        raise WebSocketDisconnect()

    async def send_json(self, payload):
        self.sent_messages.append(payload)


@pytest.mark.asyncio
async def test_chat_websocket_disconnect_runs_cleanup(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    async def _allow(_websocket) -> bool:
        return True

    async def _connect(_websocket, chat_id: str) -> None:
        events.append(("connect", chat_id))

    def _disconnect(_websocket, chat_id: str) -> None:
        events.append(("disconnect", chat_id))

    monkeypatch.setattr(websockets_module, "_verify_ws_auth", _allow)
    monkeypatch.setattr(websockets_module.chat_manager, "connect", _connect)
    monkeypatch.setattr(websockets_module.chat_manager, "disconnect", _disconnect)

    websocket = _DisconnectingWebSocket(session_id="sess-1")
    await websockets_module.chat_endpoint(websocket)

    assert ("connect", "sess-1") in events
    assert ("disconnect", "sess-1") in events


@pytest.mark.asyncio
async def test_gateway_websocket_disconnect_runs_cleanup(monkeypatch) -> None:
    fake_client = object()
    disconnected = []

    async def _allow(_websocket) -> bool:
        return True

    async def _connect(_websocket):
        return fake_client

    def _disconnect(client) -> None:
        disconnected.append(client)

    monkeypatch.setattr(gateway_module, "_verify_ws_auth", _allow)
    monkeypatch.setattr(gateway_module.gateway_manager, "connect", _connect)
    monkeypatch.setattr(gateway_module.gateway_manager, "disconnect", _disconnect)

    websocket = _DisconnectingWebSocket()
    await gateway_module.gateway_endpoint(websocket)

    assert fake_client in disconnected
