"""Coding tools: search tools (list_dir / find_files / grep / read_skill)."""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from tools.base import ShellOperations, ToolSafetyTier

from .helpers import CodingToolBase, _to_workspace_relative_path
from .native_ops import native_find, native_grep, native_ls
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

        if recursive:
            rel_path = _to_workspace_relative_path(self._workspace, target)
            try:
                result = await native_find("**/*", self._workspace, search_dir=rel_path)
            except PermissionError as exc:
                return self._error("CODING_PATH_OUTSIDE_WORKSPACE", str(exc))
            except Exception as exc:
                return self._error("CODING_LIST_DIR_FAILED", str(exc))
            lines = [line for line in result.text.splitlines() if line.strip() and not line.strip().startswith("[")]
            truncated = " (truncated)" if len(lines) >= 200 else ""
            return f"[{path}] {len(lines)} entries{truncated}\n{result.text}"

        try:
            result = await native_ls(path, self._workspace, max_depth=1)
        except PermissionError as exc:
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", str(exc))
        except Exception as exc:
            message = str(exc)
            if "not a directory" in message.lower():
                return self._error("CODING_DIRECTORY_NOT_FOUND", message)
            return self._error("CODING_LIST_DIR_FAILED", message)

        if result.is_error:
            return self._error("CODING_DIRECTORY_NOT_FOUND", result.text)

        lines = [line for line in result.text.splitlines() if line.strip() and not line.strip().startswith("[")]
        truncated = " (truncated)" if len(lines) >= 200 else ""
        return f"[{path}] {len(lines)} entries{truncated}\n{result.text}"


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

        try:
            result = await native_find(
                pattern, self._workspace,
                search_dir=_to_workspace_relative_path(self._workspace, base),
            )
        except PermissionError as exc:
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", str(exc))
        except Exception as exc:
            return self._error("CODING_FIND_FILES_FAILED", str(exc))

        return result.text


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
    """Search file contents for a pattern (regex or literal)."""

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

        safe_context = min(max(int(context or 0), 0), 10)

        try:
            result = await native_grep(
                pattern, self._workspace,
                path=_to_workspace_relative_path(self._workspace, target),
                include=glob,
                context_lines=safe_context,
                is_regex=not literal,
                ignore_case=bool(ignore_case),
            )
        except PermissionError as exc:
            return self._error("CODING_PATH_OUTSIDE_WORKSPACE", str(exc))
        except Exception as exc:
            message = str(exc)
            if "path not found" in message.lower():
                return self._error("CODING_PATH_NOT_FOUND", message)
            return self._error("CODING_GREP_FAILED", message)

        if result.is_error:
            return self._error("CODING_GREP_FAILED", result.text)

        return result.text
