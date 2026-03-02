import pytest

from runtime.provider_registry import ProviderRegistry


def test_provider_registry_deployment_targets_crud(tmp_path):
    path = tmp_path / "providers.local.json"
    registry = ProviderRegistry(path=str(path))

    created = registry.upsert_deployment_target(
        "openai_primary",
        {
            "provider": "openai",
            "type": "gateway",
            "enabled": True,
            "base_url": "https://gateway.example/v1",
            "api_key": "secret_key",
            "default_model": "gpt-4o",
        },
    )
    assert created["provider"] == "openai"
    assert created["api_key"] == "secret_key"

    listed = registry.list_deployment_targets()
    assert "openai_primary" in listed
    assert listed["openai_primary"]["enabled"] is True

    redacted = registry.list_redacted_deployment_targets()
    assert redacted["openai_primary"]["api_key"] == "***"

    updated = registry.upsert_deployment_target(
        "openai_primary",
        {
            "provider": "openai",
            "type": "gateway",
            "enabled": False,
            "api_key": "***",
        },
    )
    assert updated["enabled"] is False
    # "***" should preserve previous secret
    assert updated["api_key"] == "secret_key"

    assert registry.delete_deployment_target("openai_primary") is True
    assert registry.get_deployment_target("openai_primary") == {}


def test_provider_registry_raises_on_invalid_existing_json(tmp_path):
    path = tmp_path / "providers.local.json"
    broken = '{"version": 1, "providers": {"gmn": {"api_key": "sk-test"}}'
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(RuntimeError):
        ProviderRegistry(path=str(path))

    # Invalid file should be left untouched for manual recovery.
    assert path.read_text(encoding="utf-8") == broken


def test_provider_registry_redacts_sensitive_headers_and_preserves_masked_values(tmp_path):
    path = tmp_path / "providers.local.json"
    registry = ProviderRegistry(path=str(path))

    registry.upsert_provider(
        "gmn",
        {
            "base_url": "https://gateway.example/v1",
            "api_key": "sk-real",
            "default_model": "gpt-5.2",
            "headers": {
                "Authorization": "Bearer real-token",
                "X-Api-Key": "header-key",
                "User-Agent": "GazerTest/1.0",
            },
        },
    )

    redacted = registry.list_redacted_providers()
    headers = redacted["gmn"]["headers"]
    assert redacted["gmn"]["api_key"] == "***"
    assert headers["Authorization"] == "***"
    assert headers["X-Api-Key"] == "***"
    assert headers["User-Agent"] == "GazerTest/1.0"

    # Simulate UI round-trip with masked values.
    registry.upsert_provider(
        "gmn",
        {
            "base_url": "https://gateway.example/v1",
            "api_key": "***",
            "default_model": "gpt-5.2",
            "headers": {
                "Authorization": "***",
                "X-Api-Key": "***",
                "User-Agent": "GazerTest/2.0",
            },
        },
    )
    saved = registry.get_provider("gmn")
    assert saved["api_key"] == "sk-real"
    assert saved["headers"]["Authorization"] == "Bearer real-token"
    assert saved["headers"]["X-Api-Key"] == "header-key"
    assert saved["headers"]["User-Agent"] == "GazerTest/2.0"
