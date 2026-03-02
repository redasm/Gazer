from __future__ import annotations

import pytest

from runtime.rust_rpc import RpcRequest, RpcResponse, map_sidecar_error_code
from runtime.rust_sidecar import (
    RustFileOperations,
    RustShellOperations,
    RustSidecarClient,
    RustSidecarError,
    build_rust_sidecar_client_from_config,
)


def test_rpc_request_envelope_contains_trace_id() -> None:
    req = RpcRequest.create(
        method="shell.exec",
        params={"command": "echo hi", "cwd": "."},
        trace_id="trc_test_1",
    )
    payload = req.to_dict()
    assert payload["protocol"] == "gazer-rpc.v1"
    assert payload["trace_id"] == "trc_test_1"
    assert payload["method"] == "shell.exec"
    assert payload["params"]["command"] == "echo hi"


def test_rpc_response_maps_error_code() -> None:
    resp = RpcResponse.from_dict(
        {
            "ok": False,
            "trace_id": "trc_err_1",
            "error": {"code": "TIMEOUT", "message": "expired"},
        }
    )
    assert resp.ok is False
    assert resp.error is not None
    assert resp.error.trace_id == "trc_err_1"
    assert resp.error.mapped_code() == "TOOL_TIMEOUT"
    assert map_sidecar_error_code("NOT_SUPPORTED") == "DEVICE_ACTION_UNSUPPORTED"


@pytest.mark.asyncio
async def test_sidecar_probe_calls_minimal_health_endpoints(monkeypatch) -> None:
    client = RustSidecarClient(endpoint="http://127.0.0.1:8787", timeout_ms=1200)
    calls = []

    async def _fake_request_json(*, path: str, method: str = "GET", payload=None):
        calls.append((path, method, payload))
        return {"path": path, "ok": True}

    monkeypatch.setattr(client, "_request_json", _fake_request_json)
    probe = await client.probe_minimal()
    assert probe["endpoint"] == "http://127.0.0.1:8787"
    assert probe["health"]["path"] == "/health"
    assert probe["version"]["path"] == "/version"
    assert probe["capabilities"]["path"] == "/capabilities"
    assert [item[0] for item in calls] == ["/health", "/version", "/capabilities"]


@pytest.mark.asyncio
async def test_sidecar_rpc_error_maps_to_python_code(monkeypatch) -> None:
    client = RustSidecarClient(endpoint="http://127.0.0.1:8787", timeout_ms=1200)

    async def _fake_request_json(*, path: str, method: str = "GET", payload=None):
        assert path == "/rpc"
        assert method == "POST"
        return {
            "ok": False,
            "trace_id": "trc_rpc_1",
            "error": {"code": "PERMISSION_DENIED", "message": "blocked"},
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)
    with pytest.raises(RustSidecarError) as exc:
        await client.rpc(method="shell.exec", params={"command": "echo hi"})
    err = exc.value
    assert err.code == "PERMISSION_DENIED"
    assert err.mapped_code == "TOOL_PERMISSION_DENIED"
    assert "trace_id=trc_rpc_1" in err.to_tool_error()


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def rpc(self, *, method: str, params=None, trace_id: str = ""):
        self.calls.append((method, dict(params or {}), trace_id))
        if method == "shell.exec":
            return {"exit_code": 7, "stdout": "out", "stderr": "err"}
        if method == "files.read":
            return {"content": "hello"}
        if method == "files.exists":
            return {"exists": True}
        if method == "files.dir_exists":
            return {"exists": False}
        return {}


@pytest.mark.asyncio
async def test_rust_shell_and_file_operations_delegate_to_client() -> None:
    client = _FakeClient()
    shell_ops = RustShellOperations(client)  # type: ignore[arg-type]
    file_ops = RustFileOperations(client)  # type: ignore[arg-type]

    rc, stdout, stderr = await shell_ops.exec("echo hi", ".", timeout=9)
    assert (rc, stdout, stderr) == (7, "out", "err")

    assert await file_ops.read_file("a.txt") == "hello"
    assert await file_ops.file_exists("a.txt") is True
    assert await file_ops.dir_exists("folder") is False
    await file_ops.write_file("b.txt", "payload")

    methods = [item[0] for item in client.calls]
    assert methods == [
        "shell.exec",
        "files.read",
        "files.exists",
        "files.dir_exists",
        "files.write",
    ]


def test_build_sidecar_client_from_config() -> None:
    class _Cfg:
        def __init__(self, values):
            self._values = values

        def get(self, key_path: str, default=None):
            return self._values.get(key_path, default)

    cfg = _Cfg(
        {
            "runtime.rust_sidecar.endpoint": "http://127.0.0.1:9011",
            "runtime.rust_sidecar.timeout_ms": 4567,
            "runtime.rust_sidecar.auto_fallback_on_error": False,
            "runtime.rust_sidecar.error_fallback_threshold": 5,
        }
    )
    client = build_rust_sidecar_client_from_config(cfg)
    assert client.endpoint == "http://127.0.0.1:9011"
    assert client.timeout_ms == 4567
    assert client.auto_fallback_on_error is False
    assert client.error_fallback_threshold == 5

    with pytest.raises(ValueError):
        build_rust_sidecar_client_from_config(_Cfg({}))


@pytest.mark.asyncio
async def test_rust_shell_operations_auto_fallback_after_threshold(monkeypatch):
    client = RustSidecarClient(
        endpoint="http://127.0.0.1:8787",
        timeout_ms=1000,
        auto_fallback_on_error=True,
        error_fallback_threshold=2,
    )

    class _FallbackShell:
        def __init__(self):
            self.calls = 0

        async def exec(self, command: str, cwd: str, *, timeout: int = 30):
            self.calls += 1
            return 0, "fallback-ok", ""

    fallback = _FallbackShell()
    shell_ops = RustShellOperations(client, fallback_shell_ops=fallback)  # type: ignore[arg-type]

    async def _raise_rpc(*, method: str, params=None, trace_id: str = ""):
        raise RustSidecarError(code="UNAVAILABLE", message="down")

    monkeypatch.setattr(client, "rpc", _raise_rpc)

    with pytest.raises(RustSidecarError):
        await shell_ops.exec("echo hi", ".", timeout=3)
    # second call should hit threshold and fallback instead of raising
    rc, out, err = await shell_ops.exec("echo hi", ".", timeout=3)
    assert rc == 0
    assert out == "fallback-ok"
    assert err == ""
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_rust_shell_operations_respects_rollout_gate(monkeypatch):
    client = _FakeClient()

    class _FallbackShell:
        async def exec(self, command: str, cwd: str, *, timeout: int = 30):
            return 0, "gate-fallback", ""

    shell_ops = RustShellOperations(client, fallback_shell_ops=_FallbackShell())  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.rust_sidecar.is_rust_allowed_for_current_context", lambda: False)

    rc, out, err = await shell_ops.exec("echo hi", ".", timeout=3)
    assert rc == 0
    assert out == "gate-fallback"
    assert err == ""
    assert client.calls == []


@pytest.mark.asyncio
async def test_rust_file_operations_respects_rollout_gate(monkeypatch):
    client = _FakeClient()

    class _FallbackFile:
        async def read_file(self, path: str) -> str:
            return "fallback-content"

        async def write_file(self, path: str, content: str) -> None:
            return None

        async def file_exists(self, path: str) -> bool:
            return True

        async def dir_exists(self, path: str) -> bool:
            return True

    file_ops = RustFileOperations(client, fallback_file_ops=_FallbackFile())  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.rust_sidecar.is_rust_allowed_for_current_context", lambda: False)

    assert await file_ops.read_file("a.txt") == "fallback-content"
    await file_ops.write_file("b.txt", "x")
    assert await file_ops.file_exists("a.txt") is True
    assert await file_ops.dir_exists("d") is True
    assert client.calls == []
