"""Plugin loader: discover, validate, and load Gazer plugins.

Scans directories for ``gazer_plugin.yaml`` manifests, validates config
against declared schemas, and dynamically imports + calls plugin entry points.

Discovery priority (higher wins on id collision):
  1. ``<workspace>/extensions/``   — project-level / user plugins
  2. ``~/.gazer/extensions/``      — global user plugins
  3. ``core/extensions/``          — bundled plugins (shipped with Gazer)
"""

import importlib
import importlib.util
import hashlib
import hmac
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from runtime.config_manager import config as gazer_config
from plugins.manifest import PluginManifest, parse_manifest

logger = logging.getLogger("PluginLoader")

MANIFEST_FILENAME = "gazer_plugin.yaml"


class PluginLoader:
    """Discover and load Gazer plugins from multiple directories."""

    def __init__(
        self,
        *,
        workspace: Path,
        search_dirs: Optional[List[Path]] = None,
    ) -> None:
        self._workspace = workspace.resolve()

        # Build search dirs in priority order (first wins on id collision)
        if search_dirs is not None:
            self._search_dirs = [d.resolve() for d in search_dirs]
        else:
            self._search_dirs = self._default_search_dirs()

        # Discovered manifests: id -> PluginManifest
        self._manifests: Dict[str, PluginManifest] = {}

        # Loaded plugin APIs (for teardown): id -> PluginAPI
        self._loaded: Dict[str, Any] = {}

        # IDs of plugins that failed to load
        self._failed: Set[str] = set()

    def _default_search_dirs(self) -> List[Path]:
        """Default three-tier search directories."""
        return [
            self._workspace / "extensions",           # project-level
            Path.home() / ".gazer" / "extensions",    # user global
            Path(__file__).resolve().parent.parent / "extensions",  # bundled (core/extensions/)
        ]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> Dict[str, PluginManifest]:
        """Scan search directories for plugin manifests.

        Returns a dict of ``{plugin_id: PluginManifest}``.
        Earlier directories take priority on id collisions.
        """
        self._manifests.clear()
        for search_dir in self._search_dirs:
            if not search_dir.is_dir():
                continue
            for manifest_path in sorted(search_dir.glob(f"*/{MANIFEST_FILENAME}")):
                try:
                    manifest = parse_manifest(manifest_path)
                except Exception as exc:
                    logger.error("Failed to parse %s: %s", manifest_path, exc)
                    continue

                ok, reason = self._verify_manifest_security(manifest)
                manifest.integrity_ok = ok
                manifest.signature_ok = ok
                manifest.verification_error = "" if ok else reason
                if not ok:
                    self._failed.add(manifest.id)
                    logger.warning("Plugin '%s' skipped by security verification: %s", manifest.id, reason)
                    continue

                if manifest.id in self._manifests:
                    logger.debug(
                        "Plugin '%s' already discovered (higher priority), skipping %s",
                        manifest.id, manifest_path,
                    )
                    continue

                self._manifests[manifest.id] = manifest
                logger.info(
                    "Discovered plugin: %s v%s (%s)",
                    manifest.id, manifest.version, manifest.base_dir,
                )

        return dict(self._manifests)

    def _verify_manifest_security(self, manifest: PluginManifest) -> Tuple[bool, str]:
        signature_cfg = gazer_config.get("plugins.signature", {}) or {}
        enforce = bool(signature_cfg.get("enforce", False))
        allow_unsigned = bool(signature_cfg.get("allow_unsigned", True))

        if manifest.integrity:
            ok, reason = self._verify_integrity(manifest)
            if not ok:
                return False, reason

        if manifest.signature:
            ok, reason = self._verify_signature(manifest)
            if not ok:
                return False, reason
            return True, ""

        if not enforce or allow_unsigned:
            return True, ""
        return False, "unsigned plugin rejected by policy"

    @staticmethod
    def _sha256_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 64), b""):
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest().lower()

    def _verify_integrity(self, manifest: PluginManifest) -> Tuple[bool, str]:
        if manifest.base_dir is None:
            return False, "manifest base_dir missing"
        for rel_path, expected in manifest.integrity.items():
            rel = str(rel_path).strip()
            exp = str(expected).strip().lower()
            if not rel or not exp:
                continue
            file_path = (manifest.base_dir / rel).resolve()
            try:
                file_path.relative_to(manifest.base_dir.resolve())
            except Exception:
                return False, f"integrity path escapes plugin root: {rel}"
            if not file_path.is_file():
                return False, f"integrity file missing: {rel}"
            actual = self._sha256_file(file_path)
            if actual != exp:
                return False, f"integrity hash mismatch: {rel}"
        return True, ""

    @staticmethod
    def _signature_payload(manifest: PluginManifest) -> str:
        payload = {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "slot": manifest.slot.value,
            "entry": manifest.entry,
            "integrity": manifest.integrity,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _verify_signature(self, manifest: PluginManifest) -> Tuple[bool, str]:
        signature_cfg = gazer_config.get("plugins.signature", {}) or {}
        trusted_keys = signature_cfg.get("trusted_keys", {}) or {}
        if not isinstance(trusted_keys, dict):
            trusted_keys = {}
        key_id = str(manifest.signing_key_id or "").strip()
        if not key_id:
            return False, "signature key_id missing"
        secret = str(trusted_keys.get(key_id, "")).strip()
        if not secret:
            return False, f"untrusted signature key: {key_id}"
        expected = hmac.new(
            secret.encode("utf-8"),
            self._signature_payload(manifest).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().lower()
        actual = str(manifest.signature or "").strip().lower()
        if not hmac.compare_digest(expected, actual):
            return False, "signature mismatch"
        return True, ""

    @property
    def manifests(self) -> Dict[str, PluginManifest]:
        return dict(self._manifests)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _should_load(self, manifest: PluginManifest) -> bool:
        """Determine whether a plugin should be loaded based on config.

        Rules:
        - Plugins listed in ``plugins.disabled`` are skipped.
        - *optional* plugins need explicit listing in ``plugins.enabled``.
        - Bundled (non-optional) plugins load if their config section exists
          or if they are not explicitly disabled.
        """
        disabled: List[str] = gazer_config.get("plugins.disabled", [])
        if manifest.id in disabled:
            return False

        if manifest.optional:
            enabled: List[str] = gazer_config.get("plugins.enabled", [])
            return manifest.id in enabled

        # Non-optional (bundled): always load unless disabled
        return True

    def _get_plugin_config(self, manifest: PluginManifest) -> Dict[str, Any]:
        """Retrieve plugin-specific config from settings.yaml.

        Convention: config key matches plugin id (dots replaced with underscores).
        For bundled plugins, the config section is the plugin id directly.
        Example: plugin ``git`` reads from ``gazer_config.get("git", {})``.
        """
        config_key = manifest.id.replace("-", "_")
        raw = gazer_config.get(config_key, {})
        if isinstance(raw, dict):
            return raw
        return {}

    def _validate_config(
        self, manifest: PluginManifest, cfg: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate plugin config against its declared schema (basic check).

        Full JSON Schema validation can be added later; for now we just check
        required fields.
        """
        schema = manifest.config_schema
        if not schema:
            return cfg

        required = schema.get("required", [])
        props = schema.get("properties", {})
        for key in required:
            if key not in cfg:
                raise ValueError(
                    f"Plugin '{manifest.id}' missing required config key: {key}"
                )

        # Apply defaults from schema
        for key, prop_schema in props.items():
            if key not in cfg and "default" in prop_schema:
                cfg[key] = prop_schema["default"]

        return cfg

    def load_all(
        self,
        *,
        tool_registry: "ToolRegistry",
        hook_registry: "HookRegistry",
        workspace: Path,
        bus: Any = None,
        memory: Any = None,
        skill_loader: Any = None,
        services: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Load all discovered plugins that should be active.

        Args:
            services: Runtime objects dict (e.g. capture_manager, body, orchestrator).
                      Plugins access these via ``api.get_service(name)``.

        Returns list of successfully loaded plugin IDs.
        """
        from plugins.api import PluginAPI

        loaded_ids: List[str] = []

        for pid, manifest in self._manifests.items():
            if pid in self._loaded:
                continue  # Already loaded

            if not self._should_load(manifest):
                logger.debug("Skipping plugin '%s' (disabled or not enabled)", pid)
                continue

            try:
                cfg = self._get_plugin_config(manifest)
                cfg = self._validate_config(manifest, cfg)
            except ValueError as exc:
                logger.warning("Config validation failed for plugin '%s': %s", pid, exc)
                self._failed.add(pid)
                continue

            api = PluginAPI(
                plugin_id=pid,
                config=cfg,
                workspace=workspace,
                tool_registry=tool_registry,
                hook_registry=hook_registry,
                bus=bus,
                memory=memory,
                skill_loader=skill_loader,
                services=services,
            )

            try:
                self._call_entry(manifest, api)
                self._loaded[pid] = api
                loaded_ids.append(pid)
                logger.info("Loaded plugin: %s v%s", manifest.name, manifest.version)
            except Exception as exc:
                logger.error("Failed to load plugin '%s': %s", pid, exc, exc_info=True)
                api._teardown()
                self._failed.add(pid)

        return loaded_ids

    def _call_entry(self, manifest: PluginManifest, api: "PluginAPI") -> None:
        """Import the plugin module and call its entry function."""
        base_dir = manifest.base_dir
        if base_dir is None:
            raise RuntimeError(f"Plugin '{manifest.id}' has no base_dir set")

        module_name = manifest.entry_module
        func_name = manifest.entry_function

        # Always use a fully-qualified, unique module name to prevent
        # collisions in sys.modules (all plugins use "plugin" as module name).
        fq_name = f"gazer_plugin_{manifest.id.replace('-', '_')}.{module_name}"
        module_file = base_dir / f"{module_name.replace('.', '/')}.py"
        if not module_file.is_file():
            raise ImportError(
                f"Cannot find module '{module_name}' at {module_file}"
            )

        spec = importlib.util.spec_from_file_location(fq_name, module_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {module_file}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[fq_name] = module
        spec.loader.exec_module(module)

        entry_fn = getattr(module, func_name, None)
        if entry_fn is None:
            raise AttributeError(
                f"Plugin '{manifest.id}' module '{module_name}' has no function '{func_name}'"
            )

        entry_fn(api)

    # ------------------------------------------------------------------
    # Unloading
    # ------------------------------------------------------------------

    def unload(self, plugin_id: str) -> None:
        """Unload a plugin by tearing down its registrations."""
        api = self._loaded.pop(plugin_id, None)
        if api:
            api._teardown()
            logger.info("Unloaded plugin: %s", plugin_id)

    def unload_all(self) -> None:
        """Unload all loaded plugins."""
        for pid in list(self._loaded):
            self.unload(pid)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def loaded_ids(self) -> List[str]:
        return list(self._loaded.keys())

    @property
    def failed_ids(self) -> Set[str]:
        return set(self._failed)
