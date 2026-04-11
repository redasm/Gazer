from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ToolRegistry")


@dataclass
class ToolPolicy:
    """Per-agent tool policy."""

    allow_names: set[str] = field(default_factory=set)
    deny_names: set[str] = field(default_factory=set)
    allow_providers: set[str] = field(default_factory=set)
    deny_providers: set[str] = field(default_factory=set)
    allow_model_providers: set[str] = field(default_factory=set)
    deny_model_providers: set[str] = field(default_factory=set)
    allow_model_names: set[str] = field(default_factory=set)
    deny_model_names: set[str] = field(default_factory=set)
    allow_model_selectors: set[str] = field(default_factory=set)
    deny_model_selectors: set[str] = field(default_factory=set)


def _normalize_policy_value(values: Any, *, lowercase: bool = False) -> set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()
    normalized: set[str] = set()
    for item in values:
        value = str(item).strip()
        if not value:
            continue
        normalized.add(value.lower() if lowercase else value)
    return normalized


def _normalize_model_selector_values(values: Any) -> set[str]:
    selectors = _normalize_policy_value(values, lowercase=True)
    return {item for item in selectors if item not in {"/", "*"}}


def normalize_tool_policy(
    policy: dict[str, Any] | None,
    groups: dict[str, list[str]] | None = None,
) -> ToolPolicy:
    """Normalize raw policy dict into ``ToolPolicy``."""
    if not policy:
        return ToolPolicy()
    groups = groups or {}

    allow_names = _normalize_policy_value(policy.get("allow_names"))
    deny_names = _normalize_policy_value(policy.get("deny_names"))
    allow_providers = _normalize_policy_value(policy.get("allow_providers"), lowercase=True)
    deny_providers = _normalize_policy_value(policy.get("deny_providers"), lowercase=True)
    allow_model_providers = _normalize_policy_value(
        policy.get("allow_model_providers"),
        lowercase=True,
    )
    deny_model_providers = _normalize_policy_value(
        policy.get("deny_model_providers"),
        lowercase=True,
    )
    allow_model_names = _normalize_policy_value(policy.get("allow_model_names"), lowercase=True)
    deny_model_names = _normalize_policy_value(policy.get("deny_model_names"), lowercase=True)
    allow_model_selectors = _normalize_model_selector_values(policy.get("allow_model_selectors"))
    deny_model_selectors = _normalize_model_selector_values(policy.get("deny_model_selectors"))

    for group_name in _normalize_policy_value(policy.get("allow_groups")):
        names = groups.get(group_name, [])
        if not names:
            logger.warning("Unknown/empty allow_group: %s", group_name)
        allow_names.update(_normalize_policy_value(names))

    for group_name in _normalize_policy_value(policy.get("deny_groups")):
        names = groups.get(group_name, [])
        if not names:
            logger.warning("Unknown/empty deny_group: %s", group_name)
        deny_names.update(_normalize_policy_value(names))

    return ToolPolicy(
        allow_names=allow_names,
        deny_names=deny_names,
        allow_providers=allow_providers,
        deny_providers=deny_providers,
        allow_model_providers=allow_model_providers,
        deny_model_providers=deny_model_providers,
        allow_model_names=allow_model_names,
        deny_model_names=deny_model_names,
        allow_model_selectors=allow_model_selectors,
        deny_model_selectors=deny_model_selectors,
    )
