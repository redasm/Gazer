"""Rust backend gray rollout gate utilities.

Provides a per-tool-call access context (channel/sender) and rollout decision
helpers so execution adapters can safely decide whether rust backend is allowed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional, Set

from runtime.config_manager import config
from security.owner import get_owner_manager

_ACCESS_CONTEXT: ContextVar[Dict[str, str]] = ContextVar(
    "rust_backend_access_context",
    default={},
)


def _normalize_channel_values(raw: Any) -> Set[str]:
    if not isinstance(raw, list):
        return set()
    return {
        str(item).strip().lower()
        for item in raw
        if str(item).strip()
    }


def _rollout_config(cfg: Any = config) -> Dict[str, Any]:
    raw = cfg.get("runtime.rust_sidecar.rollout", {}) or {}
    return raw if isinstance(raw, dict) else {}


def is_rust_gray_rollout_enabled(cfg: Any = config) -> bool:
    rollout = _rollout_config(cfg)
    return bool(rollout.get("enabled", False))


def get_current_tool_access_context() -> Dict[str, str]:
    return dict(_ACCESS_CONTEXT.get({}))


@contextmanager
def push_tool_access_context(*, channel: str = "", sender_id: str = "") -> Iterator[None]:
    payload = {
        "channel": str(channel or "").strip(),
        "sender_id": str(sender_id or "").strip(),
    }
    token = _ACCESS_CONTEXT.set(payload)
    try:
        yield
    finally:
        _ACCESS_CONTEXT.reset(token)


def is_rust_allowed_for_context(
    cfg: Any = config,
    *,
    channel: Optional[str] = None,
    sender_id: Optional[str] = None,
) -> bool:
    """Return True when rust backend is allowed for given access context.

    Rollout semantics:
    - rollout disabled => always allowed.
    - rollout enabled => allowed iff (owner) OR (channel in allowlist).
    - if rollout enabled but no constraints configured, treat as allow-all.
    - when constraints exist but context is missing, deny by default.
    """
    rollout = _rollout_config(cfg)
    if not bool(rollout.get("enabled", False)):
        return True

    owner_only = bool(rollout.get("owner_only", False))
    allowed_channels = _normalize_channel_values(rollout.get("channels", []))

    if not owner_only and not allowed_channels:
        return True

    channel_value = str(channel or "").strip()
    sender_value = str(sender_id or "").strip()
    if not channel_value and not sender_value:
        return False

    is_owner = False
    if channel_value and sender_value:
        try:
            is_owner = bool(get_owner_manager().is_owner_sender(channel_value, sender_value))
        except Exception:
            is_owner = False
    if is_owner:
        return True

    if allowed_channels and channel_value.strip().lower() in allowed_channels:
        return True

    return False


def is_rust_allowed_for_current_context(cfg: Any = config) -> bool:
    ctx = get_current_tool_access_context()
    return is_rust_allowed_for_context(
        cfg,
        channel=ctx.get("channel", ""),
        sender_id=ctx.get("sender_id", ""),
    )

