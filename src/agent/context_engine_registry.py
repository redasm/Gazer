"""Registry for ContextEngine implementations.

Maintains a module-level mapping of engine ids to factory callables.
Factories can be classes, sync functions, or async functions.

Usage::

    from agent.context_engine_registry import (
        register_context_engine,
        resolve_context_engine,
        list_context_engine_ids,
    )

    # Register (typically called at startup)
    register_context_engine("legacy", LegacyContextEngine, owner="core")

    # Resolve (creates a new instance via the factory)
    engine = await resolve_context_engine("legacy")

    # List all registered ids
    ids = list_context_engine_ids()
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, Optional, Tuple, Union

from agent.context_engine import ContextEngine

logger = logging.getLogger("ContextEngineRegistry")

# Type alias: factory may be a class or a sync/async factory function
ContextEngineFactory = Union[
    type,
    Callable[[], ContextEngine],
    Callable[[], "Any"],  # async factory returning ContextEngine
]

_DEFAULT_ENGINE_ID = "legacy"

# Registry: engine_id -> (factory, owner_token)
_REGISTRY: Dict[str, Tuple[ContextEngineFactory, str]] = {}


def register_context_engine(
    engine_id: str,
    factory: ContextEngineFactory,
    owner: str = "public",
    *,
    allow_refresh: bool = False,
) -> bool:
    """Register a ContextEngine factory under *engine_id*.

    Parameters
    ----------
    engine_id:
        Unique string identifier for the engine (e.g. ``"legacy"``).
    factory:
        A class or callable that returns a :class:`ContextEngine` instance.
        Async factories (returning an ``Awaitable[ContextEngine]``) are
        supported in :func:`resolve_context_engine`.
    owner:
        Logical owner token used to prevent accidental overrides.
        ``"core"`` is reserved for built-in engines.
    allow_refresh:
        When *True* and the same *owner* re-registers the same *engine_id*,
        the factory is updated (e.g. hot-reload scenarios).

    Returns
    -------
    bool
        ``True`` when the registration succeeds, ``False`` when the id is
        already claimed by a different owner (or ``allow_refresh`` is False
        for the same owner).
    """
    engine_id = engine_id.strip()
    if not engine_id:
        logger.error("register_context_engine: engine_id must be non-empty")
        return False

    existing = _REGISTRY.get(engine_id)
    if existing is not None:
        _existing_factory, existing_owner = existing
        if existing_owner != owner:
            logger.warning(
                "Context engine %r already registered by owner %r; "
                "rejecting registration from owner %r.",
                engine_id,
                existing_owner,
                owner,
            )
            return False
        if not allow_refresh:
            logger.debug(
                "Context engine %r already registered (owner=%r); "
                "skipping (allow_refresh=False).",
                engine_id,
                owner,
            )
            return False

    _REGISTRY[engine_id] = (factory, owner)
    logger.debug("Registered context engine %r (owner=%r)", engine_id, owner)
    return True


def get_context_engine_factory(engine_id: str) -> Optional[ContextEngineFactory]:
    """Return the factory registered under *engine_id*, or ``None``."""
    entry = _REGISTRY.get(engine_id)
    return entry[0] if entry else None


def list_context_engine_ids() -> list[str]:
    """Return a list of all registered engine ids."""
    return list(_REGISTRY.keys())


async def resolve_context_engine(engine_id: Optional[str] = None) -> ContextEngine:
    """Instantiate and return the engine identified by *engine_id*.

    Falls back to the ``"legacy"`` engine when *engine_id* is ``None`` or
    empty.

    Raises
    ------
    RuntimeError
        When no engine is registered for the requested id.
    """
    target_id = (engine_id or "").strip() or _DEFAULT_ENGINE_ID
    entry = _REGISTRY.get(target_id)
    if entry is None:
        available = list_context_engine_ids()
        raise RuntimeError(
            f"Context engine {target_id!r} is not registered. "
            f"Available engines: {available or ['(none)']}"
        )

    factory, _ = entry

    # Support classes, sync factories, and async factories
    if inspect.isclass(factory):
        instance = factory()
    else:
        instance = factory()

    if inspect.isawaitable(instance):
        instance = await instance

    return instance  # type: ignore[return-value]


def ensure_context_engines_initialized() -> None:
    """Register all built-in context engines.

    Safe to call multiple times (subsequent calls are no-ops because
    ``allow_refresh=False`` by default).  Call this once at application
    startup before any engine is resolved.
    """
    from agent.legacy_context_engine import LegacyContextEngine

    register_context_engine(
        "legacy",
        LegacyContextEngine,
        owner="core",
        allow_refresh=False,
    )
    logger.debug("Built-in context engines initialized.")
