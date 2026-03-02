from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from devices.models import NodeActionResult
from devices.satellite_protocol import InvokeRequest, SessionMetadata
from runtime.rust_gate import is_rust_allowed_for_current_context
from runtime.rust_sidecar import (
    RustSidecarClient,
    RustSidecarError,
    build_rust_sidecar_client_from_config,
)

logger = logging.getLogger("SatelliteSessionManager")


@dataclass
class _PendingRequest:
    node_id: str
    future: "asyncio.Future[NodeActionResult]"
    created_at: float


class SatelliteSessionManager:
    def __init__(
        self,
        *,
        max_pending_requests_per_node: int = 64,
        pending_ttl_seconds: float = 30.0,
        heartbeat_timeout_seconds: float = 45.0,
    ) -> None:
        self._sessions: Dict[str, Any] = {}
        self._meta: Dict[str, SessionMetadata] = {}
        self._pending: Dict[str, _PendingRequest] = {}
        self._lock = asyncio.Lock()
        self._max_pending_per_node = max(1, int(max_pending_requests_per_node or 64))
        self._pending_ttl_seconds = max(1.0, float(pending_ttl_seconds or 30.0))
        self._heartbeat_timeout_seconds = max(1.0, float(heartbeat_timeout_seconds or 45.0))
        self._last_observation: Dict[str, Any] = {
            "trace_id": "",
            "latency_ms": 0.0,
            "error_code": "",
        }

    @property
    def backend(self) -> str:
        return "python"

    def _pending_count_for_node(self, node_id: str) -> int:
        return sum(1 for item in self._pending.values() if item.node_id == node_id)

    async def _cleanup_stale_pending(self) -> None:
        now = time.time()
        stale_ids = [
            req_id
            for req_id, pending in self._pending.items()
            if (now - pending.created_at) > self._pending_ttl_seconds
        ]
        for req_id in stale_ids:
            pending = self._pending.pop(req_id, None)
            if pending and not pending.future.done():
                pending.future.set_result(
                    NodeActionResult(
                        ok=False,
                        code="DEVICE_INVOKE_TIMEOUT",
                        message="Satellite pending request expired.",
                    )
                )

    async def prune_stale_sessions(self, now_ts: Optional[float] = None) -> None:
        now = float(now_ts) if now_ts is not None else time.time()
        stale_nodes = []
        for node_id, meta in self._meta.items():
            last_hb = meta.last_heartbeat_ts if meta.last_heartbeat_ts is not None else meta.connected_at
            if not last_hb:
                continue
            if (now - float(last_hb)) > self._heartbeat_timeout_seconds:
                stale_nodes.append(node_id)
        for node_id in stale_nodes:
            logger.warning("Satellite heartbeat timeout: node=%s", node_id)
            await self.unregister(node_id)

    def get_runtime_status(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "online_nodes": len(self._sessions),
            "pending_requests": len(self._pending),
            "max_pending_requests_per_node": self._max_pending_per_node,
            "pending_ttl_seconds": self._pending_ttl_seconds,
            "heartbeat_timeout_seconds": self._heartbeat_timeout_seconds,
            "last_observation": dict(self._last_observation),
        }

    async def register(self, node_id: str, websocket: Any, metadata: SessionMetadata) -> None:
        async with self._lock:
            self._sessions[node_id] = websocket
            self._meta[node_id] = metadata
            logger.info("Satellite session registered: %s", node_id)

    async def unregister(self, node_id: str) -> None:
        async with self._lock:
            self._sessions.pop(node_id, None)
            self._meta.pop(node_id, None)
            leaked_ids = [request_id for request_id, pending in self._pending.items() if pending.node_id == node_id]
            for request_id in leaked_ids:
                pending = self._pending.pop(request_id)
                if not pending.future.done():
                    pending.future.set_result(
                        NodeActionResult(ok=False, message=f"Node '{node_id}' disconnected.")
                    )
            logger.info("Satellite session unregistered: %s", node_id)

    def is_online(self, node_id: str) -> bool:
        return node_id in self._sessions

    def list_nodes(self) -> Dict[str, SessionMetadata]:
        return dict(self._meta)

    async def send_invoke(
        self,
        *,
        node_id: str,
        action: str,
        args: Dict[str, Any],
        timeout_seconds: float = 15.0,
    ) -> NodeActionResult:
        started = time.time()
        await self._cleanup_stale_pending()
        await self.prune_stale_sessions()
        websocket = self._sessions.get(node_id)
        if websocket is None:
            result = NodeActionResult(
                ok=False,
                code="DEVICE_TARGET_OFFLINE",
                message=f"Satellite node '{node_id}' is offline.",
            )
            self._last_observation = {
                "trace_id": "",
                "latency_ms": (time.time() - started) * 1000.0,
                "error_code": result.code,
            }
            return result
        if self._pending_count_for_node(node_id) >= self._max_pending_per_node:
            result = NodeActionResult(
                ok=False,
                code="DEVICE_INVOKE_OVERLOAD",
                message=f"Too many pending satellite requests for node '{node_id}'.",
            )
            self._last_observation = {
                "trace_id": "",
                "latency_ms": (time.time() - started) * 1000.0,
                "error_code": result.code,
            }
            return result

        request_id = str(uuid.uuid4())
        future: "asyncio.Future[NodeActionResult]" = asyncio.get_running_loop().create_future()
        self._pending[request_id] = _PendingRequest(
            node_id=node_id,
            future=future,
            created_at=time.time(),
        )
        frame = InvokeRequest(request_id=request_id, action=action, args=args).to_frame()
        try:
            await websocket.send_json(frame)
            result = await asyncio.wait_for(future, timeout=timeout_seconds)
            self._last_observation = {
                "trace_id": request_id,
                "latency_ms": (time.time() - started) * 1000.0,
                "error_code": str(result.code or ""),
            }
            return result
        except asyncio.TimeoutError:
            logger.warning("Satellite invoke timeout: node=%s action=%s", node_id, action)
            result = NodeActionResult(
                ok=False,
                code="DEVICE_INVOKE_TIMEOUT",
                message=f"Satellite invoke timeout for '{action}'.",
            )
            self._last_observation = {
                "trace_id": request_id,
                "latency_ms": (time.time() - started) * 1000.0,
                "error_code": result.code,
            }
            return result
        except Exception as exc:
            logger.warning("Satellite invoke send failed: node=%s action=%s err=%s", node_id, action, exc)
            result = NodeActionResult(
                ok=False,
                code="DEVICE_INVOKE_FAILED",
                message=f"Satellite invoke failed: {exc}",
            )
            self._last_observation = {
                "trace_id": request_id,
                "latency_ms": (time.time() - started) * 1000.0,
                "error_code": result.code,
            }
            return result
        finally:
            self._pending.pop(request_id, None)

    async def on_invoke_result(
        self,
        *,
        node_id: str,
        request_id: str,
        ok: bool,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        pending = self._pending.get(request_id)
        if pending is None:
            logger.debug("Ignoring unknown invoke_result request_id=%s node=%s", request_id, node_id)
            return False
        if pending.node_id != node_id:
            logger.warning(
                "Rejecting invoke_result request_id=%s expected_node=%s actual_node=%s",
                request_id,
                pending.node_id,
                node_id,
            )
            return False
        if not pending.future.done():
            pending.future.set_result(
                NodeActionResult(ok=ok, message=message or "", data=data or {})
            )
        return True

    async def touch_heartbeat(self, node_id: str, ts: Optional[float] = None) -> None:
        meta = self._meta.get(node_id)
        if meta is None:
            return
        meta.last_heartbeat_ts = ts if ts is not None else time.time()


class RustSatelliteSessionManager(SatelliteSessionManager):
    """Satellite transport manager with optional rust sidecar delegation."""

    def __init__(
        self,
        *,
        client: RustSidecarClient,
        max_pending_requests_per_node: int = 64,
        pending_ttl_seconds: float = 30.0,
        heartbeat_timeout_seconds: float = 45.0,
    ) -> None:
        super().__init__(
            max_pending_requests_per_node=max_pending_requests_per_node,
            pending_ttl_seconds=pending_ttl_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        )
        self._client = client

    @property
    def backend(self) -> str:
        return "rust"

    async def send_invoke(
        self,
        *,
        node_id: str,
        action: str,
        args: Dict[str, Any],
        timeout_seconds: float = 15.0,
    ) -> NodeActionResult:
        if not is_rust_allowed_for_current_context():
            return await super().send_invoke(
                node_id=node_id,
                action=action,
                args=args,
                timeout_seconds=timeout_seconds,
            )
        started = time.time()
        # Preferred path: rust transport RPC (if sidecar supports it).
        try:
            payload = await self._client.rpc(
                method="satellite.invoke",
                params={
                    "node_id": node_id,
                    "action": action,
                    "args": dict(args or {}),
                    "timeout_seconds": float(timeout_seconds or 15.0),
                },
            )
            ok = bool(payload.get("ok", False))
            result = NodeActionResult(
                ok=ok,
                code=str(payload.get("code", "") or ""),
                message=str(payload.get("message", "") or ""),
                data=payload.get("data", {}) if isinstance(payload.get("data"), dict) else {},
            )
            self._last_observation = {
                "trace_id": str(payload.get("trace_id", "") or ""),
                "latency_ms": (time.time() - started) * 1000.0,
                "error_code": str(result.code or ""),
            }
            return result
        except RustSidecarError:
            # Fallback to python transport path for compatibility.
            return await super().send_invoke(
                node_id=node_id,
                action=action,
                args=args,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return await super().send_invoke(
                node_id=node_id,
                action=action,
                args=args,
                timeout_seconds=timeout_seconds,
            )


def create_satellite_session_manager(cfg: Any) -> SatelliteSessionManager:
    """Create satellite manager from config with python|rust backend switch."""
    max_pending = int(cfg.get("satellite.max_pending_requests_per_node", 64) or 64)
    pending_ttl = float(cfg.get("satellite.pending_ttl_seconds", 30.0) or 30.0)
    heartbeat_timeout = float(cfg.get("satellite.heartbeat_timeout_seconds", 45.0) or 45.0)
    backend = str(cfg.get("satellite.transport_backend", "python") or "python").strip().lower()

    if backend == "rust":
        try:
            client = build_rust_sidecar_client_from_config(cfg)
            return RustSatelliteSessionManager(
                client=client,
                max_pending_requests_per_node=max_pending,
                pending_ttl_seconds=pending_ttl,
                heartbeat_timeout_seconds=heartbeat_timeout,
            )
        except Exception as exc:
            logger.warning(
                "satellite.transport_backend=rust requested but sidecar init failed: %s. "
                "Fallback to python backend.",
                exc,
            )

    return SatelliteSessionManager(
        max_pending_requests_per_node=max_pending,
        pending_ttl_seconds=pending_ttl,
        heartbeat_timeout_seconds=heartbeat_timeout,
    )
