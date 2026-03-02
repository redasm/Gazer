from fastapi import HTTPException

from tools.admin import workflows as admin_api


def test_validate_provider_entry_accepts_strict_api_mode_and_reasoning_param() -> None:
    payload = {
        "base_url": "https://gateway.example.com/v1",
        "api_key": "sk-test",
        "default_model": "gpt-5.2",
        "api": "openai-responses",
        "strict_api_mode": True,
        "reasoning_param": False,
    }

    validated = admin_api._validate_provider_entry("gmn", payload)

    assert validated["strict_api_mode"] is True
    assert validated["reasoning_param"] is False


def test_validate_provider_entry_rejects_non_boolean_strict_api_mode() -> None:
    payload = {
        "base_url": "https://gateway.example.com/v1",
        "default_model": "gpt-5.2",
        "strict_api_mode": "yes",
    }

    try:
        admin_api._validate_provider_entry("gmn", payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "strict_api_mode" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")


def test_validate_provider_entry_rejects_non_boolean_reasoning_param() -> None:
    payload = {
        "base_url": "https://gateway.example.com/v1",
        "default_model": "gpt-5.2",
        "reasoning_param": "on",
    }

    try:
        admin_api._validate_provider_entry("gmn", payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "reasoning_param" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")
