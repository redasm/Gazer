"""Cron scheduler -- persistent scheduled jobs for proactive agent execution.

Jobs are persisted to ``~/.gazer/cron/jobs.json`` and evaluated every
minute.  When a job is due, it enqueues an agent turn via the
``CommandQueue``'s CRON lane.
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
# Job model
# ---------------------------------------------------------------------------

@dataclass
class CronJob:
    """A single cron job definition."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""
    cron_expr: str = "0 * * * *"      # Every hour by default
    message: str = ""                  # Text injected as user message
    session_mode: str = "isolated"     # "main" or "isolated"
    agent_id: str = "main"             # Target agent
    enabled: bool = True
    one_shot: bool = False             # Delete after first run
    delivery_channel: str = ""         # Optional: deliver result to channel
    delivery_chat_id: str = ""         # Optional: deliver result to chat_id
    last_run: float = 0.0              # Unix timestamp of last execution
    created_at: float = field(default_factory=time.time)


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
    ) -> None:
        self._jobs: Dict[str, CronJob] = {}
        self._store = store_path or Path.home() / ".gazer" / "cron" / "jobs.json"
        self._run_callback = run_callback
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
            logger.info(f"Loaded {len(self._jobs)} cron jobs from {self._store}")
        except Exception as exc:
            logger.error(f"Failed to load cron jobs: {exc}")

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
        logger.info(f"Added cron job: {job.id} ({job.name})")
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

    async def _tick(self) -> None:
        """Check all jobs and run those that are due."""
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        for job in list(self._jobs.values()):
            if not job.enabled:
                continue
            # Prevent running the same job more than once per minute
            if now_ts - job.last_run < 55:
                continue
            if not _cron_matches(job.cron_expr, now):
                continue

            # Due!
            job.last_run = now_ts
            logger.info(f"Running cron job: {job.id} ({job.name})")
            try:
                await self._run_callback(job)
            except Exception as exc:
                logger.error(f"Cron job {job.id} failed: {exc}", exc_info=True)

            if job.one_shot:
                self._jobs.pop(job.id, None)
                logger.info(f"One-shot cron job {job.id} removed after execution")

        self.save()

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
