#!/usr/bin/env python3
"""Migrate provider registry entries to the current provider contract."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_REGISTRY_PATH = (
    os.environ.get("GAZER_MODEL_PROVIDERS_FILE", "").strip()
    or "config/model_providers.local.json"
)

ALLOWED_INPUT_TYPES = {"text", "image", "audio"}
ALLOWED_AUTH_MODES = {"", "api-key", "bearer", "none"}

ALIAS_MAP = {
    "baseUrl": "base_url",
    "apiKey": "api_key",
    "api_mode": "api",
    "auth_header": "authHeader",
    "strictApiMode": "strict_api_mode",
    "reasoningParam": "reasoning_param",
}

ALLOWED_PROVIDER_KEYS = {
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

OUTPUT_FIELD_ORDER = [
    "base_url",
    "api_key",
    "default_model",
    "api",
    "auth",
    "authHeader",
    "headers",
    "strict_api_mode",
    "reasoning_param",
    "models",
    "agents",
]


@dataclass
class ProviderMigrationReport:
    name: str
    changed: bool = False
    dropped_fields: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_headers(value: Any, report: ProviderMigrationReport) -> Dict[str, str]:
    if not isinstance(value, dict):
        if value is not None:
            report.notes.append("headers_invalid_type_reset")
        return {}
    normalized: Dict[str, str] = {}
    for key, raw in value.items():
        header_name = _safe_str(key)
        if not header_name:
            report.notes.append("headers_empty_key_dropped")
            continue
        if isinstance(raw, (dict, list)):
            report.notes.append(f"headers_{header_name}_non_scalar_dropped")
            continue
        normalized[header_name] = str(raw)
    return normalized


def _normalize_model_entry(entry: Any, report: ProviderMigrationReport) -> Dict[str, Any] | None:
    if not isinstance(entry, dict):
        report.notes.append("models_non_object_item_dropped")
        return None

    model_id = _safe_str(entry.get("id") or entry.get("name"))
    if not model_id:
        report.notes.append("models_missing_id_or_name_dropped")
        return None

    normalized: Dict[str, Any] = {}
    if "id" in entry:
        normalized["id"] = _safe_str(entry.get("id"))
    else:
        normalized["id"] = model_id
    if "name" in entry:
        normalized["name"] = _safe_str(entry.get("name"))

    input_types = entry.get("input")
    if isinstance(input_types, list):
        cleaned = []
        for item in input_types:
            value = _safe_str(item).lower()
            if not value:
                continue
            if value not in ALLOWED_INPUT_TYPES:
                report.notes.append(f"models_input_invalid_value_dropped:{value}")
                continue
            cleaned.append(value)
        normalized["input"] = cleaned
    elif input_types is not None:
        report.notes.append("models_input_non_array_dropped")

    if "reasoning" in entry:
        normalized["reasoning"] = bool(entry.get("reasoning")) if isinstance(entry.get("reasoning"), bool) else False
        if not isinstance(entry.get("reasoning"), bool):
            report.notes.append("models_reasoning_non_bool_coerced_false")

    for source_key, target_key in (("contextWindow", "contextWindow"), ("context_window", "contextWindow")):
        if source_key not in entry:
            continue
        try:
            parsed = int(entry.get(source_key))
        except (TypeError, ValueError):
            report.notes.append(f"models_{source_key}_invalid_dropped")
            continue
        if parsed <= 0:
            report.notes.append(f"models_{source_key}_non_positive_dropped")
            continue
        normalized[target_key] = parsed
        break

    for source_key, target_key in (("maxTokens", "maxTokens"), ("max_tokens", "maxTokens")):
        if source_key not in entry:
            continue
        try:
            parsed = int(entry.get(source_key))
        except (TypeError, ValueError):
            report.notes.append(f"models_{source_key}_invalid_dropped")
            continue
        if parsed <= 0:
            report.notes.append(f"models_{source_key}_non_positive_dropped")
            continue
        normalized[target_key] = parsed
        break

    cost_raw = entry.get("cost")
    if isinstance(cost_raw, dict):
        cost: Dict[str, float] = {}
        for key in ("input", "output", "cacheRead", "cacheWrite"):
            if key not in cost_raw:
                continue
            try:
                cost[key] = float(cost_raw.get(key))
            except (TypeError, ValueError):
                report.notes.append(f"models_cost_{key}_invalid_dropped")
        if cost:
            normalized["cost"] = cost
    elif cost_raw is not None:
        report.notes.append("models_cost_non_object_dropped")

    passthrough_keys = [
        key
        for key in entry.keys()
        if key
        not in {
            "id",
            "name",
            "input",
            "reasoning",
            "contextWindow",
            "context_window",
            "maxTokens",
            "max_tokens",
            "cost",
        }
    ]
    for key in passthrough_keys:
        normalized[key] = entry.get(key)

    return normalized


def _normalize_agents(value: Any, report: ProviderMigrationReport) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        report.notes.append("agents_invalid_type_reset")
        return {}
    defaults = value.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        report.notes.append("agents_defaults_invalid_type_dropped")
        sanitized = dict(value)
        sanitized.pop("defaults", None)
        return sanitized
    return dict(value)


def _normalize_provider(name: str, raw: Any) -> Tuple[Dict[str, Any], ProviderMigrationReport]:
    report = ProviderMigrationReport(name=name)
    if not isinstance(raw, dict):
        report.changed = True
        report.notes.append("provider_non_object_reset")
        return {}, report

    unknown = sorted([key for key in raw.keys() if key not in ALLOWED_PROVIDER_KEYS])
    if unknown:
        report.changed = True
        report.dropped_fields.extend(unknown)

    candidate: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in ALLOWED_PROVIDER_KEYS:
            continue
        canonical_key = ALIAS_MAP.get(key, key)
        if canonical_key in raw and key != canonical_key:
            continue
        candidate[canonical_key] = value

    normalized: Dict[str, Any] = {
        "base_url": _safe_str(candidate.get("base_url")),
        "api_key": _safe_str(candidate.get("api_key")),
        "default_model": _safe_str(candidate.get("default_model")),
        "api": _safe_str(candidate.get("api")),
        "auth": _safe_str(candidate.get("auth")).lower(),
        "authHeader": bool(candidate.get("authHeader")) if isinstance(candidate.get("authHeader"), bool) else False,
        "headers": _normalize_headers(candidate.get("headers"), report),
        "strict_api_mode": bool(candidate.get("strict_api_mode"))
        if isinstance(candidate.get("strict_api_mode"), bool)
        else True,
        "reasoning_param": (
            bool(candidate.get("reasoning_param"))
            if isinstance(candidate.get("reasoning_param"), bool)
            else None
        ),
        "models": [],
        "agents": _normalize_agents(candidate.get("agents"), report),
    }

    if normalized["auth"] not in ALLOWED_AUTH_MODES:
        report.notes.append(f"auth_invalid_reset:{normalized['auth']}")
        normalized["auth"] = ""

    models_raw = candidate.get("models")
    if models_raw is None:
        normalized["models"] = []
    elif not isinstance(models_raw, list):
        report.notes.append("models_invalid_type_reset")
        normalized["models"] = []
    else:
        models: List[Dict[str, Any]] = []
        for entry in models_raw:
            cleaned = _normalize_model_entry(entry, report)
            if cleaned is not None:
                models.append(cleaned)
        normalized["models"] = models

    canonical = {key: normalized.get(key) for key in OUTPUT_FIELD_ORDER}
    changed = canonical != raw
    report.changed = report.changed or changed
    return canonical, report


def _load_payload(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Registry file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Registry JSON root must be an object")
    return data


def _backup_path(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = Path(tempfile.gettempdir()) / "gazer-provider-registry-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root / f"{path.name}.bak-{ts}"


def migrate_registry(path: Path, dry_run: bool = False, backup_dir: Optional[Path] = None) -> int:
    payload = _load_payload(path)
    providers_raw = payload.get("providers")
    if not isinstance(providers_raw, dict):
        raise ValueError("Registry field 'providers' must be an object")

    migrated: Dict[str, Dict[str, Any]] = {}
    reports: List[ProviderMigrationReport] = []
    changed_count = 0

    for provider_name, raw_cfg in providers_raw.items():
        name = _safe_str(provider_name)
        if not name:
            continue
        normalized, report = _normalize_provider(name, raw_cfg)
        reports.append(report)
        migrated[name] = normalized
        if report.changed:
            changed_count += 1

    output = dict(payload)
    output["providers"] = migrated

    print(f"providers_total={len(migrated)} changed={changed_count} dry_run={dry_run}")
    for report in reports:
        if not report.changed:
            continue
        dropped = f" dropped={report.dropped_fields}" if report.dropped_fields else ""
        notes = f" notes={report.notes}" if report.notes else ""
        print(f"- {report.name}:{dropped}{notes}")

    if dry_run:
        return changed_count

    backup_root = backup_dir if isinstance(backup_dir, Path) else None
    if backup_root is not None:
        backup_root.mkdir(parents=True, exist_ok=True)
    backup = (
        (backup_root / f"{path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        if backup_root is not None
        else _backup_path(path)
    )
    backup.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"backup={backup}")
    print(f"written={path}")
    return changed_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate provider registry to canonical contract")
    parser.add_argument("--path", default=DEFAULT_REGISTRY_PATH, help="Path to provider registry JSON")
    parser.add_argument("--dry-run", action="store_true", help="Only print migration report without writing")
    parser.add_argument(
        "--backup-dir",
        default="",
        help="Directory for plaintext backup (default: OS temp dir).",
    )
    args = parser.parse_args()

    path = Path(args.path)
    backup_dir = Path(args.backup_dir).expanduser() if str(args.backup_dir or "").strip() else None
    changed = migrate_registry(path=path, dry_run=bool(args.dry_run), backup_dir=backup_dir)
    return 0 if changed >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
