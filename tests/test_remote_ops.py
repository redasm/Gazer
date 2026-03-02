import asyncio

import pytest

from tools.remote_ops import (
    RustSSHFileOperations,
    RustSSHShellOperations,
    SSHFileOperations,
    SSHShellOperations,
    get_ssh_operations,
)


@pytest.mark.asyncio
async def test_ssh_shell_operations_builds_command(monkeypatch):
    calls = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    ops = SSHShellOperations(
        host="10.0.0.5",
        user="robot",
        port=2222,
        identity_file="/tmp/id_rsa",
        strict_host_key_checking=False,
    )
    rc, out, err = await ops.exec("echo hello", "/workspace/project", timeout=5)
    assert rc == 0
    assert out.strip() == "ok"
    assert err == ""

    cmd = list(calls["cmd"])
    assert cmd[0] == "ssh"
    assert "robot@10.0.0.5" in cmd
    assert "cd /workspace/project && echo hello" in cmd


def test_ssh_shell_operations_requires_host():
    with pytest.raises(ValueError):
        SSHShellOperations(host="")


@pytest.mark.asyncio
async def test_ssh_file_operations_read_write(monkeypatch):
    state = {"files": {}}

    async def _fake_exec(command: str, cwd: str, *, timeout: int = 30):
        if command.startswith("cat "):
            target = command.split("cat ", 1)[1].strip().strip("'")
            if target not in state["files"]:
                return 1, "", "not found"
            return 0, state["files"][target], ""
        if "| base64 -d >" in command:
            import re
            path_match = re.search(r"base64 -d >\s+'?([^'\s]+)'?$", command)
            payload_match = re.search(r"printf %s\s+'?([^'\s]+)'?\s+\|", command)
            if not path_match or not payload_match:
                return 1, "", "bad payload"
            import base64
            decoded = base64.b64decode(payload_match.group(1)).decode("utf-8")
            state["files"][path_match.group(1)] = decoded
            return 0, "", ""
        if command.startswith("test -f "):
            target = command.split("test -f ", 1)[1].strip().strip("'")
            return (0, "", "") if target in state["files"] else (1, "", "")
        if command.startswith("test -d "):
            return 0, "", ""
        return 1, "", "unsupported"

    shell = SSHShellOperations(host="dummy")
    monkeypatch.setattr(shell, "exec", _fake_exec)
    files = SSHFileOperations(shell)

    await files.write_file("/work/a.txt", "hello")
    assert await files.file_exists("/work/a.txt") is True
    assert await files.dir_exists("/work") is True
    content = await files.read_file("/work/a.txt")
    assert content == "hello"


@pytest.mark.asyncio
async def test_ssh_shell_operations_timeout_is_standardized(monkeypatch):
    class _SlowProc:
        returncode = 0

        async def communicate(self):
            await asyncio.sleep(0.2)
            return b"", b""

        def kill(self):
            return None

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        return _SlowProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    ops = SSHShellOperations(host="10.0.0.5")
    rc, out, err = await ops.exec("echo hello", "/workspace/project", timeout=0.01)
    assert rc == -1
    assert out == ""
    assert "timed out" in err


@pytest.mark.asyncio
async def test_ssh_shell_operations_truncates_output(monkeypatch):
    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"a" * 400), (b"b" * 400)

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    ops = SSHShellOperations(host="10.0.0.5", max_output_chars=128)
    rc, out, err = await ops.exec("echo hello", "/workspace/project", timeout=5)
    assert rc == 0
    assert "(truncated)" in out
    assert "(truncated)" in err


class _FakeRustClient:
    def __init__(self) -> None:
        self.calls = []

    async def rpc(self, *, method: str, params=None, trace_id: str = ""):
        self.calls.append((method, dict(params or {}), trace_id))
        if method == "ssh.exec":
            return {"exit_code": 0, "stdout": "ok\n", "stderr": ""}
        if method == "ssh.files.read":
            return {"content": "hello"}
        if method in {"ssh.files.write", "ssh.files.exists", "ssh.files.dir_exists"}:
            if method.endswith("exists"):
                return {"exists": True}
            return {}
        return {}


@pytest.mark.asyncio
async def test_rust_ssh_shell_and_file_operations():
    client = _FakeRustClient()
    shell = RustSSHShellOperations(client=client, host="10.0.0.5")
    files = RustSSHFileOperations(shell)

    rc, out, err = await shell.exec("echo hi", "/workspace", timeout=3)
    assert rc == 0
    assert out.strip() == "ok"
    assert err == ""
    assert await files.read_file("/work/a.txt") == "hello"
    assert await files.file_exists("/work/a.txt") is True
    assert await files.dir_exists("/work") is True
    await files.write_file("/work/a.txt", "new")

    methods = [item[0] for item in client.calls]
    assert methods == [
        "ssh.exec",
        "ssh.files.read",
        "ssh.files.exists",
        "ssh.files.dir_exists",
        "ssh.files.write",
    ]


def test_get_ssh_operations_uses_runtime_backend_rust(monkeypatch):
    class _Cfg:
        def get(self, key_path: str, default=None):
            values = {
                "coding.ssh.host": "10.0.0.5",
                "coding.ssh.user": "robot",
                "coding.ssh.port": 22,
                "coding.ssh.identity_file": "",
                "coding.ssh.strict_host_key_checking": True,
                "coding.max_output_chars": 1000,
                "coding.max_parallel_tool_calls": 2,
                "runtime.backend": "rust",
            }
            return values.get(key_path, default)

    cfg = _Cfg()
    shell_ops, file_ops = get_ssh_operations(cfg, sidecar_client=_FakeRustClient())  # type: ignore[arg-type]
    assert isinstance(shell_ops, RustSSHShellOperations)
    assert isinstance(file_ops, RustSSHFileOperations)


@pytest.mark.asyncio
async def test_rust_ssh_operations_respect_rollout_gate(monkeypatch):
    client = _FakeRustClient()
    fallback_shell = SSHShellOperations(host="10.0.0.5")

    async def _fallback_exec(command: str, cwd: str, *, timeout: int = 30):
        return 0, "fallback-ok", ""

    monkeypatch.setattr(fallback_shell, "exec", _fallback_exec)
    shell = RustSSHShellOperations(
        client=client,
        host="10.0.0.5",
        fallback_shell_ops=fallback_shell,
    )
    fallback_file = SSHFileOperations(fallback_shell)

    async def _read_file(path: str) -> str:
        return "fallback-content"

    async def _write_file(path: str, content: str) -> None:
        return None

    async def _exists(path: str) -> bool:
        return True

    monkeypatch.setattr(fallback_file, "read_file", _read_file)
    monkeypatch.setattr(fallback_file, "write_file", _write_file)
    monkeypatch.setattr(fallback_file, "file_exists", _exists)
    monkeypatch.setattr(fallback_file, "dir_exists", _exists)
    files = RustSSHFileOperations(shell, fallback_file_ops=fallback_file)
    monkeypatch.setattr("tools.remote_ops.is_rust_allowed_for_current_context", lambda: False)

    rc, out, err = await shell.exec("echo hi", ".", timeout=3)
    assert rc == 0
    assert out == "fallback-ok"
    assert err == ""
    assert await files.read_file("/work/a.txt") == "fallback-content"
    await files.write_file("/work/a.txt", "x")
    assert await files.file_exists("/work/a.txt") is True
    assert await files.dir_exists("/work") is True
    assert client.calls == []
