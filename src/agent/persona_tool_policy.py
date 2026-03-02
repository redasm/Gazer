"""Persona-signal-driven tool policy linkage helpers."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Set


def _normalize_str_set(raw: Any) -> Set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _normalize_level_set_map(raw: Any) -> Dict[str, Set[str]]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Set[str]] = {}
    for key, value in raw.items():
        level = str(key).strip().lower()
        if not level:
            continue
        out[level] = _normalize_str_set(value)
    return out


def evaluate_persona_tool_policy_linkage(
    *,
    runtime_cfg: Dict[str, Any],
    signal: Optional[Dict[str, Any]],
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    linkage = cfg.get("tool_policy_linkage", {})
    linkage_cfg = linkage if isinstance(linkage, dict) else {}

    enabled = bool(linkage_cfg.get("enabled", True))
    levels = {
        str(item).strip().lower()
        for item in (linkage_cfg.get("trigger_levels", ["critical"]) or [])
        if str(item).strip()
    }
    if not levels:
        levels = {"critical"}
    high_risk_levels = {
        str(item).strip().lower()
        for item in (linkage_cfg.get("high_risk_levels", list(levels)) or [])
        if str(item).strip()
    }
    if not high_risk_levels:
        high_risk_levels = set(levels)
    sources = {
        str(item).strip().lower()
        for item in (linkage_cfg.get("sources", ["persona_eval"]) or [])
        if str(item).strip()
    }
    window_raw = linkage_cfg.get("window_seconds", 1800)
    try:
        window_seconds = max(0, int(window_raw))
    except (TypeError, ValueError):
        window_seconds = 1800

    allow_names_base = _normalize_str_set(linkage_cfg.get("allow_names"))
    deny_names_base = _normalize_str_set(linkage_cfg.get("deny_names"))
    allow_providers_base = _normalize_str_set(linkage_cfg.get("allow_providers"))
    deny_providers_base = _normalize_str_set(linkage_cfg.get("deny_providers"))
    allow_model_providers_base = _normalize_str_set(linkage_cfg.get("allow_model_providers"))
    deny_model_providers_base = _normalize_str_set(linkage_cfg.get("deny_model_providers"))
    allow_model_names_base = _normalize_str_set(linkage_cfg.get("allow_model_names"))
    deny_model_names_base = _normalize_str_set(linkage_cfg.get("deny_model_names"))
    allow_model_selectors_base = _normalize_str_set(linkage_cfg.get("allow_model_selectors"))
    deny_model_selectors_base = _normalize_str_set(linkage_cfg.get("deny_model_selectors"))

    allow_names_by_level = _normalize_level_set_map(linkage_cfg.get("allow_names_by_level"))
    deny_names_by_level = _normalize_level_set_map(linkage_cfg.get("deny_names_by_level"))
    allow_providers_by_level = _normalize_level_set_map(linkage_cfg.get("allow_providers_by_level"))
    deny_providers_by_level = _normalize_level_set_map(linkage_cfg.get("deny_providers_by_level"))
    allow_model_providers_by_level = _normalize_level_set_map(
        linkage_cfg.get("allow_model_providers_by_level")
    )
    deny_model_providers_by_level = _normalize_level_set_map(
        linkage_cfg.get("deny_model_providers_by_level")
    )
    allow_model_names_by_level = _normalize_level_set_map(linkage_cfg.get("allow_model_names_by_level"))
    deny_model_names_by_level = _normalize_level_set_map(linkage_cfg.get("deny_model_names_by_level"))
    allow_model_selectors_by_level = _normalize_level_set_map(
        linkage_cfg.get("allow_model_selectors_by_level")
    )
    deny_model_selectors_by_level = _normalize_level_set_map(
        linkage_cfg.get("deny_model_selectors_by_level")
    )

    status = {
        "enabled": enabled,
        "active": False,
        "reason": "disabled" if not enabled else "no_signal",
        "signal": {
            "level": "",
            "source": "",
            "created_at": None,
        },
        "config": {
            "trigger_levels": sorted(levels),
            "high_risk_levels": sorted(high_risk_levels),
            "sources": sorted(sources),
            "window_seconds": int(window_seconds),
        },
        "policy_overlay": {
            "allow_names": [],
            "deny_names": [],
            "allow_providers": [],
            "deny_providers": [],
            "allow_model_providers": [],
            "deny_model_providers": [],
            "allow_model_names": [],
            "deny_model_names": [],
            "allow_model_selectors": [],
            "deny_model_selectors": [],
        },
    }
    if not enabled:
        return status
    if not isinstance(signal, dict):
        return status

    level = str(signal.get("level", "")).strip().lower()
    source = str(signal.get("source", "")).strip().lower()
    created_raw = signal.get("created_at", 0.0)
    try:
        created_at = float(created_raw)
    except (TypeError, ValueError):
        created_at = 0.0
    status["signal"] = {
        "level": level,
        "source": source,
        "created_at": created_at if created_at > 0 else None,
    }

    if level not in levels:
        status["reason"] = "level_not_triggered"
        return status
    if level not in high_risk_levels:
        status["reason"] = "level_not_high_risk"
        return status
    if sources and source and source not in sources:
        status["reason"] = "source_filtered"
        return status

    now_value = float(now_ts) if now_ts is not None else time.time()
    if window_seconds > 0 and created_at > 0 and (now_value - created_at) > float(window_seconds):
        status["reason"] = "signal_stale"
        return status

    allow_names = set(allow_names_base) | set(allow_names_by_level.get(level, set()))
    deny_names = set(deny_names_base) | set(deny_names_by_level.get(level, set()))
    allow_providers = set(allow_providers_base) | set(allow_providers_by_level.get(level, set()))
    deny_providers = set(deny_providers_base) | set(deny_providers_by_level.get(level, set()))
    allow_model_providers = set(allow_model_providers_base) | set(
        allow_model_providers_by_level.get(level, set())
    )
    deny_model_providers = set(deny_model_providers_base) | set(
        deny_model_providers_by_level.get(level, set())
    )
    allow_model_names = set(allow_model_names_base) | set(allow_model_names_by_level.get(level, set()))
    deny_model_names = set(deny_model_names_base) | set(deny_model_names_by_level.get(level, set()))
    allow_model_selectors = set(allow_model_selectors_base) | set(
        allow_model_selectors_by_level.get(level, set())
    )
    deny_model_selectors = set(deny_model_selectors_base) | set(
        deny_model_selectors_by_level.get(level, set())
    )

    if allow_names and deny_names:
        allow_names = {item for item in allow_names if item not in deny_names}
    if allow_providers and deny_providers:
        allow_providers = {item for item in allow_providers if item not in deny_providers}
    if allow_model_providers and deny_model_providers:
        allow_model_providers = {
            item for item in allow_model_providers if item not in deny_model_providers
        }
    if allow_model_names and deny_model_names:
        allow_model_names = {item for item in allow_model_names if item not in deny_model_names}
    if allow_model_selectors and deny_model_selectors:
        allow_model_selectors = {
            item for item in allow_model_selectors if item not in deny_model_selectors
        }

    status["active"] = True
    status["reason"] = "active"
    status["policy_overlay"] = {
        "allow_names": sorted(allow_names),
        "deny_names": sorted(deny_names),
        "allow_providers": sorted(allow_providers),
        "deny_providers": sorted(deny_providers),
        "allow_model_providers": sorted(allow_model_providers),
        "deny_model_providers": sorted(deny_model_providers),
        "allow_model_names": sorted(allow_model_names),
        "deny_model_names": sorted(deny_model_names),
        "allow_model_selectors": sorted(allow_model_selectors),
        "deny_model_selectors": sorted(deny_model_selectors),
    }
    return status
