"""Perception subsystem: CaptureManager + pluggable capture sources."""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("GazerBrain")


def init_capture(config, memory_manager, satellite_sources: Dict[str, Any]):
    """Build and configure a :class:`CaptureManager` with screen/camera sources.

    *satellite_sources* is mutated in-place: remote screen sources are registered
    there so the Admin API WebSocket endpoint can push frames.

    Returns the ``CaptureManager`` instance.
    """
    from perception.capture import CaptureManager
    from perception.sources.screen_local import LocalScreenSource
    from perception.sources.screen_remote import RemoteScreenSource
    from perception.sources.camera_local import LocalCameraSource

    interval = config.get("perception.capture_interval", 60)
    cm = CaptureManager(memory_manager, capture_interval=interval)

    local_screen_requested = bool(config.get("perception.screen_enabled", True))
    satellite_ids: list = [
        str(sid).strip()
        for sid in config.get("perception.satellite_ids", [])
        if str(sid).strip()
    ]
    local_screen_active = False

    if local_screen_requested and satellite_ids:
        logger.warning(
            "Both local and satellite screen perception are configured; "
            "exclusive mode enforces satellite-only. "
            "Set perception.screen_enabled=false to silence this warning."
        )

    if satellite_ids:
        for sid in satellite_ids:
            remote = RemoteScreenSource(source_id=sid)
            cm.register_source(remote)
            satellite_sources[sid] = remote
        screen_mode = "satellite"
    elif local_screen_requested:
        if LocalScreenSource.is_available():
            cm.register_source(LocalScreenSource())
            local_screen_active = True
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
        "Perception screen mode: %s (local_requested=%s, local_active=%s, satellite_ids=%s)",
        screen_mode, local_screen_requested, local_screen_active, satellite_ids,
    )

    if config.get("perception.camera_enabled", False):
        cm.register_source(LocalCameraSource())

    return cm
