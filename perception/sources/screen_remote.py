"""Remote screen source -- receives frames pushed by Satellite clients."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from PIL import Image

from perception.sources.base import CaptureFrame, ContextSource

logger = logging.getLogger("perception.source.screen_remote")


class RemoteScreenSource(ContextSource):
    """Buffers frames pushed over WebSocket from a Satellite client.

    A WebSocket handler (in the Admin API) calls :meth:`push_frame` whenever
    a new image arrives.  :meth:`capture` pops the most recent frame, dropping
    any older ones so the consumer always gets the freshest image.
    """

    source_type = "screen"

    def __init__(self, source_id: str = "satellite") -> None:
        self.source_id = source_id
        self._queue: asyncio.Queue[CaptureFrame] = asyncio.Queue(maxsize=8)

    async def start(self) -> None:
        logger.info(f"RemoteScreenSource '{self.source_id}' waiting for satellite frames.")

    async def stop(self) -> None:
        # Drain remaining frames
        while not self._queue.empty():
            self._queue.get_nowait()

    async def capture(self) -> Optional[CaptureFrame]:
        """Return the newest buffered frame, or *None* if the queue is empty."""
        frame: Optional[CaptureFrame] = None
        while not self._queue.empty():
            frame = self._queue.get_nowait()
        return frame

    def push_frame(self, image: Image.Image, metadata: Optional[dict] = None) -> None:
        """Non-async entry point for the WebSocket handler to enqueue a frame.

        If the queue is full the oldest frame is dropped to make room.
        """
        frame = CaptureFrame(
            source_type=self.source_type,
            source_id=self.source_id,
            image=image,
            timestamp=datetime.now(),
            metadata=metadata or {},
        )
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            pass  # best-effort
