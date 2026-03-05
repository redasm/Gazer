import copy
from pathlib import Path

import pytest

from tools.admin import api_facade as admin_api


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
async def test_get_web_config_wizard_reports_completed_steps(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "openai/gpt-4o",
                        "fallbacks": ["openai/gpt-4o-mini"],
                    }
                }
            },
            "telegram": {"enabled": True, "token": "bot-token", "allowed_ids": ["1001"]},
            "feishu": {"enabled": False, "app_id": "", "app_secret": "", "allowed_ids": []},
            "discord": {"enabled": False, "token": "", "allowed_guild_ids": []},
            "security": {
                "owner_channel_ids": {"telegram": "1001"},
                "dm_policy": "pairing",

                "auto_approve_privileged": False,
            },
        }
    )
    fake_registry = _FakeRegistry(
        {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
                "default_model": "gpt-4o",
            }
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "get_provider_registry", lambda: fake_registry)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: _Owner())

    payload = await admin_api.get_web_config_wizard()
    steps = {step["id"]: bool(step.get("completed")) for step in payload.get("steps", [])}
    assert payload["status"] == "ok"
    assert steps["llm_provider"] is True
    assert steps["channel_onboarding"] is True
    assert steps["security_baseline"] is True


@pytest.mark.asyncio
async def test_apply_web_config_wizard_updates_config_and_keeps_protected_flag(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "models": {
                "embedding": {"provider": "openai", "model": "text-embedding-3-large"},
            },
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "openai/gpt-4o",
                        "fallbacks": ["openai/gpt-4o-mini"],
                    }
                }
            },
            "telegram": {"enabled": False, "token": "", "allowed_ids": []},
            "feishu": {"enabled": False, "app_id": "", "app_secret": "", "allowed_ids": []},
            "discord": {"enabled": False, "token": "", "allowed_guild_ids": []},
            "security": {
                "owner_channel_ids": {"telegram": "1001"},
                "dm_policy": "pairing",

                "auto_approve_privileged": False,
                "tool_groups": {},
            },
            "agents": {"list": []},
        }
    )
    fake_registry = _FakeRegistry(
        {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-old",
                "default_model": "gpt-4o",
            }
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)
    monkeypatch.setattr(admin_api, "get_provider_registry", lambda: fake_registry)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: _Owner())

    result = await admin_api.apply_web_config_wizard(
        {
            "llm": {
                "provider": "my_gateway",
                "model": "my-chat-model",
                "base_url": "https://gateway.example.com/v1",
                "api_key": "sk-new",
                "api": "openai-responses",
                "apply_embedding": True,
                "embedding_model": "my-embed-model",
            },
            "channels": {
                "telegram": {"enabled": True, "token": "bot-1", "allowed_ids": "1001,1002"},
            },
            "security": {
                "dm_policy": "allowlist",
                "owner_channel_ids": {"telegram": "1001"},

                "auto_approve_privileged": True,
            },
        }
    )

    assert result["status"] == "ok"
    assert "agents.defaults.model.primary" in result["updated_keys"]
    assert "agents.defaults.model.fallbacks" in result["updated_keys"]
    assert "security.dm_policy" in result["updated_keys"]
    assert "security.auto_approve_privileged is protected" in " ".join(result["warnings"])
    assert fake_cfg.get("agents.defaults.model.primary") == "my_gateway/my-chat-model"
    assert fake_cfg.get("agents.defaults.model.fallbacks")[0] == "my_gateway/my-chat-model"
    assert fake_cfg.get("models.embedding.model") == "my-embed-model"
    assert fake_cfg.get("security.dm_policy") == "allowlist"

    assert fake_cfg.get("security.auto_approve_privileged") is False
    assert fake_registry.get_provider("my_gateway")["api_key"] == "sk-new"


@pytest.mark.asyncio
async def test_get_web_onboarding_help_uses_fallback_when_file_missing(monkeypatch, tmp_path: Path):
    missing_file = tmp_path / "missing_guide.md"
    monkeypatch.setattr(admin_api, "_WEB_ONBOARDING_GUIDE_PATH", missing_file)
    payload = await admin_api.get_web_onboarding_help()
    assert payload["status"] == "ok"
    assert "Web Onboarding Guide" in payload["content"]
