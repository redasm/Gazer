from __future__ import annotations

import fnmatch
from typing import Any

from tools.base import Tool
from tools.registry_policy import ToolPolicy


def normalize_model_context(model_provider: str, model_name: str) -> tuple[str, str]:
    provider = str(model_provider or "").strip().lower()
    model = str(model_name or "").strip().lower()
    if not provider and model:
        if "/" in model:
            provider, model = model.split("/", 1)
        elif ":" in model:
            provider_candidate, maybe_model = model.split(":", 1)
            if provider_candidate and maybe_model:
                provider = provider_candidate
                model = maybe_model
    return provider, model


def matches_model_selector(selector: str, *, model_provider: str, model_name: str) -> bool:
    raw = str(selector or "").strip().lower()
    if not raw:
        return False
    if "/" in raw:
        provider_pattern, model_pattern = raw.split("/", 1)
    else:
        provider_pattern, model_pattern = "*", raw
    provider_pattern = provider_pattern or "*"
    model_pattern = model_pattern or "*"
    return (
        fnmatch.fnmatch(model_provider, provider_pattern)
        and fnmatch.fnmatch(model_name, model_pattern)
    )


def evaluate_tool_access_decision(
    *,
    name: str,
    tool: Tool | None,
    denylist: set[str],
    allowlist: set[str],
    policy: ToolPolicy | None,
    sender_id: str,
    channel: str,
    model_provider: str,
    model_name: str,
    provider: str = "",
    owner_context_available: bool = False,
    owner_sender: bool = False,
    owner_only: bool = False,
) -> dict[str, Any]:
    if tool is None:
        return {
            "tool": name,
            "exists": False,
            "allowed": False,
            "reason": "not_found",
        }

    resolved_model_provider, resolved_model_name = normalize_model_context(
        model_provider=model_provider,
        model_name=model_name,
    )
    has_model_context = bool(resolved_model_provider and resolved_model_name)
    rule_chain: list[dict[str, Any]] = []

    def _append_rule(rule: str, allowed: bool, reason: str, details: dict[str, Any] | None = None) -> None:
        rule_chain.append(
            {
                "rule": str(rule),
                "allowed": bool(allowed),
                "reason": str(reason),
                "details": dict(details or {}),
            }
        )

    decision = {
        "tool": name,
        "exists": True,
        "provider": provider,
        "owner_only": bool(owner_only),
        "model_context": {
            "provider": resolved_model_provider,
            "model": resolved_model_name,
            "available": has_model_context,
        },
        "allowed": True,
        "reason": "allowed",
        "checks": {
            "global_allowlist": True,
            "global_denylist": True,
            "policy_allow_names": True,
            "policy_deny_names": True,
            "policy_allow_providers": True,
            "policy_deny_providers": True,
            "policy_allow_model_providers": True,
            "policy_deny_model_providers": True,
            "policy_allow_model_names": True,
            "policy_deny_model_names": True,
            "policy_allow_model_selectors": True,
            "policy_deny_model_selectors": True,
            "owner_only": True,
            "system": True,
        },
        "rule_chain": rule_chain,
    }

    if name in denylist:
        decision["allowed"] = False
        decision["reason"] = "blocked_by_global_denylist"
        decision["checks"]["global_denylist"] = False
        _append_rule("global_denylist", False, "tool listed in global denylist")
        return decision
    _append_rule("global_denylist", True, "tool not in global denylist")
    if allowlist and name not in allowlist:
        decision["allowed"] = False
        decision["reason"] = "blocked_by_global_allowlist"
        decision["checks"]["global_allowlist"] = False
        _append_rule("global_allowlist", False, "tool missing in global allowlist")
        return decision
    _append_rule(
        "global_allowlist",
        True,
        "global allowlist empty or tool explicitly allowed",
    )

    if owner_only:
        if owner_context_available and not owner_sender:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_owner_only"
            decision["checks"]["owner_only"] = False
            _append_rule(
                "owner_only",
                False,
                "tool is owner-only and sender is not owner",
                {
                    "owner_sender": owner_sender,
                    "sender_id": str(sender_id or "").strip(),
                    "channel": str(channel or "").strip(),
                },
            )
            return decision
        if not owner_context_available:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_owner_only_no_context"
            decision["checks"]["owner_only"] = False
            _append_rule(
                "owner_only",
                False,
                "tool is owner-only; sender context unavailable, access denied (fail-closed)",
            )
            return decision
        _append_rule(
            "owner_only",
            True,
            "tool is owner-only and sender is owner",
            {
                "owner_sender": owner_sender,
                "context_available": owner_context_available,
            },
        )
    else:
        _append_rule("owner_only", True, "tool is not owner-only")

    if policy is not None:
        if name in policy.deny_names:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_policy_deny_names"
            decision["checks"]["policy_deny_names"] = False
            _append_rule("policy_deny_names", False, "tool explicitly denied by policy")
            return decision
        _append_rule("policy_deny_names", True, "tool not denied by policy name rules")
        if provider in policy.deny_providers:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_policy_deny_providers"
            decision["checks"]["policy_deny_providers"] = False
            _append_rule("policy_deny_providers", False, "tool provider denied by policy")
            return decision
        _append_rule("policy_deny_providers", True, "tool provider not denied by policy")
        if policy.allow_names and name not in policy.allow_names:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_policy_allow_names"
            decision["checks"]["policy_allow_names"] = False
            _append_rule("policy_allow_names", False, "tool not in policy allow_names")
            return decision
        _append_rule("policy_allow_names", True, "allow_names empty or tool matched allow_names")
        if policy.allow_providers and provider not in policy.allow_providers:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_policy_allow_providers"
            decision["checks"]["policy_allow_providers"] = False
            _append_rule("policy_allow_providers", False, "tool provider not in allow_providers")
            return decision
        _append_rule("policy_allow_providers", True, "allow_providers empty or provider matched")

        if has_model_context:
            if resolved_model_provider in policy.deny_model_providers:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_deny_model_providers"
                decision["checks"]["policy_deny_model_providers"] = False
                _append_rule("policy_deny_model_providers", False, "model provider denied by policy")
                return decision
            _append_rule("policy_deny_model_providers", True, "model provider not denied by policy")
            if policy.allow_model_providers and resolved_model_provider not in policy.allow_model_providers:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_allow_model_providers"
                decision["checks"]["policy_allow_model_providers"] = False
                _append_rule("policy_allow_model_providers", False, "model provider not in allow_model_providers")
                return decision
            _append_rule("policy_allow_model_providers", True, "allow_model_providers empty or model provider matched")

            if resolved_model_name in policy.deny_model_names:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_deny_model_names"
                decision["checks"]["policy_deny_model_names"] = False
                _append_rule("policy_deny_model_names", False, "model denied by policy")
                return decision
            _append_rule("policy_deny_model_names", True, "model not denied by policy")
            if policy.allow_model_names and resolved_model_name not in policy.allow_model_names:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_allow_model_names"
                decision["checks"]["policy_allow_model_names"] = False
                _append_rule("policy_allow_model_names", False, "model not in allow_model_names")
                return decision
            _append_rule("policy_allow_model_names", True, "allow_model_names empty or model matched")

            for selector in sorted(policy.deny_model_selectors):
                if matches_model_selector(
                    selector,
                    model_provider=resolved_model_provider,
                    model_name=resolved_model_name,
                ):
                    decision["allowed"] = False
                    decision["reason"] = "blocked_by_policy_deny_model_selectors"
                    decision["checks"]["policy_deny_model_selectors"] = False
                    _append_rule(
                        "policy_deny_model_selectors",
                        False,
                        "model matched deny selector",
                        {"selector": selector},
                    )
                    return decision
            _append_rule("policy_deny_model_selectors", True, "model did not match deny selectors")
            if policy.allow_model_selectors:
                allow_hit = None
                for selector in sorted(policy.allow_model_selectors):
                    if matches_model_selector(
                        selector,
                        model_provider=resolved_model_provider,
                        model_name=resolved_model_name,
                    ):
                        allow_hit = selector
                        break
                if not allow_hit:
                    decision["allowed"] = False
                    decision["reason"] = "blocked_by_policy_allow_model_selectors"
                    decision["checks"]["policy_allow_model_selectors"] = False
                    _append_rule("policy_allow_model_selectors", False, "model did not match allow selectors")
                    return decision
                _append_rule(
                    "policy_allow_model_selectors",
                    True,
                    "model matched allow selector",
                    {"selector": allow_hit},
                )
            else:
                _append_rule("policy_allow_model_selectors", True, "allow_model_selectors empty")
        else:
            _append_rule(
                "policy_model_context",
                True,
                "model context unavailable; model-level rules skipped",
                {
                    "allow_rules": bool(
                        policy.allow_model_providers
                        or policy.allow_model_names
                        or policy.allow_model_selectors
                    ),
                    "deny_rules": bool(
                        policy.deny_model_providers
                        or policy.deny_model_names
                        or policy.deny_model_selectors
                    ),
                },
            )

    return decision
