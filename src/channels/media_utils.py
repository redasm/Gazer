"""Shared media utilities for channel adapters.

Provides helpers for downloading, saving, and cleaning up media files
so that channel adapters can pass local file paths to the agent context.
"""

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MediaUtils")

# Default media directory relative to project root
_MEDIA_DIR = Path("data/media")

# Auto-cleanup files older than this (seconds)
_MAX_AGE = 3600  # 1 hour


def ensure_media_dir() -> Path:
    """Return the media directory, creating it if necessary."""
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return _MEDIA_DIR


def save_media(data: bytes, ext: str = ".png", prefix: str = "img") -> Path:
    """Save raw bytes to the media directory and return the file path.

    Args:
        data: Raw file bytes.
        ext: File extension (e.g. ".png", ".jpg").
        prefix: Filename prefix for identification.

    Returns:
        Absolute ``Path`` to the saved file.
    """
    media_dir = ensure_media_dir()
    filename = f"{prefix}_{uuid.uuid4().hex[:12]}{ext}"
    filepath = media_dir / filename
    filepath.write_bytes(data)
    logger.debug("Saved media: %s (%s bytes)", filepath, len(data))
    return filepath.resolve()


def cleanup_old_media(max_age: int = _MAX_AGE) -> int:
    """Delete media files older than *max_age* seconds.

    Returns the number of files deleted.
    """
    media_dir = ensure_media_dir()
    now = time.time()
    deleted = 0
    for f in media_dir.iterdir():
        if f.is_file():
            try:
                if now - f.stat().st_mtime > max_age:
                    f.unlink()
                    deleted += 1
            except OSError:
                pass
    if deleted:
        logger.debug("Cleaned up %s old media file(s)", deleted)
    return deleted
