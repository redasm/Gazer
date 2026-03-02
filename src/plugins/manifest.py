"""Plugin manifest: metadata declaration for Gazer plugins.

Each plugin directory contains a ``gazer_plugin.yaml`` that declares the
plugin's identity, entry point, slot type, config schema, and requirements.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("PluginManifest")


class PluginSlot(str, Enum):
    """The functional slot a plugin fills."""

    TOOL = "tool"
    CHANNEL = "channel"
    PROVIDER = "provider"
    MEMORY = "memory"


@dataclass
class PluginManifest:
    """Parsed content of a ``gazer_plugin.yaml`` file."""

    # --- Required ---
    id: str
    name: str
    version: str
    slot: PluginSlot
    entry: str  # "module:function", e.g. "plugin:setup"

    # --- Optional ---
    optional: bool = False  # If True, requires explicit allowlist to enable
    skills: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    requires: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    integrity: Dict[str, str] = field(default_factory=dict)
    signature: str = ""
    signing_key_id: str = ""

    # --- Runtime (set by loader, not from YAML) ---
    base_dir: Optional[Path] = field(default=None, repr=False)
    integrity_ok: bool = field(default=True, repr=False)
    signature_ok: bool = field(default=True, repr=False)
    verification_error: str = field(default="", repr=False)

    @property
    def entry_module(self) -> str:
        """Module part of the entry spec (before ':')."""
        return self.entry.split(":")[0]

    @property
    def entry_function(self) -> str:
        """Function part of the entry spec (after ':')."""
        parts = self.entry.split(":")
        return parts[1] if len(parts) > 1 else "setup"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = ("id", "name", "version", "slot", "entry")


def parse_manifest(yaml_path: Path) -> PluginManifest:
    """Parse a ``gazer_plugin.yaml`` file into a :class:`PluginManifest`.

    Raises ``ValueError`` on missing required fields or invalid slot.
    """
    with open(yaml_path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    # Validate required fields
    missing = [f for f in _REQUIRED_FIELDS if f not in raw]
    if missing:
        raise ValueError(
            f"Plugin manifest {yaml_path} missing required fields: {missing}"
        )

    # Parse slot enum
    try:
        slot = PluginSlot(raw["slot"])
    except ValueError:
        valid = [s.value for s in PluginSlot]
        raise ValueError(
            f"Invalid slot '{raw['slot']}' in {yaml_path}. Must be one of {valid}"
        )

    return PluginManifest(
        id=raw["id"],
        name=raw["name"],
        version=raw["version"],
        slot=slot,
        entry=raw["entry"],
        optional=raw.get("optional", False),
        skills=[str(s) for s in raw.get("skills", [])],
        config_schema=raw.get("config_schema", {}),
        requires=raw.get("requires", {}),
        description=raw.get("description", ""),
        integrity={str(k): str(v) for k, v in (raw.get("integrity", {}) or {}).items()},
        signature=(
            str((raw.get("signature", {}) or {}).get("value", ""))
            if isinstance(raw.get("signature"), dict)
            else str(raw.get("signature", ""))
        ),
        signing_key_id=(
            str((raw.get("signature", {}) or {}).get("key_id", ""))
            if isinstance(raw.get("signature"), dict)
            else str(raw.get("signing_key_id", ""))
        ),
        base_dir=yaml_path.parent,
    )
