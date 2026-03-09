"""Unified WebSocket Gateway — multiplexes all real-time communication.

Protocol:
    Client → Server (JSON):
        {"type": "chat.send",     "chat_id": "...", "content": "..."}
        {"type": "session.list"}
        {"type": "session.clear",  "session_key": "..."}
        {"type": "config.get",     "path": "..."}
        {"type": "ping"}

    Server → Client (JSON):
        {"type": "chat.stream",    "chat_id": "...", "content": "...", "is_partial": true}
        {"type": "chat.end",       "chat_id": "...", "content": "..."}
        {"type": "chat.typing",    "chat_id": "...", "is_typing": true}
        {"type": "channel.event",  "channel": "...", "event": "..."}
        {"type": "log.entry",      "entry": {...}}
        {"type": "pong"}
        {"type": "error",          "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ._shared import (
    API_QUEUES,
    CANVAS_STATE,
    _MAX_WS_MESSAGE_BYTES,
    _MAX_CHAT_MESSAGE_CHARS,
    logger,
)
from .auth import _verify_ws_auth
from .websockets import _decode_web_media_entries

router = APIRouter(tags=["gateway"])


# ---------------------------------------------------------------------------
# Gateway Connection Manager
# ---------------------------------------------------------------------------

class GatewayClient:
    """Represents a single gateway WebSocket connection."""

    __slots__ = ("ws", "chat_ids", "subscriptions", "connected_at", "is_owner")

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.chat_ids: Set[str] = {"web-main"}  # Default chat session
        self.subscriptions: Set[str] = {"chat", "log", "channel", "canvas"}
        self.connected_at = time.time()
        self.is_owner: bool = getattr(ws.state, "is_owner", False)


class GatewayManager:
    """Manages all gateway WebSocket clients."""

    def __init__(self) -> None:
        self._clients: List[GatewayClient] = []

    async def connect(self, ws: WebSocket) -> GatewayClient:
        await ws.accept()
        client = GatewayClient(ws)
        self._clients.append(client)
        return client

    def disconnect(self, client: GatewayClient) -> None:
        try:
            self._clients.remove(client)
        except ValueError:
            pass

    @property
    def clients(self) -> List[GatewayClient]:
        return list(self._clients)

    async def broadcast(self, message: dict, *, event_type: str = "") -> None:
        """Broadcast to all clients subscribed to the event type."""
        disconnected: List[GatewayClient] = []
        raw = json.dumps(message, default=str, ensure_ascii=False)
        for client in self._clients:
            if event_type and event_type not in client.subscriptions:
                continue
            try:
                await client.ws.send_text(raw)
            except Exception:
                disconnected.append(client)
        for c in disconnected:
            self.disconnect(c)

    async def broadcast_to_chat(self, chat_id: str, message: dict) -> None:
        """Broadcast to clients subscribed to a specific chat session."""
        disconnected: List[GatewayClient] = []
        raw = json.dumps(message, default=str, ensure_ascii=False)
        for client in self._clients:
            if chat_id in client.chat_ids or "*" in client.chat_ids:
                try:
                    await client.ws.send_text(raw)
                except Exception:
                    disconnected.append(client)
        for c in disconnected:
            self.disconnect(c)


gateway_manager = GatewayManager()


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

async def _handle_chat_send(client: GatewayClient, payload: dict) -> None:
    """Handle chat.send — enqueue message to Brain via IPC."""
    content = str(payload.get("content", "")).strip()
    chat_id = str(payload.get("chat_id", "web-main"))
    sender_id = "owner" if client.is_owner else "WebUser"

    # Decode media if present
    media, media_metadata = _decode_web_media_entries(payload)
    metadata = dict(media_metadata)
    extra_meta = payload.get("metadata", {})
    if isinstance(extra_meta, dict):
        metadata.update(extra_meta)

    if not content and media:
        content = "[User sent media]"
    if not content and not media:
        await client.ws.send_json({"type": "error", "message": "Empty message"})
        return
    if len(content) > _MAX_CHAT_MESSAGE_CHARS:
        await client.ws.send_json(
            {"type": "error", "message": f"Message too long (max {_MAX_CHAT_MESSAGE_CHARS} chars)"}
        )
        return

    # Track this chat_id for the client
    client.chat_ids.add(chat_id)

    input_q = API_QUEUES.get("input")
    if input_q:
        input_q.put_nowait({
            "type": "chat",
            "content": content,
            "source": "gateway",
            "chat_id": chat_id,
            "sender_id": sender_id,
            "media": media,
            "metadata": metadata,
        })
        await client.ws.send_json({"type": "chat.ack", "chat_id": chat_id})
    else:
        await client.ws.send_json({"type": "error", "message": "Brain disconnected"})


async def _handle_session_list(client: GatewayClient, payload: dict) -> None:
    """Handle session.list — return available sessions."""
    try:
        from runtime.config_manager import config
        data_dir = config.get("memory.context_backend.data_dir", "data/openviking")
        import os
        sessions_dir = os.path.join(data_dir, "sessions")
        sessions = []
        if os.path.isdir(sessions_dir):
            for fname in os.listdir(sessions_dir):
                if fname.endswith(".json"):
                    sessions.append(fname[:-5])
        await client.ws.send_json({"type": "session.list", "sessions": sessions})
    except Exception as exc:
        await client.ws.send_json({"type": "error", "message": f"session.list failed: {exc}"})


async def _handle_session_clear(client: GatewayClient, payload: dict) -> None:
    """Handle session.clear — clear a specific session."""
    session_key = str(payload.get("session_key", ""))
    if not session_key:
        await client.ws.send_json({"type": "error", "message": "session_key required"})
        return
    # Send clear request to Brain via IPC
    input_q = API_QUEUES.get("input")
    if input_q:
        input_q.put_nowait({"type": "session_clear", "session_key": session_key})
        await client.ws.send_json({"type": "session.cleared", "session_key": session_key})
    else:
        await client.ws.send_json({"type": "error", "message": "Brain disconnected"})


async def _handle_config_get(client: GatewayClient, payload: dict) -> None:
    """Handle config.get — return config value."""
    from runtime.config_manager import config, is_sensitive_config_path
    path = str(payload.get("path", ""))
    if not path:
        await client.ws.send_json({"type": "error", "message": "path required"})
        return
    if is_sensitive_config_path(path):
        await client.ws.send_json({"type": "config.value", "path": path, "value": "***"})
        return
    value = config.get(path)
    await client.ws.send_json({"type": "config.value", "path": path, "value": value})


async def _handle_subscribe(client: GatewayClient, payload: dict) -> None:
    """Handle subscribe — adjust event subscriptions."""
    events = payload.get("events", [])
    if isinstance(events, list):
        client.subscriptions = set(events)
    chat_ids = payload.get("chat_ids", [])
    if isinstance(chat_ids, list):
        client.chat_ids = set(chat_ids)
    await client.ws.send_json({
        "type": "subscribed",
        "events": sorted(client.subscriptions),
        "chat_ids": sorted(client.chat_ids),
    })


# Handler dispatch table
_HANDLERS = {
    "chat.send": _handle_chat_send,
    "session.list": _handle_session_list,
    "session.clear": _handle_session_clear,
    "config.get": _handle_config_get,
    "subscribe": _handle_subscribe,
}


# ---------------------------------------------------------------------------
# Talk Mode WebSocket — browser-based voice interaction
# ---------------------------------------------------------------------------

@router.websocket("/ws/talk")
async def talk_endpoint(websocket: WebSocket) -> None:
    """Voice WebSocket — upload audio, receive TTS audio stream.

    Protocol:
        Client → Server:
            {"type": "talk.activate"}                  # Start talk mode
            {"type": "talk.audio", "data": "<base64>"}  # Audio chunk
            {"type": "talk.utterance", "text": "..."}   # Pre-transcribed text
            {"type": "talk.deactivate"}                # Stop talk mode

        Server → Client:
            {"type": "talk.state", "state": "listening"}
            {"type": "talk.transcript", "text": "..."}  # STT result
            {"type": "talk.response", "text": "..."}    # Agent text response
            {"type": "talk.tts", "data": "<base64>"}    # TTS audio chunk
            {"type": "talk.tts_end"}                    # TTS complete
    """
    if not await _verify_ws_auth(websocket):
        return

    await websocket.accept()
    try:
        from runtime.talk_mode import TalkModeController, TalkState

        async def _on_state_change(state: TalkState) -> None:
            try:
                await websocket.send_json({"type": "talk.state", "state": state.value})
            except Exception:
                pass

        controller = TalkModeController(
            on_state_change=_on_state_change,
        )

        await websocket.send_json({"type": "talk.state", "state": controller.state.value})

        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = payload.get("type", "")

            if msg_type == "talk.activate":
                await controller.activate()

            elif msg_type == "talk.deactivate":
                await controller.deactivate()

            elif msg_type == "talk.utterance":
                text = str(payload.get("text", "")).strip()
                if text:
                    # Process utterance and send response
                    await websocket.send_json({"type": "talk.transcript", "text": text})
                    # Forward to Brain via IPC
                    input_q = API_QUEUES.get("input")
                    if input_q:
                        input_q.put({
                            "type": "chat",
                            "content": text,
                            "source": "voice",
                            "chat_id": "talk-mode",
                            "sender_id": "VoiceUser",
                        })

            elif msg_type == "talk.audio":
                # Binary audio data (base64) — would be decoded and transcribed
                # For now, acknowledge receipt
                await websocket.send_json({"type": "talk.audio_ack"})

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"Talk WebSocket error: {exc}", exc_info=True)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/gateway")
async def gateway_endpoint(websocket: WebSocket) -> None:
    """Unified WebSocket gateway — multiplexes all real-time communication."""
    if not await _verify_ws_auth(websocket):
        return

    client = await gateway_manager.connect(websocket)
    try:
        # Send welcome with capabilities
        await websocket.send_json({
            "type": "welcome",
            "version": 1,
            "capabilities": sorted(_HANDLERS.keys()),
        })

        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
                await websocket.send_json({"type": "error", "message": "Message too large"})
                continue

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            if not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "message": "Expected JSON object"})
                continue

            msg_type = payload.get("type", "")

            # Handle ping
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            # Dispatch to handler
            handler = _HANDLERS.get(msg_type)
            if handler:
                try:
                    await handler(client, payload)
                except Exception as exc:
                    logger.error(f"Gateway handler error [{msg_type}]: {exc}", exc_info=True)
                    await websocket.send_json(
                        {"type": "error", "message": f"Handler error: {exc}"}
                    )
            else:
                await websocket.send_json(
                    {"type": "error", "message": f"Unknown message type: {msg_type}"}
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"Gateway WebSocket error: {exc}", exc_info=True)
    finally:
        gateway_manager.disconnect(client)
