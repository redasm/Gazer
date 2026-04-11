from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from runtime.protocols import ConfigProvider

logger = logging.getLogger("GazerPaths")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_repo_root() -> Path:
    """Return the repository root derived from this module location."""
    return _REPO_ROOT


def resolve_runtime_root(config_manager: ConfigProvider | Any = None) -> Path:
    """Resolve the runtime root used for relative data/report paths.

    Resolution order:
    1. Config-derived workspace root
    2. ``GAZER_HOME`` environment variable
    3. Repository root
    """
    manager = config_manager
    if manager is None:
        try:
            from runtime.config_manager import config as runtime_config

            manager = runtime_config
        except Exception:
            manager = None

    resolver = getattr(manager, "_resolve_workspace_root", None)
    if callable(resolver):
        try:
            return Path(resolver()).expanduser().resolve()
        except Exception:
            logger.debug("Failed to resolve runtime root from config manager", exc_info=True)

    gazer_home = str(os.environ.get("GAZER_HOME", "") or "").strip()
    if gazer_home:
        return Path(gazer_home).expanduser().resolve()

    return resolve_repo_root()


def resolve_runtime_path(path: str | Path, *, config_manager: ConfigProvider | Any = None) -> Path:
    """Resolve *path* against the runtime root when it is relative."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (resolve_runtime_root(config_manager=config_manager) / candidate).resolve()
