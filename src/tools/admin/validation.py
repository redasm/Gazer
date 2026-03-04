"""Config and provider validation helpers extracted from _shared.py."""

from __future__ import annotations
from fastapi import HTTPException
from typing import Any, Dict, List
from tools.admin.state import _ATOMIC_OBJECT_UPDATE_PATHS
from tools.admin.utils import _MISSING
from runtime.provider_registry import get_provider_registry


def _get_nested_payload_value(payload: Any, dot_path: str) -> Any:
    current = payload
    for segment in str(dot_path).split("."):
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current

def _collect_atomic_object_updates(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    updates: Dict[str, Any] = {}
    for dot_path in _ATOMIC_OBJECT_UPDATE_PATHS:
        value = _get_nested_payload_value(payload, dot_path)
        if value is _MISSING:
            continue
        updates[dot_path] = value
    return updates

def _reject_provider_config_in_settings(payload: Any) -> None:
    """Disallow provider registry updates via /config."""
    if not isinstance(payload, dict):
        return
    models = payload.get("models")
    if not isinstance(models, dict):
        return
    if "providers" in models:
        raise HTTPException(
            status_code=400,
            detail=(
                "models.providers is managed in provider registry. "
                "Use /model-providers endpoints instead."
            ),
        )

def _validate_provider_entry(provider_name: str, provider: Any) -> Dict[str, Any]:
    """Validate and normalize a provider config payload."""
    if not isinstance(provider, dict):
        raise HTTPException(status_code=400, detail="provider config must be an object")

    name = str(provider_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="provider name is required")

    alias_map = {
        "baseUrl": "base_url",
        "apiKey": "api_key",
        "api_mode": "api",
        "auth_header": "authHeader",
        "strictApiMode": "strict_api_mode",
        "reasoningParam": "reasoning_param",
    }
    allowed_keys = {
        "base_url",
        "baseUrl",
        "api_key",
        "apiKey",
        "default_model",
        "api",
        "api_mode",
        "auth",
        "authHeader",
        "auth_header",
        "headers",
        "strict_api_mode",
        "strictApiMode",
        "reasoning_param",
        "reasoningParam",
        "models",
        "agents",
    }
    unknown_keys = sorted(
        key for key in provider.keys() if str(key) not in allowed_keys
    )
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{name}' has unknown fields {unknown_keys}. "
                "Allowed fields: api/base_url/api_key/auth/authHeader/headers/"
                "strict_api_mode/reasoning_param/models/agents/default_model."
            ),
        )

    candidate: Dict[str, Any] = {}
    for key, value in provider.items():
        source_key = str(key)
        target_key = alias_map.get(source_key, source_key)
        if target_key in provider:
            # Keep canonical field when both canonical + alias are provided.
            if target_key != source_key:
                continue
        candidate[target_key] = value

    known_api_modes = {
        "openai-responses",
        "openai-completions",
        "responses",
        "chat-completions",
        "openai_response",
    }
    valid_input_types = {"text", "image", "audio"}
    valid_auth_modes = {"", "api-key", "bearer", "none"}
    api = str(candidate.get("api", "") or "").strip()
    api_mode = str(candidate.get("api_mode", "") or "").strip()
    if api_mode and not api:
        api = api_mode

    raw_api_key = candidate.get("api_key")
    if isinstance(raw_api_key, str):
        api_key_trimmed = raw_api_key.strip()
        if not api and api_key_trimmed.lower() in known_api_modes:
            raise HTTPException(
                status_code=400,
                detail="api_key value looks like API mode. Put it in 'api' and use api_key for credentials.",
            )

    base_url_raw = candidate.get("base_url", "")
    if base_url_raw is None:
        base_url_raw = ""
    if not isinstance(base_url_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'base_url' must be a string.")
    base_url = base_url_raw.strip()

    api_key_raw = candidate.get("api_key", "")
    if api_key_raw is None:
        api_key_raw = ""
    if not isinstance(api_key_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'api_key' must be a string.")
    api_key = api_key_raw.strip()

    default_model_raw = candidate.get("default_model", "")
    if default_model_raw is None:
        default_model_raw = ""
    if not isinstance(default_model_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'default_model' must be a string.")
    default_model = default_model_raw.strip()

    auth_raw = candidate.get("auth", "")
    if auth_raw is None:
        auth_raw = ""
    if not isinstance(auth_raw, str):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'auth' must be a string.")
    auth_mode = auth_raw.strip().lower()
    if auth_mode not in valid_auth_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{name}' field 'auth' must be one of {sorted(valid_auth_modes)}.",
        )

    headers_raw = candidate.get("headers")
    if headers_raw is not None and not isinstance(headers_raw, dict):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'headers' must be an object.")
    headers: Dict[str, str] = {}
    if isinstance(headers_raw, dict):
        for key, value in headers_raw.items():
            header_name = str(key or "").strip()
            if not header_name:
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' headers has an empty key.",
                )
            if isinstance(value, (dict, list)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' headers['{header_name}'] must be scalar.",
                )
            headers[header_name] = str(value)

    auth_header_raw = candidate.get("authHeader")
    if auth_header_raw is not None and not isinstance(auth_header_raw, bool):
        raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'authHeader' must be boolean.")
    auth_header = bool(auth_header_raw) if isinstance(auth_header_raw, bool) else False

    strict_api_mode_raw = candidate.get("strict_api_mode")
    if strict_api_mode_raw is not None and not isinstance(strict_api_mode_raw, bool):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{name}' field 'strict_api_mode' must be boolean.",
        )
    strict_api_mode = bool(strict_api_mode_raw) if isinstance(strict_api_mode_raw, bool) else True

    reasoning_param_raw = candidate.get("reasoning_param")
    if reasoning_param_raw is not None and not isinstance(reasoning_param_raw, bool):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{name}' field 'reasoning_param' must be boolean.",
        )
    reasoning_param = reasoning_param_raw if isinstance(reasoning_param_raw, bool) else None

    models_cfg = candidate.get("models")
    normalized_models: List[Dict[str, Any]] = []
    if models_cfg is not None:
        if not isinstance(models_cfg, list):
            raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'models' must be an array.")
        for idx, entry in enumerate(models_cfg):
            if not isinstance(entry, dict):
                raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}] must be an object.")
            model_id = str(entry.get("id") or entry.get("name") or "").strip()
            if not model_id:
                raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}] requires 'id' or 'name'.")
            normalized_entry: Dict[str, Any] = {}
            if "id" in entry:
                normalized_entry["id"] = str(entry.get("id") or "").strip()
            if "name" in entry:
                normalized_entry["name"] = str(entry.get("name") or "").strip()
            input_types = entry.get("input")
            if input_types is not None:
                if not isinstance(input_types, list):
                    raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}].input must be an array.")
                invalid_inputs = sorted(
                    str(item).strip().lower()
                    for item in input_types
                    if str(item).strip().lower() not in valid_input_types
                )
                if invalid_inputs:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Provider '{name}' models[{idx}].input has invalid values "
                            f"{invalid_inputs}; allowed: {sorted(valid_input_types)}."
                        ),
                    )
                normalized_entry["input"] = [str(item).strip().lower() for item in input_types if str(item).strip()]
            for keys in (("maxTokens", "max_tokens"), ("contextWindow", "context_window")):
                raw_value = None
                key_name = keys[0]
                for key in keys:
                    if key in entry:
                        raw_value = entry.get(key)
                        key_name = key
                        break
                if raw_value is not None:
                    try:
                        parsed = int(raw_value)
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' models[{idx}].{key_name} must be an integer.",
                        )
                    if parsed <= 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' models[{idx}].{key_name} must be > 0.",
                        )
                    normalized_entry[key_name] = parsed
            if "reasoning" in entry and not isinstance(entry.get("reasoning"), bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' models[{idx}].reasoning must be boolean.",
                )
            if "reasoning" in entry:
                normalized_entry["reasoning"] = bool(entry.get("reasoning"))
            cost_cfg = entry.get("cost")
            if cost_cfg is not None:
                if not isinstance(cost_cfg, dict):
                    raise HTTPException(status_code=400, detail=f"Provider '{name}' models[{idx}].cost must be an object.")
                normalized_cost: Dict[str, float] = {}
                for cost_key in ("input", "output", "cacheRead", "cacheWrite"):
                    if cost_key not in cost_cfg:
                        continue
                    try:
                        normalized_cost[cost_key] = float(cost_cfg.get(cost_key))
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' models[{idx}].cost.{cost_key} must be numeric.",
                        )
                normalized_entry["cost"] = normalized_cost

            for key, value in entry.items():
                if key in normalized_entry:
                    continue
                if key in {"max_tokens", "context_window"}:
                    continue
                normalized_entry[key] = value
            if "id" not in normalized_entry and model_id:
                normalized_entry["id"] = model_id
            normalized_models.append(normalized_entry)

    agents_cfg = candidate.get("agents")
    if agents_cfg is None:
        normalized_agents: Dict[str, Any] = {}
    else:
        if not isinstance(agents_cfg, dict):
            raise HTTPException(status_code=400, detail=f"Provider '{name}' field 'agents' must be an object.")
        normalized_agents = dict(agents_cfg)
        defaults_cfg = normalized_agents.get("defaults")
        if defaults_cfg is not None and not isinstance(defaults_cfg, dict):
            raise HTTPException(status_code=400, detail=f"Provider '{name}' agents.defaults must be an object.")
        if isinstance(defaults_cfg, dict):
            model_cfg = defaults_cfg.get("model")
            if model_cfg is not None and not isinstance(model_cfg, dict):
                raise HTTPException(status_code=400, detail=f"Provider '{name}' agents.defaults.model must be an object.")
            if isinstance(model_cfg, dict):
                primary = model_cfg.get("primary")
                if primary is not None and not isinstance(primary, str):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider '{name}' agents.defaults.model.primary must be a string.",
                    )
            models_alias_cfg = defaults_cfg.get("models")
            if models_alias_cfg is not None and not isinstance(models_alias_cfg, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.models must be an object.",
                )
            workspace = defaults_cfg.get("workspace")
            if workspace is not None and not isinstance(workspace, str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.workspace must be a string.",
                )
            compaction_cfg = defaults_cfg.get("compaction")
            if compaction_cfg is not None and not isinstance(compaction_cfg, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.compaction must be an object.",
                )
            if isinstance(compaction_cfg, dict) and "mode" in compaction_cfg and not isinstance(compaction_cfg.get("mode"), str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.compaction.mode must be a string.",
                )
            for key_name in ("maxConcurrent",):
                if key_name in defaults_cfg:
                    try:
                        parsed = int(defaults_cfg.get(key_name))
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' agents.defaults.{key_name} must be an integer.",
                        )
                    if parsed <= 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Provider '{name}' agents.defaults.{key_name} must be > 0.",
                        )
            subagents_cfg = defaults_cfg.get("subagents")
            if subagents_cfg is not None and not isinstance(subagents_cfg, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{name}' agents.defaults.subagents must be an object.",
                )
            if isinstance(subagents_cfg, dict) and "maxConcurrent" in subagents_cfg:
                try:
                    parsed = int(subagents_cfg.get("maxConcurrent"))
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider '{name}' agents.defaults.subagents.maxConcurrent must be an integer.",
                    )
                if parsed <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider '{name}' agents.defaults.subagents.maxConcurrent must be > 0.",
                    )

    normalized: Dict[str, Any] = {
        "base_url": base_url,
        "api_key": api_key,
        "default_model": default_model,
        "api": api,
        "auth": auth_mode,
        "authHeader": auth_header,
        "headers": headers,
        "strict_api_mode": strict_api_mode,
        "reasoning_param": reasoning_param,
        "models": normalized_models,
        "agents": normalized_agents,
    }
    return normalized

def _validate_deployment_target_entry(target_id: str, target: Any) -> Dict[str, Any]:
    """Validate and normalize one deployment target payload."""
    if not isinstance(target, dict):
        raise HTTPException(status_code=400, detail="deployment target must be an object")
    name = str(target_id or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="target_id is required")

    provider_name = str(target.get("provider", "")).strip()
    if not provider_name:
        raise HTTPException(status_code=400, detail="deployment target 'provider' is required")

    target_type = str(target.get("type", "gateway") or "gateway").strip().lower()
    valid_types = {"local", "gateway", "dedicated", "provider"}
    if target_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"deployment target 'type' must be one of {sorted(valid_types)}",
        )

    provider_cfg = get_provider_registry().get_provider(provider_name)
    explicit_base_url = str(target.get("base_url", "") or "").strip()
    explicit_model = str(target.get("default_model", "") or "").strip()
    if not provider_cfg and (not explicit_base_url or not explicit_model):
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider '{provider_name}' not found in provider registry; "
                "set both 'base_url' and 'default_model' for explicit target override."
            ),
        )

    enabled_raw = target.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise HTTPException(status_code=400, detail="deployment target 'enabled' must be boolean")

    capacity_raw = target.get("capacity_rpm", None)
    latency_raw = target.get("latency_target_ms", None)
    weight_raw = target.get("traffic_weight", None)
    if capacity_raw is not None:
        try:
            if int(capacity_raw) <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'capacity_rpm' must be an integer > 0")
    if latency_raw is not None:
        try:
            if float(latency_raw) <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'latency_target_ms' must be > 0")
    if weight_raw is not None:
        try:
            if float(weight_raw) <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'traffic_weight' must be > 0")

    headers_raw = target.get("headers", None)
    if headers_raw is not None and not isinstance(headers_raw, dict):
        raise HTTPException(status_code=400, detail="'headers' must be an object when provided")

    normalized = {
        "provider": provider_name,
        "type": target_type,
        "enabled": bool(enabled_raw),
        "profile": str(target.get("profile", "") or "").strip(),
        "health_url": str(target.get("health_url", "") or "").strip(),
        "base_url": explicit_base_url,
        "api_key": target.get("api_key", ""),
        "default_model": explicit_model,
        "api": str(target.get("api", "") or "").strip(),
        "cost_tier": str(target.get("cost_tier", "") or "").strip(),
        "headers": headers_raw if isinstance(headers_raw, dict) else {},
    }
    if capacity_raw is not None:
        normalized["capacity_rpm"] = int(capacity_raw)
    if latency_raw is not None:
        normalized["latency_target_ms"] = float(latency_raw)
    if weight_raw is not None:
        normalized["traffic_weight"] = float(weight_raw)
    return normalized

