"""Sandbox mode -- Docker-based execution isolation.

When sandbox mode is enabled, ``ExecTool`` and file tools use these
Docker-backed operations instead of the local filesystem.
"""

import asyncio
import logging
import os
from typing import Any, Optional, Tuple

from tools.base import FileOperations, ShellOperations
from runtime.config_manager import config
from runtime.rust_gate import is_rust_allowed_for_current_context
from runtime.rust_sidecar import (
    RustSidecarClient,
    RustSidecarError,
    build_rust_sidecar_client_from_config,
)

logger = logging.getLogger("Sandbox")
DEFAULT_MAX_OUTPUT_CHARS = 100_000
DEFAULT_MAX_PARALLEL_CALLS = 4


def _trim_output(value: str, limit: int) -> str:
    text = str(value or "")
    max_chars = max(128, int(limit or DEFAULT_MAX_OUTPUT_CHARS))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


class DockerShellOperations(ShellOperations):
    """Execute commands inside a Docker container instead of the host.

    The container is created on first use and reused for the session.
    """

    def __init__(
        self,
        image: str = "python:3.11-slim",
        workspace_mode: str = "rw",  # "none", "ro", "rw"
        workspace_host: str = "",
        workspace_container: str = "/workspace",
        container_name: str = "gazer-sandbox",
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        max_parallel_calls: int = DEFAULT_MAX_PARALLEL_CALLS,
    ) -> None:
        self._image = image
        self._ws_mode = workspace_mode
        self._ws_host = workspace_host or os.getcwd()
        self._ws_container = workspace_container
        self._container = container_name
        self._max_output_chars = max(128, int(max_output_chars or DEFAULT_MAX_OUTPUT_CHARS))
        max_parallel = int(max_parallel_calls or DEFAULT_MAX_PARALLEL_CALLS)
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._started = False

    async def _ensure_container(self) -> None:
        """Start the sandbox container if not already running."""
        if self._started:
            return

        # Check if container exists
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", self._container,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0:
            # Create the container
            cmd = [
                "docker", "run", "-d",
                "--name", self._container,
                "--network", "none",  # No network by default for safety
            ]
            if self._ws_mode != "none":
                mode_flag = "ro" if self._ws_mode == "ro" else "rw"
                cmd.extend(["-v", f"{self._ws_host}:{self._ws_container}:{mode_flag}"])
            cmd.extend(["-w", self._ws_container, self._image, "sleep", "infinity"])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Failed to start sandbox container: {stderr.decode()}")
            logger.info(f"Sandbox container started: {self._container}")
        else:
            # Make sure it's running
            proc = await asyncio.create_subprocess_exec(
                "docker", "start", self._container,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

        self._started = True

    async def exec(
        self, command: str, cwd: str, *, timeout: int = 30,
    ) -> Tuple[int, str, str]:
        async with self._semaphore:
            await self._ensure_container()
            work_dir = cwd.replace("\\", "/")  # Normalize for container

            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-w", work_dir, self._container,
                "sh", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return (-1, "", f"Command timed out after {timeout}s")

            return (
                int(proc.returncode or 0),
                _trim_output(stdout.decode(errors="replace"), self._max_output_chars),
                _trim_output(stderr.decode(errors="replace"), self._max_output_chars),
            )

    async def cleanup(self) -> None:
        """Stop and remove the sandbox container."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", self._container,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            self._started = False
            logger.info(f"Sandbox container removed: {self._container}")
        except Exception as exc:
            logger.warning(f"Failed to remove sandbox container: {exc}")


class DockerFileOperations(FileOperations):
    """File operations inside the Docker sandbox container.

    Uses ``docker exec`` to read/write files inside the container.
    """

    def __init__(self, shell_ops: DockerShellOperations) -> None:
        self._shell = shell_ops

    async def read_file(self, path: str) -> str:
        rc, stdout, stderr = await self._shell.exec(f"cat {_shell_quote(path)}", cwd="/")
        if rc != 0:
            raise OSError(f"Cannot read {path}: {stderr}")
        return stdout

    async def write_file(self, path: str, content: str) -> None:
        # Use base64 to avoid shell quoting issues
        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        cmd = f"mkdir -p $(dirname {_shell_quote(path)}) && echo '{b64}' | base64 -d > {_shell_quote(path)}"
        rc, _, stderr = await self._shell.exec(cmd, cwd="/")
        if rc != 0:
            raise OSError(f"Cannot write {path}: {stderr}")

    async def file_exists(self, path: str) -> bool:
        rc, _, _ = await self._shell.exec(f"test -f {_shell_quote(path)}", cwd="/")
        return rc == 0

    async def dir_exists(self, path: str) -> bool:
        rc, _, _ = await self._shell.exec(f"test -d {_shell_quote(path)}", cwd="/")
        return rc == 0


class RustSandboxShellOperations(ShellOperations):
    """Execute sandbox commands via rust sidecar RPC."""

    def __init__(
        self,
        *,
        client: RustSidecarClient,
        image: str = "python:3.11-slim",
        workspace_mode: str = "rw",
        workspace_host: str = "",
        workspace_container: str = "/workspace",
        container_name: str = "gazer-sandbox",
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        max_parallel_calls: int = DEFAULT_MAX_PARALLEL_CALLS,
        fallback_shell_ops: Optional[ShellOperations] = None,
    ) -> None:
        self._client = client
        self._image = image
        self._ws_mode = workspace_mode
        self._ws_host = workspace_host or os.getcwd()
        self._ws_container = workspace_container
        self._container = container_name
        self._max_output_chars = max(128, int(max_output_chars or DEFAULT_MAX_OUTPUT_CHARS))
        max_parallel = int(max_parallel_calls or DEFAULT_MAX_PARALLEL_CALLS)
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._fallback_shell_ops = fallback_shell_ops

    async def exec(
        self,
        command: str,
        cwd: str,
        *,
        timeout: int = 30,
    ) -> Tuple[int, str, str]:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_shell_ops is not None:
            return await self._fallback_shell_ops.exec(command, cwd, timeout=timeout)
        if not rust_allowed:
            return -1, "", "rust backend disabled by rollout policy for current caller"
        params = {
            "command": str(command or ""),
            "cwd": str(cwd or "."),
            "timeout": int(timeout or 30),
            "image": self._image,
            "workspace_mode": self._ws_mode,
            "workspace_host": self._ws_host,
            "workspace_container": self._ws_container,
            "container_name": self._container,
            "max_output_chars": self._max_output_chars,
        }
        async with self._semaphore:
            try:
                payload = await self._client.rpc(method="sandbox.exec", params=params)
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

    async def cleanup(self) -> None:
        payload = {
            "image": self._image,
            "workspace_mode": self._ws_mode,
            "workspace_host": self._ws_host,
            "workspace_container": self._ws_container,
            "container_name": self._container,
        }
        try:
            await self._client.rpc(method="sandbox.cleanup", params=payload)
        except Exception:
            logger.debug("Rust sandbox cleanup failed", exc_info=True)


class RustSandboxFileOperations(FileOperations):
    """File operations in rust sidecar sandbox."""

    def __init__(
        self,
        shell_ops: RustSandboxShellOperations,
        *,
        fallback_file_ops: Optional[FileOperations] = None,
    ) -> None:
        self._shell = shell_ops
        self._fallback_file_ops = fallback_file_ops

    def _base_params(self) -> dict[str, Any]:
        return {
            "image": self._shell._image,
            "workspace_mode": self._shell._ws_mode,
            "workspace_host": self._shell._ws_host,
            "workspace_container": self._shell._ws_container,
            "container_name": self._shell._container,
        }

    async def read_file(self, path: str) -> str:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            return await self._fallback_file_ops.read_file(path)
        if not rust_allowed:
            raise OSError("rust backend disabled by rollout policy for current caller")
        payload = self._base_params()
        payload["path"] = str(path or "")
        result = await self._shell._client.rpc(method="sandbox.files.read", params=payload)
        return str(result.get("content", "") or "")

    async def write_file(self, path: str, content: str) -> None:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            await self._fallback_file_ops.write_file(path, content)
            return
        if not rust_allowed:
            raise OSError("rust backend disabled by rollout policy for current caller")
        payload = self._base_params()
        payload["path"] = str(path or "")
        payload["content"] = str(content or "")
        await self._shell._client.rpc(method="sandbox.files.write", params=payload)

    async def file_exists(self, path: str) -> bool:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            return await self._fallback_file_ops.file_exists(path)
        if not rust_allowed:
            return False
        payload = self._base_params()
        payload["path"] = str(path or "")
        result = await self._shell._client.rpc(method="sandbox.files.exists", params=payload)
        return bool(result.get("exists", False))

    async def dir_exists(self, path: str) -> bool:
        rust_allowed = is_rust_allowed_for_current_context()
        if not rust_allowed and self._fallback_file_ops is not None:
            return await self._fallback_file_ops.dir_exists(path)
        if not rust_allowed:
            return False
        payload = self._base_params()
        payload["path"] = str(path or "")
        result = await self._shell._client.rpc(method="sandbox.files.dir_exists", params=payload)
        return bool(result.get("exists", False))


def _shell_quote(s: str) -> str:
    """Minimal shell quoting for safety."""
    return "'" + s.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_sandbox_operations(
    cfg: Any = config,
    *,
    sidecar_client: Optional[RustSidecarClient] = None,
) -> Optional[Tuple[ShellOperations, FileOperations]]:
    """Return sandbox-backed operations if sandbox mode is enabled in config.

    Returns ``None`` if sandbox is disabled.
    """
    if not cfg.get("sandbox.enabled", False):
        return None

    image = cfg.get("sandbox.image", "python:3.11-slim")
    ws_mode = cfg.get("sandbox.workspace_mode", "rw")
    max_output_chars = int(cfg.get("coding.max_output_chars", DEFAULT_MAX_OUTPUT_CHARS) or DEFAULT_MAX_OUTPUT_CHARS)
    max_parallel_calls = int(
        cfg.get("coding.max_parallel_tool_calls", DEFAULT_MAX_PARALLEL_CALLS) or DEFAULT_MAX_PARALLEL_CALLS
    )
    runtime_backend = str(cfg.get("runtime.backend", "python") or "python").strip().lower()

    if runtime_backend == "rust":
        fallback_shell_ops = DockerShellOperations(
            image=image,
            workspace_mode=ws_mode,
            max_output_chars=max_output_chars,
            max_parallel_calls=max_parallel_calls,
        )
        fallback_file_ops = DockerFileOperations(fallback_shell_ops)
        client = sidecar_client or build_rust_sidecar_client_from_config(cfg)
        shell_ops = RustSandboxShellOperations(
            client=client,
            image=image,
            workspace_mode=ws_mode,
            max_output_chars=max_output_chars,
            max_parallel_calls=max_parallel_calls,
            fallback_shell_ops=fallback_shell_ops,
        )
        return shell_ops, RustSandboxFileOperations(shell_ops, fallback_file_ops=fallback_file_ops)

    shell_ops = DockerShellOperations(
        image=image,
        workspace_mode=ws_mode,
        max_output_chars=max_output_chars,
        max_parallel_calls=max_parallel_calls,
    )
    return shell_ops, DockerFileOperations(shell_ops)
