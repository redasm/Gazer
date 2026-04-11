"""Coding tools: file tools (read / write / edit)."""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from tools.base import FileOperations, ShellOperations

from .helpers import (
    MAX_READ_LINES,
    CodingToolBase,
    _emit_progress,
    _normalize_coding_params,
    _to_workspace_relative_path,
)
from .native_ops import (
    AmbiguousMatchError,
    NoMatchError,
    native_edit_file,
    native_read_file,
    native_write_file,
)
from .safety import _is_within_workspace

logger = logging.getLogger("CodingTools")


class ReadFileTool(CodingToolBase):
    """Read file contents with optional line-range pagination."""

    def __init__(
        self,
        workspace: Path,
        *,
        file_ops: Optional[FileOperations] = None,
        shell_ops: Optional[ShellOperations] = None,
    ):
        self._workspace = workspace.resolve()
        self._file_ops = file_ops
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "read_file"


    @property
    def description(self) -> str:
        return (
            "Read a file from the workspace. Returns numbered lines. "
            "Use offset/limit for large files."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root."},
                "file_path": {"type": "string", "description": "Alias for path (Claude-style)."},
                "offset": {
                    "type": "integer",
                    "description": "1-based starting line number. Defaults to 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to return. Defaults to 500.",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        path: str = "",
        file_path: str = "",
        offset: int = 1,
        limit: int = 500,
        _progress_callback: Any = None,
        **extra: Any,
    ) -> str:
        normalized = _normalize_coding_params(
            {"path": path, "file_path": file_path, "offset": offset, "limit": limit, **extra}
        )
        resolved_path = str(normalized.get("path", "") or "").strip()
        if not resolved_path:
            return self._error("CODING_READ_ARGS_REQUIRED", "path (or file_path) is required.")

        target = (self._workspace / resolved_path).resolve()
        if not _is_within_workspace(target, self._workspace):
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", "path must be inside the workspace.")

        safe_offset = max(1, int(offset or 1))
        safe_limit = min(max(1, int(limit or 500)), MAX_READ_LINES)
        await _emit_progress(
            _progress_callback,
            stage="prepare",
            message=f"Reading {resolved_path} (offset={safe_offset}, limit={safe_limit})",
        )

        try:
            result = await native_read_file(
                resolved_path, self._workspace,
                offset=safe_offset, limit=safe_limit,
            )
        except PermissionError as exc:
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", str(exc))
        except Exception as exc:
            message = str(exc)
            if "file not found" in message.lower():
                return self._error("CODING_FILE_NOT_FOUND", message)
            return self._error("CODING_FILE_READ_FAILED", message)

        if result.is_error:
            return self._error("CODING_FILE_NOT_FOUND", result.text)

        await _emit_progress(
            _progress_callback,
            stage="summary",
            message=f"Read {resolved_path}",
        )
        return f"[{resolved_path}]\n{result.text}"


class WriteFileTool(CodingToolBase):
    """Write or create a file."""

    def __init__(self, workspace: Path, *, file_ops: Optional[FileOperations] = None):
        self._workspace = workspace.resolve()
        self._file_ops = file_ops

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file, creating parent directories as needed. Overwrites existing files."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root."},
                "file_path": {"type": "string", "description": "Alias for path (Claude-style)."},
                "content": {"type": "string", "description": "Content to write."},
            },
            "required": ["content"],
        }

    async def execute(
        self,
        path: str = "",
        file_path: str = "",
        content: str = "",
        _progress_callback: Any = None,
        **extra: Any,
    ) -> str:
        normalized = _normalize_coding_params(
            {"path": path, "file_path": file_path, "content": content, **extra}
        )
        resolved_path = str(normalized.get("path", "") or "").strip()
        normalized_content = str(normalized.get("content", "") or "")
        if not resolved_path:
            return self._error("CODING_WRITE_ARGS_REQUIRED", "path (or file_path) is required.")
        if not normalized_content and content == "":
            return self._error("CODING_WRITE_ARGS_REQUIRED", "content is required.")

        target = (self._workspace / resolved_path).resolve()
        if not _is_within_workspace(target, self._workspace):
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", "path must be inside the workspace.")

        await _emit_progress(
            _progress_callback,
            stage="prepare",
            message=f"Writing {resolved_path}",
        )
        try:
            await native_write_file(resolved_path, self._workspace, normalized_content)
        except PermissionError as exc:
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", str(exc))
        except Exception as exc:
            return self._error("CODING_FILE_WRITE_FAILED", str(exc))

        line_count = normalized_content.count("\n") + (
            1 if normalized_content and not normalized_content.endswith("\n") else 0
        )
        await _emit_progress(
            _progress_callback,
            stage="summary",
            message=f"Wrote {line_count} line(s) to {resolved_path}",
        )
        return f"Wrote {line_count} lines to {resolved_path}."


class EditFileTool(CodingToolBase):
    """Edit a file by replacing a text match (exact first, then fuzzy fallback)."""

    def __init__(self, workspace: Path, *, file_ops: Optional[FileOperations] = None):
        self._workspace = workspace.resolve()
        self._file_ops = file_ops

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing a text match. "
            "Provide old_text (the existing snippet) and new_text (the replacement). "
            "The old_text must appear exactly once in the file. "
            "Fuzzy matching is used as fallback (whitespace normalization, smart-quote equivalence)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root."},
                "file_path": {"type": "string", "description": "Alias for path (Claude-style)."},
                "old_text": {"type": "string", "description": "Text to find (must be unique)."},
                "old_string": {"type": "string", "description": "Alias for old_text (Claude-style)."},
                "oldText": {"type": "string", "description": "Alias for old_text."},
                "new_text": {"type": "string", "description": "Replacement text."},
                "new_string": {"type": "string", "description": "Alias for new_text (Claude-style)."},
                "newText": {"type": "string", "description": "Alias for new_text."},
            },
            "required": [],
        }

    async def execute(
        self,
        path: str = "",
        file_path: str = "",
        old_text: str = "",
        new_text: str = "",
        _progress_callback: Any = None,
        **extra: Any,
    ) -> str:
        normalized = _normalize_coding_params(
            {
                "path": path,
                "file_path": file_path,
                "old_text": old_text,
                "new_text": new_text,
                **extra,
            }
        )
        resolved_path = str(normalized.get("path", "") or "").strip()
        normalized_old = str(normalized.get("old_text", "") or "")
        normalized_new = str(normalized.get("new_text", "") or "")
        if not resolved_path:
            return self._error("CODING_EDIT_ARGS_REQUIRED", "path (or file_path) is required.")
        if not normalized_old:
            return self._error("CODING_EDIT_ARGS_REQUIRED", "old_text (or old_string) is required.")
        if not normalized_new:
            return self._error("CODING_EDIT_ARGS_REQUIRED", "new_text (or new_string) is required.")

        target = (self._workspace / resolved_path).resolve()
        if not _is_within_workspace(target, self._workspace):
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", "path must be inside the workspace.")

        if not target.is_file():
            return self._error("CODING_FILE_NOT_FOUND", f"'{resolved_path}' does not exist.")

        await _emit_progress(
            _progress_callback,
            stage="prepare",
            message=f"Editing {resolved_path}",
        )
        try:
            result = await native_edit_file(
                resolved_path, self._workspace, normalized_old, normalized_new,
            )
        except PermissionError as exc:
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", str(exc))
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "file not found" in lowered:
                return self._error("CODING_FILE_NOT_FOUND", message)
            return self._error("CODING_FILE_WRITE_FAILED", message)

        if result.is_error:
            text_lower = result.text.lower()
            if "matched" in text_lower and "locations" in text_lower:
                return self._error("CODING_EDIT_AMBIGUOUS_OLD_TEXT", result.text)
            if "not found" in text_lower:
                return self._error("CODING_EDIT_OLD_TEXT_NOT_FOUND", result.text)
            return self._error("CODING_FILE_WRITE_FAILED", result.text)

        fuzzy_note = " (fuzzy match)" if "fuzzy" in (result.text or "") else ""
        await _emit_progress(
            _progress_callback,
            stage="summary",
            message=f"Edited {resolved_path}{fuzzy_note}",
        )
        return f"Edited {resolved_path}: replaced 1 occurrence{fuzzy_note}."
