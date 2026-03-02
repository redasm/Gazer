import pytest

from tools.sandbox import (
    DockerFileOperations,
    DockerShellOperations,
    RustSandboxFileOperations,
    RustSandboxShellOperations,
    get_sandbox_operations,
)


class _Cfg:
    def __init__(self, values):
        self._values = dict(values)

    def get(self, key_path: str, default=None):
        return self._values.get(key_path, default)


class _FakeRustClient:
    def __init__(self) -> None:
        self.calls = []

    async def rpc(self, *, method: str, params=None, trace_id: str = ""):
        self.calls.append((method, dict(params or {}), trace_id))
        if method == "sandbox.exec":
            return {"exit_code": 0, "stdout": "ok\n", "stderr": ""}
        if method in {"sandbox.files.read", "sandbox.files.exists", "sandbox.files.dir_exists"}:
            if method == "sandbox.files.read":
                return {"content": "payload"}
            return {"exists": True}
        return {}


def test_get_sandbox_operations_runtime_python():
    cfg = _Cfg(
        {
            "sandbox.enabled": True,
            "sandbox.image": "python:3.11-slim",
            "sandbox.workspace_mode": "rw",
            "coding.max_output_chars": 1000,
            "coding.max_parallel_tool_calls": 2,
            "runtime.backend": "python",
        }
    )
    shell_ops, file_ops = get_sandbox_operations(cfg)  # type: ignore[misc]
    assert isinstance(shell_ops, DockerShellOperations)
    assert isinstance(file_ops, DockerFileOperations)


def test_get_sandbox_operations_runtime_rust():
    cfg = _Cfg(
        {
            "sandbox.enabled": True,
            "sandbox.image": "python:3.11-slim",
            "sandbox.workspace_mode": "rw",
            "coding.max_output_chars": 1000,
            "coding.max_parallel_tool_calls": 2,
            "runtime.backend": "rust",
        }
    )
    shell_ops, file_ops = get_sandbox_operations(cfg, sidecar_client=_FakeRustClient())  # type: ignore[misc,arg-type]
    assert isinstance(shell_ops, RustSandboxShellOperations)
    assert isinstance(file_ops, RustSandboxFileOperations)


@pytest.mark.asyncio
async def test_rust_sandbox_shell_and_files():
    client = _FakeRustClient()
    shell = RustSandboxShellOperations(client=client)
    files = RustSandboxFileOperations(shell)

    rc, out, err = await shell.exec("echo hi", ".", timeout=3)
    assert rc == 0
    assert out.strip() == "ok"
    assert err == ""

    assert await files.read_file("/work/a.txt") == "payload"
    assert await files.file_exists("/work/a.txt") is True
    assert await files.dir_exists("/work") is True
    await files.write_file("/work/a.txt", "new")

    methods = [item[0] for item in client.calls]
    assert methods == [
        "sandbox.exec",
        "sandbox.files.read",
        "sandbox.files.exists",
        "sandbox.files.dir_exists",
        "sandbox.files.write",
    ]


@pytest.mark.asyncio
async def test_rust_sandbox_operations_respect_rollout_gate(monkeypatch):
    client = _FakeRustClient()
    fallback_shell = DockerShellOperations()

    async def _fallback_exec(command: str, cwd: str, *, timeout: int = 30):
        return 0, "fallback-ok", ""

    monkeypatch.setattr(fallback_shell, "exec", _fallback_exec)
    shell = RustSandboxShellOperations(client=client, fallback_shell_ops=fallback_shell)
    fallback_files = DockerFileOperations(fallback_shell)

    async def _read_file(path: str) -> str:
        return "fallback-content"

    async def _write_file(path: str, content: str) -> None:
        return None

    async def _exists(path: str) -> bool:
        return True

    monkeypatch.setattr(fallback_files, "read_file", _read_file)
    monkeypatch.setattr(fallback_files, "write_file", _write_file)
    monkeypatch.setattr(fallback_files, "file_exists", _exists)
    monkeypatch.setattr(fallback_files, "dir_exists", _exists)
    files = RustSandboxFileOperations(shell, fallback_file_ops=fallback_files)
    monkeypatch.setattr("tools.sandbox.is_rust_allowed_for_current_context", lambda: False)

    rc, out, err = await shell.exec("echo hi", ".", timeout=3)
    assert rc == 0
    assert out == "fallback-ok"
    assert err == ""
    assert await files.read_file("/work/a.txt") == "fallback-content"
    await files.write_file("/work/a.txt", "x")
    assert await files.file_exists("/work/a.txt") is True
    assert await files.dir_exists("/work") is True
    assert client.calls == []
