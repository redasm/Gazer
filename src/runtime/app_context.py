"""Lightweight dependency-injection container for Gazer runtime objects.

``brain.py`` creates an :class:`AppContext` during startup and populates it
with all runtime-injected services.  Admin API routers (and any other
consumer) retrieve service references via :func:`get_app_context` instead
of scattering ``import tools.admin.state as _state; _state.XXX`` across the
codebase.

Usage::

    from runtime.app_context import get_app_context

    ctx = get_app_context()
    router = ctx.llm_router
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Centralised holder for runtime-injected service instances.

    Fields start as ``None``; ``brain.py`` populates them during
    initialisation.  Getter functions in ``state.py`` delegate here so
    existing code keeps working without modification.
    """

    # --- Core services (always populated after brain.start) ---
    llm_router: Any = None
    orchestrator: Any = None
    tool_registry: Any = None
    usage_tracker: Any = None
    trajectory_store: Any = None
    prompt_cache_tracker: Any = None
    tool_batching_tracker: Any = None

    # --- Optional services ---
    canvas_state: Any = None
    cron_scheduler: Any = None
    hook_bus: Any = None
    hook_token: Optional[str] = None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_ctx: Optional[AppContext] = None


def get_app_context() -> Optional[AppContext]:
    """Return the current :class:`AppContext` singleton (or ``None`` if not
    yet initialised)."""
    return _ctx


def set_app_context(ctx: AppContext) -> None:
    """Set the global :class:`AppContext` singleton.

    Should be called exactly once by ``brain.py`` during startup.
    """
    global _ctx
    if _ctx is not None:
        logger.warning("AppContext is being replaced; this usually indicates a restart.")
    _ctx = ctx
    logger.info("AppContext initialised with %d populated fields.",
                sum(1 for f in ctx.__dataclass_fields__ if getattr(ctx, f) is not None))
