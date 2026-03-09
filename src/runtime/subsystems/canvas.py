"""Canvas / A2UI initializer."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("GazerBrain")


def init_canvas(config, app_context, on_change: Callable) -> Optional[Any]:
    """Create :class:`CanvasState` and wire it into *app_context*.

    Returns the ``CanvasState`` instance, or *None* when canvas is disabled.
    """
    if not config.get("canvas.enabled", True):
        return None

    from tools.canvas import CanvasState

    cs = CanvasState(
        max_panels=config.get("canvas.max_panels", 20),
        max_content_size=config.get("canvas.max_content_size", 65536),
        on_change=on_change,
    )
    app_context.canvas_state = cs
    logger.info("Canvas/A2UI initialized.")
    return cs
