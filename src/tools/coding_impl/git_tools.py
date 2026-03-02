"""Coding tools: git tools.

Extracted from coding.py.
"""

import asyncio
import logging
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base import ShellOperations, ToolSafetyTier

from .helpers import MAX_OUTPUT_CHARS, CodingToolBase

logger = logging.getLogger("CodingTools")


class GitStatusTool(CodingToolBase):
    """Show git working tree status."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "git_status"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return "Show the current git status (modified, staged, untracked files)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **_: Any) -> str:
        try:
            if self._shell_ops is not None:
                rc, stdout, stderr = await self._shell_ops.exec(
                    "git status --porcelain", str(self._workspace), timeout=30
                )
                proc_rc = rc
                stdout_bytes = stdout.encode()
                stderr_bytes = stderr.encode()
            else:
                proc = await asyncio.create_subprocess_exec(
                    "git", "status", "--porcelain",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self._workspace),
                )
                stdout_bytes, stderr_bytes = await proc.communicate()
                proc_rc = proc.returncode
        except FileNotFoundError:
            return self._error("CODING_GIT_NOT_INSTALLED", "git is not installed.")

        if proc_rc != 0:
            return self._error("CODING_GIT_STATUS_FAILED", stderr_bytes.decode(errors='replace').strip())

        output = stdout_bytes.decode(errors='replace').rstrip()
        if not output.strip():
            return "Working tree is clean."

        lines = output.split("\n")
        parsed = []
        for line in lines:
            if len(line) < 4:
                continue
            status = line[:2].strip()
            filepath = line[3:]
            parsed.append(f"[{status}] {filepath}")

        return "\n".join(parsed)


class GitDiffTool(CodingToolBase):
    """Show git diff output."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return "Show the git diff for the working tree, or for a specific file."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Optional file path to diff. Omit for all changes.",
                },
                "staged": {
                    "type": "boolean",
                    "description": "If true, show staged (cached) changes. Defaults to false.",
                },
            },
        }

    async def execute(self, file: Optional[str] = None, staged: bool = False, **_: Any) -> str:
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--cached")
        if file:
            cmd.extend(["--", file])

        try:
            if self._shell_ops is not None:
                shell_cmd = " ".join(shlex.quote(part) for part in cmd)
                rc, stdout, stderr = await self._shell_ops.exec(shell_cmd, str(self._workspace), timeout=30)
                proc_rc = rc
                stdout_bytes = stdout.encode()
                stderr_bytes = stderr.encode()
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self._workspace),
                )
                stdout_bytes, stderr_bytes = await proc.communicate()
                proc_rc = proc.returncode
        except FileNotFoundError:
            return self._error("CODING_GIT_NOT_INSTALLED", "git is not installed.")

        if proc_rc != 0:
            return self._error("CODING_GIT_DIFF_FAILED", stderr_bytes.decode(errors='replace').strip())

        output = stdout_bytes.decode(errors='replace').strip()
        if not output:
            return "No diff output (no changes)."

        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
        return output


class GitCommitTool(CodingToolBase):
    """Stage files and create a git commit."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "git_commit"

    @property
    def description(self) -> str:
        return (
            "Stage specified files (or all changes) and create a git commit. "
            "Use '.' to stage all changes."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message."},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files to stage. Use ['.'] to stage all. Defaults to ['.'].",
                },
            },
            "required": ["message"],
        }

    async def execute(self, message: str, files: Optional[List[str]] = None, **_: Any) -> str:
        files = files or ["."]
        # Stage
        add_cmd = ["git", "add"] + files
        try:
            if self._shell_ops is not None:
                add_shell = " ".join(shlex.quote(part) for part in add_cmd)
                rc, _stdout, stderr_text = await self._shell_ops.exec(
                    add_shell, str(self._workspace), timeout=30
                )
                if rc != 0:
                    return self._error(
                        "CODING_GIT_STAGE_FAILED",
                        f"Error staging files: {stderr_text.strip()}",
                    )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *add_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self._workspace),
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    return self._error(
                        "CODING_GIT_STAGE_FAILED",
                        f"Error staging files: {stderr.decode(errors='replace').strip()}",
                    )
        except FileNotFoundError:
            return self._error("CODING_GIT_NOT_INSTALLED", "git is not installed.")

        # Commit
        try:
            if self._shell_ops is not None:
                commit_cmd = "git commit -m " + shlex.quote(message)
                rc, stdout_text, stderr_text = await self._shell_ops.exec(
                    commit_cmd, str(self._workspace), timeout=40
                )
                if rc != 0:
                    return self._error("CODING_GIT_COMMIT_FAILED", stderr_text.strip() or stdout_text.strip())
                return stdout_text.strip() or "Commit created."
            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace),
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            return self._error("CODING_GIT_NOT_INSTALLED", "git is not installed.")

        if proc.returncode != 0:
            err = stderr.decode(errors='replace').strip()
            out = stdout.decode(errors='replace').strip()
            return self._error("CODING_GIT_COMMIT_FAILED", err or out)

        return stdout.decode(errors='replace').strip()


class GitLogTool(CodingToolBase):
    """Show recent git log entries."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "git_log"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return "Show recent git log entries with commit hash, author, date, and message."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of log entries to show. Defaults to 10.",
                },
            },
        }

    async def execute(self, count: int = 10, **_: Any) -> str:
        count = min(max(count, 1), 50)
        try:
            if self._shell_ops is not None:
                cmd = (
                    f"git log --max-count={count} "
                    + shlex.quote("--format=%h %ad %an | %s")
                    + " --date=short"
                )
                rc, stdout_text, stderr_text = await self._shell_ops.exec(
                    cmd, str(self._workspace), timeout=30
                )
                if rc != 0:
                    return self._error("CODING_GIT_LOG_FAILED", stderr_text.strip())
                output = stdout_text.strip()
                return output or "No commits found."
            proc = await asyncio.create_subprocess_exec(
                "git", "log", f"--max-count={count}",
                "--format=%h %ad %an | %s", "--date=short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace),
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            return self._error("CODING_GIT_NOT_INSTALLED", "git is not installed.")

        if proc.returncode != 0:
            return self._error("CODING_GIT_LOG_FAILED", stderr.decode(errors='replace').strip())

        output = stdout.decode(errors='replace').strip()
        return output or "No commits found."


class GitPushTool(CodingToolBase):
    """Push commits to a remote repository."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "git_push"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.PRIVILEGED

    @property
    def description(self) -> str:
        return (
            "Push local commits to a remote repository. "
            "Optionally specify remote name and branch."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "remote": {
                    "type": "string",
                    "description": "Remote name. Defaults to 'origin'.",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to push. Defaults to current branch.",
                },
                "force": {
                    "type": "boolean",
                    "description": "If true, force push (--force-with-lease). Defaults to false.",
                },
                "set_upstream": {
                    "type": "boolean",
                    "description": "If true, set upstream tracking (-u). Defaults to false.",
                },
            },
        }

    async def execute(
        self,
        remote: str = "origin",
        branch: Optional[str] = None,
        force: bool = False,
        set_upstream: bool = False,
        **_: Any,
    ) -> str:
        cmd = ["git", "push"]
        if force:
            cmd.append("--force-with-lease")
        if set_upstream:
            cmd.append("-u")
        cmd.append(remote)
        if branch:
            cmd.append(branch)

        try:
            if self._shell_ops is not None:
                shell_cmd = " ".join(shlex.quote(part) for part in cmd)
                rc, stdout, stderr = await self._shell_ops.exec(
                    shell_cmd, str(self._workspace), timeout=60
                )
                proc_rc = rc
                stdout_bytes = stdout.encode()
                stderr_bytes = stderr.encode()
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self._workspace),
                )
                stdout_bytes, stderr_bytes = await proc.communicate()
                proc_rc = proc.returncode
        except FileNotFoundError:
            return self._error("CODING_GIT_NOT_INSTALLED", "git is not installed.")

        # git push writes progress to stderr even on success
        stdout = stdout_bytes.decode(errors='replace').strip()
        stderr = stderr_bytes.decode(errors='replace').strip()

        if proc_rc != 0:
            return self._error("CODING_GIT_PUSH_FAILED", f"(exit {proc_rc}) {stderr or stdout}")

        # Combine both streams for the success message
        parts = [p for p in (stdout, stderr) if p]
        return "\n".join(parts) or "Push completed (no output)."


class GitBranchTool(CodingToolBase):
    """List, create, or delete git branches."""

    def __init__(self, workspace: Path, *, shell_ops: Optional[ShellOperations] = None):
        self._workspace = workspace.resolve()
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "git_branch"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    @property
    def description(self) -> str:
        return (
            "List branches, create a new branch, or delete a branch. "
            "Omit 'name' to list all branches."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Branch name to create or delete. Omit to list branches.",
                },
                "delete": {
                    "type": "boolean",
                    "description": "If true, delete the named branch. Defaults to false.",
                },
                "checkout": {
                    "type": "boolean",
                    "description": "If true, switch to the branch after creating. Defaults to false.",
                },
            },
        }

    async def _run_git(self, *args: str) -> tuple:
        """Run a git command and return (returncode, stdout, stderr)."""
        if self._shell_ops is not None:
            cmd = " ".join(shlex.quote(part) for part in ("git",) + args)
            rc, out, err = await self._shell_ops.exec(cmd, str(self._workspace), timeout=30)
            return rc, out.strip(), err.strip()
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace),
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
        except FileNotFoundError:
            return (-1, "", "git is not installed.")
        return (
            proc.returncode,
            stdout_bytes.decode(errors='replace').strip(),
            stderr_bytes.decode(errors='replace').strip(),
        )

    async def execute(
        self,
        name: Optional[str] = None,
        delete: bool = False,
        checkout: bool = False,
        **_: Any,
    ) -> str:
        # List branches
        if not name:
            rc, out, err = await self._run_git("branch", "-a", "--no-color")
            if rc != 0:
                return self._error("CODING_GIT_BRANCH_LIST_FAILED", err)
            return out or "No branches found."

        # Delete branch
        if delete:
            rc, out, err = await self._run_git("branch", "-d", name)
            if rc != 0:
                return self._error("CODING_GIT_BRANCH_DELETE_FAILED", f"Error deleting branch: {err}")
            return out or f"Deleted branch {name}."

        # Create branch
        rc, out, err = await self._run_git("branch", name)
        if rc != 0:
            return self._error("CODING_GIT_BRANCH_CREATE_FAILED", f"Error creating branch: {err}")

        result = out or f"Created branch {name}."

        # Optionally checkout
        if checkout:
            rc, out, err = await self._run_git("checkout", name)
            if rc != 0:
                return f"{result}\n{self._error('CODING_GIT_BRANCH_CHECKOUT_FAILED', f'Error switching: {err}')}"
            result += f"\nSwitched to branch '{name}'."

        return result


