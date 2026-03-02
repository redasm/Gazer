"""OpenViking backend bootstrap checks.

This module centralizes startup-time validation for the OpenViking memory backend.
It validates config, required package availability, and runtime paths.
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("GazerOpenVikingBootstrap")

_VALID_BACKEND_MODES = {"openviking", "disabled"}


@dataclass(frozen=True)
class OpenVikingBootstrapSettings:
    enabled: bool
    mode: str
    data_dir: Path
    config_file: str
    session_prefix: str
    default_user: str
    commit_every_messages: int


def _cfg_get(cfg: Any, key_path: str, default: Any) -> Any:
    getter = getattr(cfg, "get", None)
    if callable(getter):
        try:
            return getter(key_path, default)
        except Exception:
            return default
    return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_openviking_settings(cfg: Any) -> OpenVikingBootstrapSettings:
    mode = str(_cfg_get(cfg, "memory.context_backend.mode", "openviking") or "").strip().lower()
    if mode not in _VALID_BACKEND_MODES:
        raise RuntimeError(
            f"Invalid memory.context_backend.mode='{mode}'. "
            f"Expected one of: {sorted(_VALID_BACKEND_MODES)}"
        )

    data_dir_raw = str(_cfg_get(cfg, "memory.context_backend.data_dir", "data/openviking") or "").strip()
    if not data_dir_raw:
        raise RuntimeError("memory.context_backend.data_dir must not be empty")

    data_dir_path = Path(data_dir_raw).expanduser()
    if not data_dir_path.is_absolute():
        data_dir_path = (Path.cwd() / data_dir_path).resolve()

    config_file = str(_cfg_get(cfg, "memory.context_backend.config_file", "") or "").strip()
    session_prefix = str(
        _cfg_get(cfg, "memory.context_backend.session_prefix", "gazer")
        or "gazer"
    ).strip()
    default_user = str(
        _cfg_get(cfg, "memory.context_backend.default_user", "owner")
        or "owner"
    ).strip()
    enabled = _to_bool(_cfg_get(cfg, "memory.context_backend.enabled", False))
    raw_commit_every = _cfg_get(cfg, "memory.context_backend.commit_every_messages", 8)
    try:
        commit_every_messages = max(1, int(raw_commit_every))
    except (TypeError, ValueError):
        commit_every_messages = 8

    return OpenVikingBootstrapSettings(
        enabled=enabled,
        mode=mode,
        data_dir=data_dir_path,
        config_file=config_file,
        session_prefix=session_prefix,
        default_user=default_user,
        commit_every_messages=commit_every_messages,
    )


def ensure_openviking_ready(cfg: Any) -> OpenVikingBootstrapSettings:
    """Validate OpenViking runtime prerequisites.

    Raises:
        RuntimeError: when backend is enabled but requirements are not satisfied.
    """
    settings = load_openviking_settings(cfg)
    if not settings.enabled:
        return settings
    if settings.mode != "openviking":
        raise RuntimeError(
            "memory.context_backend.enabled=true requires memory.context_backend.mode='openviking'"
        )

    try:
        importlib.import_module("openviking")
    except Exception as exc:
        raise RuntimeError(
            "OpenViking backend is enabled but package 'openviking' is unavailable. "
            "Install dependency: pip install openviking"
        ) from exc

    settings.data_dir.mkdir(parents=True, exist_ok=True)

    if settings.config_file:
        cfg_path = Path(settings.config_file).expanduser()
        if not cfg_path.is_absolute():
            cfg_path = (Path.cwd() / cfg_path).resolve()
        if not cfg_path.is_file():
            raise RuntimeError(
                "memory.context_backend.config_file is configured but file does not exist: "
                f"{cfg_path}"
            )
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", str(cfg_path))

    logger.info("OpenViking backend preflight passed. data_dir=%s", settings.data_dir)

    # Optional: Inject embedding config into OpenViking execution environment
    embedding_enabled = _to_bool(_cfg_get(cfg, "models.embedding.enabled", False))
    if embedding_enabled:
        try:
            from runtime.provider_registry import get_provider_registry
            provider_name = str(_cfg_get(cfg, "models.embedding.provider", "") or "").strip()
            model_name = str(_cfg_get(cfg, "models.embedding.model", "") or "").strip()
            
            if provider_name and model_name:
                provider_cfg = get_provider_registry().get_provider(provider_name)
                env_key = provider_name.upper().replace("-", "_").replace(".", "_")
                api_key = provider_cfg.get("api_key") or os.getenv(f"{env_key}_API_KEY", "")
                base_url = provider_cfg.get("base_url", "")
                
                if api_key:
                    os.environ["OPENVIKING_API_KEY"] = api_key
                if base_url:
                    os.environ["OPENVIKING_BASE_URL"] = base_url
                os.environ["OPENVIKING_EMBEDDING_MODEL"] = model_name
                logger.info(f"Injected OpenViking embedding overrides: provider={provider_name}, model={model_name}")
        except Exception as e:
            logger.warning(f"Failed to inject OpenViking embedding overrides: {e}")

    return settings
