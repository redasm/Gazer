from __future__ import annotations

"""WebSocket endpoints and connection managers."""

import json
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tools.admin.state import _MAX_WS_MESSAGE_BYTES, _MAX_CHAT_MESSAGE_CHARS, logger
from .auth import _verify_ws_auth
from tools.admin.state import API_QUEUES, get_canvas_state

router = APIRouter(tags=["websockets"])


# ---------------------------------------------------------------------------
# Connection managers
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages generic status WebSocket connections."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast(self, message: dict) -> None:
        disconnected: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as exc:
                logger.warning("WS broadcast failed: %s", exc)
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


class ChatConnectionManager:
    """WebSocket manager keyed by chat session id."""

    def __init__(self) -> None:
        self._connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, chat_id: str) -> None:
        await websocket.accept()
        self._connections.setdefault(chat_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, chat_id: str) -> None:
        conns = self._connections.get(chat_id, [])
        try:
            conns.remove(websocket)
        except ValueError:
            pass
        if not conns:
            self._connections.pop(chat_id, None)

    async def broadcast(self, chat_id: str, message: dict) -> None:
        disconnected: List[WebSocket] = []
        for connection in list(self._connections.get(chat_id, [])):
            try:
                await connection.send_json(message)
            except Exception as exc:
                logger.warning("Chat WS broadcast failed [chat_id=%s]: %s", chat_id, exc)
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn, chat_id)


# Singleton instances — used by other modules (e.g. config_routes broadcasts)
manager = ConnectionManager()
chat_manager = ChatConnectionManager()


# ---------------------------------------------------------------------------
# Helper — decode media entries (inline, used only by chat WS)
# ---------------------------------------------------------------------------

def _decode_web_media_entries(payload: Dict[str, Any]):
    from channels.media_utils import save_media
    import base64
    import mimetypes
    raw_media = payload.get("media")
    if not raw_media or not isinstance(raw_media, list):
        return [], {}

    media_paths: List[str] = []
    metadata: Dict[str, Any] = {"web_media": []}
    for i, entry in enumerate(raw_media):
        if isinstance(entry, str):
            if entry.startswith("data:"):
                # Handle data URL: data:image/png;base64,iVBORw0K...
                header, b64_str = entry.split(",", 1)
                mime = header.split(";", 1)[0].split(":", 1)[1]
                ext = mimetypes.guess_extension(mime) or ".bin"
                path = save_media(base64.b64decode(b64_str), ext=ext, prefix="web")
                media_paths.append(path)
                metadata["web_media"].append({"mime": mime})
            else:
                media_paths.append(entry)
        elif isinstance(entry, dict):
            if "data_b64" in entry:
                mime = entry.get("mime_type", "application/octet-stream")
                ext = mimetypes.guess_extension(mime) or ".bin"
                # If they passed an explicit filename it might have an extension
                if "filename" in entry and "." in entry["filename"]:
                    ext = "." + entry["filename"].split(".")[-1]
                path = save_media(base64.b64decode(entry["data_b64"]), ext=ext, prefix="web")
                media_paths.append(path)
                entry_meta = {k: v for k, v in entry.items() if k not in ("data_b64", "url", "path", "data_url")}
                entry_meta["mime"] = mime
                metadata["web_media"].append(entry_meta)
            else:
                ref = entry.get("url") or entry.get("path") or entry.get("data_url", "")
                if ref:
                    if ref.startswith("data:"):
                        header, b64_str = ref.split(",", 1)
                        mime = header.split(";", 1)[0].split(":", 1)[1]
                        ext = mimetypes.guess_extension(mime) or ".bin"
                        path = save_media(base64.b64decode(b64_str), ext=ext, prefix="web")
                        media_paths.append(path)
                        metadata["web_media"].append({"mime": mime})
                    else:
                        media_paths.append(str(ref))
                        entry_meta = {k: v for k, v in entry.items() if k not in ("url", "path", "data_url")}
                        if entry_meta:
                            metadata["web_media"].append(entry_meta)

    return media_paths, metadata


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.websocket("/ws/status")
async def websocket_endpoint(websocket: WebSocket):
    """Status WebSocket -- heartbeat / system events."""
    if not await _verify_ws_auth(websocket):
        return
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "welcome", "data": "Gazer System Connected"})
        while True:
            data = await websocket.receive_text()
            if len(data.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
                await websocket.send_json({"type": "error", "message": "Message too large"})
                continue
            await websocket.send_json({"type": "pong", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/ws/canvas")
async def canvas_endpoint(websocket: WebSocket):
    """Canvas WebSocket -- syncs A2UI canvas state with the web UI."""
    try:
        logger.info("Canvas WebSocket connection attempt from %s (Origin: %s)", websocket.client.host if websocket.client else 'unknown', websocket.headers.get('origin', 'none'))
        if not await _verify_ws_auth(websocket):
            logger.warning("Canvas WebSocket auth rejected")
            return
        logger.info("Canvas WebSocket auth passed")
    except Exception as e:
        logger.error("Canvas WebSocket auth exception: %s", e)
        return

    # Lazy import to avoid circular dependencies
    from tools.admin_api import canvas_ws_manager
    
    logger.info("Canvas WebSocket accepting connection...")
    await canvas_ws_manager.connect(websocket)
    logger.info("Canvas WebSocket connection accepted and added to manager.")
    try:
        # Send initial state immediately upon connection
        cs = get_canvas_state()
        if cs is not None:
            raw_json = json.dumps({"type": "canvas_update", **cs.to_dict()}, default=str, ensure_ascii=False)
            await websocket.send_text(raw_json)
        else:
            raw_json = json.dumps({"type": "canvas_update", "panels": [], "version": 0}, default=str, ensure_ascii=False)
            await websocket.send_text(raw_json)
            
        while True:
            try:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
                    continue
                
                # The Web UI sends user actions (from interactive A2UI components)
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    action = payload.get("userAction")
                    if action and API_QUEUES.get("input") is not None:
                        API_QUEUES["input"].put_nowait({
                            "type": "a2ui_action",
                            "action": action,
                            "source": "web_canvas"
                        })
            except WebSocketDisconnect:
                raise
            except Exception as e:
                # Log but do not crash the websocket connection on malformed payloads
                logger.error("Error handling canvas websocket message: %s", e)
    except WebSocketDisconnect:
        logger.info("Canvas WebSocket client disconnected cleanly.")
    except Exception as e:
        logger.error("Canvas WebSocket FATAL error: %s", e, exc_info=True)
    finally:
        canvas_ws_manager.disconnect(websocket)


@router.websocket("/ws/chat")
async def chat_endpoint(websocket: WebSocket):
    """Chat WebSocket -- relays messages between the web UI and the Brain."""
    if not await _verify_ws_auth(websocket):
        return
    session_id = websocket.query_params.get("session_id", "web-main")
    sender_id = "owner" if getattr(websocket.state, "is_owner", False) else "WebUser"
    await chat_manager.connect(websocket, session_id)
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
                await websocket.send_json({"type": "error", "message": "Message too large"})
                continue
            payload: Dict[str, Any] = {}
            content = raw
            media: List[str] = []
            metadata: Dict[str, Any] = {}
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    # Handle application-level pings to keep the connection alive
                    if payload.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                        continue
                        
                    content = str(payload.get("content", "")).strip()
                    media, media_metadata = _decode_web_media_entries(payload)
                    metadata = dict(media_metadata)
                    extra_meta = payload.get("metadata", {})
                    if isinstance(extra_meta, dict):
                        metadata.update(extra_meta)
            except json.JSONDecodeError:
                pass

            if not content and media:
                content = "[User sent media]"
            if not content and not media:
                await websocket.send_json({"type": "error", "message": "Empty message"})
                continue
            if len(content) > _MAX_CHAT_MESSAGE_CHARS:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Message too long (max {_MAX_CHAT_MESSAGE_CHARS} characters)",
                    }
                )
                continue

            if API_QUEUES["input"]:
                API_QUEUES["input"].put_nowait({
                    "type": "chat",
                    "content": content,
                    "source": "web_chat",
                    "chat_id": session_id,
                    "sender_id": sender_id,
                    "media": media,
                    "metadata": metadata,
                })
            else:
                await websocket.send_json({"type": "error", "message": "Brain disconnected"})
    except WebSocketDisconnect:
        chat_manager.disconnect(websocket, session_id)
