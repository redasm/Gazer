"""Coding tools: exec tool.

Extracted from coding.py.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base import ShellOperations, ToolSafetyTier

from .helpers import (
    CodingToolBase,
    _IMAGE_SUFFIXES,
    _create_nexum_tool,
    _render_nexum_tool_result,
)
from .safety import _is_within_workspace, check_dangerous_command

logger = logging.getLogger("CodingTools")


class ExecTool(CodingToolBase):
    """Run a shell command inside the workspace.

    Safety tier: PRIVILEGED -- commands run with full host access.
    A built-in dangerous-command guard rejects destructive patterns
    (similar to OpenClaw's elevated-mode gating).
    """

    def __init__(
        self,
        workspace: Path,
        timeout_limit: int = 120,
        shell_ops: Optional[ShellOperations] = None,
    ):
        self._workspace = workspace.resolve()
        self._timeout_limit = timeout_limit
        self._shell_ops = shell_ops

    @property
    def name(self) -> str:
        return "exec"

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.PRIVILEGED

    @property
    def description(self) -> str:
        return (
            "Run a shell command in the workspace and return stdout/stderr. "
            "Use for builds, tests, package management, or any CLI task."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "workdir": {
                    "type": "string",
                    "description": "Working directory relative to workspace root. Defaults to '.'.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait. Defaults to 30.",
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, workdir: str = ".", timeout: int = 30, **_: Any) -> str:
        # --- Dangerous-command guard ---
        warning = check_dangerous_command(command)
        if warning:
            logger.warning(f"ExecTool blocked: {command!r}")
            return warning

        cwd = (self._workspace / workdir).resolve()
        if not _is_within_workspace(cwd, self._workspace):
            return self._error("CODING_WORKDIR_OUTSIDE_WORKSPACE", "workdir must be inside the workspace.")

        timeout = min(max(timeout, 1), self._timeout_limit)
        logger.info(f"ExecTool: running {command!r} in {cwd} (timeout={timeout}s)")

        import time as _time
        exec_start = _time.time()

        if self._shell_ops is not None:
            logger.info("ExecTool shell_ops is ignored in Nexum mode.")

        nexum_tool = _create_nexum_tool("bash", str(cwd))
        if nexum_tool is None:
            return self._error("CODING_EXEC_BACKEND_UNAVAILABLE", "Nexum bash tool is unavailable.")

        try:
            result_obj = await nexum_tool.execute(
                "gazer_exec",
                {"command": command, "timeout": timeout},
                None,
                None,
            )
            result = _render_nexum_tool_result(result_obj)
        except Exception as exc:
            message = str(exc)
            if "timed out" in message.lower():
                return self._error("CODING_EXEC_TIMEOUT", message)
            return self._error("CODING_EXEC_FAILED", message)

        # --- Auto-detect image files created by the command ---
        media_paths = self._detect_created_images(command, result, exec_start)
        if media_paths:
            from tools.media_marker import MEDIA_MARKER
            for mp in media_paths:
                result += f"\n{MEDIA_MARKER}{mp}"
            logger.info(f"ExecTool auto-detected image(s): {media_paths}")

        return result

    @staticmethod
    def _detect_created_images(
        command: str, stdout: str, exec_start: float,
    ) -> List[str]:
        """Find image files created by the command.

        Strategy:
        1. Extract literal file paths (in quotes) from the command string.
        2. Extract file paths from stdout.
        3. If the command references a directory + image extension pattern but
           with a dynamic filename, glob that directory for images modified
           after *exec_start*.
        """
        found: List[str] = []
        seen: set = set()
        all_text = command + "\n" + stdout

        # --- 1. Literal quoted paths (Windows or Unix) ---
        for m in re.finditer(
            r"['\"]([A-Za-z]:[^'\"\n]+\.[a-zA-Z]{2,5})['\"]" r"|" r"['\"](/[^'\"\n]+\.[a-zA-Z]{2,5})['\"]",
            all_text,
        ):
            raw = m.group(1) or m.group(2)
            p = Path(raw.replace("\\\\", "\\"))
            if p.suffix.lower() in _IMAGE_SUFFIXES and p.is_file() and str(p) not in seen:
                found.append(str(p))
                seen.add(str(p))

        # --- 2. Unquoted paths in stdout ---
        for m in re.finditer(r"([A-Za-z]:\\[^\s\"'<>|]+\.(?:png|jpe?g|gif|bmp|webp))", stdout, re.IGNORECASE):
            p = Path(m.group(1))
            if p.is_file() and str(p) not in seen:
                found.append(str(p))
                seen.add(str(p))

        if found:
            return found

        # --- 3. Dynamic filenames: if command looks image-related, glob dirs ---
        image_keywords = re.compile(
            r"screenshot|bitmap|\bsave\b|\.png|\.jpe?g|\.bmp|\.gif|capture|screen",
            re.IGNORECASE,
        )
        if not image_keywords.search(command):
            return []

        # Extract directory paths from the command (quoted strings ending with \)
        for m in re.finditer(r"['\"]([A-Za-z]:[^'\"\n]*\\)", command):
            dir_path = Path(m.group(1).replace("\\\\", "\\"))
            if not dir_path.is_dir():
                continue
            try:
                for f in dir_path.iterdir():
                    if (
                        f.suffix.lower() in _IMAGE_SUFFIXES
                        and f.is_file()
                        and f.stat().st_mtime >= exec_start
                        and str(f) not in seen
                    ):
                        found.append(str(f))
                        seen.add(str(f))
            except OSError:
                pass

        return found


