"""Model provider registry stored outside settings.yaml."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from runtime.utils import FileLockError, file_lock

logger = logging.getLogger("GazerProviderRegistry")

DEFAULT_REGISTRY_PATH = os.environ.get(
    "GAZER_MODEL_PROVIDERS_FILE",
    "config/model_providers.local.json",
).strip() or "config/model_providers.local.json"

_MASKED_VALUE = "***"
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
}
_SENSITIVE_HEADER_KEYWORDS = ("token", "secret", "api-key", "apikey", "authorization")

DEFAULT_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "default_model": "gpt-4o",
    },
    "ollama_local": {
        "base_url": os.getenv("OLLAMA_HOST", "http://localhost:11434/v1"),
        "api_key": "ollama",
        "default_model": "llama3",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "default_model": "deepseek-coder",
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "default_model": "qwen-plus",
    },
}


class ProviderRegistry:
    """File-backed provider registry."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = str(path or DEFAULT_REGISTRY_PATH).strip() or DEFAULT_REGISTRY_PATH
        self._lock_path = f"{self.path}.lock"
        self._data: Dict[str, Any] = {}
        self._load()

    def _default_payload(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "providers": copy.deepcopy(DEFAULT_PROVIDERS),
            # target_id -> deployment target config
            # {
            #   "provider": "openai",
            #   "type": "gateway",   # local | gateway | dedicated
            #   "enabled": True,
            #   "base_url": "...",   # optional override
            #   "api_key": "...",    # optional override
            #   "default_model": "...",
            #   "health_url": "..."
            # }
            "deployment_targets": {},
        }

    def _load(self) -> None:
        """Load registry from primary path only."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if os.path.exists(self.path):
            payload = self._read_payload(self.path)
            if payload is None:
                raise RuntimeError(
                    f"Failed to load provider registry '{self.path}': invalid JSON content. "
                    "Fix the file and restart."
                )
        else:
            self._data = self._default_payload()
            self.save()
            return

        providers = payload.get("providers")
        if not isinstance(providers, dict):
            raise RuntimeError(
                f"Failed to load provider registry '{self.path}': field 'providers' must be an object."
            )
        targets = payload.get("deployment_targets")
        if not isinstance(targets, dict):
            payload["deployment_targets"] = {}
        payload.setdefault("version", 1)
        self._data = payload

    def _read_payload(self, path: str) -> Optional[Dict[str, Any]]:
        """Read provider registry payload from *path*."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.loads(fh.read())
        except Exception as exc:
            logger.error("Failed to read provider registry '%s': %s", path, exc)
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _is_sensitive_header_name(name: str) -> bool:
        normalized = str(name or "").strip().lower()
        if not normalized:
            return False
        if normalized in _SENSITIVE_HEADER_NAMES:
            return True
        return any(keyword in normalized for keyword in _SENSITIVE_HEADER_KEYWORDS)

    @classmethod
    def _redact_headers(cls, headers: Any) -> Any:
        if not isinstance(headers, dict):
            return headers
        redacted: Dict[str, Any] = {}
        for key, value in headers.items():
            header_name = str(key)
            if cls._is_sensitive_header_name(header_name) and str(value or "").strip():
                redacted[header_name] = _MASKED_VALUE
            else:
                redacted[header_name] = value
        return redacted

    @staticmethod
    def _restore_masked_headers(
        *,
        previous_cfg: Dict[str, Any],
        next_cfg: Dict[str, Any],
    ) -> None:
        next_headers = next_cfg.get("headers")
        if not isinstance(next_headers, dict):
            return
        prev_headers = previous_cfg.get("headers")
        prev_map = prev_headers if isinstance(prev_headers, dict) else {}
        merged: Dict[str, Any] = {}
        for key, value in next_headers.items():
            if str(value or "").strip() == _MASKED_VALUE and key in prev_map:
                merged[key] = prev_map[key]
            else:
                merged[key] = value
        next_cfg["headers"] = merged

    def save(self) -> None:
        try:
            config_dir = os.path.dirname(self.path) or "."
            os.makedirs(config_dir, exist_ok=True)
            with file_lock(self._lock_path, timeout=5.0):
                fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps(self._data, ensure_ascii=False, indent=2))
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp_path, self.path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
        except FileLockError as exc:
            logger.error("Failed to save provider registry (lock timeout): %s", exc)
            raise RuntimeError(f"Failed to save provider registry (lock timeout): {exc}") from exc
        except Exception as exc:
            logger.error("Failed to save provider registry: %s", exc)
            raise RuntimeError(f"Failed to save provider registry: {exc}") from exc

    def list_providers(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self._data.get("providers", {}))

    def list_redacted_providers(self) -> Dict[str, Dict[str, Any]]:
        out = self.list_providers()
        for _, cfg in out.items():
            if not isinstance(cfg, dict):
                continue
            if "api_key" in cfg:
                cfg["api_key"] = _MASKED_VALUE if cfg.get("api_key") else ""
            if "headers" in cfg:
                cfg["headers"] = self._redact_headers(cfg.get("headers"))
        return out

    def get_provider(self, name: str) -> Dict[str, Any]:
        providers = self._data.get("providers", {})
        if not isinstance(providers, dict):
            return {}
        raw = providers.get(name, {})
        if isinstance(raw, dict) and raw:
            return copy.deepcopy(raw)
        lookup = str(name or "").strip().lower()
        if not lookup:
            return {}
        for key, value in providers.items():
            if str(key).strip().lower() == lookup and isinstance(value, dict):
                return copy.deepcopy(value)
        return {}

    def upsert_provider(self, name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("provider name is required")
        providers = self._data.setdefault("providers", {})
        if not isinstance(providers, dict):
            self._data["providers"] = {}
            providers = self._data["providers"]
        previous = providers.get(clean_name)
        prev_cfg = previous if isinstance(previous, dict) else {}
        next_cfg = copy.deepcopy(cfg if isinstance(cfg, dict) else {})
        if next_cfg.get("api_key") == _MASKED_VALUE:
            next_cfg["api_key"] = prev_cfg.get("api_key", "")
        self._restore_masked_headers(previous_cfg=prev_cfg, next_cfg=next_cfg)
        providers[clean_name] = next_cfg
        self.save()
        return copy.deepcopy(next_cfg)

    def delete_provider(self, name: str) -> bool:
        providers = self._data.get("providers", {})
        if not isinstance(providers, dict):
            return False
        if name not in providers:
            return False
        del providers[name]
        self.save()
        return True

    def list_deployment_targets(self) -> Dict[str, Dict[str, Any]]:
        raw = self._data.get("deployment_targets", {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): copy.deepcopy(val if isinstance(val, dict) else {})
            for key, val in raw.items()
        }

    def list_redacted_deployment_targets(self) -> Dict[str, Dict[str, Any]]:
        out = self.list_deployment_targets()
        for _, cfg in out.items():
            if not isinstance(cfg, dict):
                continue
            if "api_key" in cfg:
                cfg["api_key"] = _MASKED_VALUE if cfg.get("api_key") else ""
            if "headers" in cfg:
                cfg["headers"] = self._redact_headers(cfg.get("headers"))
        return out

    def get_deployment_target(self, target_id: str) -> Dict[str, Any]:
        targets = self._data.get("deployment_targets", {})
        if not isinstance(targets, dict):
            return {}
        raw = targets.get(target_id, {})
        return copy.deepcopy(raw if isinstance(raw, dict) else {})

    def upsert_deployment_target(self, target_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        clean_target_id = str(target_id or "").strip()
        if not clean_target_id:
            raise ValueError("target_id is required")
        targets = self._data.setdefault("deployment_targets", {})
        if not isinstance(targets, dict):
            self._data["deployment_targets"] = {}
            targets = self._data["deployment_targets"]

        previous = targets.get(clean_target_id)
        prev_cfg = previous if isinstance(previous, dict) else {}
        next_cfg = copy.deepcopy(cfg if isinstance(cfg, dict) else {})
        if next_cfg.get("api_key") == _MASKED_VALUE:
            next_cfg["api_key"] = prev_cfg.get("api_key", "")
        self._restore_masked_headers(previous_cfg=prev_cfg, next_cfg=next_cfg)
        targets[clean_target_id] = next_cfg
        self.save()
        return copy.deepcopy(next_cfg)

    def delete_deployment_target(self, target_id: str) -> bool:
        targets = self._data.get("deployment_targets", {})
        if not isinstance(targets, dict):
            return False
        clean_target_id = str(target_id or "").strip()
        if clean_target_id not in targets:
            return False
        del targets[clean_target_id]
        self.save()
        return True


_provider_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    global _provider_registry
    if _provider_registry is None:
        _provider_registry = ProviderRegistry()
    return _provider_registry
