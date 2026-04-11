from __future__ import annotations

import asyncio
import collections
import io
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from PIL import Image

from devices.satellite_protocol import (
    FRAME_TYPE_ACK, FRAME_TYPE_ERROR, FRAME_TYPE_FRAME, FRAME_TYPE_HEARTBEAT,
    FRAME_TYPE_HELLO, FRAME_TYPE_INVOKE_RESULT,
    SatelliteProtocolError, SessionMetadata,
    ensure_frame, ensure_hello, ensure_invoke_result,
)
from devices.satellite_session import SatelliteSessionManager, create_satellite_session_manager
from security.pairing import get_pairing_manager
from tools.admin.auth import _extract_ws_token, _verify_ws_auth, verify_admin_token
from tools.admin.state import (
    API_QUEUES,
    get_satellite_session_manager,
    SATELLITE_SOURCES,
    config,
)
from tools.admin.strategy_helpers import (
    _consume_satellite_frame_budget,
    _decode_frame_payload,
    _validate_satellite_node_auth,
)

# Maximum upload size (bytes); read dynamically so config changes take effect on restart.
_MAX_UPLOAD_BYTES: int = int(config.get("api.max_upload_bytes", 10 * 1024 * 1024))

# Last received satellite snapshot image (used by the debug view endpoint).
_latest_satellite_image = None

app = APIRouter()
logger = logging.getLogger("satellite")



@app.post("/satellite/snapshot", dependencies=[Depends(verify_admin_token)])
async def upload_satellite_snapshot(file: UploadFile = File(...)):
    """Legacy: receive a single snapshot from a Satellite client."""
    global _latest_satellite_image
    try:
        content = await file.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Upload too large (max {_MAX_UPLOAD_BYTES} bytes)",
            )
        image = Image.open(io.BytesIO(content))
        _latest_satellite_image = image

        # Route into any registered RemoteScreenSource
        for src in SATELLITE_SOURCES.values():
            src.push_frame(image)

        return {"status": "received", "size": len(content)}
    except Exception as e:
        logger.error("Failed to process satellite snapshot: %s", e)
        return {"status": "error", "message": str(e)}

@app.websocket("/ws/satellite")
async def satellite_ws(websocket: WebSocket):
    """Satellite WS endpoint with hello/auth, frame uplink, and invoke_result downlink."""
    if not await _verify_ws_auth(websocket):
        return
    await websocket.accept()
    source_id = websocket.query_params.get("source_id", "satellite")
    source = SATELLITE_SOURCES.get(source_id)

    client_ip = websocket.client.host if websocket.client else ""
    authed_node_id = ""
    budget_state: Dict[str, Any] = {"frames": collections.deque(), "total_bytes": 0}
    frame_window_seconds = float(config.get("satellite.frame_window_seconds", 2.0) or 2.0)
    _default_max_frame_bytes = 4 * int(config.get("api.max_ws_message_bytes", 256 * 1024))
    max_frame_bytes_per_window = int(
        config.get("satellite.max_frame_bytes_per_window", _default_max_frame_bytes)
        or _default_max_frame_bytes
    )
    logger.info("Satellite WS connected: source_id=%s ip=%s", source_id, client_ip)
    try:
        while True:
            await get_satellite_session_manager().prune_stale_sessions()
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message and message["text"] is not None:
                try:
                    raw_frame = json.loads(message["text"])
                    frame = ensure_frame(raw_frame)
                except (json.JSONDecodeError, SatelliteProtocolError) as exc:
                    await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": str(exc)})
                    continue

                frame_type = frame["type"]
                if frame_type == FRAME_TYPE_HELLO:
                    try:
                        hello = ensure_hello(frame)
                    except SatelliteProtocolError as exc:
                        await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": str(exc)})
                        await websocket.close(code=4003, reason="Invalid hello")
                        return

                    node_id = hello["node_id"]
                    ok, message = _validate_satellite_node_auth(node_id, hello["token"])
                    if not ok:
                        await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": message})
                        await websocket.close(code=4003, reason="Satellite auth failed")
                        return

                    authed_node_id = node_id
                    source = SATELLITE_SOURCES.get(authed_node_id) or source
                    if source is None:
                        await websocket.close(code=4001, reason=f"No RemoteScreenSource for '{authed_node_id}'")
                        return
                    await get_satellite_session_manager().register(
                        authed_node_id,
                        websocket,
                        SessionMetadata(
                            node_id=authed_node_id,
                            version=hello["version"],
                            authenticated=True,
                            client_ip=client_ip,
                            user_agent=websocket.headers.get("user-agent", ""),
                            connected_at=time.time(),
                        ),
                    )
                    await websocket.send_json({"type": FRAME_TYPE_ACK, "message": "hello_ok", "node_id": authed_node_id})
                    continue

                if not authed_node_id:
                    await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": "hello required before data frames."})
                    await websocket.close(code=4003, reason="hello required")
                    return

                if frame_type == FRAME_TYPE_HEARTBEAT:
                    ts = frame.get("ts")
                    ts_float = float(ts) if isinstance(ts, (int, float)) else time.time()
                    await get_satellite_session_manager().touch_heartbeat(authed_node_id, ts=ts_float)
                    await websocket.send_json({"type": FRAME_TYPE_ACK, "message": "heartbeat_ok"})
                    continue

                if frame_type == FRAME_TYPE_FRAME:
                    try:
                        data = _decode_frame_payload(frame)
                    except SatelliteProtocolError as exc:
                        await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": str(exc)})
                        continue
                    if len(data) > _MAX_UPLOAD_BYTES:
                        await websocket.close(code=1009, reason="Frame too large")
                        return
                    if not _consume_satellite_frame_budget(
                        state=budget_state,
                        size_bytes=len(data),
                        window_seconds=frame_window_seconds,
                        max_bytes_per_window=max_frame_bytes_per_window,
                    ):
                        await websocket.send_json(
                            {"type": FRAME_TYPE_ERROR, "message": "Frame rate exceeded server backpressure budget."}
                        )
                        await websocket.close(code=1013, reason="Backpressure")
                        return
                    image = Image.open(io.BytesIO(data))
                    source.push_frame(image, metadata={"transport": "websocket", "node_id": authed_node_id})
                    continue

                if frame_type == FRAME_TYPE_INVOKE_RESULT:
                    try:
                        result = ensure_invoke_result(frame)
                    except SatelliteProtocolError as exc:
                        await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": str(exc)})
                        continue
                    await get_satellite_session_manager().on_invoke_result(
                        node_id=authed_node_id,
                        request_id=result["request_id"],
                        ok=result["ok"],
                        message=result["message"],
                        data=result["data"],
                    )
                    continue

                await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": f"Unhandled frame type: {frame_type}"})
                continue

            if "bytes" in message and message["bytes"] is not None:
                data = message["bytes"]
                if not authed_node_id:
                    await websocket.send_json({"type": FRAME_TYPE_ERROR, "message": "hello required before binary frames."})
                    await websocket.close(code=4003, reason="hello required")
                    return
                if len(data) > _MAX_UPLOAD_BYTES:
                    await websocket.close(code=1009, reason="Frame too large")
                    return
                if not _consume_satellite_frame_budget(
                    state=budget_state,
                    size_bytes=len(data),
                    window_seconds=frame_window_seconds,
                    max_bytes_per_window=max_frame_bytes_per_window,
                ):
                    await websocket.send_json(
                        {"type": FRAME_TYPE_ERROR, "message": "Frame rate exceeded server backpressure budget."}
                    )
                    await websocket.close(code=1013, reason="Backpressure")
                    return
                image = Image.open(io.BytesIO(data))
                source.push_frame(image, metadata={"transport": "websocket_binary", "node_id": authed_node_id})
                continue
    except WebSocketDisconnect:
        logger.info("Satellite WS disconnected: source_id=%s node_id=%s", source_id, authed_node_id or 'unknown')
    except Exception as exc:
        logger.error("Satellite WS error (%s): %s", source_id, exc)
    finally:
        if authed_node_id:
            await get_satellite_session_manager().unregister(authed_node_id)

@app.get("/satellite/session/status", dependencies=[Depends(verify_admin_token)])
async def get_satellite_session_status():
    """Runtime status for satellite transport and session health."""
    await get_satellite_session_manager().prune_stale_sessions()
    nodes = get_satellite_session_manager().list_nodes()
    return {
        "status": "ok",
        "backend": getattr(get_satellite_session_manager(), "backend", "python"),
        "manager": get_satellite_session_manager().get_runtime_status(),
        "nodes": {
            node_id: {
                "version": meta.version,
                "authenticated": meta.authenticated,
                "connected_at": meta.connected_at,
                "last_heartbeat_ts": meta.last_heartbeat_ts,
                "client_ip": meta.client_ip,
            }
            for node_id, meta in nodes.items()
        },
    }

@app.get("/satellite/view", dependencies=[Depends(verify_admin_token)])
async def view_satellite_snapshot():
    """Debug: view the last received satellite snapshot."""
    if _latest_satellite_image:
        img_byte_arr = io.BytesIO()
        _latest_satellite_image.save(img_byte_arr, format="JPEG")
        return Response(content=img_byte_arr.getvalue(), media_type="image/jpeg")
    return {"error": "No image received yet"}

@app.get("/pairing/pending", dependencies=[Depends(verify_admin_token)])
async def list_pending_pairings():
    """List all pending pairing requests (admin view)."""
    return {"pending": get_pairing_manager().list_pending()}

@app.get("/pairing/approved", dependencies=[Depends(verify_admin_token)])
async def list_approved_senders():
    """List all approved senders by channel."""
    return {"approved": get_pairing_manager().list_approved()}

@app.post("/pairing/approve", dependencies=[Depends(verify_admin_token)])
async def approve_pairing(data: Dict[str, Any]):
    """Approve a pending pairing code.

    Body: ``{"code": "123456"}``
    """
    code = data.get("code", "")
    req = get_pairing_manager().approve(code)
    if req is None:
        raise HTTPException(status_code=404, detail="Invalid or expired pairing code")
    return {
        "status": "approved",
        "channel": req.channel,
        "sender_id": req.sender_id,
    }

@app.post("/pairing/reject", dependencies=[Depends(verify_admin_token)])
async def reject_pairing(data: Dict[str, Any]):
    """Reject a pending pairing code.

    Body: ``{"code": "123456"}``
    """
    code = data.get("code", "")
    req = get_pairing_manager().reject(code)
    if req is None:
        raise HTTPException(status_code=404, detail="Invalid or expired pairing code")
    return {"status": "rejected", "channel": req.channel, "sender_id": req.sender_id}

@app.post("/pairing/revoke", dependencies=[Depends(verify_admin_token)])
async def revoke_sender(data: Dict[str, Any]):
    """Revoke an approved sender.

    Body: ``{"channel": "telegram", "sender_id": "12345"}``
    """
    channel = data.get("channel", "")
    sender_id = data.get("sender_id", "")
    if not channel or not sender_id:
        raise HTTPException(status_code=400, detail="channel and sender_id required")
    ok = get_pairing_manager().revoke(channel, sender_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sender not found in approved list")
    return {"status": "revoked"}
