from __future__ import annotations

"""Configuration management router — config CRUD, web wizard, validation.

This module contains all routes and helpers for:
  - GET/POST /config
  - /web/config-wizard, /web/config/validate, /web/config-wizard/apply
  - /web/help/onboarding

Helper functions for config redaction, flattening, policy validation,
provider validation, and web-wizard state building live here.
"""

import copy
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException

from ._shared import (
    config, logger,
    _PROJECT_ROOT,
    _WEB_ONBOARDING_GUIDE_PATH,
    _is_subpath,
    _redact_config,
    _filter_masked_sensitive,
    _flatten_config,
    _MISSING,
    _ATOMIC_OBJECT_UPDATE_PATHS,
    _get_nested_payload_value,
    _collect_atomic_object_updates,
    _reject_provider_config_in_settings,
    _validate_provider_entry,
    _validate_deployment_target_entry,
    _append_policy_audit,
    _capture_strategy_snapshot,
    get_owner_manager,
    get_provider_registry,
    TOOL_REGISTRY,
)
from .auth import verify_admin_token

router = APIRouter(tags=["config"])

try:
    from tools.registry import ToolPolicy, normalize_tool_policy
except ImportError:

    ToolPolicy = None  # type: ignore
    normalize_tool_policy = None  # type: ignore

try:
    from runtime.config_manager import (
        is_internal_admin_config_path,
        is_sensitive_config_path,
    )
except ImportError:
    is_sensitive_config_path = None  # type: ignore
    is_internal_admin_config_path = None  # type: ignore

# Security-critical config keys that cannot be changed via the web API
_PROTECTED_NAMESPACES = {
    "security.dm_policy",
    "security.auto_approve_privileged",
    "api.cors_origins",
    "api.cors_credentials",
    "api.cookie_secure",
    "api.cookie_samesite",
    "api.allow_admin_bearer_token",
    "api.export_allowed_dirs",
    "api.allow_audit_buffer_clear",
}

_INTERNAL_CONFIG_PREFIXES = {
    "agents.defaults.planning",
}

_DEPRECATED_CONFIG_KEYS = {
    "api.allow_loopback_without_token",
    "api.local_bypass_environments",
}


# ---------------------------------------------------------------------------
# Policy helper functions  (shared with policy.py via re-export)
# ---------------------------------------------------------------------------

def _resolve_global_policy() -> Dict[str, Any]:
    policy_v3 = config.get("security.tool_policy_v3", {}) or {}
    if not isinstance(policy_v3, dict):
        policy_v3 = {}
    return {
        "allow_names": config.get("security.tool_allowlist", []),
        "deny_names": config.get("security.tool_denylist", []),
        "allow_providers": config.get("security.tool_allow_providers", []),
        "deny_providers": config.get("security.tool_deny_providers", []),
        "allow_model_providers": policy_v3.get("allow_model_providers", []),
        "deny_model_providers": policy_v3.get("deny_model_providers", []),
        "allow_model_names": policy_v3.get("allow_model_names", []),
        "deny_model_names": policy_v3.get("deny_model_names", []),
        "allow_model_selectors": policy_v3.get("allow_model_selectors", []),
        "deny_model_selectors": policy_v3.get("deny_model_selectors", []),
    }


def _normalize_str_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def _policy_to_payload(policy: "ToolPolicy") -> Dict[str, List[str]]:
    return {
        "allow_names": sorted(set(policy.allow_names)),
        "deny_names": sorted(set(policy.deny_names)),
        "allow_providers": sorted(set(policy.allow_providers)),
        "deny_providers": sorted(set(policy.deny_providers)),
        "allow_model_providers": sorted(set(policy.allow_model_providers)),
        "deny_model_providers": sorted(set(policy.deny_model_providers)),
        "allow_model_names": sorted(set(policy.allow_model_names)),
        "deny_model_names": sorted(set(policy.deny_model_names)),
        "allow_model_selectors": sorted(set(policy.allow_model_selectors)),
        "deny_model_selectors": sorted(set(policy.deny_model_selectors)),
    }


def _merge_allow_deny_sets(
    base_allow: set[str],
    base_deny: set[str],
    *,
    allow_values: Optional[set[str]] = None,
    deny_values: Optional[set[str]] = None,
) -> tuple[set[str], set[str]]:
    merged_allow = set(base_allow)
    incoming_allow = set(allow_values or set())
    if incoming_allow:
        merged_allow = merged_allow.intersection(incoming_allow) if merged_allow else incoming_allow
    merged_deny = set(base_deny) | set(deny_values or set())
    if merged_allow and merged_deny:
        merged_allow = {item for item in merged_allow if item not in merged_deny}
    return merged_allow, merged_deny


def _merge_policy_names(
    base: "ToolPolicy",
    *,
    allow_names: Optional[set[str]] = None,
    deny_names: Optional[set[str]] = None,
    allow_providers: Optional[set[str]] = None,
    deny_providers: Optional[set[str]] = None,
    allow_model_providers: Optional[set[str]] = None,
    deny_model_providers: Optional[set[str]] = None,
    allow_model_names: Optional[set[str]] = None,
    deny_model_names: Optional[set[str]] = None,
    allow_model_selectors: Optional[set[str]] = None,
    deny_model_selectors: Optional[set[str]] = None,
) -> "ToolPolicy":
    merged_allow_names, merged_deny_names = _merge_allow_deny_sets(
        set(base.allow_names), set(base.deny_names),
        allow_values=allow_names, deny_values=deny_names,
    )
    merged_allow_providers, merged_deny_providers = _merge_allow_deny_sets(
        set(base.allow_providers), set(base.deny_providers),
        allow_values=allow_providers, deny_values=deny_providers,
    )
    merged_allow_model_providers, merged_deny_model_providers = _merge_allow_deny_sets(
        set(base.allow_model_providers), set(base.deny_model_providers),
        allow_values=allow_model_providers, deny_values=deny_model_providers,
    )
    merged_allow_model_names, merged_deny_model_names = _merge_allow_deny_sets(
        set(base.allow_model_names), set(base.deny_model_names),
        allow_values=allow_model_names, deny_values=deny_model_names,
    )
    merged_allow_model_selectors, merged_deny_model_selectors = _merge_allow_deny_sets(
        set(base.allow_model_selectors), set(base.deny_model_selectors),
        allow_values=allow_model_selectors, deny_values=deny_model_selectors,
    )
    return ToolPolicy(
        allow_names=merged_allow_names,
        deny_names=merged_deny_names,
        allow_providers=merged_allow_providers,
        deny_providers=merged_deny_providers,
        allow_model_providers=merged_allow_model_providers,
        deny_model_providers=merged_deny_model_providers,
        allow_model_names=merged_allow_model_names,
        deny_model_names=merged_deny_model_names,
        allow_model_selectors=merged_allow_model_selectors,
        deny_model_selectors=merged_deny_model_selectors,
    )


def _resolve_agents_overlay_policy(
    agents_target_dir: Optional[str], *, include_debug: bool = False
) -> Dict[str, Any]:
    from agent.agents_md import resolve_agents_overlay
    workspace = _PROJECT_ROOT
    target_rel = str(agents_target_dir or "").strip()
    target_path = workspace
    if target_rel:
        candidate = (workspace / target_rel).resolve()
        if not _is_subpath(workspace, candidate):
            raise HTTPException(status_code=400, detail="'agents_target_dir' must stay inside workspace")
        target_path = candidate
    payload = resolve_agents_overlay(workspace, target_path)
    out = {
        "target_dir": payload.get("target_dir", "."),
        "files": payload.get("files", []),
        "allowed_tools": [str(item).strip() for item in payload.get("allowed_tools", []) if str(item).strip()],
        "deny_tools": [str(item).strip() for item in payload.get("deny_tools", []) if str(item).strip()],
        "routing_hints": [str(item).strip() for item in payload.get("routing_hints", []) if str(item).strip()],
        "conflicts": payload.get("conflicts", []),
    }
    if include_debug:
        out["skill_priority"] = payload.get("skill_priority", [])
        out["combined_text"] = payload.get("combined_text", "")
        out["debug"] = payload.get("debug", [])
    return out


def _detect_policy_conflicts(layers: Dict[str, "ToolPolicy"]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    names = [name for name in layers.keys() if str(name).strip()]
    subjects: List[tuple[str, str, str]] = [
        ("tool_name", "allow_names", "deny_names"),
        ("tool_provider", "allow_providers", "deny_providers"),
        ("model_provider", "allow_model_providers", "deny_model_providers"),
        ("model_name", "allow_model_names", "deny_model_names"),
        ("model_selector", "allow_model_selectors", "deny_model_selectors"),
    ]

    for name in names:
        policy = layers.get(name)
        if policy is None:
            continue
        for subject, allow_attr, deny_attr in subjects:
            overlap = sorted(set(getattr(policy, allow_attr, set())) & set(getattr(policy, deny_attr, set())))
            for item in overlap:
                key = (subject, name, item)
                if key in seen:
                    continue
                conflicts.append({
                    "type": "allow_deny_conflict", "subject": subject,
                    "value": item, "allowed_in": name, "denied_in": name,
                })
                seen.add(key)

    for left_idx, left_name in enumerate(names):
        left = layers.get(left_name)
        if left is None:
            continue
        for right_name in names[left_idx + 1:]:
            right = layers.get(right_name)
            if right is None:
                continue
            for subject, allow_attr, deny_attr in subjects:
                left_allow = set(getattr(left, allow_attr, set()))
                left_deny = set(getattr(left, deny_attr, set()))
                right_allow = set(getattr(right, allow_attr, set()))
                right_deny = set(getattr(right, deny_attr, set()))
                for item in sorted(left_allow & right_deny):
                    key = (subject, f"{left_name}->{right_name}", item)
                    if key in seen:
                        continue
                    conflicts.append({
                        "type": "allow_deny_conflict", "subject": subject,
                        "value": item, "allowed_in": left_name, "denied_in": right_name,
                    })
                    seen.add(key)
                for item in sorted(right_allow & left_deny):
                    key = (subject, f"{right_name}->{left_name}", item)
                    if key in seen:
                        continue
                    conflicts.append({
                        "type": "allow_deny_conflict", "subject": subject,
                        "value": item, "allowed_in": right_name, "denied_in": left_name,
                    })
                    seen.add(key)
    return conflicts


def _validate_policy_config(new_config: Dict[str, Any]) -> None:
    """Validate policy-related config sections before applying updates."""
    security_patch = new_config.get("security", {}) if isinstance(new_config.get("security"), dict) else {}
    agents_patch = new_config.get("agents", {}) if isinstance(new_config.get("agents"), dict) else {}
    tool_policy_v3_patch = (
        security_patch.get("tool_policy_v3")
        if isinstance(security_patch.get("tool_policy_v3"), dict)
        else {}
    )

    owner_channel_ids_candidate = security_patch.get("owner_channel_ids")
    if owner_channel_ids_candidate is None:
        owner_channel_ids_candidate = config.get("security.owner_channel_ids", {})
    if owner_channel_ids_candidate is None:
        owner_channel_ids_candidate = {}
    if not isinstance(owner_channel_ids_candidate, dict):
        raise HTTPException(status_code=400, detail="'security.owner_channel_ids' must be an object")
    for channel, sender_id in owner_channel_ids_candidate.items():
        if not str(channel).strip() or not str(sender_id).strip():
            raise HTTPException(
                status_code=400,
                detail="'security.owner_channel_ids' requires non-empty channel and sender_id",
            )

    groups_candidate = security_patch.get("tool_groups")
    if groups_candidate is None:
        groups_candidate = config.get("security.tool_groups", {})
    if not isinstance(groups_candidate, dict):
        raise HTTPException(status_code=400, detail="'security.tool_groups' must be an object")
    for key, value in groups_candidate.items():
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise HTTPException(
                status_code=400,
                detail=f"'security.tool_groups.{key}' must be a string array",
            )

    tool_policy_v3_candidate = config.get("security.tool_policy_v3", {}) or {}
    if not isinstance(tool_policy_v3_candidate, dict):
        tool_policy_v3_candidate = {}
    if tool_policy_v3_patch:
        merged_v3 = dict(tool_policy_v3_candidate)
        merged_v3.update(tool_policy_v3_patch)
        tool_policy_v3_candidate = merged_v3

    # Model provider conflicts
    global_allow_model_providers = {item.lower() for item in _normalize_str_list(tool_policy_v3_candidate.get("allow_model_providers"))}
    global_deny_model_providers = {item.lower() for item in _normalize_str_list(tool_policy_v3_candidate.get("deny_model_providers"))}
    overlap = sorted(global_allow_model_providers & global_deny_model_providers)
    if overlap:
        raise HTTPException(status_code=400, detail=f"'security.tool_policy_v3' has model provider conflicts: {overlap}")

    # Model name conflicts
    global_allow_model_names = {item.lower() for item in _normalize_str_list(tool_policy_v3_candidate.get("allow_model_names"))}
    global_deny_model_names = {item.lower() for item in _normalize_str_list(tool_policy_v3_candidate.get("deny_model_names"))}
    overlap = sorted(global_allow_model_names & global_deny_model_names)
    if overlap:
        raise HTTPException(status_code=400, detail=f"'security.tool_policy_v3' has model name conflicts: {overlap}")

    # Model selector conflicts
    global_allow_model_selectors = {item.lower() for item in _normalize_str_list(tool_policy_v3_candidate.get("allow_model_selectors"))}
    global_deny_model_selectors = {item.lower() for item in _normalize_str_list(tool_policy_v3_candidate.get("deny_model_selectors"))}
    overlap = sorted(global_allow_model_selectors & global_deny_model_selectors)
    if overlap:
        raise HTTPException(status_code=400, detail=f"'security.tool_policy_v3' has model selector conflicts: {overlap}")

# ---------------------------------------------------------------------------
# Web wizard helpers
# ---------------------------------------------------------------------------

def _wizard_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        marker = value.strip().lower()
        if marker in {"1", "true", "yes", "on", "enabled"}:
            return True
        if marker in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _wizard_parse_id_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base if isinstance(base, dict) else {})
    if not isinstance(patch, dict):
        return out
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out.get(key, {}), value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _is_local_provider(provider_name: str, provider_cfg: Dict[str, Any]) -> bool:
    name = str(provider_name or "").strip().lower()
    base_url = str(provider_cfg.get("base_url", "") or "").strip().lower()
    if "ollama" in name:
        return True
    return "localhost" in base_url or "127.0.0.1" in base_url


def _provider_has_key(provider_name: str, provider_cfg: Dict[str, Any]) -> bool:
    if _is_local_provider(provider_name, provider_cfg):
        return True
    key = str(provider_cfg.get("api_key", "") or "").strip()
    return bool(key and key not in {"***"})


def _build_web_config_validation_report(
    *,
    config_patch: Optional[Dict[str, Any]] = None,
    providers_patch: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg_candidate = _deep_merge_dict(
        copy.deepcopy(config.data if isinstance(config.data, dict) else {}),
        config_patch or {},
    )
    registry = get_provider_registry()
    providers_current = registry.list_providers() if hasattr(registry, "list_providers") else {}
    providers_candidate = _deep_merge_dict(
        providers_current if isinstance(providers_current, dict) else {},
        providers_patch or {},
    )

    issues: List[Dict[str, Any]] = []
    fixes: List[Dict[str, Any]] = []

    def _issue(level: str, code: str, path: str, message: str, fix: Optional[Dict[str, Any]] = None) -> None:
        payload = {"level": str(level), "code": str(code), "path": str(path), "message": str(message)}
        if fix is not None:
            payload["suggested_fix"] = fix
            fixes.append({"path": str(fix.get("path", path)), "value": fix.get("value"), "reason": str(fix.get("reason", code))})
        issues.append(payload)

    def _split_model_ref(model_ref: str) -> Tuple[str, str]:
        text = str(model_ref or "").strip()
        if "/" not in text:
            return "", ""
        provider, model = text.split("/", 1)
        return provider.strip(), model.strip()

    def _resolve_target_model_ref(target: str) -> Tuple[str, str]:
        defaults_cfg = cfg_candidate.get("agents", {}).get("defaults", {})
        model_cfg = defaults_cfg.get("model", {}) if isinstance(defaults_cfg, dict) else {}
        primary_ref = ""
        fallback_ref = ""
        if isinstance(model_cfg, str):
            primary_ref = str(model_cfg).strip()
        elif isinstance(model_cfg, dict):
            primary_ref = str(model_cfg.get("primary", "") or "").strip()
            raw_fallbacks = model_cfg.get("fallbacks", [])
            if isinstance(raw_fallbacks, list) and raw_fallbacks:
                fallback_ref = str(raw_fallbacks[0] or "").strip()
        if not fallback_ref:
            fallback_ref = primary_ref
        if target == "slow_brain":
            return primary_ref, "agents.defaults.model.primary"
        return fallback_ref, "agents.defaults.model.fallbacks"

    def _check_model_target(path_prefix: str) -> None:
        model_ref, ref_path = _resolve_target_model_ref(path_prefix)
        provider_name, model_name = _split_model_ref(model_ref)
        if not model_ref:
            _issue("error", "missing_model_ref", ref_path, f"{path_prefix} model ref is required (format: provider/model).")
            return
        if not provider_name or not model_name:
            _issue("error", "invalid_model_ref", ref_path, f"{path_prefix} model ref must be 'provider/model', got '{model_ref}'.")
            return
        provider_cfg = providers_candidate.get(provider_name, {}) if isinstance(providers_candidate, dict) else {}
        if not isinstance(provider_cfg, dict) or not provider_cfg:
            _issue("error", "provider_not_found", ref_path, f"Provider '{provider_name}' not found in provider registry.")
            return
        if not str(provider_cfg.get("base_url", "")).strip():
            _issue("warning", "provider_base_url_missing", f"providers.{provider_name}.base_url", f"Provider '{provider_name}' has empty base_url.")
        if not str(provider_cfg.get("default_model", "")).strip() and not model_name:
            _issue("warning", "provider_default_model_missing", f"providers.{provider_name}.default_model", f"Provider '{provider_name}' has empty default_model.")
        if not _provider_has_key(provider_name, provider_cfg):
            _issue("warning", "provider_api_key_missing", f"providers.{provider_name}.api_key", f"Provider '{provider_name}' has no API key configured.")

    _check_model_target("slow_brain")
    _check_model_target("fast_brain")

    telegram_cfg = cfg_candidate.get("telegram", {}) if isinstance(cfg_candidate.get("telegram"), dict) else {}
    feishu_cfg = cfg_candidate.get("feishu", {}) if isinstance(cfg_candidate.get("feishu"), dict) else {}
    discord_cfg = cfg_candidate.get("discord", {}) if isinstance(cfg_candidate.get("discord"), dict) else {}
    security_cfg = cfg_candidate.get("security", {}) if isinstance(cfg_candidate.get("security"), dict) else {}
    owner_map = security_cfg.get("owner_channel_ids", {}) if isinstance(security_cfg.get("owner_channel_ids"), dict) else {}
    dm_policy = str(security_cfg.get("dm_policy", "pairing") or "pairing").strip().lower()

    if _wizard_bool(telegram_cfg.get("enabled", False), False):
        if not str(telegram_cfg.get("token", "")).strip():
            _issue("error", "telegram_token_missing", "telegram.token", "Telegram enabled but token is empty.")
        if not _wizard_parse_id_list(telegram_cfg.get("allowed_ids", [])):
            _issue("warning", "telegram_allowed_ids_empty", "telegram.allowed_ids", "Telegram enabled but allowed_ids is empty.")
    if _wizard_bool(feishu_cfg.get("enabled", False), False):
        if not str(feishu_cfg.get("app_id", "")).strip():
            _issue("error", "feishu_app_id_missing", "feishu.app_id", "Feishu enabled but app_id is empty.")
        if not str(feishu_cfg.get("app_secret", "")).strip():
            _issue("error", "feishu_app_secret_missing", "feishu.app_secret", "Feishu enabled but app_secret is empty.")
    if _wizard_bool(discord_cfg.get("enabled", False), False):
        if not str(discord_cfg.get("token", "")).strip():
            _issue("error", "discord_token_missing", "discord.token", "Discord enabled but token is empty.")
        if not _wizard_parse_id_list(discord_cfg.get("allowed_guild_ids", [])):
            _issue("warning", "discord_allowed_guild_ids_empty", "discord.allowed_guild_ids", "Discord enabled but allowed_guild_ids is empty.")

    if dm_policy == "open":
        _issue("warning", "dm_policy_open", "security.dm_policy", "DM policy is 'open'.",
               fix={"path": "security.dm_policy", "value": "pairing", "reason": "use pairing for safer onboarding"})
    if not isinstance(owner_map, dict) or not owner_map:
        _issue("warning", "owner_channel_ids_missing", "security.owner_channel_ids", "No owner channel ids configured.")
    auto_privileged = bool(security_cfg.get("auto_approve_privileged", False))
    if auto_privileged:
        _issue("warning", "auto_approve_privileged_enabled", "security.auto_approve_privileged",
               "auto_approve_privileged should remain disabled for production safety.")


    owner_manager = get_owner_manager()
    if not str(getattr(owner_manager, "admin_token", "") or "").strip():
        _issue("warning", "admin_token_missing", "owner.admin_token", "Admin token is empty; Web admin endpoints are weakly protected.")

    error_count = sum(1 for item in issues if str(item.get("level", "")) == "error")
    warning_count = sum(1 for item in issues if str(item.get("level", "")) == "warning")
    score = max(0, 100 - (error_count * 25) - (warning_count * 8))
    return {
        "status": "ok",
        "ok": error_count == 0,
        "summary": {"errors": error_count, "warnings": warning_count, "score": score},
        "issues": issues,
        "fixes": fixes,
    }


def _build_web_config_wizard_state() -> Dict[str, Any]:
    cfg = config.to_safe_dict()
    registry = get_provider_registry()
    providers_raw = registry.list_providers() if hasattr(registry, "list_providers") else {}
    providers_redacted = registry.list_redacted_providers() if hasattr(registry, "list_redacted_providers") else {}

    defaults_cfg = cfg.get("agents", {}).get("defaults", {}) if isinstance(cfg.get("agents"), dict) else {}
    model_cfg = defaults_cfg.get("model", {}) if isinstance(defaults_cfg, dict) else {}
    primary_ref = ""
    fast_ref = ""
    if isinstance(model_cfg, str):
        primary_ref = str(model_cfg).strip()
    elif isinstance(model_cfg, dict):
        primary_ref = str(model_cfg.get("primary", "") or "").strip()
        raw_fallbacks = model_cfg.get("fallbacks", [])
        if isinstance(raw_fallbacks, list) and raw_fallbacks:
            fast_ref = str(raw_fallbacks[0] or "").strip()
    if not fast_ref:
        fast_ref = primary_ref

    def _split_ref(ref: str) -> Tuple[str, str]:
        text = str(ref or "").strip()
        if "/" not in text:
            return "", ""
        provider, model = text.split("/", 1)
        return provider.strip(), model.strip()

    slow_provider, slow_model = _split_ref(primary_ref)
    fast_provider, fast_model = _split_ref(fast_ref)

    selected_provider_ready = False
    for provider_name in {slow_provider, fast_provider}:
        if not provider_name:
            continue
        prov_cfg = providers_raw.get(provider_name, {}) if isinstance(providers_raw, dict) else {}
        if isinstance(prov_cfg, dict) and _provider_has_key(provider_name, prov_cfg):
            selected_provider_ready = True

    channels = {
        "telegram": {
            "enabled": _wizard_bool(cfg.get("telegram", {}).get("enabled", False), False) if isinstance(cfg.get("telegram"), dict) else False,
            "token_set": bool(str(cfg.get("telegram", {}).get("token", "")).strip()) if isinstance(cfg.get("telegram"), dict) else False,
            "allowed_count": len(_wizard_parse_id_list(cfg.get("telegram", {}).get("allowed_ids", []))) if isinstance(cfg.get("telegram"), dict) else 0,
        },
        "feishu": {
            "enabled": _wizard_bool(cfg.get("feishu", {}).get("enabled", False), False) if isinstance(cfg.get("feishu"), dict) else False,
            "app_id_set": bool(str(cfg.get("feishu", {}).get("app_id", "")).strip()) if isinstance(cfg.get("feishu"), dict) else False,
            "app_secret_set": bool(str(cfg.get("feishu", {}).get("app_secret", "")).strip()) if isinstance(cfg.get("feishu"), dict) else False,
            "allowed_count": len(_wizard_parse_id_list(cfg.get("feishu", {}).get("allowed_ids", []))) if isinstance(cfg.get("feishu"), dict) else 0,
        },
        "discord": {
            "enabled": _wizard_bool(cfg.get("discord", {}).get("enabled", False), False) if isinstance(cfg.get("discord"), dict) else False,
            "token_set": bool(str(cfg.get("discord", {}).get("token", "")).strip()) if isinstance(cfg.get("discord"), dict) else False,
            "allowed_count": len(_wizard_parse_id_list(cfg.get("discord", {}).get("allowed_guild_ids", []))) if isinstance(cfg.get("discord"), dict) else 0,
        },
    }
    enabled_channels = [name for name, meta in channels.items() if bool(meta.get("enabled", False))]
    channel_credentials_ready = True
    for channel_name in enabled_channels:
        meta = channels[channel_name]
        if channel_name == "telegram" and not bool(meta.get("token_set", False)):
            channel_credentials_ready = False
        if channel_name == "feishu" and (not bool(meta.get("app_id_set", False)) or not bool(meta.get("app_secret_set", False))):
            channel_credentials_ready = False
        if channel_name == "discord" and not bool(meta.get("token_set", False)):
            channel_credentials_ready = False

    validation = _build_web_config_validation_report()
    security_cfg = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}
    owner_ids = security_cfg.get("owner_channel_ids", {}) if isinstance(security_cfg.get("owner_channel_ids"), dict) else {}
    owner_ready = isinstance(owner_ids, dict) and bool(owner_ids)
    dm_policy = str(security_cfg.get("dm_policy", "pairing") or "pairing").strip().lower()
    auto_privileged = bool(security_cfg.get("auto_approve_privileged", False))


    steps = [
        {
            "id": "llm_provider",
            "title": "模型服务商与主模型",
            "completed": bool(slow_provider and fast_provider and slow_model and fast_model and selected_provider_ready),
            "details": {
                "primary": {"ref": primary_ref, "provider": slow_provider, "model": slow_model},
                "fast": {"ref": fast_ref, "provider": fast_provider, "model": fast_model},
                "provider_ready": selected_provider_ready,
            },
            "suggestions": [
                "确保 slow/fast brain 都已选择 provider 和 model。",
                "远程 provider 需要配置 API Key；本地 Ollama 可以免 key。",
            ],
        },
        {
            "id": "channel_onboarding",
            "title": "渠道接入（Telegram/Feishu/Discord）",
            "completed": bool(enabled_channels and channel_credentials_ready),
            "details": {
                "enabled_channels": enabled_channels,
                "channel_credentials_ready": channel_credentials_ready,
                "channels": channels,
            },
            "suggestions": [
                "至少启用一个渠道并填入必要凭证。",
                "启用渠道后建议配置 owner_channel_ids 做运维闭环。",
            ],
        },
        {
            "id": "security_baseline",
            "title": "安全基线（owner/鉴权/危险工具）",
            "completed": bool(owner_ready and dm_policy != "open" and not auto_privileged),
            "details": {
                "owner_channel_ids_ready": owner_ready,
                "dm_policy": dm_policy,
                "auto_approve_privileged": auto_privileged,

                "validation_score": validation.get("summary", {}).get("score", 0),
            },
            "suggestions": [
                "将 dm_policy 设为 pairing 或 allowlist。",
                "保持 auto_approve_privileged=false，使用 owner_only 工具限制。",
            ],
        },
    ]

    return {
        "status": "ok",
        "generated_at": time.time(),
        "steps": steps,
        "providers": providers_redacted if isinstance(providers_redacted, dict) else {},
        "validation": validation,
        "help": {
            "guide_endpoint": "/web/help/onboarding",
            "validation_endpoint": "/web/config/validate",
            "apply_endpoint": "/web/config-wizard/apply",
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/config", dependencies=[Depends(verify_admin_token)])
async def get_config():
    """Return config with sensitive values redacted."""
    return config.to_safe_dict()


@router.post("/config", dependencies=[Depends(verify_admin_token)])
async def update_config(new_config: Dict[str, Any]):
    from .websockets import manager  # local import avoids circular deps

    filtered_config = _filter_masked_sensitive(new_config, config.data)
    _reject_provider_config_in_settings(filtered_config)
    _validate_policy_config(filtered_config)
    flat_updates = _flatten_config(filtered_config)
    changed_updates = {
        key: value for key, value in flat_updates.items()
        if config.get(key) != value
    }
    atomic_object_updates = _collect_atomic_object_updates(filtered_config)
    for key_path, value in atomic_object_updates.items():
        if config.get(key_path) == value:
            continue
        stale_nested_keys = [
            key for key in changed_updates.keys()
            if key == key_path or key.startswith(f"{key_path}.")
        ]
        for stale_key in stale_nested_keys:
            changed_updates.pop(stale_key, None)
        changed_updates[key_path] = value

    before_values = {key: config.get(key) for key in changed_updates.keys()}

    for changed_key in changed_updates.keys():
        if changed_key in _DEPRECATED_CONFIG_KEYS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Config key '{changed_key}' has been removed and is no longer supported. "
                    "Delete it from settings.yaml instead of updating it through the admin API."
                ),
            )
        if changed_key in _PROTECTED_NAMESPACES:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot modify protected config key '{changed_key}' via this endpoint. "
                       f"Edit config/settings.yaml directly.",
            )
        if (
            changed_key in _INTERNAL_CONFIG_PREFIXES
            or any(changed_key.startswith(f"{prefix}.") for prefix in _INTERNAL_CONFIG_PREFIXES)
            or (callable(is_internal_admin_config_path) and is_internal_admin_config_path(changed_key))
        ):
            raise HTTPException(
                status_code=403,
                detail=f"Cannot modify internal config key '{changed_key}' via this endpoint.",
            )

    if changed_updates:
        config.set_many(changed_updates)
        policy_keys = sorted(
            key for key in changed_updates.keys()
            if key.startswith("security.tool_")
        )
        if policy_keys:
            _append_policy_audit(
                action="policy.config.updated",
                details={"keys": policy_keys, "count": len(policy_keys)},
            )
        strategy_keys = sorted(
            key for key in changed_updates.keys()
            if (
                key.startswith("security.tool_")
                or key.startswith("personality.mental_process")
                or key.startswith("personality.runtime.auto_correction")
            )
        )
        if strategy_keys:
            _capture_strategy_snapshot(
                category="config_strategy",
                before={key: before_values.get(key) for key in strategy_keys},
                after={key: changed_updates.get(key) for key in strategy_keys},
                actor="admin",
                source="/config",
                metadata={"key_count": len(strategy_keys)},
            )
    await manager.broadcast({"type": "config_updated", "data": _redact_config(filtered_config)})
    return {"status": "success", "message": "Config updated"}


@router.get("/web/config-wizard", dependencies=[Depends(verify_admin_token)])
async def get_web_config_wizard():
    return _build_web_config_wizard_state()


@router.post("/web/config/validate", dependencies=[Depends(verify_admin_token)])
async def validate_web_config(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        payload = {}
    cfg_patch = payload.get("config_patch", payload.get("config", {}))
    providers_patch = payload.get("providers_patch", payload.get("providers", {}))
    if not isinstance(cfg_patch, dict):
        cfg_patch = {}
    if not isinstance(providers_patch, dict):
        providers_patch = {}
    return _build_web_config_validation_report(config_patch=cfg_patch, providers_patch=providers_patch)


@router.post("/web/config-wizard/apply", dependencies=[Depends(verify_admin_token)])
async def apply_web_config_wizard(payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        payload = {}
    updates: Dict[str, Any] = {}
    warnings: List[str] = []
    registry = get_provider_registry()

    llm = payload.get("llm", {})
    if isinstance(llm, dict):
        provider_name = str(llm.get("provider", "")).strip()
        model_name = str(llm.get("model", "")).strip()
        if provider_name:
            current_provider = registry.get_provider(provider_name) if hasattr(registry, "get_provider") else {}
            if not isinstance(current_provider, dict):
                current_provider = {}
            candidate_provider = dict(current_provider)
            if "base_url" in llm:
                candidate_provider["base_url"] = str(llm.get("base_url", "") or "").strip()
            if "api_key" in llm:
                candidate_provider["api_key"] = str(llm.get("api_key", "") or "").strip()
            if "default_model" in llm:
                candidate_provider["default_model"] = str(llm.get("default_model", "") or "").strip()
            elif model_name and not str(candidate_provider.get("default_model", "")).strip():
                candidate_provider["default_model"] = model_name
            if "api" in llm:
                candidate_provider["api"] = str(llm.get("api", "") or "").strip()
            validated_provider = _validate_provider_entry(provider_name, candidate_provider)
            if hasattr(registry, "upsert_provider"):
                registry.upsert_provider(provider_name, validated_provider)

            resolved_model = model_name or str(candidate_provider.get("default_model", "") or "").strip()
            if not resolved_model:
                raise HTTPException(status_code=400, detail="llm.model or llm.default_model is required")
            selected_ref = f"{provider_name}/{resolved_model}"

            targets = llm.get("profile_targets", ["primary", "fast"])
            target_list = targets if isinstance(targets, list) else ["primary", "fast"]
            for target in target_list:
                target_key = str(target).strip().lower()
                if target_key in {"primary", "slow", "slow_brain"}:
                    updates["agents.defaults.model.primary"] = selected_ref
                elif target_key in {"fast", "fallback", "fast_brain"}:
                    updates["agents.defaults.model.fallbacks"] = [selected_ref]

            # Embedding model
            if _wizard_bool(llm.get("apply_embedding"), False):
                embedding_model = str(llm.get("embedding_model", "") or "").strip()
                if embedding_model:
                    updates["models.embedding.model"] = embedding_model
                    updates["models.embedding.provider"] = provider_name

    channels = payload.get("channels", {})
    if isinstance(channels, dict):
        telegram = channels.get("telegram", {})
        if isinstance(telegram, dict):
            if "enabled" in telegram:
                updates["telegram.enabled"] = _wizard_bool(telegram.get("enabled"), False)
            if "token" in telegram:
                updates["telegram.token"] = str(telegram.get("token", "") or "").strip()
            if "allowed_ids" in telegram:
                updates["telegram.allowed_ids"] = _wizard_parse_id_list(telegram.get("allowed_ids"))
        feishu = channels.get("feishu", {})
        if isinstance(feishu, dict):
            if "enabled" in feishu:
                updates["feishu.enabled"] = _wizard_bool(feishu.get("enabled"), False)
            if "app_id" in feishu:
                updates["feishu.app_id"] = str(feishu.get("app_id", "") or "").strip()
            if "app_secret" in feishu:
                updates["feishu.app_secret"] = str(feishu.get("app_secret", "") or "").strip()
            if "allowed_ids" in feishu:
                updates["feishu.allowed_ids"] = _wizard_parse_id_list(feishu.get("allowed_ids"))
        discord = channels.get("discord", {})
        if isinstance(discord, dict):
            if "enabled" in discord:
                updates["discord.enabled"] = _wizard_bool(discord.get("enabled"), False)
            if "token" in discord:
                updates["discord.token"] = str(discord.get("token", "") or "").strip()
            if "allowed_guild_ids" in discord:
                updates["discord.allowed_guild_ids"] = _wizard_parse_id_list(discord.get("allowed_guild_ids"))

    security = payload.get("security", {})
    if isinstance(security, dict):
        if "dm_policy" in security:
            dm_policy = str(security.get("dm_policy", "")).strip().lower()
            if dm_policy not in {"open", "allowlist", "pairing"}:
                raise HTTPException(status_code=400, detail="security.dm_policy must be one of ['open','allowlist','pairing']")
            updates["security.dm_policy"] = dm_policy
        if "owner_channel_ids" in security:
            owner_map = security.get("owner_channel_ids", {})
            if not isinstance(owner_map, dict):
                raise HTTPException(status_code=400, detail="security.owner_channel_ids must be an object")
            normalized_owner = {
                str(channel).strip(): str(sender).strip()
                for channel, sender in owner_map.items()
                if str(channel).strip() and str(sender).strip()
            }
            updates["security.owner_channel_ids"] = normalized_owner

        if "auto_approve_privileged" in security:
            warnings.append("security.auto_approve_privileged is protected and was not changed by wizard endpoint.")

    if updates:
        _validate_policy_config({"security": {"owner_channel_ids": updates.get("security.owner_channel_ids", config.get("security.owner_channel_ids", {}))}})
        config.set_many(updates)

    validation = _build_web_config_validation_report()
    return {
        "status": "ok",
        "updated_keys": sorted(updates.keys()),
        "warnings": warnings,
        "validation": validation,
    }


@router.get("/web/help/onboarding", dependencies=[Depends(verify_admin_token)])
async def get_web_onboarding_help():
    if _WEB_ONBOARDING_GUIDE_PATH.is_file():
        try:
            content = _WEB_ONBOARDING_GUIDE_PATH.read_text(encoding="utf-8")
        except Exception:
            content = ""
    else:
        content = ""
    if not content:
        content = (
            "# Web Onboarding Guide\n\n"
            "1. Configure provider and model.\n"
            "2. Enable at least one channel.\n"
            "3. Set owner channel IDs and secure DM policy.\n"
            "4. Run /web/config/validate and fix warnings.\n"
        )
    return {"status": "ok", "path": str(_WEB_ONBOARDING_GUIDE_PATH), "content": content}
