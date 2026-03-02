"""Local screen capture source using mss."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from perception.sources.base import CaptureFrame, ContextSource

logger = logging.getLogger("perception.source.screen_local")

try:
    import mss
    from PIL import Image
except ImportError:
    mss = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment,misc]


class LocalScreenSource(ContextSource):
    """Captures the local screen via mss.

    Parameters
    ----------
    monitor_idx : int
        ``-1`` for all monitors stitched, ``0`` for primary, ``1+`` for a
        specific monitor.
    max_size : tuple[int, int]
        Maximum (width, height) after thumbnail resize.
    """

    source_type = "screen"
    source_id = "local"

    def __init__(
        self,
        monitor_idx: int = -1,
        max_size: tuple[int, int] = (1280, 720),
    ) -> None:
        self._monitor_idx = monitor_idx
        self._max_size = max_size
        self._last_error: str = ""

    @staticmethod
    def is_available() -> bool:
        return bool(mss and Image)

    @property
    def last_error(self) -> str:
        return self._last_error

    async def start(self) -> None:
        if not self.is_available():
            self._last_error = "Dependency missing: mss and pillow are required."
            logger.warning(
                "mss/Pillow not installed -- LocalScreenSource disabled. "
                "Install with: pip install mss pillow"
            )

    async def stop(self) -> None:
        pass  # mss context is opened per-capture

    async def capture(self) -> Optional[CaptureFrame]:
        if not mss or not Image:
            self._last_error = "Dependency missing: mss and pillow are required."
            return None

        try:
            with mss.mss() as sct:
                # mss monitors: 0=all, 1=primary, 2=second ...
                target = 0 if self._monitor_idx == -1 else self._monitor_idx + 1
                if target >= len(sct.monitors):
                    logger.warning(f"Monitor {self._monitor_idx} out of range, using all.")
                    target = 0
                raw = sct.grab(sct.monitors[target])
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.thumbnail(self._max_size)
            self._last_error = ""
            return CaptureFrame(
                source_type=self.source_type,
                source_id=self.source_id,
                image=img,
                timestamp=datetime.now(),
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.error(f"Local screen capture failed: {exc}")
            return None
