"""Cron scheduler -- persistent scheduled jobs for proactive agent execution.

Jobs are persisted to ``~/.gazer/cron/jobs.json`` and evaluated every
30 seconds.  When a job is due, it enqueues an agent turn.

Schedule kinds
--------------
* ``cron``  -- standard 5-field cron expression (with optional timezone).
* ``every`` -- fire every N seconds (e.g. ``every_seconds=3600`` for hourly).
* ``at``    -- fire daily at a fixed local time (e.g. ``at_time="09:00"``).

Failure handling
----------------
Consecutive errors increment ``consecutive_errors`` and trigger an
exponential back-off skip (max 8 skips).  When ``failure_alert_after``
consecutive errors are reached, the failure can be routed to a separate
channel/chat via ``failure_channel`` / ``failure_chat_id``.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger("CronScheduler")

# ---------------------------------------------------------------------------
# Minimal cron expression parser (supports: * and numeric fields for
# minute, hour, day-of-month, month, day-of-week)
# ---------------------------------------------------------------------------

def _cron_matches(expr: str, now: datetime) -> bool:
    """Check if a 5-field cron expression matches *now*.

    Fields: minute hour day month weekday (0=Mon .. 6=Sun)
    Supports: ``*``, single numbers, comma-separated values, ranges (``1-5``),
    and step values (``*/5``).
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        return False

    values = [now.minute, now.hour, now.day, now.month, now.weekday()]
    ranges_list = [
        (0, 59), (0, 23), (1, 31), (1, 12), (0, 6),
    ]

    for field_str, current_val, (lo, hi) in zip(fields, values, ranges_list):
        if not _field_matches(field_str, current_val, lo, hi):
            return False
    return True


def _field_matches(field_str: str, value: int, lo: int, hi: int) -> bool:
    for part in field_str.split(","):
        part = part.strip()
        if part == "*":
            return True
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if base == "*":
                if (value - lo) % step == 0:
                    return True
            else:
                start = int(base)
                if value >= start and (value - start) % step == 0:
                    return True
        elif "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= value <= int(b):
                return True
        else:
            if int(part) == value:
                return True
    return False


# ---------------------------------------------------------------------------
# Timezone helper
# ---------------------------------------------------------------------------

def _local_now(tz_name: str) -> datetime:
    """Return the current datetime in *tz_name* (best-effort).

    Resolution order:
    1. ``zoneinfo.ZoneInfo`` (Python 3.9+ stdlib)
    2. ``pytz`` if available
    3. Fallback to UTC with a warning.
    """
    if not tz_name or tz_name.upper() == "UTC":
        return datetime.now(timezone.utc)
    # Try zoneinfo (stdlib, Python >= 3.9)
    try:
        from zoneinfo import ZoneInfo  # type: ignore[import]
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        pass
    # Try pytz
    try:
        import pytz  # type: ignore[import]
        tz = pytz.timezone(tz_name)
        return datetime.now(tz)
    except Exception:
        pass
    logger.warning("Unknown timezone %r — falling back to UTC", tz_name)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

@dataclass
class CronJob:
    """A single cron job definition.

    Schedule kinds
    ~~~~~~~~~~~~~~
    ``schedule_kind = "cron"``  -- use ``cron_expr`` + optional ``timezone``.
    ``schedule_kind = "every"`` -- fire every ``every_seconds`` seconds.
    ``schedule_kind = "at"``    -- fire daily at ``at_time`` (``"HH:MM"`` local).
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""

    # ---- Schedule ----------------------------------------------------------
    schedule_kind: str = "cron"          # "cron" | "every" | "at"
    cron_expr: str = "0 * * * *"         # Used when schedule_kind == "cron"
    every_seconds: int = 3600            # Used when schedule_kind == "every"
    at_time: str = ""                    # "HH:MM" used when schedule_kind == "at"
    timezone: str = "UTC"                # Timezone for "cron" / "at" schedules

    # ---- Payload -----------------------------------------------------------
    message: str = ""                    # Text injected as user message
    session_mode: str = "isolated"       # "main" or "isolated"
    agent_id: str = "main"               # Target agent
    enabled: bool = True
    one_shot: bool = False               # Delete after first run

    # ---- Primary delivery --------------------------------------------------
    delivery_channel: str = ""           # Channel for result delivery
    delivery_chat_id: str = ""           # Chat ID for result delivery

    # ---- Failure routing ---------------------------------------------------
    failure_channel: str = ""            # Separate channel for failure alerts
    failure_chat_id: str = ""            # Separate chat ID for failure alerts
    failure_alert_after: int = 3         # Alert after N consecutive errors (0 = disabled)

    # ---- Runtime state -----------------------------------------------------
    last_run: float = 0.0                # Unix timestamp of last execution attempt
    created_at: float = field(default_factory=time.time)
    consecutive_errors: int = 0          # Reset to 0 on success
    last_failure_alert_at: float = 0.0   # Timestamp of last failure alert sent
    last_duration_ms: float = 0.0        # Duration of last run in milliseconds
    last_error: str = ""                 # Last error message (empty on success)

    # ---- Telemetry (filled in by the run callback) -------------------------
    last_run_tokens: int = 0             # Tokens used in last run
    last_run_model: str = ""             # Model used in last run


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class CronScheduler:
    """Persistent cron scheduler.

    Usage::

        sched = CronScheduler(run_callback=my_func)
        sched.load()
        await sched.start()
    """

    def __init__(
        self,
        run_callback: Callable[[CronJob], Awaitable[Optional[str]]],
        store_path: Optional[Path] = None,
        alert_callback: Optional[Callable[[CronJob], Awaitable[None]]] = None,
    ) -> None:
        self._jobs: Dict[str, CronJob] = {}
        self._store = store_path or Path.home() / ".gazer" / "cron" / "jobs.json"
        self._run_callback = run_callback
        self._alert_callback = alert_callback
        self._running = False
        self._tick_interval = 30  # Check every 30 seconds

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load jobs from disk."""
        if not self._store.is_file():
            logger.info("No cron jobs file found; starting empty.")
            return
        try:
            data = json.loads(self._store.read_text(encoding="utf-8"))
            for raw in data:
                job = CronJob(**{k: v for k, v in raw.items() if k in CronJob.__dataclass_fields__})
                self._jobs[job.id] = job
            logger.info("Loaded %s cron jobs from %s", len(self._jobs), self._store)
        except Exception as exc:
            logger.error("Failed to load cron jobs: %s", exc)

    def save(self) -> None:
        """Persist jobs to disk."""
        self._store.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(j) for j in self._jobs.values()]
        tmp = self._store.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._store)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, job: CronJob) -> CronJob:
        self._jobs[job.id] = job
        self.save()
        logger.info("Added cron job: %s (%s)", job.id, job.name)
        return job

    def remove(self, job_id: str) -> bool:
        removed = self._jobs.pop(job_id, None) is not None
        if removed:
            self.save()
        return removed

    def edit(self, job_id: str, **updates: Any) -> Optional[CronJob]:
        job = self._jobs.get(job_id)
        if not job:
            return None
        for k, v in updates.items():
            if hasattr(job, k):
                setattr(job, k, v)
        self.save()
        return job

    def list_jobs(self) -> List[CronJob]:
        return list(self._jobs.values())

    def get(self, job_id: str) -> Optional[CronJob]:
        return self._jobs.get(job_id)

    # ------------------------------------------------------------------
    # Scheduler loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler tick loop."""
        self._running = True
        logger.info("CronScheduler started")
        while self._running:
            await self._tick()
            await asyncio.sleep(self._tick_interval)

    def stop(self) -> None:
        self._running = False
        logger.info("CronScheduler stopped")

    async def _tick(self) -> None:  # noqa: C901
        """Check all jobs and run those that are due."""
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        for job in list(self._jobs.values()):
            if not job.enabled:
                continue

            # Back-off: skip if too many consecutive errors
            # Back-off window doubles per error: 1, 2, 4, 8 … ticks (capped at 8)
            if job.consecutive_errors > 0:
                backoff_ticks = min(2 ** (job.consecutive_errors - 1), 8)
                backoff_seconds = backoff_ticks * self._tick_interval
                if now_ts - job.last_run < backoff_seconds:
                    continue

            if not self._is_due(job, now, now_ts):
                continue

            # Due -- run it
            job.last_run = now_ts
            run_start = time.monotonic()
            logger.info("Running cron job: %s (%s) [kind=%s]", job.id, job.name, job.schedule_kind)
            try:
                await self._run_callback(job)
                # Success: reset error counter
                job.consecutive_errors = 0
                job.last_error = ""
            except Exception as exc:
                job.consecutive_errors += 1
                job.last_error = str(exc)[:500]
                logger.error(
                    "Cron job %s failed (consecutive_errors=%d): %s",
                    job.id, job.consecutive_errors, exc, exc_info=True,
                )
                # Failure alert routing
                await self._maybe_send_failure_alert(job, now_ts)
            finally:
                job.last_duration_ms = (time.monotonic() - run_start) * 1000

            if job.one_shot:
                self._jobs.pop(job.id, None)
                logger.info("One-shot cron job %s removed after execution", job.id)

        self.save()

    def _is_due(self, job: "CronJob", now: datetime, now_ts: float) -> bool:  # noqa: C901
        """Return True when *job* should fire at the current moment."""
        kind = job.schedule_kind

        if kind == "every":
            interval = max(1, int(job.every_seconds))
            return (now_ts - job.last_run) >= interval

        if kind == "at":
            # Fire once per day at the configured local time (HH:MM)
            at = job.at_time.strip()
            if not at or ":" not in at:
                return False
            try:
                hh, mm = at.split(":", 1)
                target_hour, target_min = int(hh), int(mm)
            except ValueError:
                return False
            # Resolve local time (best-effort timezone support via pytz/zoneinfo)
            local_now = _local_now(job.timezone)
            if local_now.hour != target_hour or local_now.minute != target_min:
                return False
            # Prevent double-firing within the same minute
            return (now_ts - job.last_run) >= 55

        # Default: "cron" kind
        # Prevent running more than once per minute
        if (now_ts - job.last_run) < 55:
            return False
        local_now = _local_now(job.timezone)
        return _cron_matches(job.cron_expr, local_now)

    async def _maybe_send_failure_alert(
        self,
        job: "CronJob",
        now_ts: float,
    ) -> None:
        """Send a failure alert when the threshold is reached and cooldown has passed."""
        threshold = int(job.failure_alert_after)
        if threshold <= 0 or job.consecutive_errors < threshold:
            return
        if not job.failure_channel or not job.failure_chat_id:
            return
        # Cooldown: at most one alert per hour
        cooldown = 3600.0
        if now_ts - job.last_failure_alert_at < cooldown:
            return
        job.last_failure_alert_at = now_ts
        logger.warning(
            "Cron job %s (%s) failure alert: %d consecutive errors, last: %s",
            job.id, job.name, job.consecutive_errors, job.last_error,
        )
        # Dispatch is fire-and-forget via a registered alert_callback
        if self._alert_callback:
            try:
                await self._alert_callback(job)
            except Exception as exc:
                logger.error("Failure alert dispatch failed for job %s: %s", job.id, exc)

    async def force_run(self, job_id: str) -> Optional[str]:
        """Force-run a specific job regardless of schedule."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        job.last_run = time.time()
        try:
            result = await self._run_callback(job)
            self.save()
            return result
        except Exception as exc:
            return f"Error: {exc}"
