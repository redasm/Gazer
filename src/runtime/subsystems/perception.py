"""Perception subsystem: CaptureManager + pluggable capture sources."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("GazerBrain")


def init_capture(config, memory_manager):
    """Build and configure a :class:`CaptureManager` with screen/camera sources.

    Returns the ``CaptureManager`` instance.
    """
    from perception.capture import CaptureManager
    from perception.sources.screen_local import LocalScreenSource
    from perception.sources.camera_local import LocalCameraSource

    interval = config.get("perception.capture_interval", 60)
    cm = CaptureManager(memory_manager, capture_interval=interval)

    local_screen_requested = bool(config.get("perception.screen_enabled", True))

    if local_screen_requested:
        if LocalScreenSource.is_available():
            cm.register_source(LocalScreenSource())
            screen_mode = "local"
        else:
            logger.warning(
                "perception.screen_enabled=true but local screen capture dependency is "
                "missing; install with: pip install mss pillow"
            )
            screen_mode = "disabled"
    else:
        screen_mode = "disabled"

    logger.info(
        "Perception screen mode: %s (local_requested=%s)",
        screen_mode, local_screen_requested,
    )

    if config.get("perception.camera_enabled", False):
        cm.register_source(LocalCameraSource())

    return cm
