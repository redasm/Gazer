"""Local camera capture source using OpenCV."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from perception.sources.base import CaptureFrame, ContextSource

logger = logging.getLogger("perception.source.camera_local")

try:
    import cv2
    from PIL import Image
except ImportError:
    cv2 = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment,misc]


class LocalCameraSource(ContextSource):
    """Grabs single frames from a local camera via OpenCV.

    The camera is kept open between :meth:`start` and :meth:`stop` so that
    :meth:`capture` returns quickly without re-opening the device.

    Parameters
    ----------
    device_index : int
        OpenCV camera device index (typically ``0``).
    width, height : int
        Requested capture resolution.
    """

    source_type = "camera"
    source_id = "local"

    def __init__(
        self,
        device_index: int = 0,
        width: int = 640,
        height: int = 480,
    ) -> None:
        self._device_index = device_index
        self._width = width
        self._height = height
        self._cap = None

    async def start(self) -> None:
        if not cv2:
            logger.warning("OpenCV not installed -- LocalCameraSource disabled.")
            return
        loop = asyncio.get_running_loop()
        self._cap = await loop.run_in_executor(None, cv2.VideoCapture, self._device_index)
        if not self._cap.isOpened():
            logger.error(f"Cannot open camera device {self._device_index}.")
            self._cap = None
            return
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        logger.info(f"LocalCameraSource opened device {self._device_index}.")

    async def stop(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None
            logger.info("LocalCameraSource released camera.")

    async def capture(self) -> Optional[CaptureFrame]:
        if not self._cap:
            return None
        ret, frame = self._cap.read()
        if not ret:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        return CaptureFrame(
            source_type=self.source_type,
            source_id=self.source_id,
            image=img,
            timestamp=datetime.now(),
        )
