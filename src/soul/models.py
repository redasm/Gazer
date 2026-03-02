
import os
import logging
from typing import Dict, Any, Optional, Tuple

import runtime.config_manager as config_manager
from runtime.provider_registry import get_provider_registry

logger = logging.getLogger("GazerModels")


class ModelRegistry:
    """
    Manages LLM model selection from OpenClaw-style config:
    - agents.defaults.model.primary
    - agents.defaults.model.fallbacks
    - agents.defaults.models (alias map)
    """

    @staticmethod
    def get_provider_config(provider_name: str) -> Dict[str, Any]:
        """Get raw config for a provider from provider registry file."""
        return get_provider_registry().get_provider(provider_name)

    @staticmethod
    def _get_agents_defaults() -> Dict[str, Any]:
        raw = config_manager.config.get("agents.defaults", {}) or {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _get_model_defaults() -> Dict[str, Any]:
        defaults = ModelRegistry._get_agents_defaults()
        model_cfg = defaults.get("model", {})
        if isinstance(model_cfg, str):
            text = str(model_cfg).strip()
            return {"primary": text, "fallbacks": []}
        if isinstance(model_cfg, dict):
            return dict(model_cfg)
        return {}

    @staticmethod
    def _get_model_catalog() -> Dict[str, Dict[str, Any]]:
        defaults = ModelRegistry._get_agents_defaults()
        raw = defaults.get("models", {})
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for key, value in raw.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            out[key_text] = dict(value) if isinstance(value, dict) else {}
        return out

    @staticmethod
    def _resolve_profile_selection(profile_name: str) -> str:
        profile = str(profile_name or "").strip().lower()
        model_cfg = ModelRegistry._get_model_defaults()
        primary = str(model_cfg.get("primary", "") or "").strip()
        fallbacks_raw = model_cfg.get("fallbacks", [])
        fallbacks = (
            [str(item).strip() for item in fallbacks_raw if str(item).strip()]
            if isinstance(fallbacks_raw, list)
            else []
        )

        if profile in {"slow", "slow_brain", "primary"}:
            return primary
        if profile in {"fast", "fast_brain", "fallback"}:
            return fallbacks[0] if fallbacks else primary
        return ""

    @staticmethod
    def _split_model_ref(model_ref: str) -> Tuple[Optional[str], Optional[str]]:
        text = str(model_ref or "").strip()
        if "/" not in text:
            return None, None
        provider, model = text.split("/", 1)
        provider = provider.strip()
        model = model.strip()
        if not provider or not model:
            return None, None
        return provider, model

    @staticmethod
    def _resolve_alias_model_ref(selection: str) -> str:
        sel = str(selection or "").strip()
        if not sel:
            return ""
        if "/" in sel:
            return sel

        needle = sel.lower()
        catalog = ModelRegistry._get_model_catalog()
        for model_ref, entry in catalog.items():
            ref_text = str(model_ref or "").strip()
            if not ref_text:
                continue
            if ref_text.lower() == needle:
                return ref_text
            alias = str(entry.get("alias", "") or "").strip().lower() if isinstance(entry, dict) else ""
            if alias and alias == needle:
                return ref_text
        return ""

    @staticmethod
    def resolve_model_ref(profile_name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Resolve profile target into (provider_name, model_name).

        - slow_brain -> agents.defaults.model.primary
        - fast_brain -> agents.defaults.model.fallbacks[0] (or primary when absent)
        """
        selection = ModelRegistry._resolve_profile_selection(profile_name)
        if not selection:
            logger.warning("Missing model selection for profile: %s", profile_name)
            return None, None

        resolved = ModelRegistry._resolve_alias_model_ref(selection)
        if not resolved:
            logger.warning(
                "Invalid model ref for profile '%s': expected 'provider/model' or alias from agents.defaults.models, got=%s",
                profile_name,
                selection,
            )
            return None, None

        provider_name, model_name = ModelRegistry._split_model_ref(resolved)
        if not provider_name or not model_name:
            logger.warning("Malformed model ref for profile '%s': %s", profile_name, resolved)
            return None, None
        return provider_name, model_name

    @staticmethod
    def resolve_model(profile_name: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[Dict[str, str]]]:
        """
        Resolve (api_key, base_url, model_name, headers) for a profile target.

        Profile targets:
        - slow_brain / primary
        - fast_brain / fallback
        """
        provider_name, model_name = ModelRegistry.resolve_model_ref(profile_name)
        if not provider_name:
            return None, None, None, None

        provider_config = ModelRegistry.get_provider_config(provider_name)
        if not provider_config and provider_name != "openai":
            logger.warning("Provider '%s' not found in registry.", provider_name)

        env_key = provider_name.upper().replace("-", "_").replace(".", "_")
        api_key = provider_config.get("api_key") or os.getenv(f"{env_key}_API_KEY")
        base_url = provider_config.get("base_url")

        if provider_name == "openai" and not base_url:
            base_url = "https://api.openai.com/v1"

        if not model_name:
            model_name = str(provider_config.get("default_model", "") or "").strip() or None

        raw_headers = provider_config.get("headers")
        headers = raw_headers if isinstance(raw_headers, dict) else None

        logger.info("Resolved %s -> Provider: %s, Model: %s", profile_name, provider_name, model_name)
        return api_key, base_url, model_name, headers

    @staticmethod
    def list_models():
        """Return configured providers and OpenClaw-style default model selection."""
        return {
            "providers": list(get_provider_registry().list_providers().keys()),
            "defaults": config_manager.config.get("agents.defaults", {}),
            "resolved": {
                "slow_brain": "/".join([part for part in ModelRegistry.resolve_model_ref("slow_brain") if part]),
                "fast_brain": "/".join([part for part in ModelRegistry.resolve_model_ref("fast_brain") if part]),
            },
        }
