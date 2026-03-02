from __future__ import annotations

from typing import Any, Dict

from runtime.provider_registry import get_provider_registry
from soul.models import ModelRegistry


def resolve_openai_compatible_cloud_config(
    cloud_cfg: Dict[str, Any],
    *,
    default_model: str = "",
    require_base_url: bool = True,
) -> Dict[str, Any]:
    """Resolve cloud config from inline fields + provider registry reference.

    Priority:
    1) Explicit fields in `cloud_cfg` (api_key/base_url/model)
    2) Referenced provider in model provider registry (`provider_ref`)
    3) `default_model` fallback for model
    """
    cfg = cloud_cfg if isinstance(cloud_cfg, dict) else {}
    provider_mode = str(cfg.get("provider", "disabled") or "disabled").strip()
    if provider_mode == "disabled":
        return {
            "enabled": False,
            "reason": "disabled",
            "provider_mode": provider_mode,
            "provider_ref": "",
            "base_url": "",
            "api_key": "",
            "model": "",
        }

    provider_ref = str(cfg.get("provider_ref", "") or "").strip()
    # Support inheriting fast_brain provider from agent defaults model fallback.
    if provider_mode in {"fast_brain", "agents.defaults.model.fallbacks"} and not provider_ref:
        provider_name, _ = ModelRegistry.resolve_model_ref("fast_brain")
        provider_ref = str(provider_name or "").strip()
    # Support selecting provider directly via `provider`,
    # e.g. "dashscope"/"openai"/"deepseek", not only `provider_ref`.
    if not provider_ref and provider_mode not in {"openai_compatible", "openai-compatible"}:
        provider_ref = provider_mode
    base_url = str(cfg.get("base_url", "") or "").strip()
    api_key = str(cfg.get("api_key", "") or "").strip()
    model = str(cfg.get("model", "") or "").strip() or str(default_model or "").strip()
    reason = ""

    if provider_ref:
        provider = get_provider_registry().get_provider(provider_ref)
        if isinstance(provider, dict) and provider:
            base_url = base_url or str(provider.get("base_url", "") or "").strip()
            api_key = api_key or str(provider.get("api_key", "") or "").strip()
            model = model or str(provider.get("default_model", "") or "").strip()
        else:
            reason = f"provider_ref_not_found:{provider_ref}"

    if not api_key:
        reason = reason or "missing_api_key"
        return {
            "enabled": False,
            "reason": reason,
            "provider_mode": provider_mode,
            "provider_ref": provider_ref,
            "base_url": base_url,
            "api_key": "",
            "model": model,
        }

    if require_base_url and not base_url:
        reason = reason or "missing_base_url"
        return {
            "enabled": False,
            "reason": reason,
            "provider_mode": provider_mode,
            "provider_ref": provider_ref,
            "base_url": "",
            "api_key": api_key,
            "model": model,
        }

    if not model:
        reason = reason or "missing_model"
        return {
            "enabled": False,
            "reason": reason,
            "provider_mode": provider_mode,
            "provider_ref": provider_ref,
            "base_url": base_url,
            "api_key": api_key,
            "model": "",
        }

    return {
        "enabled": True,
        "reason": "",
        "provider_mode": provider_mode,
        "provider_ref": provider_ref,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }
