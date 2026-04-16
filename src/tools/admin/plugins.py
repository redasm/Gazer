from __future__ import annotations

"""Plugin marketplace router — install, toggle, threat scan status."""

import shutil
from pathlib import Path
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException

from tools.admin.state import config, logger
from tools.admin.strategy_helpers import _append_policy_audit
from .auth import verify_admin_token
from plugins.loader import PluginLoader
from security.threat_scan import scan_directory as threat_scan_directory


def _plugin_loader() -> PluginLoader:
    return PluginLoader(workspace=Path.cwd())


def _plugin_install_base(global_install: bool = False) -> Path:
    if global_install:
        return Path.home() / ".gazer" / "extensions"
    return Path("extensions")


def _scan_plugin_source_for_threats(source: Path) -> Dict[str, Any]:
    scan_cfg = config.get("security.threat_scan", {}) or {}
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}
    return threat_scan_directory(source, scan_cfg)


def _plugin_market_snapshot() -> Dict[str, Any]:
    loader = _plugin_loader()
    manifests = loader.discover()
    enabled = {
        str(item).strip()
        for item in (config.get("plugins.enabled", []) or [])
        if str(item).strip()
    }
    disabled = {
        str(item).strip()
        for item in (config.get("plugins.disabled", []) or [])
        if str(item).strip()
    }
    items: List[Dict[str, Any]] = []
    for manifest in manifests.values():
        items.append(
            {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "slot": manifest.slot.value,
                "optional": bool(manifest.optional),
                "description": manifest.description,
                "base_dir": str(manifest.base_dir) if manifest.base_dir else "",
                "enabled": manifest.id in enabled and manifest.id not in disabled,
                "disabled": manifest.id in disabled,
                "integrity_ok": bool(manifest.integrity_ok),
                "signature_ok": bool(manifest.signature_ok),
                "verification_error": str(manifest.verification_error or ""),
            }
        )
    items.sort(key=lambda item: item["id"])
    return {
        "items": items,
        "total": len(items),
        "failed_ids": sorted(loader.failed_ids),
    }

router = APIRouter(tags=["plugins"])


@router.get("/plugins/market", dependencies=[Depends(verify_admin_token)])
async def list_plugins_market():
    snapshot = _plugin_market_snapshot()
    return {"status": "ok", **snapshot}


@router.get("/plugins/market/{plugin_id}", dependencies=[Depends(verify_admin_token)])
async def get_plugin_market_item(plugin_id: str):
    pid = str(plugin_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="plugin_id is required")
    snapshot = _plugin_market_snapshot()
    for item in snapshot["items"]:
        if item["id"] == pid:
            return {"status": "ok", "plugin": item}
    raise HTTPException(status_code=404, detail="Plugin not found")


@router.post("/plugins/market/install", dependencies=[Depends(verify_admin_token)])
async def install_plugin_market(payload: Dict[str, Any]):
    from plugins.manifest import parse_manifest

    source_raw = str(payload.get("source", "")).strip()
    if not source_raw:
        raise HTTPException(status_code=400, detail="'source' is required")
    source = Path(source_raw).expanduser().resolve()
    if not source.is_dir():
        raise HTTPException(status_code=400, detail="'source' must be an existing plugin directory")

    manifest_path = source / "gazer_plugin.yaml"
    if not manifest_path.is_file():
        raise HTTPException(status_code=400, detail="Plugin manifest 'gazer_plugin.yaml' is missing")

    try:
        manifest = parse_manifest(manifest_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid plugin manifest: {exc}")

    verifier = _plugin_loader()
    ok, reason = verifier._verify_manifest_security(manifest)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Plugin verification failed: {reason}")

    threat_scan = _scan_plugin_source_for_threats(source)
    if bool(threat_scan.get("blocked", False)):
        _append_policy_audit(
            action="plugins.market.install.blocked",
            details={
                "plugin_id": manifest.id,
                "version": manifest.version,
                "reason": "threat_scan_blocked",
                "threat_scan": {
                    "status": str(threat_scan.get("status", "")),
                    "provider": str(threat_scan.get("provider", "")),
                    "fail_mode": str(threat_scan.get("fail_mode", "")),
                    "findings": len(threat_scan.get("findings", []) or []),
                    "errors": len(threat_scan.get("errors", []) or []),
                },
            },
        )
        raise HTTPException(status_code=400, detail="Plugin threat scan blocked installation")

    global_install = bool(payload.get("global_install", False))
    target_base = _plugin_install_base(global_install=global_install)
    target_base.mkdir(parents=True, exist_ok=True)
    target_dir = target_base / manifest.id
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source, target_dir)

    if bool(payload.get("enable", True)):
        enabled = [
            str(item).strip()
            for item in (config.get("plugins.enabled", []) or [])
            if str(item).strip()
        ]
        if manifest.id not in enabled:
            enabled.append(manifest.id)
            config.set_many({"plugins.enabled": sorted(set(enabled))})
            config.save()

    _append_policy_audit(
        action="plugins.market.install",
        details={
            "plugin_id": manifest.id,
            "version": manifest.version,
            "global_install": global_install,
            "threat_scan": {
                "status": str(threat_scan.get("status", "")),
                "provider": str(threat_scan.get("provider", "")),
                "fail_mode": str(threat_scan.get("fail_mode", "")),
                "blocked": bool(threat_scan.get("blocked", False)),
                "findings": len(threat_scan.get("findings", []) or []),
                "errors": len(threat_scan.get("errors", []) or []),
            },
        },
    )
    return {
        "status": "ok",
        "plugin": {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "slot": manifest.slot.value,
            "install_path": str(target_dir),
        },
        "threat_scan": threat_scan,
    }


@router.post("/plugins/market/toggle", dependencies=[Depends(verify_admin_token)])
async def toggle_plugin_market(payload: Dict[str, Any]):
    plugin_id = str(payload.get("plugin_id", "")).strip()
    if not plugin_id:
        raise HTTPException(status_code=400, detail="'plugin_id' is required")
    enabled_flag = bool(payload.get("enabled", True))

    enabled = [
        str(item).strip()
        for item in (config.get("plugins.enabled", []) or [])
        if str(item).strip()
    ]
    disabled = [
        str(item).strip()
        for item in (config.get("plugins.disabled", []) or [])
        if str(item).strip()
    ]

    if enabled_flag:
        if plugin_id not in enabled:
            enabled.append(plugin_id)
        disabled = [item for item in disabled if item != plugin_id]
    else:
        enabled = [item for item in enabled if item != plugin_id]
        if plugin_id not in disabled:
            disabled.append(plugin_id)

    config.set_many(
        {
            "plugins.enabled": sorted(set(enabled)),
            "plugins.disabled": sorted(set(disabled)),
        }
    )
    config.save()
    _append_policy_audit(
        action="plugins.market.toggle",
        details={"plugin_id": plugin_id, "enabled": enabled_flag},
    )
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "enabled": enabled_flag,
    }


@router.get("/security/threat-scan/status", dependencies=[Depends(verify_admin_token)])
async def get_threat_scan_status():
    scan_cfg = config.get("security.threat_scan", {}) or {}
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}
    return {
        "status": "ok",
        "threat_scan": {
            "enabled": bool(scan_cfg.get("enabled", False)),
            "provider": str(scan_cfg.get("provider", "virustotal") or "virustotal"),
            "fail_mode": str(scan_cfg.get("fail_mode", "open") or "open"),
            "request_timeout_seconds": float(scan_cfg.get("request_timeout_seconds", 8.0) or 8.0),
            "max_files": int(scan_cfg.get("max_files", 64) or 64),
            "api_key_configured": bool(str(scan_cfg.get("api_key", "") or "").strip()),
        },
    }
