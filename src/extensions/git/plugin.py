"""Git Tools plugin — bundled Layer 2 plugin.

Registers git_status, git_diff, git_commit, git_log, git_push, git_branch.
These tools are imported from ``tools.coding`` (they remain there as
source-of-truth for the tool classes).
"""

from plugins.api import PluginAPI
from tools.coding import (
    GitStatusTool,
    GitDiffTool,
    GitCommitTool,
    GitLogTool,
    GitPushTool,
    GitBranchTool,
)


def setup(api: PluginAPI) -> None:
    """Plugin entry point — register all git tools."""
    workspace = api.get_service("coding_workspace", api.workspace)
    shell_ops = api.get_service("coding_shell_ops")

    for tool_cls in [
        GitStatusTool,
        GitDiffTool,
        GitCommitTool,
        GitLogTool,
        GitPushTool,
        GitBranchTool,
    ]:
        api.register_tool(tool_cls(workspace, shell_ops=shell_ops))
