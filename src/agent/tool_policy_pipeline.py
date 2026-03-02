"""Structured tool-policy pipeline helpers.

This module keeps policy merge semantics centralized and provides
step-by-step diagnostics for observability.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from tools.registry import ToolPolicy


def merge_tool_policy_constraints(
    base: ToolPolicy,
    *,
    allow_names: Optional[Set[str]] = None,
    deny_names: Optional[Set[str]] = None,
    allow_providers: Optional[Set[str]] = None,
    deny_providers: Optional[Set[str]] = None,
    allow_model_providers: Optional[Set[str]] = None,
    deny_model_providers: Optional[Set[str]] = None,
    allow_model_names: Optional[Set[str]] = None,
    deny_model_names: Optional[Set[str]] = None,
    allow_model_selectors: Optional[Set[str]] = None,
    deny_model_selectors: Optional[Set[str]] = None,
) -> ToolPolicy:
    """Merge a constrained overlay into ``base`` policy.

    Allow-lists are intersected across steps (when both sides are set).
    Deny-lists are unioned across steps.
    """

    resolved_allow_names = set(base.allow_names)
    incoming_allow_names = set(allow_names or set())
    if incoming_allow_names:
        if resolved_allow_names:
            resolved_allow_names = resolved_allow_names.intersection(incoming_allow_names)
        else:
            resolved_allow_names = incoming_allow_names
    resolved_deny_names = set(base.deny_names) | set(deny_names or set())
    if resolved_allow_names and resolved_deny_names:
        resolved_allow_names = {item for item in resolved_allow_names if item not in resolved_deny_names}

    resolved_allow_providers = set(base.allow_providers)
    incoming_allow_providers = set(allow_providers or set())
    if incoming_allow_providers:
        if resolved_allow_providers:
            resolved_allow_providers = resolved_allow_providers.intersection(incoming_allow_providers)
        else:
            resolved_allow_providers = incoming_allow_providers
    resolved_deny_providers = set(base.deny_providers) | set(deny_providers or set())
    if resolved_allow_providers and resolved_deny_providers:
        resolved_allow_providers = {
            item for item in resolved_allow_providers if item not in resolved_deny_providers
        }

    resolved_allow_model_providers = set(base.allow_model_providers)
    incoming_allow_model_providers = set(allow_model_providers or set())
    if incoming_allow_model_providers:
        if resolved_allow_model_providers:
            resolved_allow_model_providers = resolved_allow_model_providers.intersection(
                incoming_allow_model_providers
            )
        else:
            resolved_allow_model_providers = incoming_allow_model_providers
    resolved_deny_model_providers = set(base.deny_model_providers) | set(deny_model_providers or set())
    if resolved_allow_model_providers and resolved_deny_model_providers:
        resolved_allow_model_providers = {
            item for item in resolved_allow_model_providers if item not in resolved_deny_model_providers
        }

    resolved_allow_model_names = set(base.allow_model_names)
    incoming_allow_model_names = set(allow_model_names or set())
    if incoming_allow_model_names:
        if resolved_allow_model_names:
            resolved_allow_model_names = resolved_allow_model_names.intersection(incoming_allow_model_names)
        else:
            resolved_allow_model_names = incoming_allow_model_names
    resolved_deny_model_names = set(base.deny_model_names) | set(deny_model_names or set())
    if resolved_allow_model_names and resolved_deny_model_names:
        resolved_allow_model_names = {
            item for item in resolved_allow_model_names if item not in resolved_deny_model_names
        }

    resolved_allow_model_selectors = set(base.allow_model_selectors)
    incoming_allow_model_selectors = set(allow_model_selectors or set())
    if incoming_allow_model_selectors:
        if resolved_allow_model_selectors:
            resolved_allow_model_selectors = resolved_allow_model_selectors.intersection(
                incoming_allow_model_selectors
            )
        else:
            resolved_allow_model_selectors = incoming_allow_model_selectors
    resolved_deny_model_selectors = set(base.deny_model_selectors) | set(deny_model_selectors or set())
    if resolved_allow_model_selectors and resolved_deny_model_selectors:
        resolved_allow_model_selectors = {
            item for item in resolved_allow_model_selectors if item not in resolved_deny_model_selectors
        }

    return ToolPolicy(
        allow_names=resolved_allow_names,
        deny_names=resolved_deny_names,
        allow_providers=resolved_allow_providers,
        deny_providers=resolved_deny_providers,
        allow_model_providers=resolved_allow_model_providers,
        deny_model_providers=resolved_deny_model_providers,
        allow_model_names=resolved_allow_model_names,
        deny_model_names=resolved_deny_model_names,
        allow_model_selectors=resolved_allow_model_selectors,
        deny_model_selectors=resolved_deny_model_selectors,
    )


def _policy_counts(policy: ToolPolicy) -> Dict[str, int]:
    return {
        "allow_names": len(policy.allow_names),
        "deny_names": len(policy.deny_names),
        "allow_providers": len(policy.allow_providers),
        "deny_providers": len(policy.deny_providers),
        "allow_model_providers": len(policy.allow_model_providers),
        "deny_model_providers": len(policy.deny_model_providers),
        "allow_model_names": len(policy.allow_model_names),
        "deny_model_names": len(policy.deny_model_names),
        "allow_model_selectors": len(policy.allow_model_selectors),
        "deny_model_selectors": len(policy.deny_model_selectors),
    }


def apply_tool_policy_pipeline_steps(
    *,
    base: ToolPolicy,
    steps: List[Dict[str, Any]],
) -> Tuple[ToolPolicy, List[Dict[str, Any]]]:
    """Apply pipeline steps and return effective policy + diagnostics."""

    current = base
    diagnostics: List[Dict[str, Any]] = []
    for raw_step in steps:
        label = str(raw_step.get("label", "unnamed_step") or "unnamed_step").strip() or "unnamed_step"
        raw_overlay = raw_step.get("overlay", {})
        overlay = raw_overlay if isinstance(raw_overlay, dict) else {}
        invalid_fields: Dict[str, str] = {}

        def _normalize_overlay_set(field: str, *, lowercase: bool = False) -> Set[str]:
            raw_value = overlay.get(field)
            if raw_value is None:
                return set()
            if isinstance(raw_value, (list, tuple, set)):
                out: Set[str] = set()
                for item in raw_value:
                    text = str(item or "").strip()
                    if not text:
                        continue
                    out.add(text.lower() if lowercase else text)
                return out
            invalid_fields[field] = f"expected array/set, got {type(raw_value).__name__}"
            return set()

        kwargs = {
            "allow_names": _normalize_overlay_set("allow_names"),
            "deny_names": _normalize_overlay_set("deny_names"),
            "allow_providers": _normalize_overlay_set("allow_providers", lowercase=True),
            "deny_providers": _normalize_overlay_set("deny_providers", lowercase=True),
            "allow_model_providers": _normalize_overlay_set("allow_model_providers", lowercase=True),
            "deny_model_providers": _normalize_overlay_set("deny_model_providers", lowercase=True),
            "allow_model_names": _normalize_overlay_set("allow_model_names", lowercase=True),
            "deny_model_names": _normalize_overlay_set("deny_model_names", lowercase=True),
            "allow_model_selectors": _normalize_overlay_set("allow_model_selectors", lowercase=True),
            "deny_model_selectors": _normalize_overlay_set("deny_model_selectors", lowercase=True),
        }
        applied = any(bool(values) for values in kwargs.values())
        before = current
        if applied:
            current = merge_tool_policy_constraints(before, **kwargs)
        changed = _policy_counts(before) != _policy_counts(current) or (
            before != current if isinstance(before, ToolPolicy) else False
        )
        diagnostics.append(
            {
                "label": label,
                "applied": bool(applied),
                "changed": bool(changed),
                "overlay_counts": {key: len(val) for key, val in kwargs.items()},
                "result_counts": _policy_counts(current),
                "invalid_fields": dict(invalid_fields),
            }
        )
    return current, diagnostics
