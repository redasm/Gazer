"""State persistence for GazerFlow workflows.

Stores per-workflow state (cursors, checkpoints) as JSON files in
``~/.gazer/flow_state/``.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("FlowState")


class StateStore:
    """JSON-file backed state store for workflows."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir or (Path.home() / ".gazer" / "flow_state")
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, flow_name: str) -> Path:
        safe = flow_name.replace("/", "_").replace("\\", "_")
        return self._base / f"{safe}.json"

    def _checkpoint_path(self, flow_name: str) -> Path:
        safe = flow_name.replace("/", "_").replace("\\", "_")
        return self._base / f"{safe}.checkpoint.json"

    def load(self, flow_name: str, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load persisted state for a flow, merging with defaults."""
        state = dict(defaults or {})
        path = self._path(flow_name)
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    saved = json.load(fh)
                if isinstance(saved, dict):
                    state.update(saved)
            except Exception as exc:
                logger.warning("Failed to load state for '%s': %s", flow_name, exc)
        return state

    def save(self, flow_name: str, state: Dict[str, Any]) -> None:
        """Persist state for a flow."""
        path = self._path(flow_name)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("Failed to save state for '%s': %s", flow_name, exc)

    def clear(self, flow_name: str) -> None:
        """Remove persisted state."""
        path = self._path(flow_name)
        if path.is_file():
            path.unlink()

    def list_flows(self) -> list[str]:
        """List flow names with persisted state."""
        return [p.stem for p in self._base.glob("*.json")]

    def save_checkpoint(self, flow_name: str, checkpoint: Dict[str, Any]) -> None:
        """Persist runtime checkpoint for interruption recovery."""
        path = self._checkpoint_path(flow_name)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(checkpoint, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("Failed to save checkpoint for '%s': %s", flow_name, exc)

    def load_checkpoint(self, flow_name: str) -> Optional[Dict[str, Any]]:
        """Load runtime checkpoint for interruption recovery."""
        path = self._checkpoint_path(flow_name)
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            logger.warning("Failed to load checkpoint for '%s': %s", flow_name, exc)
            return None

    def clear_checkpoint(self, flow_name: str) -> None:
        """Delete runtime checkpoint."""
        path = self._checkpoint_path(flow_name)
        if path.is_file():
            path.unlink()
