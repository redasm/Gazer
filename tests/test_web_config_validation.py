import copy

import pytest

from tools.admin import workflows as admin_api


class _FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        cursor = self.data
        for part in str(key_path).split("."):
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                return default
        return cursor

    def to_safe_dict(self):
        return copy.deepcopy(self.data)

    def set_many(self, updates):
        for key, value in updates.items():
            parts = str(key).split(".")
            cursor = self.data
            for part in parts[:-1]:
                if part not in cursor or not isinstance(cursor[part], dict):
                    cursor[part] = {}
                cursor = cursor[part]
            cursor[parts[-1]] = value


class _FakeRegistry:
    def __init__(self, providers=None):
        self.providers = copy.deepcopy(providers or {})

    def list_providers(self):
        return copy.deepcopy(self.providers)

    def list_redacted_providers(self):
        out = copy.deepcopy(self.providers)
        for cfg in out.values():
            if isinstance(cfg, dict) and "api_key" in cfg:
                cfg["api_key"] = "***" if cfg.get("api_key") else ""
        return out

    def get_provider(self, name):
        raw = self.providers.get(name, {})
        return copy.deepcopy(raw if isinstance(raw, dict) else {})

    def upsert_provider(self, name, cfg):
        self.providers[str(name)] = copy.deepcopy(cfg if isinstance(cfg, dict) else {})
        return copy.deepcopy(self.providers[str(name)])


class _Owner:
    admin_token = "admin-token"


@pytest.mark.asyncio
async def test_web_config_validate_reports_errors_and_fix_suggestions(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "agents": {"defaults": {"model": {"primary": "", "fallbacks": []}}},
            "security": {"owner_channel_ids": {}, "dm_policy": "pairing", "tool_max_tier": "standard"},
        }
    )
    fake_registry = _FakeRegistry({})
    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "get_provider_registry", lambda: fake_registry)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: _Owner())

    payload = {
        "config_patch": {
            "security": {
                "dm_policy": "open",
                "tool_max_tier": "privileged",
            }
        }
    }
    report = await admin_api.validate_web_config(payload)
    assert report["status"] == "ok"
    assert report["summary"]["errors"] >= 2
    assert report["summary"]["warnings"] >= 2
    assert any(item.get("code") == "dm_policy_open" for item in report["issues"])
    assert any(item.get("path") == "security.dm_policy" for item in report["fixes"])
    assert any(item.get("path") == "security.tool_max_tier" for item in report["fixes"])


@pytest.mark.asyncio
async def test_web_config_validate_allows_local_provider_without_api_key(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "ollama_local/llama3",
                        "fallbacks": ["ollama_local/llama3"],
                    }
                }
            },
            "telegram": {"enabled": False, "token": "", "allowed_ids": []},
            "feishu": {"enabled": False, "app_id": "", "app_secret": "", "allowed_ids": []},
            "discord": {"enabled": False, "token": "", "allowed_guild_ids": []},
            "security": {
                "owner_channel_ids": {"telegram": "1001"},
                "dm_policy": "pairing",
                "tool_max_tier": "standard",
                "auto_approve_privileged": False,
            },
        }
    )
    fake_registry = _FakeRegistry(
        {
            "ollama_local": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "",
                "default_model": "llama3",
            }
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "get_provider_registry", lambda: fake_registry)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: _Owner())

    report = await admin_api.validate_web_config({})
    issue_codes = {str(item.get("code", "")) for item in report.get("issues", [])}
    assert "provider_api_key_missing" not in issue_codes
    assert report["summary"]["errors"] == 0
