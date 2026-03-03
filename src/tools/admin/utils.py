"""Shared utility functions for Admin API routers.

Pure-logic helpers (JSONL, config redaction, path validation, etc.) that are
used across multiple Admin API router sub-modules.  These have no dependency
on runtime globals and can be tested independently.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from runtime.config_manager import config, is_sensitive_config_path
from tools.admin.state import (
    _EXPORT_DEFAULT_ALLOWED_DIRS,
    _EXPORT_DEFAULT_DIR,
    _PROJECT_ROOT,
    _PROTECTED_EXPORT_TARGETS,
)

logger = logging.getLogger("GazerAdminAPI")


# ---------------------------------------------------------------------------
# JSONL helpers (used by multiple routers)
# ---------------------------------------------------------------------------

def _append_jsonl_record(path: Path, payload: Dict[str, Any]) -> None:
    """Append a single JSON record to a JSONL file (creates parent dirs)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("Failed to append JSONL record: %s", path, exc_info=True)


def _read_jsonl_tail(path: Path, limit: int = 500) -> List[Dict[str, Any]]:
    """Read the last *limit* records from a JSONL file."""
    safe_limit = max(1, min(int(limit), 5000))
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-safe_limit:]
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        row = line.strip()
        if not row:
            continue
        try:
            obj = json.loads(row)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _dedupe_dict_rows(
    rows: List[Dict[str, Any]], *, id_keys: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """De-duplicate a list of dicts by *id_keys* (or full JSON equality)."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if id_keys:
            key_parts = [str(row.get(k, "")) for k in id_keys]
            marker = "|".join(key_parts)
        else:
            marker = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Config helpers (used by config_routes and others)
# ---------------------------------------------------------------------------

def _is_sensitive_config_keypath(path: str) -> bool:
    """Best-effort sensitive path check aligned with ConfigManager."""
    try:
        return bool(is_sensitive_config_path(path))
    except Exception:
        return False


def _redact_config(data: Any, path: str = "", _depth: int = 0) -> Any:
    """Deep-copy config data with sensitive values replaced by ``'***'``."""
    if _depth > 20:
        return "..."
    if path and _is_sensitive_config_keypath(path):
        return "***" if data else ""
    if isinstance(data, dict):
        return {
            k: _redact_config(v, f"{path}.{k}" if path else str(k), _depth + 1)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact_config(item, path, _depth + 1) for item in data]
    return data


def _filter_masked_sensitive(
    data: Any,
    original: Optional[Any] = None,
    path: str = "",
    _depth: int = 0,
) -> Any:
    """Replace masked sensitive values (``'***'``) with original values."""
    if _depth > 20:
        return data
    if isinstance(data, dict):
        result = {}
        orig_dict = original if isinstance(original, dict) else {}
        for k, v in data.items():
            orig_v = orig_dict.get(k)
            current_path = f"{path}.{k}" if path else str(k)
            if _is_sensitive_config_keypath(current_path) and v == "***":
                if orig_v is not None:
                    result[k] = orig_v
                continue
            if isinstance(v, dict):
                result[k] = _filter_masked_sensitive(v, orig_v, current_path, _depth + 1)
            elif isinstance(v, list):
                result[k] = _filter_masked_sensitive(v, orig_v, current_path, _depth + 1)
            else:
                result[k] = v
        return result
    if isinstance(data, list):
        out_list: List[Any] = []
        orig_list = original if isinstance(original, list) else []
        for idx, item in enumerate(data):
            orig_item = orig_list[idx] if idx < len(orig_list) else None
            out_list.append(_filter_masked_sensitive(item, orig_item, path, _depth + 1))
        return out_list
    return data


def _flatten_config(data: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten nested config dict to dot-path keys."""
    out: Dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(_flatten_config(v, key))
            else:
                out[key] = v
    return out


# ---------------------------------------------------------------------------
# Export path resolver (used by observability / training exports)
# ---------------------------------------------------------------------------

def _resolve_export_output_path(
    *,
    output_raw: str,
    default_filename: str,
) -> Path:
    """Resolve and validate an export output path.

    Ensures the path is under an allowed export directory.
    """
    import tempfile

    default_path = (_PROJECT_ROOT / _EXPORT_DEFAULT_DIR / default_filename).resolve()
    if not output_raw:
        default_path.parent.mkdir(parents=True, exist_ok=True)
        return default_path

    requested = Path(output_raw).expanduser()
    resolved = (requested if requested.is_absolute() else (_PROJECT_ROOT / requested)).resolve()

    allowed_raw = config.get("api.export_allowed_dirs", _EXPORT_DEFAULT_ALLOWED_DIRS)
    allowed_items = allowed_raw if isinstance(allowed_raw, list) else _EXPORT_DEFAULT_ALLOWED_DIRS
    allowed_roots: List[Path] = []
    for entry in allowed_items:
        text = str(entry or "").strip()
        if not text:
            continue
        root = Path(text).expanduser()
        root = (root if root.is_absolute() else (_PROJECT_ROOT / root)).resolve()
        allowed_roots.append(root)
    default_root = (_PROJECT_ROOT / _EXPORT_DEFAULT_DIR).resolve()
    if default_root not in allowed_roots:
        allowed_roots.append(default_root)
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        allowed_roots.append(temp_root)
    except Exception:
        pass
    if not allowed_roots:
        allowed_roots = [(_PROJECT_ROOT / _EXPORT_DEFAULT_DIR).resolve()]

    allowed = any(resolved == root or root in resolved.parents for root in allowed_roots)
    if not allowed:
        allowed_desc = [str(item) for item in allowed_roots]
        raise HTTPException(
            status_code=400,
            detail=f"output_path must be under allowed export dirs: {allowed_desc}",
        )
    if resolved in _PROTECTED_EXPORT_TARGETS:
        raise HTTPException(status_code=400, detail="output_path points to protected config file")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


# ---------------------------------------------------------------------------
# Misc shared helpers
# ---------------------------------------------------------------------------

def _is_subpath(base: Path, target: Path) -> bool:
    """Return True if *target* resolves under *base*."""
    try:
        base_resolved = base.resolve(strict=False)
        target_resolved = target.resolve(strict=False)
        return target_resolved == base_resolved or target_resolved.is_relative_to(base_resolved)
    except (OSError, ValueError):
        return False


_MISSING = object()

_TOOL_ERROR_PATTERN = re.compile(r"^Error\s+\[([A-Z0-9_]+)\]:\s*(.*)$", re.IGNORECASE)
