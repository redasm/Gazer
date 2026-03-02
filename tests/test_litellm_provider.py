import pytest
from types import SimpleNamespace

pytest.importorskip("litellm")

import llm.litellm_provider as litellm_provider_module
from llm.litellm_provider import LiteLLMProvider


def _provider() -> LiteLLMProvider:
    return LiteLLMProvider(
        api_key="test-key",
        api_base="https://example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
    )


def test_responses_api_requires_explicit_api_mode() -> None:
    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://example.com/v1",
        default_model="gpt-5.2",
        api_mode=None,
    )
    assert provider._should_use_responses_api("gpt-5.2") is False


def test_responses_input_uses_output_text_for_assistant_history() -> None:
    provider = _provider()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]

    converted = provider._messages_to_responses_input(messages)

    assert converted[0]["content"][0]["type"] == "input_text"
    assert converted[1]["content"][0]["type"] == "input_text"
    assert converted[2]["content"][0]["type"] == "output_text"
    assert converted[3]["content"][0]["type"] == "input_text"


def test_responses_input_maps_tool_calls_and_results() -> None:
    provider = _provider()
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": {"q": "rust"}},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "search",
            "content": "result payload",
        },
    ]

    converted = provider._messages_to_responses_input(messages)

    function_call_items = [item for item in converted if item.get("type") == "function_call"]
    function_call_output_items = [item for item in converted if item.get("type") == "function_call_output"]

    assert function_call_items
    assert function_call_items[0]["call_id"] == "call_1"
    assert function_call_items[0]["name"] == "search"
    assert '"q": "rust"' in function_call_items[0]["arguments"]

    assert function_call_output_items
    assert function_call_output_items[0]["call_id"] == "call_1"
    assert function_call_output_items[0]["output"] == "result payload"


def test_parse_responses_output_function_call() -> None:
    provider = _provider()
    response = SimpleNamespace(
        output_text="",
        output=[
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "canvas_snapshot",
                "arguments": "{\"format\":\"png\"}",
            }
        ],
        status="completed",
        id="resp_test",
        model="gpt-5.2",
    )

    parsed = provider._parse_response(response)

    assert parsed.has_tool_calls
    assert parsed.tool_calls[0].id == "call_2"
    assert parsed.tool_calls[0].name == "canvas_snapshot"
    assert parsed.tool_calls[0].arguments["format"] == "png"


@pytest.mark.asyncio
async def test_reasoning_param_skipped_for_non_openai_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        model_settings={"gpt-5.2": {"reasoning": True}},
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert "reasoning" not in captured


@pytest.mark.asyncio
async def test_reasoning_param_kept_for_official_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://api.openai.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        model_settings={"gpt-5.2": {"reasoning": True, "reasoning_supported": True}},
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert captured.get("reasoning") == {"enabled": True}


@pytest.mark.asyncio
async def test_responses_api_strict_mode_errors_when_aresponses_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_acompletion(**kwargs):
        raise AssertionError("acompletion should not be called in strict responses mode")

    monkeypatch.setattr(litellm_provider_module, "aresponses", None)
    monkeypatch.setattr(litellm_provider_module, "acompletion", _fake_acompletion)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        strict_api_mode=True,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert response.error is True
    assert "litellm.aresponses is unavailable" in response.content


@pytest.mark.asyncio
async def test_responses_api_non_strict_mode_falls_back_to_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def _fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            id="chatcmpl_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", None)
    monkeypatch.setattr(litellm_provider_module, "acompletion", _fake_acompletion)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        strict_api_mode=False,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert response.content == "ok"
    assert captured.get("model") == "gpt-5.2"


@pytest.mark.asyncio
async def test_reasoning_param_force_override_sends_for_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        model_settings={"gpt-5.2": {"reasoning": True}},
        reasoning_param=True,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert captured.get("reasoning") == {"enabled": True}


@pytest.mark.asyncio
async def test_reasoning_supported_false_skips_even_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://api.openai.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        model_settings={"gpt-5.2": {"reasoning": True, "reasoning_supported": False}},
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert "reasoning" not in captured


@pytest.mark.asyncio
async def test_auth_header_injected_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        auth_header=True,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert captured.get("extra_headers", {}).get("Authorization") == "Bearer test-key"


@pytest.mark.asyncio
async def test_auth_mode_api_key_injects_authorization_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        auth_mode="api-key",
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert captured.get("extra_headers", {}).get("Authorization") == "Bearer test-key"


@pytest.mark.asyncio
async def test_auth_mode_none_disables_auto_auth_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        auth_mode="none",
        auth_header=True,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert "Authorization" not in (captured.get("extra_headers") or {})


@pytest.mark.asyncio
async def test_auth_header_does_not_override_existing_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="ok",
            output=[],
            status="completed",
            id="resp_test",
            model="gpt-5.2",
        )

    monkeypatch.setattr(litellm_provider_module, "aresponses", _fake_aresponses)

    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
        default_model="gpt-5.2",
        api_mode="openai-responses",
        extra_headers={"Authorization": "Bearer manual-token", "X-Test": "1"},
        auth_header=True,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        model="gpt-5.2",
        max_tokens=64,
        temperature=0,
    )

    assert not response.error
    assert captured.get("extra_headers", {}).get("Authorization") == "Bearer manual-token"
    assert captured.get("extra_headers", {}).get("X-Test") == "1"
