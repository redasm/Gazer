"""Base class for agent tools."""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union


class CancellationToken:
    """Lightweight cancellation token inspired by AbortController/AbortSignal.

    Pass an instance into tool ``execute()`` calls so that long-running
    operations can check ``token.is_cancelled`` and bail out early.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        """Await until cancelled."""
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        """Raise ``asyncio.CancelledError`` if cancelled."""
        if self._event.is_set():
            raise asyncio.CancelledError("Operation cancelled by user")


# ---------------------------------------------------------------------------
# Operations protocol for tool backend abstraction
# ---------------------------------------------------------------------------

class FileOperations:
    """Pluggable file I/O backend.

    Override to delegate file reads/writes to remote systems (SSH, Docker, etc.).
    Default implementation uses the local filesystem.
    """

    async def read_file(self, path: str) -> str:
        """Read file content as UTF-8 string."""
        from pathlib import Path as _P
        return _P(path).read_text(encoding="utf-8", errors="replace")

    async def write_file(self, path: str, content: str) -> None:
        """Write content to a file (create parent dirs as needed)."""
        from pathlib import Path as _P
        p = _P(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def file_exists(self, path: str) -> bool:
        from pathlib import Path as _P
        return _P(path).is_file()

    async def dir_exists(self, path: str) -> bool:
        from pathlib import Path as _P
        return _P(path).is_dir()


class ShellOperations:
    """Pluggable shell execution backend.

    Override to run commands via SSH, Docker exec, etc.
    Default implementation uses local subprocess.
    """

    async def exec(
        self, command: str, cwd: str, *, timeout: int = 30,
    ) -> tuple:
        """Execute a shell command. Returns (exit_code, stdout, stderr)."""
        import asyncio as _aio
        proc = await _aio.create_subprocess_shell(
            command, cwd=cwd,
            stdout=_aio.subprocess.PIPE,
            stderr=_aio.subprocess.PIPE,
        )
        stdout, stderr = await _aio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )





class Tool(ABC):
    """
    Abstract base class for agent tools.
    
    Tools are capabilities that the agent can use to interact with
    the environment, such as reading files, executing commands, etc.
    """
    
    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema for tool parameters."""
        pass

    @property
    def provider(self) -> str:
        """Logical provider/category name for policy filtering."""
        return "core"

    @property
    def owner_only(self) -> bool:
        """Whether this tool is restricted to owner senders only.

        Override and return ``True`` in subclasses for tools that run
        arbitrary code, access hardware, or perform privileged operations.
        Non-owner senders will not see these tools.
        """
        return False

    @property
    def is_read_only(self) -> bool:
        """Whether this tool is read-only / informational.

        Override in subclasses. Used for policy hints but not access control.
        """
        return False

    @property
    def bypass_release_gate(self) -> bool:
        """Whether this tool bypasses release gate enforcement.
        
        Override and return ``True`` for inherently safe tools (e.g. basic info gathering)
        that should remain available even when the system is under an active release gate.
        """
        return False



    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        Execute the tool with given parameters.
        
        Args:
            **kwargs: Tool-specific parameters.
        
        Returns:
            String result of the tool execution.
        """
        pass

    def validate_params(self, params: Dict[str, Any]) -> List[str]:
        """Validate tool parameters against JSON schema. Returns error list (empty if valid)."""
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return self._validate(params, {**schema, "type": "object"}, "")

    def _validate(self, val: Any, schema: Dict[str, Any], path: str) -> List[str]:
        t, label = schema.get("type"), path or "parameter"
        if t in self._TYPE_MAP and not isinstance(val, self._TYPE_MAP[t]):
            return [f"{label} should be {t}"]
        
        errors = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number") and isinstance(val, (int, float)):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string" and isinstance(val, str):
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {path + '.' + k if path else k}")
            for k, v in val.items():
                if k in props:
                    errors.extend(self._validate(v, props[k], path + '.' + k if path else k))
        if t == "array" and "items" in schema:
            for i, item in enumerate(val):
                errors.extend(self._validate(item, schema["items"], f"{path}[{i}]" if path else f"[{i}]"))
        return errors
    
    def to_schema(self) -> Dict[str, Any]:
        """Convert tool to OpenAI-compatible function schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
