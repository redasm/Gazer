"""Rust sidecar client and backend adapters."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from runtime.rust_gate import is_rust_allowed_for_current_context
from runtime.rust_rpc import RpcRequest, RpcResponse, map_sidecar_error_code
from tools.base import FileOperations, ShellOperations

logger = logging.getLogger("RustSidecar")


class RustSidecarError(RuntimeError):
    """Error raised when calling rust sidecar endpoints."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        trace_id: str = "",
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "UNKNOWN").strip() or "UNKNOWN"
        self.message = str(message or "sidecar call failed").strip() or "sidecar call failed"
        self.trace_id = str(trace_id or "").strip()
        self.status_code = int(status_code) if status_code is not None else None
        self.mapped_code = map_sidecar_error_code(self.code)

    def to_tool_error(self) -> str:
        head = f"Error [{self.mapped_code}]: {self.message}"
        if self.trace_id:
            head = f"{head} (trace_id={self.trace_id})"
        return head


class RustSidecarClient:
    """Async client for Rust sidecar HTTP endpoints."""

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_ms: int = 3000,
        auto_fallback_on_error: bool = True,
        error_fallback_threshold: int = 3,
    ) -> None:
        normalized = str(endpoint or "").strip().rstrip("/")
        if not normalized:
            raise ValueError("runtime.rust_sidecar.endpoint is required.")
        self.endpoint = normalized
        self.timeout_ms = max(1, int(timeout_ms or 3000))
        self.auto_fallback_on_error = bool(auto_fallback_on_error)
        self.error_fallback_threshold = max(1, int(error_fallback_threshold or 3))
        self._error_streak = 0

    @property
    def error_streak(self) -> int:
        return self._error_streak

    def record_success(self) -> None:
        self._error_streak = 0

    def record_failure(self) -> None:
        self._error_streak += 1

    def should_fallback(self) -> bool:
        return self.auto_fallback_on_error and self._error_streak >= self.error_fallback_threshold

    async def _request_json(
        self,
        *,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_path = "/" + str(path or "").lstrip("/")
        url = f"{self.endpoint}{clean_path}"
        body = b""
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        timeout_seconds = float(self.timeout_ms) / 1000.0

        def _do_request() -> tuple[int, str]:
            req = urlrequest.Request(
                url=url,
                method=str(method or "GET").upper(),
                data=body if payload is not None else None,
                headers=headers,
            )
            with urlrequest.urlopen(req, timeout=timeout_seconds) as resp:
                status = int(getattr(resp, "status", 200))
                raw = resp.read().decode("utf-8", errors="replace")
                return status, raw

        try:
            status_code, raw_text = await asyncio.to_thread(_do_request)
        except urlerror.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise RustSidecarError(
                code="UNAVAILABLE",
                message=f"sidecar HTTP error {exc.code}: {detail or exc.reason}",
                status_code=int(exc.code),
            ) from exc
        except urlerror.URLError as exc:
            raise RustSidecarError(
                code="CONNECTION_FAILED",
                message=f"sidecar connection failed: {exc.reason}",
            ) from exc
        except asyncio.TimeoutError as exc:
            raise RustSidecarError(
                code="TIMEOUT",
                message=f"sidecar request timed out after {self.timeout_ms}ms",
            ) from exc
        except TimeoutError as exc:
            raise RustSidecarError(
                code="TIMEOUT",
                message=f"sidecar request timed out after {self.timeout_ms}ms",
            ) from exc
        except OSError as exc:
            raise RustSidecarError(
                code="CONNECTION_FAILED",
                message=f"sidecar I/O error: {exc}",
            ) from exc

        if status_code >= 400:
            raise RustSidecarError(
                code="UNAVAILABLE",
                message=f"sidecar HTTP error {status_code}: {raw_text}",
                status_code=status_code,
            )

        try:
            parsed = json.loads(raw_text) if raw_text else {}
        except ValueError as exc:
            raise RustSidecarError(
                code="BAD_REQUEST",
                message=f"sidecar returned non-JSON response: {raw_text[:200]}",
                status_code=status_code,
            ) from exc
        if not isinstance(parsed, dict):
            raise RustSidecarError(
                code="BAD_REQUEST",
                message="sidecar response must be a JSON object",
                status_code=status_code,
            )
        return parsed

    async def health(self) -> Dict[str, Any]:
        return await self._request_json(path="/health")

    async def version(self) -> Dict[str, Any]:
        return await self._request_json(path="/version")

    async def capabilities(self) -> Dict[str, Any]:
        return await self._request_json(path="/capabilities")

    async def probe_minimal(self) -> Dict[str, Any]:
        """Fetch minimal readiness endpoints required by phase-0 contract."""
        health_payload = await self.health()
        version_payload = await self.version()
        capability_payload = await self.capabilities()
        return {
            "endpoint": self.endpoint,
            "health": health_payload,
            "version": version_payload,
            "capabilities": capability_payload,
        }

    async def rpc(
        self,
        *,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        trace_id: str = "",
    ) -> Dict[str, Any]:
        request = RpcRequest.create(method=method, params=params, trace_id=trace_id)
        payload = await self._request_json(path="/rpc", method="POST", payload=request.to_dict())
        response = RpcResponse.from_dict(payload)
        if response.ok:
            return dict(response.result)
        error = response.error
        if error is None:
            raise RustSidecarError(code="UNKNOWN", message="sidecar returned an invalid RPC error payload")
        raise RustSidecarError(
            code=error.code,
            message=error.message,
            trace_id=error.trace_id or response.trace_id,
        )


class RustShellOperations(ShellOperations):
    """Shell operations routed via rust sidecar RPC."""

    def __init__(
        self,
        client: RustSidecarClient,
        *,
        fallback_shell_ops: Optional[ShellOperations] = None,
    ) -> None:
        self._client = client
        self._fallback_shell_ops = fallback_shell_ops

    def _should_fallback(self) -> bool:
        probe = getattr(self._client, "should_fallback", None)
        return bool(probe()) if callable(probe) else False

    def _record_success(self) -> None:
        fn = getattr(self._client, "record_success", None)
        if callable(fn):
            fn()

    def _record_failure(self) -> None:
        fn = getattr(self._client, "record_failure", None)
        if callable(fn):
            fn()

    async def exec(
        self,
        command: str,
        cwd: str,
        *,
        timeout: int = 30,
    ) -> tuple:
        if not is_rust_allowed_for_current_context():
            if self._fallback_shell_ops is not None:
                return await self._fallback_shell_ops.exec(command, cwd, timeout=timeout)
            raise RustSidecarError(
                code="PERMISSION_DENIED",
                message="rust backend disabled by rollout policy for current caller",
            )
        if self._fallback_shell_ops is not None and self._should_fallback():
            return await self._fallback_shell_ops.exec(command, cwd, timeout=timeout)
        try:
            result = await self._client.rpc(
                method="shell.exec",
                params={
                    "command": str(command or ""),
                    "cwd": str(cwd or "."),
                    "timeout": int(timeout or 30),
                },
            )
            self._record_success()
        except RustSidecarError:
            self._record_failure()
            if self._fallback_shell_ops is not None and self._should_fallback():
                return await self._fallback_shell_ops.exec(command, cwd, timeout=timeout)
            raise
        return (
            int(result.get("exit_code", 0) or 0),
            str(result.get("stdout", "") or ""),
            str(result.get("stderr", "") or ""),
        )


class RustFileOperations(FileOperations):
    """File operations routed via rust sidecar RPC."""

    def __init__(
        self,
        client: RustSidecarClient,
        *,
        fallback_file_ops: Optional[FileOperations] = None,
    ) -> None:
        self._client = client
        self._fallback_file_ops = fallback_file_ops

    def _should_fallback(self) -> bool:
        probe = getattr(self._client, "should_fallback", None)
        return bool(probe()) if callable(probe) else False

    def _record_success(self) -> None:
        fn = getattr(self._client, "record_success", None)
        if callable(fn):
            fn()

    def _record_failure(self) -> None:
        fn = getattr(self._client, "record_failure", None)
        if callable(fn):
            fn()

    async def read_file(self, path: str) -> str:
        if not is_rust_allowed_for_current_context():
            if self._fallback_file_ops is not None:
                return await self._fallback_file_ops.read_file(path)
            raise RustSidecarError(
                code="PERMISSION_DENIED",
                message="rust backend disabled by rollout policy for current caller",
            )
        if self._fallback_file_ops is not None and self._should_fallback():
            return await self._fallback_file_ops.read_file(path)
        try:
            result = await self._client.rpc(method="files.read", params={"path": str(path or "")})
            self._record_success()
        except RustSidecarError:
            self._record_failure()
            if self._fallback_file_ops is not None and self._should_fallback():
                return await self._fallback_file_ops.read_file(path)
            raise
        return str(result.get("content", "") or "")

    async def write_file(self, path: str, content: str) -> None:
        if not is_rust_allowed_for_current_context():
            if self._fallback_file_ops is not None:
                await self._fallback_file_ops.write_file(path, content)
                return
            raise RustSidecarError(
                code="PERMISSION_DENIED",
                message="rust backend disabled by rollout policy for current caller",
            )
        if self._fallback_file_ops is not None and self._should_fallback():
            await self._fallback_file_ops.write_file(path, content)
            return
        try:
            await self._client.rpc(
                method="files.write",
                params={"path": str(path or ""), "content": str(content or "")},
            )
            self._record_success()
        except RustSidecarError:
            self._record_failure()
            if self._fallback_file_ops is not None and self._should_fallback():
                await self._fallback_file_ops.write_file(path, content)
                return
            raise

    async def file_exists(self, path: str) -> bool:
        if not is_rust_allowed_for_current_context():
            if self._fallback_file_ops is not None:
                return await self._fallback_file_ops.file_exists(path)
            raise RustSidecarError(
                code="PERMISSION_DENIED",
                message="rust backend disabled by rollout policy for current caller",
            )
        if self._fallback_file_ops is not None and self._should_fallback():
            return await self._fallback_file_ops.file_exists(path)
        try:
            result = await self._client.rpc(method="files.exists", params={"path": str(path or "")})
            self._record_success()
        except RustSidecarError:
            self._record_failure()
            if self._fallback_file_ops is not None and self._should_fallback():
                return await self._fallback_file_ops.file_exists(path)
            raise
        return bool(result.get("exists", False))

    async def dir_exists(self, path: str) -> bool:
        if not is_rust_allowed_for_current_context():
            if self._fallback_file_ops is not None:
                return await self._fallback_file_ops.dir_exists(path)
            raise RustSidecarError(
                code="PERMISSION_DENIED",
                message="rust backend disabled by rollout policy for current caller",
            )
        if self._fallback_file_ops is not None and self._should_fallback():
            return await self._fallback_file_ops.dir_exists(path)
        try:
            result = await self._client.rpc(method="files.dir_exists", params={"path": str(path or "")})
            self._record_success()
        except RustSidecarError:
            self._record_failure()
            if self._fallback_file_ops is not None and self._should_fallback():
                return await self._fallback_file_ops.dir_exists(path)
            raise
        return bool(result.get("exists", False))


def build_rust_sidecar_client_from_config(cfg: Any) -> RustSidecarClient:
    """Create sidecar client from config-like object."""
    endpoint = str(cfg.get("runtime.rust_sidecar.endpoint", "") or "").strip()
    timeout_raw = cfg.get("runtime.rust_sidecar.timeout_ms", 3000)
    try:
        timeout_ms = int(timeout_raw)
    except (TypeError, ValueError):
        timeout_ms = 3000
    auto_fallback = bool(cfg.get("runtime.rust_sidecar.auto_fallback_on_error", True))
    threshold_raw = cfg.get("runtime.rust_sidecar.error_fallback_threshold", 3)
    try:
        threshold = int(threshold_raw)
    except (TypeError, ValueError):
        threshold = 3
    return RustSidecarClient(
        endpoint=endpoint,
        timeout_ms=timeout_ms,
        auto_fallback_on_error=auto_fallback,
        error_fallback_threshold=threshold,
    )
