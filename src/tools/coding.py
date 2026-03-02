"""Coding tools façade — re-exports from coding_impl subpackage.

This module previously contained all coding tool implementations inline
(~1769 lines).  The code has been refactored into the ``coding_impl``
package (helpers, safety, exec_tool, file_tools, search_tools, git_tools).
This file is kept as a thin re-export layer so that existing consumers
such as ``runtime/brain.py``, ``extensions/git/plugin.py``, and
``cli/interactive.py`` continue to work via ``from tools.coding import X``.
"""

from tools.coding_impl.exec_tool import ExecTool
from tools.coding_impl.file_tools import EditFileTool, ReadFileTool, WriteFileTool
from tools.coding_impl.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitPushTool,
    GitStatusTool,
)
from tools.coding_impl.helpers import (
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
from tools.coding_impl.safety import _is_within_workspace, check_dangerous_command
from tools.coding_impl.search_tools import FindFilesTool, GrepTool, ListDirTool, ReadSkillTool

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
