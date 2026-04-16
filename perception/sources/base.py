"""Base abstractions for context sources."""

from __future__ import annotations

import io
import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from PIL import Image


@dataclass
class CaptureFrame:
    """A single observation captured from any context source."""

    source_type: str          # "screen", "camera", ...
    source_id: str            # "local", ...
    image: Image.Image
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_base64(self, format: str = "JPEG", quality: int = 80) -> str:
        """Encode the image as a base64 string for VLM consumption."""
        buf = io.BytesIO()
        self.image.save(buf, format=format, quality=quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")


class ContextSource(ABC):
    """Abstract base class for all context sources (local or remote)."""

    source_type: str = ""
    source_id: str = ""

    @abstractmethod
    async def capture(self) -> Optional[CaptureFrame]:
        """Return one frame, or *None* if no data is available right now."""

    @abstractmethod
    async def start(self) -> None:
        """Acquire resources (camera, socket, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Release resources."""
