import agent.adapter as adapter_module
from agent.adapter import GazerAgent


def test_build_litellm_provider_uses_agents_default_primary_when_default_model_missing(monkeypatch):
    provider_cfg = {
        "base_url": "https://gmn.example.com/v1",
        "api_key": "sk-test",
        "default_model": "",
        "agents": {
            "defaults": {
                "model": {
                    "primary": "gmn/gpt-5.3-codex",
                }
            }
        },
    }
    monkeypatch.setattr(
        adapter_module.ModelRegistry,
        "get_provider_config",
        staticmethod(lambda _name: dict(provider_cfg)),
    )
    agent = GazerAgent.__new__(GazerAgent)

    provider = agent._build_litellm_provider("gmn")

    assert provider is not None
    assert provider.get_default_model() == "gpt-5.3-codex"


def test_build_litellm_provider_uses_auth_mode_when_auth_header_omitted(monkeypatch):
    provider_cfg = {
        "base_url": "https://gmn.example.com/v1",
        "api_key": "sk-test",
        "default_model": "gpt-5.2",
        "api": "openai-responses",
        "auth": "api-key",
        "authHeader": False,
    }
    monkeypatch.setattr(
        adapter_module.ModelRegistry,
        "get_provider_config",
        staticmethod(lambda _name: dict(provider_cfg)),
    )
    agent = GazerAgent.__new__(GazerAgent)

    provider = agent._build_litellm_provider("gmn")

    assert provider is not None
    assert provider.auth_mode == "api-key"
    assert provider.auth_header is True
