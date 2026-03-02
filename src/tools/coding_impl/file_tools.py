"""Coding tools: file tools.

Extracted from coding.py.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from tools.base import FileOperations, ShellOperations, ToolSafetyTier

from .helpers import (
    MAX_READ_LINES,
    CodingToolBase,
    _create_nexum_tool,
    _load_nexum_edit_diff_module,
    _normalize_coding_params,
    _render_nexum_tool_result,
    _to_workspace_relative_path,
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
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

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

        if self._file_ops is not None or self._shell_ops is not None:
            logger.info("ReadFileTool file_ops/shell_ops are ignored in Nexum mode.")

        nexum_tool = _create_nexum_tool("read", str(self._workspace))
        if nexum_tool is None:
            return self._error("CODING_READ_BACKEND_UNAVAILABLE", "Nexum read tool is unavailable.")

        rel_path = _to_workspace_relative_path(self._workspace, target)
        safe_offset = max(1, int(offset or 1))
        safe_limit = min(max(1, int(limit or 500)), MAX_READ_LINES)
        try:
            result_obj = await nexum_tool.execute(
                "gazer_read_file",
                {"path": rel_path, "offset": safe_offset, "limit": safe_limit},
                None,
                None,
            )
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "file not found" in lowered:
                return self._error("CODING_FILE_NOT_FOUND", message)
            return self._error("CODING_FILE_READ_FAILED", message)

        return f"[{resolved_path}]\n{_render_nexum_tool_result(result_obj)}"


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

        if self._file_ops is not None:
            logger.info("WriteFileTool file_ops is ignored in Nexum mode.")

        nexum_tool = _create_nexum_tool("write", str(self._workspace))
        if nexum_tool is None:
            return self._error("CODING_WRITE_BACKEND_UNAVAILABLE", "Nexum write tool is unavailable.")

        rel_path = _to_workspace_relative_path(self._workspace, target)
        try:
            await nexum_tool.execute(
                "gazer_write_file",
                {"path": rel_path, "content": normalized_content},
                None,
                None,
            )
        except Exception as exc:
            return self._error("CODING_FILE_WRITE_FAILED", str(exc))

        line_count = normalized_content.count("\n") + (
            1 if normalized_content and not normalized_content.endswith("\n") else 0
        )
        return f"Wrote {line_count} lines to {resolved_path}."


def _fuzzy_find(content: str, old_text: str) -> Optional[tuple[int, int, str]]:
    """Find a unique fuzzy match using Nexum edit-diff helpers."""
    pi_edit_diff = _load_nexum_edit_diff_module()
    if pi_edit_diff is None:
        return None

    match = pi_edit_diff.fuzzy_find_text(content, old_text)
    if not bool(getattr(match, "found", False)):
        return None

    if bool(getattr(match, "used_fuzzy_match", False)):
        fuzzy_old = pi_edit_diff.normalize_for_fuzzy_match(old_text)
        occurrences = str(match.content_for_replacement).count(str(fuzzy_old))
    else:
        occurrences = content.count(old_text)

    if occurrences != 1:
        return None
    return int(match.index), int(match.match_length), str(match.content_for_replacement)


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

        if self._file_ops is not None:
            logger.info("EditFileTool file_ops is ignored in Nexum mode.")

        if not target.is_file():
            return self._error("CODING_FILE_NOT_FOUND", f"'{resolved_path}' does not exist.")

        fuzzy_used = False
        try:
            before = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return self._error("CODING_FILE_READ_FAILED", f"Error reading file: {exc}")

        fuzzy = _fuzzy_find(before, normalized_old)
        if fuzzy is not None and before.count(normalized_old) == 0:
            fuzzy_used = True

        nexum_tool = _create_nexum_tool("edit", str(self._workspace))
        if nexum_tool is None:
            return self._error("CODING_EDIT_BACKEND_UNAVAILABLE", "Nexum edit tool is unavailable.")

        rel_path = _to_workspace_relative_path(self._workspace, target)
        try:
            await nexum_tool.execute(
                "gazer_edit_file",
                {"path": rel_path, "oldText": normalized_old, "newText": normalized_new},
                None,
                None,
            )
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "file not found" in lowered:
                return self._error("CODING_FILE_NOT_FOUND", message)
            if "occurrences" in lowered and "unique" in lowered:
                return self._error("CODING_EDIT_AMBIGUOUS_OLD_TEXT", message)
            if "could not find the exact text" in lowered:
                return self._error("CODING_EDIT_OLD_TEXT_NOT_FOUND", message)
            return self._error("CODING_FILE_WRITE_FAILED", message)

        if fuzzy_used:
            return f"Edited {resolved_path}: replaced 1 occurrence (fuzzy match)."
        return f"Edited {resolved_path}: replaced 1 occurrence."


