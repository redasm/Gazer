"""Coding tools implementation package.

Re-exports all public tool classes and helpers for backward-compatible
``from tools.coding import X`` usage via the façade in ``tools/coding.py``.
"""

from .exec_tool import ExecTool
from .file_tools import EditFileTool, ReadFileTool, WriteFileTool
from .git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitPushTool,
    GitStatusTool,
)
from .helpers import (
    MAX_OUTPUT_CHARS,
    MAX_READ_LINES,
    CodingToolBase,
    _create_nexum_tool,
    _ensure_nexum_modules,
    _load_nexum_coding_tool_module,
    _load_nexum_edit_diff_module,
    _normalize_coding_params,
    _nexum_packages_root,
    _render_nexum_tool_result,
    _run_shell_command,
    _to_workspace_relative_path,
)
from .safety import _is_within_workspace, check_dangerous_command
from .search_tools import FindFilesTool, GrepTool, ListDirTool, ReadSkillTool

__all__ = [
    # Tool classes
    "ExecTool",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListDirTool",
    "FindFilesTool",
    "GrepTool",
    "ReadSkillTool",
    "GitStatusTool",
    "GitDiffTool",
    "GitCommitTool",
    "GitLogTool",
    "GitPushTool",
    "GitBranchTool",
    # Base / helpers
    "CodingToolBase",
    "check_dangerous_command",
    "_is_within_workspace",
    "_create_nexum_tool",
    "_render_nexum_tool_result",
    "_normalize_coding_params",
    "_to_workspace_relative_path",
    "_run_shell_command",
    "_nexum_packages_root",
    "_ensure_nexum_modules",
    "_load_nexum_coding_tool_module",
    "_load_nexum_edit_diff_module",
    "MAX_OUTPUT_CHARS",
    "MAX_READ_LINES",
]
