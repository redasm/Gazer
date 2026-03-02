from fastapi import HTTPException

from tools.admin import workflows as admin_api


def test_validate_provider_entry_normalizes_provider_contract_fields() -> None:
    payload = {
        "baseUrl": "https://gmn.example.com/v1",
        "apiKey": "sk-test",
        "default_model": "gpt-5.3-codex",
        "api": "openai-responses",
        "auth": "api-key",
        "authHeader": True,
        "headers": {
            "User-Agent": "OpenClaw/2026.2.14",
            "Accept": "application/json",
        },
        "strictApiMode": True,
        "reasoningParam": False,
        "models": [
            {
                "id": "gpt-5.3-codex",
                "reasoning": True,
                "input": ["text", "image"],
                "cost": {"input": 1.75, "output": 14},
                "contextWindow": 400000,
                "maxTokens": 128000,
            }
        ],
        "agents": {
            "defaults": {
                "model": {"primary": "gmn/gpt-5.3-codex"},
                "models": {"gmn/gpt-5.3-codex": {"alias": "GPT 5.3 Codex"}},
                "workspace": "/root/.openclaw/workspace",
                "compaction": {"mode": "safeguard"},
                "maxConcurrent": 4,
                "subagents": {"maxConcurrent": 8},
            }
        },
    }

    validated = admin_api._validate_provider_entry("gmn", payload)

    assert validated["base_url"] == "https://gmn.example.com/v1"
    assert validated["api_key"] == "sk-test"
    assert validated["auth"] == "api-key"
    assert validated["authHeader"] is True
    assert validated["strict_api_mode"] is True
    assert validated["reasoning_param"] is False
    assert isinstance(validated["models"], list)
    assert isinstance(validated["agents"], dict)
    assert "baseUrl" not in validated
    assert "apiKey" not in validated
    assert "strictApiMode" not in validated
    assert "reasoningParam" not in validated


def test_validate_provider_entry_rejects_invalid_agents_shape() -> None:
    payload = {
        "base_url": "https://gmn.example.com/v1",
        "api_key": "sk-test",
        "default_model": "gpt-5.3-codex",
        "agents": [],
    }

    try:
        admin_api._validate_provider_entry("gmn", payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "agents" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")


def test_validate_provider_entry_rejects_unknown_fields() -> None:
    payload = {
        "base_url": "https://gmn.example.com/v1",
        "api_key": "sk-test",
        "default_model": "gpt-5.3-codex",
        "extraField": "unexpected",
    }

    try:
        admin_api._validate_provider_entry("gmn", payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "unknown fields" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")
