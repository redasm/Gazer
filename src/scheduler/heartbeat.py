"""Heartbeat runner -- periodic self-check for proactive agent behavior.

Reads ``HEARTBEAT.md`` from the workspace and runs the checklist through
the agent at a configurable interval.  This enables the agent to
proactively check calendars, emails, system health, etc.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("HeartbeatRunner")

# Sentinel response the agent can return to indicate "nothing to do"
HEARTBEAT_OK = "HEARTBEAT_OK"


class HeartbeatRunner:
    """Runs the HEARTBEAT.md checklist at a fixed interval.

    Usage::

        hb = HeartbeatRunner(
            workspace=Path("."),
            run_callback=agent_turn_func,
            interval_seconds=300,
        )
        await hb.start()
    """

    def __init__(
        self,
        workspace: Path,
        run_callback: Callable[[str], Awaitable[Optional[str]]],
        interval_seconds: int = 300,
    ) -> None:
        self._workspace = workspace
        self._run_callback = run_callback
        self._interval = interval_seconds
        self._running = False

    @property
    def heartbeat_file(self) -> Path:
        return self._workspace / "HEARTBEAT.md"

    def _load_checklist(self) -> Optional[str]:
        """Load HEARTBEAT.md content, returning None if missing."""
        hb = self.heartbeat_file
        if not hb.is_file():
            return None
        try:
            content = hb.read_text(encoding="utf-8").strip()
            return content if content else None
        except OSError:
            return None

    async def start(self) -> None:
        """Start the heartbeat loop."""
        self._running = True
        logger.info("HeartbeatRunner started (interval=%ss)", self._interval)
        while self._running:
            await asyncio.sleep(self._interval)
            await self._tick()

    def stop(self) -> None:
        self._running = False
        logger.info("HeartbeatRunner stopped")

    async def _tick(self) -> None:
        checklist = self._load_checklist()
        if not checklist:
            return

        now = datetime.now()
        time_ctx = now.strftime("%Y-%m-%d %H:%M (%A)")
        hour = now.hour

        # Determine time-of-day section
        if 6 <= hour < 12:
            period = "Morning"
        elif 12 <= hour < 18:
            period = "Afternoon"
        elif 18 <= hour < 23:
            period = "Evening"
        else:
            period = "Night"

        prompt = (
            f"[Heartbeat Check — {time_ctx} — {period}]\n\n"
            f"Run through the following heartbeat checklist.  "
            f"Only report items that need attention.  "
            f"If everything is OK, respond with just: {HEARTBEAT_OK}\n\n"
            f"{checklist}"
        )

        logger.debug("Running heartbeat check")
        try:
            result = await self._run_callback(prompt)
            if result and HEARTBEAT_OK not in result:
                logger.info("Heartbeat produced actionable output (%s chars)", len(result))
            else:
                logger.debug("Heartbeat: all OK")
        except Exception as exc:
            logger.error("Heartbeat check failed: %s", exc, exc_info=True)
