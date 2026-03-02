from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


FRAME_TYPE_HELLO = "hello"
FRAME_TYPE_HEARTBEAT = "heartbeat"
FRAME_TYPE_FRAME = "frame"
FRAME_TYPE_INVOKE = "invoke"
FRAME_TYPE_INVOKE_RESULT = "invoke_result"
FRAME_TYPE_ERROR = "error"
FRAME_TYPE_ACK = "ack"

SUPPORTED_FRAME_TYPES = {
    FRAME_TYPE_HELLO,
    FRAME_TYPE_HEARTBEAT,
    FRAME_TYPE_FRAME,
    FRAME_TYPE_INVOKE,
    FRAME_TYPE_INVOKE_RESULT,
    FRAME_TYPE_ERROR,
    FRAME_TYPE_ACK,
}


class SatelliteProtocolError(ValueError):
    pass


def ensure_frame(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise SatelliteProtocolError("Frame must be a JSON object.")
    frame_type = str(raw.get("type", "")).strip()
    if not frame_type:
        raise SatelliteProtocolError("Frame type is required.")
    if frame_type not in SUPPORTED_FRAME_TYPES:
        raise SatelliteProtocolError(f"Unsupported frame type: {frame_type}")
    return raw


def ensure_hello(raw: Dict[str, Any]) -> Dict[str, str]:
    if raw.get("type") != FRAME_TYPE_HELLO:
        raise SatelliteProtocolError("Expected hello frame.")
    node_id = str(raw.get("node_id", "")).strip()
    token = str(raw.get("token", "")).strip()
    version = str(raw.get("version", "1")).strip() or "1"
    if not node_id:
        raise SatelliteProtocolError("hello.node_id is required.")
    if not token:
        raise SatelliteProtocolError("hello.token is required.")
    return {"node_id": node_id, "token": token, "version": version}


def ensure_invoke_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    if raw.get("type") != FRAME_TYPE_INVOKE_RESULT:
        raise SatelliteProtocolError("Expected invoke_result frame.")
    request_id = str(raw.get("request_id", "")).strip()
    if not request_id:
        raise SatelliteProtocolError("invoke_result.request_id is required.")
    ok = bool(raw.get("ok", False))
    message = str(raw.get("message", "")).strip()
    data = raw.get("data", {})
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SatelliteProtocolError("invoke_result.data must be an object.")
    return {
        "request_id": request_id,
        "ok": ok,
        "message": message,
        "data": data,
    }


@dataclass
class InvokeRequest:
    request_id: str
    action: str
    args: Dict[str, Any]

    def to_frame(self) -> Dict[str, Any]:
        return {
            "type": FRAME_TYPE_INVOKE,
            "request_id": self.request_id,
            "action": self.action,
            "args": self.args,
        }


@dataclass
class SessionMetadata:
    node_id: str
    version: str = "1"
    authenticated: bool = False
    client_ip: str = ""
    user_agent: str = ""
    connected_at: float = 0.0
    last_heartbeat_ts: Optional[float] = None
