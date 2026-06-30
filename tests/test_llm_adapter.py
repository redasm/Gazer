"""Tests for soul.llm_adapter — provider-backed structured LLM adapter."""

import pytest

from soul.llm_adapter import LLMProviderStructuredAdapter, _extract_json


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    """Minimal stand-in for llm.base.LLMProvider."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list = []

    async def chat(self, messages, tools=None, model=None):
        self.calls.append((messages, tools, model))
        return _FakeResponse(self._content)


class TestExtractJson:
    def test_plain_object(self) -> None:
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        raw = '```json\n{"pass": true, "reason": ""}\n```'
        assert _extract_json(raw) == {"pass": True, "reason": ""}

    def test_json_embedded_in_prose(self) -> None:
        raw = "Here you go: [1, 2, 3] hope that helps"
        assert _extract_json(raw) == [1, 2, 3]


class TestProviderAdapter:
    @pytest.mark.asyncio
    async def test_call_structured_parses_fenced_json(self) -> None:
        provider = _FakeProvider('```json\n{"openness": 0.1}\n```')
        adapter = LLMProviderStructuredAdapter(provider, model="m")
        result = await adapter.call_structured("prompt")
        assert result == {"openness": 0.1}
        # Provider was called with a user message and the configured model.
        assert provider.calls[0][2] == "m"

    @pytest.mark.asyncio
    async def test_call_returns_text(self) -> None:
        provider = _FakeProvider("hello")
        adapter = LLMProviderStructuredAdapter(provider)
        assert await adapter.call("hi") == "hello"
