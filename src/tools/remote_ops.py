"""Remote operations backends for coding tools."""

from __future__ import annotations

import asyncio
import base64
import os
import shlex
from typing import Any, Dict, Optional, Tuple

from tools.base import FileOperations, ShellOperations
from runtime.rust_gate import is_rust_allowed_for_current_context
from runtime.rust_sidecar import (
    RustSidecarClient,
    RustSidecarError,
    build_rust_sidecar_client_from_config,
)

DEFAULT_MAX_OUTPUT_CHARS = 100_000
DEFAULT_MAX_PARALLEL_CALLS = 4


def _trim_output(value: str, limit: int) -> str:
    text = str(value or "")
    max_chars = max(128, int(limit or DEFAULT_MAX_OUTPUT_CHARS))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


class SSHShellOperations(ShellOperations):
    """Execute commands on a remote host over SSH using system ssh binary."""

    def __init__(
        self,
        *,
        host: str,
        user: str = "",
        port: int = 22,
        identity_file: str = "",
        strict_host_key_checking: bool = True,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        max_parallel_calls: int = DEFAULT_MAX_PARALLEL_CALLS,
    ) -> None:
        self._host = str(host or "").strip()
        self._user = str(user or "").strip()
        self._port = int(port or 22)
        self._identity_file = str(identity_file or "").strip()
        self._strict_host_key_checking = bool(strict_host_key_checking)
        self._max_output_chars = max(128, int(max_output_chars or DEFAULT_MAX_OUTPUT_CHARS))
        max_parallel = int(max_parallel_calls or DEFAULT_MAX_PARALLEL_CALLS)
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        if not self._host:
            raise ValueError("SSH host is required.")

    async def exec(self, command: str, cwd: str, *, timeout: int = 30) -> tuple:
        remote_target = self._host
        if self._user:
            remote_target = f"{self._user}@{self._host}"

        remote_cwd = str(cwd or ".").strip() or "."
        remote_script = f"cd {shlex.quote(remote_cwd)} && {command}"

        ssh_cmd = [
            "ssh",
            "-p",
            str(self._port),
            remote_target,
            remote_script,
        ]
        if self._identity_file:
            ssh_cmd[1:1] = ["-i", self._identity_file]
        if not self._strict_host_key_checking:
            ssh_cmd[1:1] = [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
            ]

        async with self._semaphore:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return -1, "", f"Command timed out after {timeout}s"
            return (
                int(proc.returncode or 0),
                _trim_output(stdout.decode(errors="replace"), self._max_output_chars),
                _trim_output(stderr.decode(errors="replace"), self._max_output_chars),
            )


class SSHFileOperations(FileOperations):
    """File operations over SSH backed by ``SSHShellOperations``."""

    def __init__(self, shell_ops: ShellOperations) -> None:
        self._shell = shell_ops

    async def read_file(self, path: str) -> str:
        rc, stdout, stderr = await self._shell.exec(f"cat {shlex.quote(path)}", cwd="/")
        if rc != 0:
            raise OSError(stderr.strip() or f"failed to read remote file: {path}")
        return stdout

    async def write_file(self, path: str, content: str) -> None:
        payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
        parent = os.path.dirname(path) or "."
        cmd = (
            f"mkdir -p {shlex.quote(parent)} && "
            f"printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(path)}"
        )
        rc, _stdout, stderr = await self._shell.exec(cmd, cwd="/")
        if rc != 0:
            raise OSError(stderr.strip() or f"failed to write remote file: {path}")

    async def file_exists(self, path: str) -> bool:
        rc, _stdout, _stderr = await self._shell.exec(f"test -f {shlex.quote(path)}", cwd="/")
        return rc == 0

    async def dir_exists(self, path: str) -> bool:
        rc, _stdout, _stderr = await self._shell.exec(f"test -d {shlex.quote(path)}", cwd="/")
        return rc == 0


class RustSSHShellOperations(ShellOperations):
    """Execute SSH commands via rust sidecar RPC."""

    def __init__(
        self,
        *,
        client: RustSidecarClient,
        host: str,
        user: str = "",
        port: int = 22,
        identity_file: str = "",
        strict_host_key_checking: bool = True,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        max_parallel_calls: int = DEFAULT_MAX_PARALLEL_CALLS,
        fallback_shell_ops: Optional[ShellOperations] = None,
    ) -> None:
        self._client = client
        self._host = str(host or "").strip()
        self._user = str(user or "").strip()
        self._port = int(port or 22)
        self._identity_file = str(identity_file or "").strip()
        self._strict_host_key_checking = bool(strict_host_key_checking)
        self._max_output_chars = max(128, int(max_output_chars or DEFAULT_MAX_OUTPUT_CHARS))
        max_parallel = int(max_parallel_calls or DEFAULT_MAX_PARALLEL_CALLS)
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._fallback_shell_ops = fallback_shell_ops
        if not self._host:
            raise ValueError("SSH host is required.")

    async def exec(self, command: str, cwd: str, *, timeout: int = 30) -> tuple:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_shell_ops is not None:
            return await self._fallback_shell_ops.exec(command, cwd, timeout=timeout)
        if not rust_allowed:
            return -1, "", "rust backend disabled by rollout policy for current caller"
        params = {
            "host": self._host,
            "user": self._user,
            "port": self._port,
            "identity_file": self._identity_file,
            "strict_host_key_checking": self._strict_host_key_checking,
            "command": str(command or ""),
            "cwd": str(cwd or "."),
            "timeout": int(timeout or 30),
            "max_output_chars": self._max_output_chars,
        }
        async with self._semaphore:
            try:
                payload = await self._client.rpc(method="ssh.exec", params=params)
            except RustSidecarError as exc:
                detail = exc.message
                if exc.trace_id:
                    detail = f"{detail} (trace_id={exc.trace_id})"
                return -1, "", detail

        return (
            int(payload.get("exit_code", 0) or 0),
            _trim_output(str(payload.get("stdout", "") or ""), self._max_output_chars),
            _trim_output(str(payload.get("stderr", "") or ""), self._max_output_chars),
        )


class RustSSHFileOperations(FileOperations):
    """File operations via rust sidecar SSH RPC."""

    def __init__(
        self,
        shell_ops: RustSSHShellOperations,
        *,
        fallback_file_ops: Optional[FileOperations] = None,
    ) -> None:
        self._shell = shell_ops
        self._fallback_file_ops = fallback_file_ops

    def _base_params(self) -> Dict[str, Any]:
        return {
            "host": self._shell._host,
            "user": self._shell._user,
            "port": self._shell._port,
            "identity_file": self._shell._identity_file,
            "strict_host_key_checking": self._shell._strict_host_key_checking,
        }

    async def read_file(self, path: str) -> str:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            return await self._fallback_file_ops.read_file(path)
        if not rust_allowed:
            raise OSError("rust backend disabled by rollout policy for current caller")
        params = self._base_params()
        params["path"] = str(path or "")
        payload = await self._shell._client.rpc(method="ssh.files.read", params=params)
        return str(payload.get("content", "") or "")

    async def write_file(self, path: str, content: str) -> None:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            await self._fallback_file_ops.write_file(path, content)
            return
        if not rust_allowed:
            raise OSError("rust backend disabled by rollout policy for current caller")
        params = self._base_params()
        params["path"] = str(path or "")
        params["content"] = str(content or "")
        await self._shell._client.rpc(method="ssh.files.write", params=params)

    async def file_exists(self, path: str) -> bool:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            return await self._fallback_file_ops.file_exists(path)
        if not rust_allowed:
            return False
        params = self._base_params()
        params["path"] = str(path or "")
        payload = await self._shell._client.rpc(method="ssh.files.exists", params=params)
        return bool(payload.get("exists", False))

    async def dir_exists(self, path: str) -> bool:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            return await self._fallback_file_ops.dir_exists(path)
        if not rust_allowed:
            return False
        params = self._base_params()
        params["path"] = str(path or "")
        payload = await self._shell._client.rpc(method="ssh.files.dir_exists", params=params)
        return bool(payload.get("exists", False))


def get_ssh_operations(
    cfg: Any,
    *,
    sidecar_client: Optional[RustSidecarClient] = None,
) -> Tuple[ShellOperations, FileOperations]:
    """Build SSH operations from config with python/rust backend selection."""
    host = str(cfg.get("coding.ssh.host", "") or "").strip()
    user = str(cfg.get("coding.ssh.user", "") or "").strip()
    port = int(cfg.get("coding.ssh.port", 22) or 22)
    identity_file = str(cfg.get("coding.ssh.identity_file", "") or "").strip()
    strict_host_key = bool(cfg.get("coding.ssh.strict_host_key_checking", True))
    max_output_chars = int(cfg.get("coding.max_output_chars", DEFAULT_MAX_OUTPUT_CHARS) or DEFAULT_MAX_OUTPUT_CHARS)
    max_parallel_calls = int(
        cfg.get("coding.max_parallel_tool_calls", DEFAULT_MAX_PARALLEL_CALLS) or DEFAULT_MAX_PARALLEL_CALLS
    )
    runtime_backend = str(cfg.get("runtime.backend", "python") or "python").strip().lower()

    if runtime_backend == "rust":
        fallback_shell_ops = SSHShellOperations(
            host=host,
            user=user,
            port=port,
            identity_file=identity_file,
            strict_host_key_checking=strict_host_key,
            max_output_chars=max_output_chars,
            max_parallel_calls=max_parallel_calls,
        )
        fallback_file_ops = SSHFileOperations(fallback_shell_ops)
        client = sidecar_client or build_rust_sidecar_client_from_config(cfg)
        shell_ops = RustSSHShellOperations(
            client=client,
            host=host,
            user=user,
            port=port,
            identity_file=identity_file,
            strict_host_key_checking=strict_host_key,
            max_output_chars=max_output_chars,
            max_parallel_calls=max_parallel_calls,
            fallback_shell_ops=fallback_shell_ops,
        )
        return shell_ops, RustSSHFileOperations(shell_ops, fallback_file_ops=fallback_file_ops)

    shell_ops = SSHShellOperations(
        host=host,
        user=user,
        port=port,
        identity_file=identity_file,
        strict_host_key_checking=strict_host_key,
        max_output_chars=max_output_chars,
        max_parallel_calls=max_parallel_calls,
    )
    return shell_ops, SSHFileOperations(shell_ops)
