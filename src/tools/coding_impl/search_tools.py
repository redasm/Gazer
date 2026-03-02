"""Coding tools: search tools.

Extracted from coding.py.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base import ShellOperations, ToolSafetyTier

from .helpers import (
    CodingToolBase,
    _create_nexum_tool,
    _render_nexum_tool_result,
    _to_workspace_relative_path,
)
from .safety import _is_within_workspace

logger = logging.getLogger("CodingTools")


class ListDirTool(CodingToolBase):
    """List directory contents."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return "List files and directories at a given path."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to workspace root. Defaults to '.'.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "If true, list recursively (max 200 entries). Defaults to false.",
                },
            },
        }

    async def execute(self, path: str = ".", recursive: bool = False, **_: Any) -> str:
        target = (self._workspace / path).resolve()
        if not _is_within_workspace(target, self._workspace):
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", "path must be inside the workspace.")
        if self._shell_ops is not None:
            logger.info("ListDirTool shell_ops is ignored in Nexum mode.")

        if recursive:
            # Nexum's ls tool is non-recursive; use find tool for recursive listing.
            nexum_find = _create_nexum_tool("find", str(self._workspace))
            if nexum_find is None:
                return self._error("CODING_LIST_DIR_BACKEND_UNAVAILABLE", "Nexum find tool is unavailable.")
            try:
                result_obj = await nexum_find.execute(
                    "gazer_list_dir_recursive",
                    {
                        "pattern": "**/*",
                        "path": _to_workspace_relative_path(self._workspace, target),
                        "limit": 200,
                    },
                    None,
                    None,
                )
            except Exception as exc:
                return self._error("CODING_LIST_DIR_FAILED", str(exc))
            rendered = _render_nexum_tool_result(result_obj)
            lines = [line for line in rendered.splitlines() if line.strip() and not line.strip().startswith("[")]
            truncated = " (truncated)" if len(lines) >= 200 else ""
            return f"[{path}] {len(lines)} entries{truncated}\n{rendered}"

        nexum_ls = _create_nexum_tool("ls", str(self._workspace))
        if nexum_ls is None:
            return self._error("CODING_LIST_DIR_BACKEND_UNAVAILABLE", "Nexum ls tool is unavailable.")
        try:
            result_obj = await nexum_ls.execute(
                "gazer_list_dir",
                {"path": _to_workspace_relative_path(self._workspace, target), "limit": 200},
                None,
                None,
            )
        except Exception as exc:
            message = str(exc)
            if "not a directory" in message.lower():
                return self._error("CODING_DIRECTORY_NOT_FOUND", message)
            return self._error("CODING_LIST_DIR_FAILED", message)

        rendered = _render_nexum_tool_result(result_obj)
        lines = [line for line in rendered.splitlines() if line.strip() and not line.strip().startswith("[")]
        truncated = " (truncated)" if len(lines) >= 200 else ""
        return f"[{path}] {len(lines)} entries{truncated}\n{rendered}"


class FindFilesTool(CodingToolBase):
    """Find files matching a glob pattern."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "find_files"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return "Find files matching a glob pattern within the workspace."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory relative to workspace root. Defaults to '.'.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, path: str = ".", **_: Any) -> str:
        base = (self._workspace / path).resolve()
        if not _is_within_workspace(base, self._workspace):
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", "path must be inside the workspace.")
        if self._shell_ops is not None:
            logger.info("FindFilesTool shell_ops is ignored in Nexum mode.")

        nexum_find = _create_nexum_tool("find", str(self._workspace))
        if nexum_find is None:
            return self._error("CODING_FIND_BACKEND_UNAVAILABLE", "Nexum find tool is unavailable.")
        try:
            result_obj = await nexum_find.execute(
                "gazer_find_files",
                {
                    "pattern": pattern,
                    "path": _to_workspace_relative_path(self._workspace, base),
                    "limit": 200,
                },
                None,
                None,
            )
        except Exception as exc:
            return self._error("CODING_FIND_FILES_FAILED", str(exc))
        return _render_nexum_tool_result(result_obj)


class ReadSkillTool(CodingToolBase):
    """Read a skill's SKILL.md instructions by name."""

    def __init__(self, skill_loader=None):
        self._skill_loader = skill_loader

    def set_skill_loader(self, loader) -> None:
        self._skill_loader = loader

    @property
    def name(self) -> str:
        return "read_skill"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return "Read a skill's full instructions (SKILL.md body) by name."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The name of the skill to read.",
                },
            },
            "required": ["skill_name"],
        }

    async def execute(self, skill_name: str, **_: Any) -> str:
        if not self._skill_loader:
            return self._error("CODING_SKILL_LOADER_MISSING", "No skill loader configured.")
        body = self._skill_loader.get_instructions(skill_name)
        if not body:
            available = list(self._skill_loader.skills.keys())
            return self._error(
                "CODING_SKILL_NOT_FOUND",
                f"Skill '{skill_name}' not found. Available: {available}",
            )
        return body


class GrepTool(CodingToolBase):
    """Search file contents for a pattern (regex or literal).

    Respects common ignore patterns (.git, node_modules, __pycache__).
    Returns matching lines with file path, line number, and optional context.
    """

    _SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}
    _BINARY_EXTENSIONS = {
        ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".o", ".a",
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".wasm",
    }

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "grep"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return (
            "Search file contents for a regex or literal pattern. "
            "Returns matching lines with file path and line number. "
            "Supports context lines, glob filtering, and case-insensitive search. "
            "Respects .gitignore-style exclusions."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex by default, or literal if literal=true).",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search, relative to workspace. Defaults to '.'.",
                },
                "glob": {
                    "type": "string",
                    "description": "Filter files by glob pattern, e.g. '*.py' or '*.ts'.",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search. Defaults to false.",
                },
                "literal": {
                    "type": "boolean",
                    "description": "Treat pattern as literal string instead of regex. Defaults to false.",
                },
                "context": {
                    "type": "integer",
                    "description": "Number of lines to show before and after each match. Defaults to 0.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matches to return. Defaults to 100.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: Optional[str] = None,
        ignore_case: bool = False,
        literal: bool = False,
        context: int = 0,
        limit: int = 100,
        **_: Any,
    ) -> str:
        target = (self._workspace / path).resolve()
        if not _is_within_workspace(target, self._workspace):
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", "path must be inside the workspace.")
        if self._shell_ops is not None:
            logger.info("GrepTool shell_ops is ignored in Nexum mode.")

        nexum_grep = _create_nexum_tool("grep", str(self._workspace))
        if nexum_grep is None:
            return self._error("CODING_GREP_BACKEND_UNAVAILABLE", "Nexum grep tool is unavailable.")
        safe_limit = min(max(int(limit or 100), 1), 500)
        safe_context = min(max(int(context or 0), 0), 10)
        try:
            result_obj = await nexum_grep.execute(
                "gazer_grep",
                {
                    "pattern": pattern,
                    "path": _to_workspace_relative_path(self._workspace, target),
                    "glob": glob,
                    "ignoreCase": bool(ignore_case),
                    "literal": bool(literal),
                    "context": safe_context,
                    "limit": safe_limit,
                },
                None,
                None,
            )
        except Exception as exc:
            message = str(exc)
            if "path not found" in message.lower():
                return self._error("CODING_PATH_NOT_FOUND", message)
            return self._error("CODING_GREP_FAILED", message)
        return _render_nexum_tool_result(result_obj)

    def _search_file(
        self, fpath: Path, rx: re.Pattern, ctx: int, max_matches: int, out: List[str]
    ) -> None:
        """Search a single file and append formatted matches to *out*."""
        try:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, UnicodeDecodeError):
            return

        try:
            rel = fpath.relative_to(self._workspace)
        except ValueError:
            rel = fpath

        for i, line in enumerate(lines):
            if len(out) >= max_matches:
                return
            if rx.search(line):
                if ctx == 0:
                    out.append(f"{rel}:{i + 1}: {line}")
                else:
                    start = max(0, i - ctx)
                    end = min(len(lines), i + ctx + 1)
                    block = []
                    for j in range(start, end):
                        marker = ">" if j == i else " "
                        block.append(f"{rel}:{j + 1}:{marker}{lines[j]}")
                    out.append("\n".join(block))
                    if len(out) < max_matches:
                        out.append("--")


