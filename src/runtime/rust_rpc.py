"""Rust sidecar RPC contract helpers.

Phase-0 scope:
- Shared request/response envelope with trace_id propagation.
- Sidecar -> Python error code mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import uuid

RUST_RPC_PROTOCOL = "gazer-rpc.v1"

# Sidecar-specific / generic infra errors -> Python-facing error codes.
RUST_TO_PYTHON_ERROR_CODE_MAP: Dict[str, str] = {
    "BAD_REQUEST": "TOOL_ARGS_INVALID",
    "INVALID_ARGUMENT": "TOOL_ARGS_INVALID",
    "PERMISSION_DENIED": "TOOL_PERMISSION_DENIED",
    "NOT_FOUND": "TOOL_NOT_FOUND",
    "NOT_SUPPORTED": "DEVICE_ACTION_UNSUPPORTED",
    "TIMEOUT": "TOOL_TIMEOUT",
    "DEADLINE_EXCEEDED": "TOOL_TIMEOUT",
    "UNAVAILABLE": "RUST_SIDECAR_UNAVAILABLE",
    "CONNECTION_FAILED": "RUST_SIDECAR_UNAVAILABLE",
    "INTERNAL": "RUST_SIDECAR_INTERNAL",
    "UNKNOWN": "RUST_SIDECAR_ERROR",
}


def new_trace_id() -> str:
    """Create a short trace id for request/response correlation."""
    return f"trc_{uuid.uuid4().hex[:12]}"


def map_sidecar_error_code(code: str) -> str:
    """Map sidecar error code to Python-side canonical code."""
    key = str(code or "").strip().upper() or "UNKNOWN"
    return RUST_TO_PYTHON_ERROR_CODE_MAP.get(key, "RUST_SIDECAR_ERROR")


@dataclass(frozen=True)
class RpcRequest:
    """Standard RPC request envelope."""

    method: str
    params: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    protocol: str = RUST_RPC_PROTOCOL

    @classmethod
    def create(
        cls,
        *,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        trace_id: str = "",
        protocol: str = RUST_RPC_PROTOCOL,
    ) -> "RpcRequest":
        method_name = str(method or "").strip()
        if not method_name:
            raise ValueError("RPC method is required.")
        request_trace = str(trace_id or "").strip() or new_trace_id()
        payload = dict(params or {})
        return cls(
            method=method_name,
            params=payload,
            trace_id=request_trace,
            protocol=str(protocol or RUST_RPC_PROTOCOL).strip() or RUST_RPC_PROTOCOL,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol": self.protocol,
            "trace_id": self.trace_id,
            "method": self.method,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class RpcError:
    """Standard RPC error envelope."""

    code: str
    message: str
    trace_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]], trace_id: str = "") -> "RpcError":
        error = payload or {}
        code = str(error.get("code", "UNKNOWN")).strip() or "UNKNOWN"
        message = str(error.get("message", "sidecar call failed")).strip() or "sidecar call failed"
        event_trace = str(error.get("trace_id", "")).strip() or str(trace_id or "").strip()
        details_raw = error.get("details")
        details = details_raw if isinstance(details_raw, dict) else {}
        return cls(code=code, message=message, trace_id=event_trace, details=details)

    def mapped_code(self) -> str:
        return map_sidecar_error_code(self.code)


@dataclass(frozen=True)
class RpcResponse:
    """Standard RPC response envelope."""

    ok: bool
    trace_id: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[RpcError] = None

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "RpcResponse":
        body = payload or {}
        ok = bool(body.get("ok", False))
        trace_id = str(body.get("trace_id", "")).strip()
        result_raw = body.get("result")
        result = result_raw if isinstance(result_raw, dict) else {}
        error: Optional[RpcError] = None
        if not ok:
            error = RpcError.from_dict(body.get("error"), trace_id=trace_id)
        return cls(ok=ok, trace_id=trace_id, result=result, error=error)
