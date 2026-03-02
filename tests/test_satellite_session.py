import asyncio
import time
from typing import Any, Dict, List

from devices.satellite_protocol import SessionMetadata
from devices.satellite_session import (
    RustSatelliteSessionManager,
    SatelliteSessionManager,
    create_satellite_session_manager,
)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.frames: List[Dict[str, Any]] = []

    async def send_json(self, frame: Dict[str, Any]) -> None:
        self.frames.append(frame)


async def _invoke_and_respond(manager: SatelliteSessionManager) -> None:
    ws = _FakeWebSocket()
    await manager.register(
        "sat-01",
        ws,
        metadata=SessionMetadata(node_id="sat-01", authenticated=True),
    )

    async def _respond() -> None:
        await asyncio.sleep(0.01)
        request_id = ws.frames[0]["request_id"]
        await manager.on_invoke_result(
            node_id="sat-01",
            request_id=request_id,
            ok=True,
            message="ok",
            data={"x": 1},
        )

    responder = asyncio.create_task(_respond())
    result = await manager.send_invoke(node_id="sat-01", action="input.mouse.click", args={"x": 1, "y": 2})
    await responder
    assert result.ok is True
    assert result.message == "ok"
    assert result.data["x"] == 1


def test_satellite_session_invoke_roundtrip() -> None:
    manager = SatelliteSessionManager()
    asyncio.run(_invoke_and_respond(manager))


async def _invoke_timeout(manager: SatelliteSessionManager) -> None:
    ws = _FakeWebSocket()
    await manager.register(
        "sat-01",
        ws,
        metadata=SessionMetadata(node_id="sat-01", authenticated=True, connected_at=time.time()),
    )
    result = await manager.send_invoke(
        node_id="sat-01",
        action="input.mouse.click",
        args={"x": 1, "y": 2},
        timeout_seconds=0.01,
    )
    assert result.ok is False
    assert result.code == "DEVICE_INVOKE_TIMEOUT"
    assert "timeout" in result.message.lower()
    status = manager.get_runtime_status()
    assert "last_observation" in status
    assert status["last_observation"]["error_code"] == "DEVICE_INVOKE_TIMEOUT"
    assert status["last_observation"]["latency_ms"] >= 0


def test_satellite_session_timeout_code() -> None:
    manager = SatelliteSessionManager()
    asyncio.run(_invoke_timeout(manager))


async def _pending_overload(manager: SatelliteSessionManager) -> None:
    ws = _FakeWebSocket()
    await manager.register(
        "sat-01",
        ws,
        metadata=SessionMetadata(node_id="sat-01", authenticated=True, connected_at=time.time()),
    )
    # Keep one invoke request pending, then assert second one is rejected by overload guard.
    first_call = asyncio.create_task(
        manager.send_invoke(
            node_id="sat-01",
            action="input.mouse.click",
            args={"x": 1, "y": 2},
            timeout_seconds=0.2,
        )
    )
    await asyncio.sleep(0.01)

    result = await manager.send_invoke(
        node_id="sat-01",
        action="input.mouse.click",
        args={"x": 1, "y": 2},
        timeout_seconds=0.5,
    )
    assert result.ok is False
    assert result.code == "DEVICE_INVOKE_OVERLOAD"
    await first_call


def test_satellite_session_pending_overload() -> None:
    manager = SatelliteSessionManager(max_pending_requests_per_node=1)
    asyncio.run(_pending_overload(manager))


def test_satellite_session_prune_stale_heartbeat() -> None:
    manager = SatelliteSessionManager(heartbeat_timeout_seconds=0.01)
    ws = _FakeWebSocket()
    asyncio.run(
        manager.register(
            "sat-01",
            ws,
            metadata=SessionMetadata(
                node_id="sat-01",
                authenticated=True,
                connected_at=time.time() - 10,
                last_heartbeat_ts=time.time() - 10,
            ),
        )
    )
    asyncio.run(manager.prune_stale_sessions())
    assert manager.is_online("sat-01") is False


class _Cfg:
    def __init__(self, values):
        self._values = values

    def get(self, key_path: str, default=None):
        return self._values.get(key_path, default)


def test_create_satellite_session_manager_python_backend() -> None:
    manager = create_satellite_session_manager(
        _Cfg(
            {
                "satellite.transport_backend": "python",
                "satellite.max_pending_requests_per_node": 2,
                "satellite.pending_ttl_seconds": 3.0,
                "satellite.heartbeat_timeout_seconds": 5.0,
            }
        )
    )
    assert isinstance(manager, SatelliteSessionManager)
    assert manager.backend == "python"


def test_create_satellite_session_manager_rust_fallback_without_endpoint() -> None:
    manager = create_satellite_session_manager(
        _Cfg(
            {
                "satellite.transport_backend": "rust",
                "satellite.max_pending_requests_per_node": 2,
                "satellite.pending_ttl_seconds": 3.0,
                "satellite.heartbeat_timeout_seconds": 5.0,
                "runtime.rust_sidecar.endpoint": "",
            }
        )
    )
    # no endpoint -> fallback python
    assert manager.backend == "python"


def test_create_satellite_session_manager_rust_backend(monkeypatch) -> None:
    class _Client:
        async def rpc(self, *, method: str, params=None, trace_id: str = ""):
            return {"ok": True, "message": "ok", "data": {}}

    monkeypatch.setattr(
        "devices.satellite_session.build_rust_sidecar_client_from_config",
        lambda _cfg: _Client(),
    )
    manager = create_satellite_session_manager(
        _Cfg(
            {
                "satellite.transport_backend": "rust",
                "satellite.max_pending_requests_per_node": 2,
                "satellite.pending_ttl_seconds": 3.0,
                "satellite.heartbeat_timeout_seconds": 5.0,
                "runtime.rust_sidecar.endpoint": "http://127.0.0.1:8787",
            }
        )
    )
    assert manager.backend == "rust"


def test_rust_satellite_manager_backend_label() -> None:
    class _Client:
        async def rpc(self, *, method: str, params=None, trace_id: str = ""):
            return {"ok": True, "message": "ok", "data": {}}

    manager = RustSatelliteSessionManager(client=_Client())  # type: ignore[arg-type]
    assert manager.backend == "rust"


def test_rust_satellite_manager_rollout_gate_falls_back_to_python(monkeypatch) -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls = []

        async def rpc(self, *, method: str, params=None, trace_id: str = ""):
            self.calls.append((method, dict(params or {}), trace_id))
            return {"ok": True, "message": "ok", "data": {}}

    async def _run() -> None:
        client = _Client()
        manager = RustSatelliteSessionManager(client=client)  # type: ignore[arg-type]
        ws = _FakeWebSocket()
        await manager.register(
            "sat-01",
            ws,
            metadata=SessionMetadata(node_id="sat-01", authenticated=True),
        )

        async def _respond() -> None:
            await asyncio.sleep(0.01)
            request_id = ws.frames[0]["request_id"]
            await manager.on_invoke_result(
                node_id="sat-01",
                request_id=request_id,
                ok=True,
                message="ok",
                data={"x": 1},
            )

        monkeypatch.setattr("devices.satellite_session.is_rust_allowed_for_current_context", lambda: False)
        responder = asyncio.create_task(_respond())
        result = await manager.send_invoke(
            node_id="sat-01",
            action="input.mouse.click",
            args={"x": 1, "y": 2},
        )
        await responder
        assert result.ok is True
        assert client.calls == []

    asyncio.run(_run())
