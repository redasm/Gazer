"""Cron job execution run log — persistent JSONL per-job history.

Each job writes one ``*.jsonl`` file under ``<store_dir>/runs/<job_id>.jsonl``.
Entries record outcome, duration, token usage, and model identity so admin
dashboards can show rich execution history.

Inspired by OpenClaw's ``cron/run-log.ts``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger("CronRunLog")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RUN_LOG_MAX_BYTES: int = 2_000_000   # ~2 MB per-job log before pruning
DEFAULT_RUN_LOG_KEEP_LINES: int = 2_000      # Lines retained after prune

CronRunStatus = Literal["ok", "error", "skipped"]

# Module-level per-path async write locks (prevents concurrent appends to the
# same JSONL file when the scheduler runs many jobs simultaneously).
_WRITE_LOCKS: Dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _assert_safe_job_id(job_id: str) -> str:
    """Validate job_id to prevent path traversal."""
    trimmed = job_id.strip()
    if not trimmed:
        raise ValueError("invalid cron job id: empty")
    if "/" in trimmed or "\\" in trimmed or "\0" in trimmed or ".." in trimmed:
        raise ValueError(f"invalid cron job id: {trimmed!r}")
    return trimmed


def resolve_run_log_path(store_path: Path, job_id: str) -> Path:
    """Return the JSONL log path for *job_id*, guarded against traversal."""
    store_path = store_path.resolve()
    runs_dir = (store_path.parent / "runs").resolve()
    safe_id = _assert_safe_job_id(job_id)
    log_path = (runs_dir / f"{safe_id}.jsonl").resolve()
    # Strict prefix check — must be inside runs_dir
    if not str(log_path).startswith(str(runs_dir) + os.sep):
        raise ValueError(f"invalid cron job id: {job_id!r}")
    return log_path


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CronRunLogEntry:
    """One finished-run record written to the JSONL log."""

    ts: float                              # Unix timestamp (seconds)
    job_id: str
    action: str = "finished"              # Always "finished" for queryable entries
    status: Optional[CronRunStatus] = None
    error: Optional[str] = None
    summary: Optional[str] = None
    run_at_ms: Optional[float] = None     # When the run started (Unix ms)
    duration_ms: Optional[float] = None  # Wall-clock duration
    next_run_at_ms: Optional[float] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    @classmethod
    def make(
        cls,
        *,
        job_id: str,
        status: CronRunStatus,
        error: Optional[str] = None,
        summary: Optional[str] = None,
        duration_ms: Optional[float] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> "CronRunLogEntry":
        now_ms = time.time() * 1000
        return cls(
            ts=time.time(),
            job_id=job_id,
            status=status,
            error=error,
            summary=summary,
            run_at_ms=now_ms,
            duration_ms=duration_ms,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


@dataclass
class CronRunLogPageResult:
    entries: List[Dict[str, Any]]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: Optional[int]


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def _get_write_lock(resolved_path: str) -> asyncio.Lock:
    if resolved_path not in _WRITE_LOCKS:
        _WRITE_LOCKS[resolved_path] = asyncio.Lock()
    return _WRITE_LOCKS[resolved_path]


def _prune_if_needed(path: Path, max_bytes: int, keep_lines: int) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    try:
        raw = path.read_text(encoding="utf-8")
        lines = [l for l in raw.split("\n") if l.strip()]
        kept = lines[max(0, len(lines) - keep_lines):]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.replace(path)
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.warning("run-log prune failed (%s): %s", path, exc)


async def append_run_log(
    store_path: Path,
    entry: CronRunLogEntry,
    *,
    max_bytes: int = DEFAULT_RUN_LOG_MAX_BYTES,
    keep_lines: int = DEFAULT_RUN_LOG_KEEP_LINES,
) -> None:
    """Append *entry* to the job's JSONL log file (async-safe)."""
    try:
        log_path = resolve_run_log_path(store_path, entry.job_id)
    except ValueError as exc:
        logger.error("append_run_log: %s", exc)
        return

    resolved = str(log_path)
    lock = _get_write_lock(resolved)
    async with lock:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(str(log_path.parent), 0o700)
            except OSError:
                pass
            # Strip None values before serialising
            payload = {k: v for k, v in asdict(entry).items() if v is not None}
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            try:
                os.chmod(resolved, 0o600)
            except OSError:
                pass
            _prune_if_needed(log_path, max_bytes, keep_lines)
        except OSError as exc:
            logger.error("append_run_log failed (%s): %s", log_path, exc)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def _parse_entries(raw: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            if obj.get("action") != "finished":
                continue
            if not isinstance(obj.get("job_id"), str) or not obj["job_id"].strip():
                continue
            if not isinstance(obj.get("ts"), (int, float)):
                continue
            if job_id and obj["job_id"] != job_id:
                continue
            result.append(obj)
        except (json.JSONDecodeError, KeyError):
            continue
    return result


def read_run_log_page(
    store_path: Path,
    *,
    job_id: Optional[str] = None,
    status: Optional[str] = None,
    query: Optional[str] = None,
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> CronRunLogPageResult:
    """Return a paginated page of run log entries (synchronous).

    Parameters
    ----------
    store_path:
        Path to the cron ``jobs.json`` store file (used to locate the
        ``runs/`` sub-directory).
    job_id:
        When set, only entries for this job are returned.
    status:
        ``"ok"``, ``"error"``, ``"skipped"``, or ``None`` / ``"all"`` for all.
    query:
        Free-text filter applied to summary, error, and job_id fields.
    sort_dir:
        ``"desc"`` (newest first, default) or ``"asc"``.
    limit / offset:
        Pagination controls (limit capped at 200).
    """
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))

    entries: List[Dict[str, Any]]

    if job_id:
        try:
            log_path = resolve_run_log_path(store_path, job_id)
        except ValueError:
            return CronRunLogPageResult(
                entries=[], total=0, offset=0, limit=limit, has_more=False, next_offset=None
            )
        try:
            raw = log_path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        entries = _parse_entries(raw, job_id)
    else:
        runs_dir = (store_path.parent / "runs").resolve()
        entries = []
        try:
            for f in runs_dir.glob("*.jsonl"):
                try:
                    raw = f.read_text(encoding="utf-8")
                    entries.extend(_parse_entries(raw))
                except OSError:
                    continue
        except OSError:
            pass

    # Filter by status
    if status and status != "all":
        entries = [e for e in entries if e.get("status") == status]

    # Free-text filter
    if query:
        q = query.lower()
        entries = [
            e for e in entries
            if q in (e.get("summary") or "").lower()
            or q in (e.get("error") or "").lower()
            or q in (e.get("job_id") or "").lower()
        ]

    # Sort
    entries.sort(key=lambda e: float(e.get("ts", 0)), reverse=(sort_dir != "asc"))

    total = len(entries)
    offset = min(offset, total)
    page = entries[offset: offset + limit]
    next_off = offset + len(page)
    return CronRunLogPageResult(
        entries=page,
        total=total,
        offset=offset,
        limit=limit,
        has_more=next_off < total,
        next_offset=next_off if next_off < total else None,
    )
