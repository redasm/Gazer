"""Coding tools: exec tool."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base import ShellOperations

from .helpers import CodingToolBase, _IMAGE_SUFFIXES, _emit_progress
from .native_ops import native_exec
from .safety import _is_within_workspace, check_dangerous_command

logger = logging.getLogger("CodingTools")


class ExecTool(CodingToolBase):
    """Run a shell command inside the workspace.

    Warning: commands run with full host access.
    A built-in dangerous-command guard rejects destructive patterns
    (similar to OpenClaw's elevated-mode gating).
    """

    # Warn once per process about running without a sandbox.
    _sandbox_warned: bool = False

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
    def owner_only(self) -> bool:
        return True

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

    async def execute(
        self,
        command: str,
        workdir: str = ".",
        timeout: int = 30,
        _progress_callback: Any = None,
        **_: Any,
    ) -> str:
        if not ExecTool._sandbox_warned:
            ExecTool._sandbox_warned = True
            try:
                from runtime.config_manager import config as _cfg
                if (
                    str(_cfg.get("coding.exec_backend", "local")).strip().lower() == "local"
                    and not bool(_cfg.get("sandbox.enabled", False))
                ):
                    logger.warning(
                        "ExecTool: running without sandbox (sandbox.enabled=false). "
                        "Commands execute with full host access. "
                        "Set coding.exec_backend=sandbox for production deployments."
                    )
            except Exception:
                pass

        warning = check_dangerous_command(command)
        if warning:
            logger.warning("ExecTool blocked: %r", command)
            return warning

        cwd = (self._workspace / workdir).resolve()
        if not _is_within_workspace(cwd, self._workspace):
            return self._error("CODING_WORKDIR_OUTSIDE_WORKSPACE", "workdir must be inside the workspace.")

        timeout = min(max(timeout, 1), self._timeout_limit)
        logger.info("ExecTool: running %r in %s (timeout=%ss)", command, cwd, timeout)
        await _emit_progress(
            _progress_callback,
            stage="prepare",
            message=f"Preparing exec in {cwd}",
        )

        import time as _time
        exec_start = _time.time()

        try:
            result = await native_exec(
                command,
                cwd,
                timeout=float(timeout),
                progress_callback=_progress_callback,
            )
            output = result.text
        except Exception as exc:
            message = str(exc)
            if "timed out" in message.lower():
                return self._error("CODING_EXEC_TIMEOUT", message)
            return self._error("CODING_EXEC_FAILED", message)

        media_paths = self._detect_created_images(command, output, exec_start)
        if media_paths:
            from tools.media_marker import MEDIA_MARKER
            for mp in media_paths:
                output += f"\n{MEDIA_MARKER}{mp}"
            logger.info("ExecTool auto-detected image(s): %s", media_paths)
            await _emit_progress(
                _progress_callback,
                stage="media",
                message=f"Detected {len(media_paths)} generated media file(s)",
            )

        return output

    @staticmethod
    def _detect_created_images(
        command: str, stdout: str, exec_start: float,
    ) -> List[str]:
        found: List[str] = []
        seen: set = set()
        all_text = command + "\n" + stdout

        for m in re.finditer(
            r"['\"]([A-Za-z]:[^'\"\n]+\.[a-zA-Z]{2,5})['\"]" r"|" r"['\"](/[^'\"\n]+\.[a-zA-Z]{2,5})['\"]",
            all_text,
        ):
            raw = m.group(1) or m.group(2)
            p = Path(raw.replace("\\\\", "\\"))
            if p.suffix.lower() in _IMAGE_SUFFIXES and p.is_file() and str(p) not in seen:
                found.append(str(p))
                seen.add(str(p))

        for m in re.finditer(r"([A-Za-z]:\\[^\s\"'<>|]+\.(?:png|jpe?g|gif|bmp|webp))", stdout, re.IGNORECASE):
            p = Path(m.group(1))
            if p.is_file() and str(p) not in seen:
                found.append(str(p))
                seen.add(str(p))

        if found:
            return found

        image_keywords = re.compile(
            r"screenshot|bitmap|\bsave\b|\.png|\.jpe?g|\.bmp|\.gif|capture|screen",
            re.IGNORECASE,
        )
        if not image_keywords.search(command):
            return []

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
