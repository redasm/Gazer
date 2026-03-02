"""Optional external threat-intel scan integration (e.g. VirusTotal)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List
from urllib import error as urlerror
from urllib import request as urlrequest


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _collect_files(root: Path, *, max_files: int) -> List[Path]:
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        files.append(path)
        if len(files) >= max_files:
            break
    return files


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _BaseThreatScanner:
    provider_name = "none"

    def scan_files(self, files: List[Path]) -> Dict[str, Any]:
        raise NotImplementedError


class _VirusTotalThreatScanner(_BaseThreatScanner):
    provider_name = "virustotal"

    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "https://www.virustotal.com/api/v3").rstrip("/")
        self._timeout_seconds = max(1.0, float(timeout_seconds or 8.0))
        if not self._api_key:
            raise RuntimeError("VirusTotal api_key is required")

    def _lookup_hash(self, sha256_hex: str) -> Dict[str, Any]:
        req = urlrequest.Request(
            f"{self._base_url}/files/{sha256_hex}",
            headers={"x-apikey": self._api_key, "accept": "application/json"},
            method="GET",
        )
        with urlrequest.urlopen(req, timeout=self._timeout_seconds) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))

    def scan_files(self, files: List[Path]) -> Dict[str, Any]:
        findings: List[Dict[str, Any]] = []
        errors: List[str] = []
        scanned = 0
        for file_path in files:
            scanned += 1
            sha256_hex = _sha256_file(file_path)
            try:
                payload = self._lookup_hash(sha256_hex)
            except urlerror.HTTPError as exc:
                if int(getattr(exc, "code", 0) or 0) == 404:
                    continue
                errors.append(f"{file_path}: http_{exc.code}")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{file_path}: {exc}")
                continue

            stats = (
                (((payload.get("data") or {}).get("attributes") or {}).get("last_analysis_stats") or {})
                if isinstance(payload, dict)
                else {}
            )
            malicious = _safe_int(stats.get("malicious"), 0)
            suspicious = _safe_int(stats.get("suspicious"), 0)
            if malicious > 0 or suspicious > 0:
                findings.append(
                    {
                        "path": str(file_path),
                        "sha256": sha256_hex,
                        "malicious": malicious,
                        "suspicious": suspicious,
                        "severity": "high" if malicious > 0 else "medium",
                        "provider": self.provider_name,
                    }
                )

        status = "ok" if not errors else ("partial" if findings or scanned > 0 else "error")
        return {
            "status": status,
            "provider": self.provider_name,
            "scanned_files": scanned,
            "findings": findings,
            "errors": errors,
        }


def _build_scanner(scan_cfg: Dict[str, Any]) -> _BaseThreatScanner:
    provider = str(scan_cfg.get("provider", "virustotal") or "virustotal").strip().lower()
    if provider == "virustotal":
        return _VirusTotalThreatScanner(
            api_key=str(scan_cfg.get("api_key", "") or ""),
            base_url=str(scan_cfg.get("base_url", "https://www.virustotal.com/api/v3") or ""),
            timeout_seconds=float(scan_cfg.get("request_timeout_seconds", 8.0) or 8.0),
        )
    raise RuntimeError(f"Unsupported threat scan provider: {provider}")


def scan_directory(root: Path, scan_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Scan a plugin/upload directory and return fail-open/fail-closed decision payload."""
    cfg = scan_cfg if isinstance(scan_cfg, dict) else {}
    enabled = bool(cfg.get("enabled", False))
    fail_mode = str(cfg.get("fail_mode", "open") or "open").strip().lower()
    fail_mode = fail_mode if fail_mode in {"open", "closed"} else "open"
    max_files = max(1, min(_safe_int(cfg.get("max_files", 64), 64), 2048))

    if not enabled:
        return {
            "enabled": False,
            "status": "skipped",
            "provider": str(cfg.get("provider", "virustotal") or "virustotal"),
            "fail_mode": fail_mode,
            "blocked": False,
            "scanned_files": 0,
            "findings": [],
            "errors": [],
            "reason": "disabled",
        }

    files = _collect_files(root, max_files=max_files)
    try:
        scanner = _build_scanner(cfg)
        result = scanner.scan_files(files)
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": True,
            "status": "error",
            "provider": str(cfg.get("provider", "virustotal") or "virustotal"),
            "fail_mode": fail_mode,
            "blocked": bool(fail_mode == "closed"),
            "scanned_files": len(files),
            "findings": [],
            "errors": [str(exc)],
        }

    findings = result.get("findings", []) if isinstance(result.get("findings"), list) else []
    malicious_count = sum(
        1
        for item in findings
        if isinstance(item, dict)
        and (_safe_int(item.get("malicious"), 0) > 0 or str(item.get("severity", "")).strip().lower() == "high")
    )
    blocked = malicious_count > 0
    return {
        "enabled": True,
        "status": str(result.get("status", "ok") or "ok"),
        "provider": str(result.get("provider", cfg.get("provider", "virustotal")) or "virustotal"),
        "fail_mode": fail_mode,
        "blocked": bool(blocked),
        "scanned_files": _safe_int(result.get("scanned_files"), len(files)),
        "findings": findings,
        "errors": result.get("errors", []) if isinstance(result.get("errors"), list) else [],
    }
