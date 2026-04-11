"""Native coding tool operations.

Provides file I/O, search, and shell execution primitives used by the
coding tool classes.  Replaces the previous Nexum submodule delegation
with pure-Python implementations backed by the standard library.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_OUTPUT_SIZE = 1_000_000  # 1 MB
MAX_GREP_MATCHES = 200
MAX_FIND_RESULTS = 500
DEFAULT_EXEC_TIMEOUT = 120.0
MAX_EXEC_PROGRESS_EVENTS_PER_STREAM = 8

EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", ".tox",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".egg-info",
})

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodingToolResult:
    """Uniform return type for all native coding operations."""

    text: str = ""
    diff: str | None = None
    is_error: bool = False


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------


def normalize_path(path: str, workspace: Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = workspace / p
    return p.resolve()


def check_workspace_boundary(path: Path, workspace: Path) -> None:
    resolved = path.resolve()
    ws_resolved = workspace.resolve()
    try:
        resolved.relative_to(ws_resolved)
    except ValueError:
        raise PermissionError(
            f"Path '{resolved}' is outside the workspace '{ws_resolved}'"
        ) from None


def truncate_output(text: str, max_size: int = DEFAULT_MAX_OUTPUT_SIZE) -> tuple[str, bool]:
    if len(text) <= max_size:
        return text, False
    return text[:max_size] + f"\n... [truncated, {len(text) - max_size} chars omitted]", True


def _relative_posix(target: Path, workspace: Path) -> str:
    try:
        return str(target.relative_to(workspace.resolve())).replace("\\", "/")
    except ValueError:
        return str(target)


# ---------------------------------------------------------------------------
# Edit-diff engine (fuzzy matching)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditResult:
    new_content: str
    diff: str
    match_type: str  # "exact" | "fuzzy"


class AmbiguousMatchError(Exception):
    def __init__(self, count: int) -> None:
        super().__init__(f"Search text matched {count} locations -- edit refused")
        self.count = count


class NoMatchError(Exception):
    def __init__(self) -> None:
        super().__init__("Search text not found in file")


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_SMART_QUOTE_TABLE = str.maketrans({
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
})


def _normalize_quotes(text: str) -> str:
    return text.translate(_SMART_QUOTE_TABLE)


def _fuzzy_normalize(text: str) -> str:
    return _normalize_whitespace(_normalize_quotes(text))


def apply_edit(
    content: str,
    search: str,
    replace: str,
    *,
    file_path: str = "<file>",
) -> EditResult:
    """Search-and-replace with exact-first, fuzzy-fallback semantics."""
    count = content.count(search)
    if count == 1:
        new_content = content.replace(search, replace, 1)
        diff = _make_diff(content, new_content, file_path)
        return EditResult(new_content=new_content, diff=diff, match_type="exact")
    if count > 1:
        raise AmbiguousMatchError(count)

    norm_search = _fuzzy_normalize(search)
    if not norm_search:
        raise NoMatchError()

    lines = content.splitlines(keepends=True)
    search_lines = search.splitlines(keepends=True)
    num_search = len(search_lines)

    matches: list[int] = []
    for i in range(len(lines) - num_search + 1):
        candidate = "".join(lines[i : i + num_search])
        if _fuzzy_normalize(candidate) == norm_search:
            matches.append(i)

    if len(matches) == 0:
        raise NoMatchError()
    if len(matches) > 1:
        raise AmbiguousMatchError(len(matches))

    start = matches[0]
    before = "".join(lines[:start])
    after = "".join(lines[start + num_search :])
    new_content = before + replace + after
    diff = _make_diff(content, new_content, file_path)
    return EditResult(new_content=new_content, diff=diff, match_type="fuzzy")


def _make_diff(old: str, new: str, file_path: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
        lineterm="",
    )
    return "".join(diff_lines)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


async def native_read_file(
    path: str,
    workspace: Path,
    *,
    offset: int = 1,
    limit: int = 500,
) -> CodingToolResult:
    file_path = normalize_path(path, workspace)
    check_workspace_boundary(file_path, workspace)

    if not file_path.is_file():
        return CodingToolResult(text=f"File not found: {path}", is_error=True)

    text = await asyncio.to_thread(file_path.read_text, encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    total = len(lines)

    start = max(1, offset)
    end = min(start + limit - 1, total)

    selected = lines[start - 1 : end]
    numbered = "".join(f"{start + i}|{line}" for i, line in enumerate(selected))

    output, truncated = truncate_output(numbered)
    meta = f"[lines {start}-{end} of {total}]"
    if truncated:
        meta += " [truncated]"

    return CodingToolResult(text=f"{meta}\n{output}")


async def native_write_file(
    path: str,
    workspace: Path,
    content: str,
) -> CodingToolResult:
    file_path = normalize_path(path, workspace)
    check_workspace_boundary(file_path, workspace)

    existed = file_path.exists()

    def _write() -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    await asyncio.to_thread(_write)

    action = "Updated" if existed else "Created"
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    rel = _relative_posix(file_path, workspace)
    return CodingToolResult(text=f"{action} {rel} ({line_count} lines)")


async def native_edit_file(
    path: str,
    workspace: Path,
    old_str: str,
    new_str: str,
) -> CodingToolResult:
    file_path = normalize_path(path, workspace)
    check_workspace_boundary(file_path, workspace)

    if not file_path.is_file():
        return CodingToolResult(text=f"File not found: {path}", is_error=True)

    content = await asyncio.to_thread(file_path.read_text, encoding="utf-8", errors="replace")
    rel = _relative_posix(file_path, workspace)

    try:
        result = apply_edit(content, old_str, new_str, file_path=rel)
    except AmbiguousMatchError as exc:
        return CodingToolResult(text=str(exc), is_error=True)
    except NoMatchError as exc:
        return CodingToolResult(text=str(exc), is_error=True)

    await asyncio.to_thread(file_path.write_text, result.new_content, encoding="utf-8")
    return CodingToolResult(
        text=f"Applied edit to {rel} (match type: {result.match_type})",
        diff=result.diff,
    )


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------


async def native_exec(
    command: str,
    cwd: Path,
    *,
    timeout: float = DEFAULT_EXEC_TIMEOUT,
    progress_callback: Any = None,
) -> CodingToolResult:
    async def _emit(stage: str, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {"stage": stage, "message": message, **extra}
        try:
            maybe = progress_callback(payload)
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception:
            pass

    async def _read_stream(stream: Any, stream_name: str) -> str:
        chunks: list[str] = []
        emitted = 0
        line_count = 0
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            chunks.append(text)
            preview = text.strip()
            if not preview:
                continue
            line_count += 1
            if emitted < 3 or line_count % 25 == 0:
                emitted += 1
                await _emit(
                    stream_name,
                    f"[{stream_name}] {preview[:160]}",
                    stream=stream_name,
                    line_count=line_count,
                )
            if emitted >= MAX_EXEC_PROGRESS_EVENTS_PER_STREAM:
                continue
        return "".join(chunks)

    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await _emit("launch", f"Started command in {cwd}")
        stdout_task = asyncio.create_task(_read_stream(proc.stdout, "stdout"))
        stderr_task = asyncio.create_task(_read_stream(proc.stderr, "stderr"))
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        stdout = await stdout_task
        stderr = await stderr_task
        exit_code = proc.returncode or 0
        timed_out = False
    except asyncio.TimeoutError:
        if proc is not None:
            proc.kill()
            await proc.wait()
        stdout = await stdout_task if "stdout_task" in locals() else ""
        stderr = await stderr_task if "stderr_task" in locals() else ""
        exit_code = -1
        timed_out = True
        await _emit("timeout", f"Command timed out after {timeout:.0f}s")

    parts: list[str] = []
    if stdout:
        out, _ = truncate_output(stdout, 500_000)
        parts.append(out)
    if stderr:
        err, _ = truncate_output(stderr, 500_000)
        parts.append(f"[stderr]\n{err}")

    output = "\n".join(parts) if parts else "(no output)"
    if timed_out:
        output = f"[timed out after {timeout:.0f}s]\n{output}"
    else:
        await _emit("complete", f"Command exited with code {exit_code}")

    text = f"[exit code: {exit_code}]\n{output}"
    return CodingToolResult(text=text, is_error=exit_code != 0)


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


def _list_tree(base: Path, current: Path, depth: int, max_depth: int) -> list[str]:
    entries: list[str] = []
    if depth > max_depth:
        return entries
    try:
        children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        entries.append(f"{'  ' * depth}[permission denied]")
        return entries

    for child in children:
        rel = child.relative_to(base)
        suffix = "/" if child.is_dir() else ""
        entries.append(f"{rel}{suffix}")
        if child.is_dir() and depth < max_depth:
            entries.extend(_list_tree(base, child, depth + 1, max_depth))
    return entries


async def native_ls(
    path: str,
    workspace: Path,
    *,
    max_depth: int = 1,
    progress_callback: Any = None,
) -> CodingToolResult:
    dir_path = normalize_path(path, workspace)
    check_workspace_boundary(dir_path, workspace)

    if not dir_path.is_dir():
        return CodingToolResult(text=f"Not a directory: {path}", is_error=True)

    max_depth = max(1, max_depth)
    if progress_callback is not None:
        maybe = progress_callback({"stage": "scan", "message": f"Listing {path} (depth={max_depth})"})
        if asyncio.iscoroutine(maybe):
            await maybe
    entries = await asyncio.to_thread(_list_tree, dir_path, dir_path, 1, max_depth)
    output = "\n".join(entries) if entries else "(empty directory)"
    output, _ = truncate_output(output)
    if progress_callback is not None:
        maybe = progress_callback({"stage": "summary", "message": f"Listed {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"})
        if asyncio.iscoroutine(maybe):
            await maybe
    return CodingToolResult(text=output)


# ---------------------------------------------------------------------------
# File find (glob)
# ---------------------------------------------------------------------------


async def native_find(
    pattern: str,
    workspace: Path,
    *,
    search_dir: str = ".",
    progress_callback: Any = None,
) -> CodingToolResult:
    base = normalize_path(search_dir, workspace)
    check_workspace_boundary(base, workspace)

    if not base.is_dir():
        return CodingToolResult(text=f"Not a directory: {search_dir}", is_error=True)
    if progress_callback is not None:
        maybe = progress_callback({"stage": "scan", "message": f"Searching {search_dir} for {pattern}"})
        if asyncio.iscoroutine(maybe):
            await maybe

    def _find_sync() -> list[str]:
        found: list[str] = []
        for p in base.glob(pattern):
            try:
                rel_parts = p.relative_to(base).parts
            except ValueError:
                rel_parts = p.parts
            if any(part in EXCLUDED_DIRS for part in rel_parts):
                continue
            if p.is_file():
                found.append(_relative_posix(p, workspace))
                if len(found) >= MAX_FIND_RESULTS:
                    break
        return found

    matches = await asyncio.to_thread(_find_sync)

    if not matches:
        if progress_callback is not None:
            maybe = progress_callback({"stage": "summary", "message": f"No files matched {pattern}"})
            if asyncio.iscoroutine(maybe):
                await maybe
        return CodingToolResult(text=f"No files found matching '{pattern}'")

    header = f"Found {len(matches)} file(s)"
    if len(matches) >= MAX_FIND_RESULTS:
        header += f" (showing first {MAX_FIND_RESULTS})"
    output, _ = truncate_output("\n".join(matches))
    if progress_callback is not None:
        maybe = progress_callback({"stage": "summary", "message": header})
        if asyncio.iscoroutine(maybe):
            await maybe
    return CodingToolResult(text=f"{header}\n{output}")


# ---------------------------------------------------------------------------
# Grep (regex / literal search)
# ---------------------------------------------------------------------------


def _search_file(
    file_path: Path,
    regex: re.Pattern[str],
    context: int,
    workspace: Path,
) -> list[str]:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError):
        return []

    lines = text.splitlines()
    rel = _relative_posix(file_path, workspace)

    matched_indices: set[int] = set()
    for i, line in enumerate(lines):
        if regex.search(line):
            matched_indices.add(i)

    if not matched_indices:
        return []

    display_indices: set[int] = set()
    for idx in matched_indices:
        for c in range(max(0, idx - context), min(len(lines), idx + context + 1)):
            display_indices.add(c)

    results: list[str] = []
    prev_idx = -2
    for idx in sorted(display_indices):
        if idx > prev_idx + 1 and prev_idx >= 0:
            results.append("--")
        prefix = ">" if idx in matched_indices else " "
        results.append(f"{rel}:{idx + 1}:{prefix} {lines[idx]}")
        prev_idx = idx

    return results


async def native_grep(
    pattern: str,
    workspace: Path,
    *,
    path: str = ".",
    include: str | None = None,
    context_lines: int = 0,
    is_regex: bool = True,
    ignore_case: bool = False,
    progress_callback: Any = None,
) -> CodingToolResult:
    target = normalize_path(path, workspace)
    check_workspace_boundary(target, workspace)

    flags = re.IGNORECASE if ignore_case else 0
    try:
        raw = pattern if is_regex else re.escape(pattern)
        regex = re.compile(raw, flags)
    except re.error as exc:
        return CodingToolResult(text=f"Invalid regex: {exc}", is_error=True)

    context = max(0, context_lines)
    if progress_callback is not None:
        maybe = progress_callback({"stage": "scan", "message": f"Searching {path} for {pattern}"})
        if asyncio.iscoroutine(maybe):
            await maybe

    def _grep_sync() -> list[str] | None:
        if target.is_file():
            return _search_file(target, regex, context, workspace)
        if target.is_dir():
            glob_pattern = include or "**/*"
            results: list[str] = []
            for fp in sorted(target.glob(glob_pattern)):
                if not fp.is_file():
                    continue
                try:
                    rel_parts = fp.relative_to(target).parts
                except ValueError:
                    rel_parts = fp.parts
                if any(part in EXCLUDED_DIRS for part in rel_parts):
                    continue
                results.extend(_search_file(fp, regex, context, workspace))
                if len(results) >= MAX_GREP_MATCHES:
                    return results[:MAX_GREP_MATCHES]
            return results
        return None

    all_matches = await asyncio.to_thread(_grep_sync)
    if all_matches is None:
        return CodingToolResult(text=f"Path not found: {path}", is_error=True)

    if not all_matches:
        if progress_callback is not None:
            maybe = progress_callback({"stage": "summary", "message": f"No matches for {pattern}"})
            if asyncio.iscoroutine(maybe):
                await maybe
        return CodingToolResult(text=f"No matches for '{pattern}'")

    output, _ = truncate_output("\n".join(all_matches))
    if progress_callback is not None:
        maybe = progress_callback({"stage": "summary", "message": f"Found {len(all_matches)} matching line(s)"})
        if asyncio.iscoroutine(maybe):
            await maybe
    return CodingToolResult(text=output)
