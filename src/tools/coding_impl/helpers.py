"""Coding tools: helpers.

Shared utilities for coding tool classes.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base import ShellOperations, Tool

logger = logging.getLogger("CodingTools")

MAX_OUTPUT_CHARS = 100_000
MAX_READ_LINES = 10_000

_IMAGE_SUFFIXES = frozenset(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'))


def _to_workspace_relative_path(workspace: Path, target: Path) -> str:
    try:
        rel = target.relative_to(workspace)
    except ValueError:
        return str(target)
    rel_str = str(rel).replace("\\", "/")
    return rel_str or "."


def _extract_structured_text(value: Any, depth: int = 0) -> Optional[str]:
    if depth > 6:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_extract_structured_text(item, depth + 1) for item in value]
        merged = "".join(part for part in parts if isinstance(part, str))
        return merged or None
    if not isinstance(value, dict):
        return None

    if isinstance(value.get("text"), str):
        return str(value["text"])
    if isinstance(value.get("content"), str):
        return str(value["content"])
    if isinstance(value.get("content"), list):
        return _extract_structured_text(value.get("content"), depth + 1)
    if isinstance(value.get("parts"), list):
        return _extract_structured_text(value.get("parts"), depth + 1)
    raw_value = value.get("value")
    if isinstance(raw_value, str) and raw_value:
        raw_type = str(value.get("type", "") or "").lower()
        raw_kind = str(value.get("kind", "") or "").lower()
        if "text" in raw_type or raw_kind == "text":
            return raw_value
    return None


def _normalize_coding_params(params: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(params)

    def _is_blank(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def _promote_alias(primary_key: str, alias_key: str) -> None:
        if alias_key not in normalized:
            return
        alias_value = normalized.pop(alias_key)
        if primary_key not in normalized or _is_blank(normalized.get(primary_key)):
            normalized[primary_key] = alias_value

    _promote_alias("path", "file_path")
    _promote_alias("old_text", "old_string")
    _promote_alias("new_text", "new_string")
    _promote_alias("old_text", "oldText")
    _promote_alias("new_text", "newText")

    for key in ("content", "old_text", "new_text"):
        extracted = _extract_structured_text(normalized.get(key))
        if isinstance(extracted, str):
            normalized[key] = extracted

    return normalized


async def _run_shell_command(
    *,
    shell_ops: Optional[ShellOperations],
    command: str,
    cwd: Path,
    timeout: int = 30,
) -> tuple[int, str, str]:
    if shell_ops is not None:
        return await shell_ops.exec(command, str(cwd), timeout=timeout)
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode,
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
    )


class CodingToolBase(Tool):
    @property
    def provider(self) -> str:
        return "coding"

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"
