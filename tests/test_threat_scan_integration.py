from pathlib import Path

import pytest
from fastapi import HTTPException

from security.threat_scan import scan_directory
from tools.admin import workflows as admin_api


class _FakeConfig:
    def __init__(self, data):
        self.data = data
        self.saved = False

    def get(self, key_path, default=None):
        cur = self.data
        for part in str(key_path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set_many(self, updates):
        for key, value in updates.items():
            parts = str(key).split(".")
            cur = self.data
            for part in parts[:-1]:
                if part not in cur or not isinstance(cur[part], dict):
                    cur[part] = {}
                cur = cur[part]
            cur[parts[-1]] = value

    def save(self):
        self.saved = True


def _write_plugin_stub(base: Path) -> Path:
    plugin_dir = base / "demo_plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "gazer_plugin.yaml").write_text(
        "\n".join(
            [
                "id: demo.plugin",
                "name: Demo Plugin",
                "version: 0.1.0",
                "slot: tool",
                "entry: plugin:setup",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text("def setup(api):\n    return None\n", encoding="utf-8")
    return plugin_dir


def test_threat_scan_fail_open_and_fail_closed(tmp_path: Path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello", encoding="utf-8")

    fail_open = scan_directory(
        tmp_path,
        {"enabled": True, "provider": "unsupported", "fail_mode": "open", "max_files": 10},
    )
    assert fail_open["status"] == "error"
    assert fail_open["blocked"] is False

    fail_closed = scan_directory(
        tmp_path,
        {"enabled": True, "provider": "unsupported", "fail_mode": "closed", "max_files": 10},
    )
    assert fail_closed["status"] == "error"
    assert fail_closed["blocked"] is True


@pytest.mark.asyncio
async def test_install_plugin_market_blocks_when_threat_scan_blocks(monkeypatch, tmp_path: Path):
    plugin_dir = _write_plugin_stub(tmp_path)
    fake_cfg = _FakeConfig({"plugins": {"enabled": []}, "security": {"threat_scan": {"enabled": True}}})

    class _Verifier:
        @staticmethod
        def _verify_manifest_security(_manifest):
            return True, ""

    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "_plugin_loader", lambda: _Verifier())
    monkeypatch.setattr(admin_api, "_plugin_install_base", lambda global_install=False: tmp_path / "installed")
    monkeypatch.setattr(
        admin_api,
        "_scan_plugin_source_for_threats",
        lambda _source: {
            "enabled": True,
            "status": "ok",
            "provider": "virustotal",
            "fail_mode": "open",
            "blocked": True,
            "findings": [{"path": "plugin.py", "severity": "high"}],
            "errors": [],
        },
    )
    monkeypatch.setattr(admin_api, "_append_policy_audit", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException, match="threat scan blocked"):
        await admin_api.install_plugin_market({"source": str(plugin_dir), "enable": True})


@pytest.mark.asyncio
async def test_install_plugin_market_fail_open_allows_install(monkeypatch, tmp_path: Path):
    plugin_dir = _write_plugin_stub(tmp_path)
    fake_cfg = _FakeConfig({"plugins": {"enabled": []}, "security": {"threat_scan": {"enabled": True}}})

    class _Verifier:
        @staticmethod
        def _verify_manifest_security(_manifest):
            return True, ""

    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "_plugin_loader", lambda: _Verifier())
    monkeypatch.setattr(admin_api, "_plugin_install_base", lambda global_install=False: tmp_path / "installed")
    monkeypatch.setattr(
        admin_api,
        "_scan_plugin_source_for_threats",
        lambda _source: {
            "enabled": True,
            "status": "error",
            "provider": "virustotal",
            "fail_mode": "open",
            "blocked": False,
            "findings": [],
            "errors": ["network timeout"],
        },
    )
    monkeypatch.setattr(admin_api, "_append_policy_audit", lambda *args, **kwargs: None)

    payload = await admin_api.install_plugin_market({"source": str(plugin_dir), "enable": True})
    assert payload["status"] == "ok"
    assert payload["threat_scan"]["status"] == "error"
    assert fake_cfg.saved is True
    assert "demo.plugin" in (fake_cfg.get("plugins.enabled", []) or [])
