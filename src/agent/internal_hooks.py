"""Global internal hook registry.

Provides a module-level singleton event bus that is visible across the
entire Gazer process regardless of how modules are imported.  Events are
routed by a two-level key: ``type`` (broad) and ``type:action`` (specific).

Event types
~~~~~~~~~~~
* ``command``   -- slash-command execution (action = command name, e.g. ``new``)
* ``session``   -- session lifecycle (action = ``start`` / ``reset`` / ``end``)
* ``agent``     -- agent bootstrap / shutdown (action = ``bootstrap``)
* ``gateway``   -- gateway startup / shutdown (action = ``startup``)
* ``message``   -- message pipeline events:
    - ``received``     inbound message consumed from the bus
    - ``sent``         outbound response published
    - ``transcribed``  audio transcription complete (carries ``transcript``)
    - ``preprocessed`` message fully pre-processed before LLM dispatch

Inspired by OpenClaw's ``hooks/internal-hooks.ts``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger("InternalHooks")

HookHandler = Callable[["InternalHookEvent"], Any]

# ---------------------------------------------------------------------------
# Module-level singleton (equivalent to globalThis in OpenClaw).
# Using a dict at module scope guarantees a single instance across all
# re-imports within the same interpreter process.
# ---------------------------------------------------------------------------
_handlers: Dict[str, List[HookHandler]] = {}


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

@dataclass
class InternalHookEvent:
    """A single hook event dispatched through the registry."""

    type: str
    """Broad event category: ``command``, ``session``, ``agent``, ``gateway``, ``message``."""

    action: str
    """Specific action within the type, e.g. ``received``, ``sent``, ``bootstrap``."""

    session_key: str
    """Session this event relates to (may be empty for gateway-level events)."""

    context: Dict[str, Any] = field(default_factory=dict)
    """Event-specific payload fields (e.g. ``channel``, ``transcript``, etc.)."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    messages: List[str] = field(default_factory=list)
    """Optional response messages that hooks can push back to the caller."""


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_internal_hook(event_key: str, handler: HookHandler) -> None:
    """Register *handler* for *event_key*.

    *event_key* may be a broad type (``"message"``) to receive all actions
    under that type, or a specific ``"type:action"`` pair
    (e.g. ``"message:transcribed"``).
    """
    if event_key not in _handlers:
        _handlers[event_key] = []
    _handlers[event_key].append(handler)


def unregister_internal_hook(event_key: str, handler: HookHandler) -> None:
    """Remove a previously registered handler."""
    bucket = _handlers.get(event_key)
    if not bucket:
        return
    try:
        bucket.remove(handler)
    except ValueError:
        pass
    if not bucket:
        _handlers.pop(event_key, None)


def clear_internal_hooks() -> None:
    """Remove all registered handlers (useful in tests)."""
    _handlers.clear()


def get_registered_event_keys() -> List[str]:
    """Return all event keys that have at least one registered handler."""
    return list(_handlers.keys())


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_hook_event(
    type: str,
    action: str,
    session_key: str,
    context: Optional[Dict[str, Any]] = None,
) -> InternalHookEvent:
    """Build an :class:`InternalHookEvent` with common fields pre-filled."""
    return InternalHookEvent(
        type=type,
        action=action,
        session_key=session_key,
        context=context or {},
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def trigger_internal_hook(event: InternalHookEvent) -> None:
    """Fire all handlers registered for *event*.

    Handlers are called in registration order.  Both broad-type handlers
    (registered as ``"message"``) and specific-action handlers
    (registered as ``"message:transcribed"``) are invoked.  Errors in
    individual handlers are caught and logged without stopping other handlers.
    """
    type_handlers = list(_handlers.get(event.type, []))
    specific_handlers = list(_handlers.get(f"{event.type}:{event.action}", []))
    all_handlers = type_handlers + specific_handlers

    if not all_handlers:
        return

    for handler in all_handlers:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.warning(
                "Internal hook error [%s:%s]",
                event.type, event.action,
                exc_info=True,
            )


def fire_and_forget_hook(
    coro: "Coroutine[Any, Any, Any]",
    label: str,
    log: logging.Logger = logger,
) -> None:
    """Schedule *coro* as a background asyncio task; swallow and log errors.

    Silently skips when no event loop is running (e.g. during unit tests that
    call synchronous code paths).
    """
    async def _run() -> None:
        try:
            await coro
        except Exception as exc:
            log.warning("%s: %s", label, exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        # No running event loop — skip silently
        pass
