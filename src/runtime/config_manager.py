import copy
import os
import yaml
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from runtime.utils import file_lock, FileLockError

logger = logging.getLogger("GazerConfig")
_PRUNE_MARKER = object()
from config.defaults import DEFAULT_CONFIG, LATEST_CONFIG_VERSION

# Sensitive keys used for config redaction and masked-value restoration.
# Format: dot-separated paths, supports:
# - `*` for one path segment
# - `**` for zero or more path segments
_SENSITIVE_KEY_PATTERNS: List[str] = [
    "telegram.token",              # Telegram bot token
    "discord.token",               # Discord bot token
    "hooks.token",                  # Webhook auth token
    "email.password",               # Email password
    "feishu.app_secret",            # Feishu app secret
    "**.api_key",                   # Generic API keys in nested config
    "**.token",                     # Generic tokens
    "**.secret",                    # Generic secrets
    "**.password",                  # Generic passwords
    "**.credentials",               # Credentials objects/fields
    "web.search.brave_api_key",     # Brave uses a prefixed key name
    # Keep object shape for trusted_keys; mask direct leaf entries only.
    "plugins.signature.trusted_keys.*",
]

_INTERNAL_ADMIN_CONFIG_PREFIXES: List[str] = [
    "agents.defaults.planning",
]


def _normalize_path(path: str) -> List[str]:
    """Normalize a dot path into lowercase segments."""
    if not path:
        return []
    return [part.strip().lower() for part in str(path).strip().split(".") if part.strip()]


def _match_pattern_parts(pattern_parts: List[str], path_parts: List[str]) -> bool:
    """Match wildcard path pattern parts against path parts."""
    pi = 0
    ti = 0
    while pi < len(pattern_parts):
        pp = pattern_parts[pi]
        if pp == "**":
            # `**` at the end consumes all remaining segments.
            if pi == len(pattern_parts) - 1:
                return True
            # Try to align the remainder of the pattern at each suffix.
            next_parts = pattern_parts[pi + 1 :]
            for next_ti in range(ti, len(path_parts) + 1):
                if _match_pattern_parts(next_parts, path_parts[next_ti:]):
                    return True
            return False

        if ti >= len(path_parts):
            return False
        if pp != "*" and pp != path_parts[ti]:
            return False
        pi += 1
        ti += 1
    return ti == len(path_parts)


def _match_pattern(pattern: str, path: str) -> bool:
    """Match a dot-separated pattern with wildcards against a path."""
    pattern_parts = _normalize_path(pattern)
    path_parts = _normalize_path(path)
    if not pattern_parts:
        return False
    return _match_pattern_parts(pattern_parts, path_parts)


def is_sensitive_config_path(path: str) -> bool:
    """Return True when *path* points to a sensitive config field."""
    normalized = ".".join(_normalize_path(path))
    if not normalized:
        return False
    for pattern in _SENSITIVE_KEY_PATTERNS:
        if _match_pattern(pattern, normalized):
            return True
    return False


def is_internal_admin_config_path(path: str) -> bool:
    """Return True when *path* is internal-only for the admin web config API."""
    normalized = ".".join(_normalize_path(path))
    if not normalized:
        return False
    for prefix in _INTERNAL_ADMIN_CONFIG_PREFIXES:
        normalized_prefix = ".".join(_normalize_path(prefix))
        if normalized == normalized_prefix or normalized.startswith(f"{normalized_prefix}."):
            return True
    return False

class ConfigManager:
    """
    Gazer configuration center.
    Supports YAML persistence and in-memory dynamic updates.
    """
    PERSONA_SYSTEM_PROMPT_KEY = "personality.system_prompt"
    NON_PERSISTED_KEYS = {PERSONA_SYSTEM_PROMPT_KEY}

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config_path = config_path
        self._lock_path = f"{self.config_path}.lock"
        self.data: Dict[str, Any] = {}
        self._last_mtime = 0
        self._load()

    def _resolve_workspace_root(self) -> Path:
        cfg_path = Path(self.config_path).expanduser()
        if not cfg_path.is_absolute():
            cfg_path = (Path.cwd() / cfg_path).resolve()
        else:
            cfg_path = cfg_path.resolve()
        cfg_dir = cfg_path.parent
        if cfg_dir.name.lower() == "config":
            return cfg_dir.parent
        return cfg_dir

    def _resolve_soul_path(self) -> Path:
        return self._resolve_workspace_root() / "assets" / "SOUL.md"

    def _read_soul_prompt(self) -> str:
        soul_path = self._resolve_soul_path()
        if not soul_path.is_file():
            return ""
        try:
            return soul_path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.debug("Failed to read SOUL prompt from %s", soul_path, exc_info=True)
            return ""

    def _write_soul_prompt(self, prompt: str) -> None:
        soul_path = self._resolve_soul_path()
        try:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            text = str(prompt or "").strip()
            payload = f"{text}\n" if text else ""
            soul_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Failed to write SOUL prompt to '{soul_path}': {exc}") from exc

    def _sync_persona_system_prompt(self, loaded_prompt: str = "") -> None:
        soul_prompt = self._read_soul_prompt()
        if not soul_prompt and str(loaded_prompt or "").strip():
            self._write_soul_prompt(str(loaded_prompt or "").strip())
            soul_prompt = self._read_soul_prompt()
            logger.info("Migrated personality.system_prompt into %s", self._resolve_soul_path())

        if soul_prompt:
            self._set_in_memory(self.PERSONA_SYSTEM_PROMPT_KEY, soul_prompt)
        else:
            fallback = str(
                self.get(
                    self.PERSONA_SYSTEM_PROMPT_KEY,
                    DEFAULT_CONFIG.get("personality", {}).get("system_prompt", ""),
                )
                or ""
            )
            if fallback:
                self._set_in_memory(self.PERSONA_SYSTEM_PROMPT_KEY, fallback)

    @staticmethod
    def _delete_dot_path(payload: Dict[str, Any], key_path: str) -> None:
        keys = [segment for segment in str(key_path).split(".") if segment]
        if not keys or not isinstance(payload, dict):
            return
        current: Any = payload
        stack: List[tuple[Dict[str, Any], str]] = []
        for key in keys[:-1]:
            if not isinstance(current, dict) or key not in current:
                return
            stack.append((current, key))
            current = current[key]
        if not isinstance(current, dict):
            return
        current.pop(keys[-1], None)
        # Prune now-empty parent objects, but stop at the root.
        for parent, child_key in reversed(stack):
            child = parent.get(child_key)
            if isinstance(child, dict) and not child:
                parent.pop(child_key, None)
            else:
                break

    def _load(self):
        """Load config from file; use defaults and save if not found."""
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
        if os.path.exists(self.config_path):
            try:
                mtime = os.path.getmtime(self.config_path)
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                self.data = loaded if isinstance(loaded, dict) else {}
                loaded_prompt = ""
                if isinstance(loaded, dict):
                    personality = loaded.get("personality", {})
                    if isinstance(personality, dict):
                        loaded_prompt = str(personality.get("system_prompt", "") or "")
                self._last_mtime = mtime
                self._validate_schema_strict()
                # Merge defaults to ensure newly added config keys exist
                self._merge_defaults(self.data, DEFAULT_CONFIG)
                # Deprecated: privileged owner bypass is now unconditional for owner ids.
                sec = self.data.get("security")
                if isinstance(sec, dict) and "auto_approve_owner_privileged" in sec:
                    sec.pop("auto_approve_owner_privileged", None)
                    logger.warning(
                        "Deprecated config key 'security.auto_approve_owner_privileged' "
                        "found and removed. Owner bypass is now always enforced."
                    )
                # Restore sensitive fields from env-var defaults when stripped
                self._restore_sensitive_from_defaults(self.data, DEFAULT_CONFIG)
                # Keep persona prompt single-sourced from assets/SOUL.md.
                self._sync_persona_system_prompt(loaded_prompt=loaded_prompt)
                logger.info(f"Configuration loaded from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}.")
                raise RuntimeError(f"Failed to load config '{self.config_path}': {e}") from e
        else:
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self._sync_persona_system_prompt()
            self.save()

    def _validate_schema_strict(self) -> None:
        """Fail fast in development when deprecated config keys are present."""
        deprecated_paths: List[str] = []
        if isinstance(self.data, dict):
            models = self.data.get("models")
            if isinstance(models, dict):
                if "active_profile" in models:
                    deprecated_paths.append("models.active_profile")
                if "embedding_provider" in models:
                    deprecated_paths.append("models.embedding_provider")
                if "embedding_model" in models:
                    deprecated_paths.append("models.embedding_model")

        if deprecated_paths:
            joined = ", ".join(deprecated_paths)
            raise RuntimeError(
                "Deprecated config keys detected: "
                f"{joined}. Use agents.defaults.model.primary/fallbacks."
            )

    def check_reload(self):
        """Check if the config file has been modified and reload if so."""
        if os.path.exists(self.config_path):
            mtime = os.path.getmtime(self.config_path)
            if mtime > self._last_mtime:
                self._load()

    def _merge_defaults(self, target: dict, source: dict):
        """Recursively merge default values into target."""
        for k, v in source.items():
            if k not in target:
                target[k] = copy.deepcopy(v)
            elif isinstance(v, dict) and isinstance(target.get(k), dict):
                self._merge_defaults(target[k], v)

    def _restore_sensitive_from_defaults(
        self, target: dict, defaults: dict, path: str = ""
    ) -> None:
        """Restore sensitive fields that were stripped on save.

        If a sensitive field in the loaded config is empty but the
        DEFAULT_CONFIG (which reads env vars) has a non-empty value,
        use the default value.  This allows secrets to live in env vars
        while the YAML file stays clean.
        """
        for key, default_value in defaults.items():
            current_path = f"{path}.{key}" if path else key
            if isinstance(default_value, dict) and isinstance(target.get(key), dict):
                self._restore_sensitive_from_defaults(target[key], default_value, current_path)
            elif self._is_sensitive_path(current_path):
                # If file value is empty/stripped but env var provides a value, use it
                file_value = target.get(key, "")
                if (not file_value or file_value == "***") and default_value:
                    target[key] = default_value
                    logger.debug(f"Restored sensitive field '{current_path}' from environment.")

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get config value by dot-separated path, e.g. 'api.model'."""
        if key_path == self.PERSONA_SYSTEM_PROMPT_KEY:
            soul_prompt = self._read_soul_prompt()
            if soul_prompt:
                self._set_in_memory(self.PERSONA_SYSTEM_PROMPT_KEY, soul_prompt)
                return soul_prompt
        keys = key_path.split(".")
        current = self.data
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current

    def set(self, key_path: str, value: Any):
        """Set a config value by dot-separated path and auto-save."""
        if key_path == self.PERSONA_SYSTEM_PROMPT_KEY:
            text = str(value or "").strip()
            self._write_soul_prompt(text)
            self._set_in_memory(key_path, text)
        else:
            self._set_in_memory(key_path, value)
        self.save()

    def set_many(self, updates: Dict[str, Any]) -> None:
        """Set multiple config values and save once."""
        for key_path, value in updates.items():
            if key_path == self.PERSONA_SYSTEM_PROMPT_KEY:
                text = str(value or "").strip()
                self._write_soul_prompt(text)
                self._set_in_memory(key_path, text)
            else:
                self._set_in_memory(key_path, value)
        self.save()

    def _set_in_memory(self, key_path: str, value: Any) -> None:
        """Set a config value by dot-separated path without saving."""
        keys = key_path.split(".")
        current = self.data
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    def save(self):
        """Persist current config to YAML file.

        Values are persisted as entered (except keys single-sourced
        outside YAML, such as ``personality.system_prompt``). Use
        ``to_safe_dict()`` when
        exposing config through APIs to avoid leaking sensitive fields.
        """
        try:
            config_dir = os.path.dirname(self.config_path) or "."
            os.makedirs(config_dir, exist_ok=True)
            # Persist full effective config without pruning default-valued keys.
            # This keeps settings.yaml explicit and avoids "missing key" surprises.
            persist_data = copy.deepcopy(self.data) if isinstance(self.data, dict) else {}
            for key_path in self.NON_PERSISTED_KEYS:
                self._delete_dot_path(persist_data, key_path)
            persist_data = self._order_for_persist(persist_data)
            with file_lock(self._lock_path, timeout=5.0):
                fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".yaml.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        yaml.safe_dump(persist_data, f, allow_unicode=True, sort_keys=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_path, self.config_path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                self._last_mtime = os.path.getmtime(self.config_path)
            logger.info("Configuration saved.")
        except FileLockError as e:
            logger.error(f"Failed to save config (lock timeout): {e}")
            raise RuntimeError(f"Failed to save config (lock timeout): {e}") from e
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise RuntimeError(f"Failed to save config: {e}") from e

    def _order_for_persist(self, value: Any, path: str = "") -> Any:
        """Return a copy of *value* with stable key ordering for YAML persistence."""
        if isinstance(value, dict):
            preferred_orders: Dict[str, List[str]] = {
                "": [
                    "config_version",
                    "models",
                    "wake_word",
                    "personality",
                    "voice",
                    "visual",
                    "perception",
                    "asr",
                    "devices",
                    "satellite",
                    "coding",
                    "runtime",
                    "feishu",
                    "telegram",
                    "discord",
                    "web",
                    "memory",
                    "api",
                    "security",
                    "scheduler",
                    "hooks",
                    "sandbox",
                    "agents",
                    "plugins",
                    "skill_registry",
                    "skills",
                    "canvas",
                    "trainer",
                    "observability",
                    "email",
                    "gmail_push",
                    "body",
                    "ui",
                ],
                "models": ["embedding", "router", "prompt_cache", "deployment_profiles"],
                "models.embedding": ["provider", "model"],
                "models.router": [
                    "enabled",
                    "strategy",
                    "strategy_template",
                    "candidates",
                    "deployment_targets",
                    "rollout",
                    "complexity_routing",
                    "budget",
                    "outlier_ejection",
                    "deployment_orchestrator",
                ],
                "models.prompt_cache": [
                    "enabled",
                    "ttl_seconds",
                    "max_items",
                    "segment_policy",
                    "scope_fields",
                    "sanitize_sensitive",
                ],
                "voice": ["provider", "voice_id", "rate", "volume", "cloud"],
                "voice.cloud": [
                    "provider",
                    "provider_ref",
                    "base_url",
                    "api_key",
                    "model",
                    "strict_required",
                    "request_timeout_seconds",
                    "response_format",
                    "retry_count",
                    "fallback_to_edge",
                ],
                "perception": [
                    "screen_enabled",
                    "camera_enabled",
                    "camera_device_index",
                    "capture_interval",
                    "satellite_ids",
                    "action_enabled",
                    "spatial_enabled",
                    "spatial",
                ],
                "perception.spatial": ["provider", "route_mode", "cloud"],
                "perception.spatial.cloud": [
                    "provider",
                    "provider_ref",
                    "base_url",
                    "api_key",
                    "model",
                    "strict_required",
                    "request_timeout_seconds",
                    "poll_interval_seconds",
                    "max_calls_per_minute",
                    "estimated_cost_per_call_usd",
                    "max_cost_per_minute_usd",
                ],
                "asr": ["provider", "model_size", "input_device", "route_mode", "cloud"],
                "asr.cloud": [
                    "provider",
                    "provider_ref",
                    "base_url",
                    "api_key",
                    "model",
                    "strict_required",
                    "request_timeout_seconds",
                    "max_calls_per_minute",
                    "estimated_cost_per_call_usd",
                    "max_cost_per_minute_usd",
                ],
                "devices": [
                    "default_target",
                    "local",
                    "local_node_id",
                    "local_node_label",
                    "body_node",
                    "satellite",
                ],
                "devices.local": ["backend"],
                "satellite": [
                    "transport_backend",
                    "max_pending_requests_per_node",
                    "pending_ttl_seconds",
                    "heartbeat_timeout_seconds",
                    "frame_window_seconds",
                    "max_frame_bytes_per_window",
                ],
                "coding": [
                    "exec_backend",
                    "max_output_chars",
                    "max_parallel_tool_calls",
                    "allow_local_fallback",
                    "ssh",
                ],
                "runtime": ["backend", "rust_sidecar"],
                "runtime.rust_sidecar": [
                    "endpoint",
                    "timeout_ms",
                    "auto_fallback_on_error",
                    "error_fallback_threshold",
                    "rollout",
                ],
                "runtime.rust_sidecar.rollout": [
                    "enabled",
                    "owner_only",
                    "channels",
                ],
                "feishu": ["enabled", "app_id", "app_secret", "allowed_ids", "simulated_typing", "media_analysis"],
                "feishu.simulated_typing": [
                    "enabled",
                    "text",
                    "min_interval_seconds",
                    "auto_recall_on_reply",
                ],
                "feishu.media_analysis": [
                    "enabled",
                    "include_inbound_summary",
                    "analyze_images",
                    "transcribe_audio",
                    "analyze_video_keyframe",
                    "timeout_seconds",
                    "audio_whisper_model",
                ],
                "web": ["search"],
                "web.search": [
                    "brave_api_key",
                    "perplexity_api_key",
                    "perplexity_base_url",
                    "perplexity_model",
                    "primary_provider",
                    "primary_only",
                    "report_file",
                    "relevance_gate",
                    "providers_order",
                    "providers_enabled",
                    "scenario_routing",
                ],
                "web.search.providers_enabled": ["brave", "perplexity", "duckduckgo", "bing_rss", "wikipedia"],
                "web.search.relevance_gate": ["enabled", "min_score", "allow_low_relevance_fallback"],
                "web.search.scenario_routing": ["enabled", "auto_detect", "profiles"],
                "web.search.scenario_routing.profiles": ["general", "news", "reference", "tech"],
                "memory": ["context_backend", "tool_result_persistence"],
                "memory.context_backend": [
                    "enabled",
                    "mode",
                    "data_dir",
                    "config_file",
                    "session_prefix",
                    "default_user",
                    "commit_every_messages",
                ],
                "memory.tool_result_persistence": [
                    "enabled",
                    "mode",
                    "allow_tools",
                    "deny_tools",
                    "persist_on_error",
                    "min_result_chars",
                    "max_result_chars",
                ],
                "observability": [
                    "release_gate_health_thresholds",
                    "cost_quality_slo_targets",
                    "efficiency_baseline_targets",
                    "alerts",
                ],
                "api": [
                    "cors_origins",
                    "cors_credentials",
                    "allow_loopback_without_token",
                    "max_ws_message_bytes",
                    "max_chat_message_chars",
                    "max_upload_bytes",
                    "session_max_age_seconds",
                    "session_max_records",
                    "cookie_secure",
                    "cookie_samesite",
                    "allow_admin_bearer_token",
                    "export_allowed_dirs",
                    "allow_audit_buffer_clear",
                    "local_bypass_environments",
                    "mcp",
                ],
                "api.mcp": [
                    "enabled",
                    "rate_limit_requests",
                    "rate_limit_window_seconds",
                    "allow_tools",
                    "allow_resources",
                    "allow_prompts",
                    "allowed_resource_prefixes",
                    "allowed_prompt_names",
                    "audit_retain",
                ],
                "agents": [
                    "defaults",
                ],
                "agents.defaults": [
                    "model",
                    "models",
                    "workspace",
                    "compaction",
                    "maxConcurrent",
                    "subagents",
                ],
                "agents.defaults.model": ["primary", "fallbacks"],
                "agents.defaults.compaction": ["mode"],
                "agents.defaults.subagents": ["maxConcurrent"],
            }
            preferred = preferred_orders.get(path, [])
            ordered: Dict[str, Any] = {}

            # Add preferred keys first when present.
            for key in preferred:
                if key in value:
                    child_path = f"{path}.{key}" if path else key
                    ordered[key] = self._order_for_persist(value[key], child_path)

            # Keep remaining keys in existing insertion order.
            for key, child in value.items():
                if key in ordered:
                    continue
                child_path = f"{path}.{key}" if path else key
                ordered[key] = self._order_for_persist(child, child_path)
            return ordered

        if isinstance(value, list):
            return [self._order_for_persist(item, path) for item in value]

        return value

    def _prune_defaults_for_persist(self, value: Any, default: Any, has_default: bool = False) -> Any:
        """Strip values equal to defaults before writing config file."""
        if isinstance(value, dict):
            result: Dict[str, Any] = {}
            default_dict = default if (has_default and isinstance(default, dict)) else {}
            for key, item in value.items():
                child_has_default = key in default_dict
                if child_has_default:
                    pruned = self._prune_defaults_for_persist(
                        item,
                        default_dict[key],
                        has_default=True,
                    )
                    if pruned is _PRUNE_MARKER:
                        continue
                    result[key] = pruned
                else:
                    result[key] = copy.deepcopy(item)
            if has_default and not result:
                return _PRUNE_MARKER
            return result

        if has_default and value == default:
            return _PRUNE_MARKER
        return copy.deepcopy(value)

    def to_safe_dict(self) -> Dict[str, Any]:
        """Return a copy of config with sensitive values masked.

        Use this when returning config through the web admin API to
        avoid leaking secrets to the frontend.
        """
        safe = self._mask_sensitive(copy.deepcopy(self.data))
        soul_prompt = str(self.get(self.PERSONA_SYSTEM_PROMPT_KEY, "") or "")
        personality = safe.get("personality")
        if not isinstance(personality, dict):
            personality = {}
            safe["personality"] = personality
        personality["system_prompt"] = soul_prompt
        for prefix in _INTERNAL_ADMIN_CONFIG_PREFIXES:
            self._delete_path(safe, prefix)
        return safe

    def _delete_path(self, data: Dict[str, Any], path: str) -> None:
        """Delete a nested dot-path from *data* when present."""
        parts = _normalize_path(path)
        if not parts:
            return
        current: Any = data
        parents: List[tuple[Dict[str, Any], str]] = []
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                return
            parents.append((current, part))
            current = current[part]
        leaf = parts[-1]
        if not isinstance(current, dict) or leaf not in current:
            return
        del current[leaf]
        for parent, key in reversed(parents):
            child = parent.get(key)
            if isinstance(child, dict) and not child:
                del parent[key]
            else:
                break

    def _mask_sensitive(
        self, data: Dict[str, Any], path: str = ""
    ) -> Dict[str, Any]:
        """Recursively mask sensitive fields for safe API exposure.

        Returns:
            Copy with sensitive values replaced by '***'.
        """
        result = {}
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key

            if self._is_sensitive_path(current_path):
                result[key] = "***" if value else ""
            elif isinstance(value, dict):
                result[key] = self._mask_sensitive(value, current_path)
            else:
                result[key] = value
        return result

    def _is_sensitive_path(self, path: str) -> bool:
        """Check if a config path matches any sensitive key pattern.
        
        Supports wildcard (*) for one path segment and (**) for recursive matching.
        """
        return is_sensitive_config_path(path)

    def _match_pattern(self, pattern: str, path: str) -> bool:
        """Match a dot-separated pattern with wildcards against a path.
        
        Example: "plugins.signature.trusted_keys.*" matches "plugins.signature.trusted_keys.prod"
        """
        return _match_pattern(pattern, path)

# Lazy singleton -- avoids file I/O at import time.
_config_instance: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """Return the lazy ConfigManager singleton."""
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigManager()
    return _config_instance


class _ConfigProxy:
    """Transparent proxy so ``from runtime.config_manager import config`` works
    without triggering file I/O at import time.  All attribute access is
    forwarded to the lazily-initialized ConfigManager singleton."""

    def __getattr__(self, name: str):
        return getattr(get_config(), name)

    def __setattr__(self, name: str, value):
        setattr(get_config(), name, value)


config = _ConfigProxy()
