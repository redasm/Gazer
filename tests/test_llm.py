"""Tests for llm.base and llm.failover."""

import pytest
from unittest.mock import AsyncMock
from llm.base import LLMResponse, ToolCallRequest, LLMProvider
from llm.failover import FailoverProvider


# ---------------------------------------------------------------------------
# LLMResponse / ToolCallRequest
# ---------------------------------------------------------------------------

class TestLLMResponse:
    def test_defaults(self):
        r = LLMResponse(content="hi")
        assert r.content == "hi"
        assert r.tool_calls == []
        assert r.finish_reason == "stop"
        assert r.usage == {}
        assert r.error is False
        assert r.has_tool_calls is False

    def test_has_tool_calls(self):
        tc = ToolCallRequest(id="1", name="echo", arguments={"text": "hi"})
        r = LLMResponse(content=None, tool_calls=[tc])
        assert r.has_tool_calls is True

    def test_error_response(self):
        r = LLMResponse(content="oops", error=True, finish_reason="error")
        assert r.error is True


class TestToolCallRequest:
    def test_fields(self):
        tc = ToolCallRequest(id="abc", name="search", arguments={"q": "test"})
        assert tc.id == "abc"
        assert tc.name == "search"
        assert tc.arguments == {"q": "test"}


# ---------------------------------------------------------------------------
# Stub provider for FailoverProvider tests
# ---------------------------------------------------------------------------

class StubProvider(LLMProvider):
    """Minimal concrete LLMProvider for testing."""

    def __init__(self, response: LLMResponse = None, raise_exc: Exception = None):
        super().__init__()
        self._response = response or LLMResponse(content="ok")
        self._raise = raise_exc
        self.call_count = 0

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.call_count += 1
        if self._raise:
            raise self._raise
        return self._response

    def get_default_model(self):
        return "stub-model"


# ---------------------------------------------------------------------------
# FailoverProvider
# ---------------------------------------------------------------------------

class TestFailoverProvider:
    def test_requires_providers(self):
        with pytest.raises(ValueError, match="At least one provider"):
            FailoverProvider(providers=[])

    def test_get_default_model(self):
        p = StubProvider()
        fp = FailoverProvider(providers=[(p, "my-model")])
        assert fp.get_default_model() == "my-model"

    @pytest.mark.asyncio
    async def test_success_first_provider(self):
        p = StubProvider(LLMResponse(content="first"))
        fp = FailoverProvider(providers=[(p, "m")])
        resp = await fp.chat(messages=[])
        assert resp.content == "first"
        assert p.call_count == 1

    @pytest.mark.asyncio
    async def test_failover_on_error_response(self):
        bad = StubProvider(LLMResponse(content="fail", error=True))
        good = StubProvider(LLMResponse(content="ok"))
        fp = FailoverProvider(providers=[(bad, "m1"), (good, "m2")])
        resp = await fp.chat(messages=[])
        assert resp.content == "ok"
        assert bad.call_count == 1
        assert good.call_count == 1

    @pytest.mark.asyncio
    async def test_failover_on_exception(self):
        bad = StubProvider(raise_exc=RuntimeError("boom"))
        good = StubProvider(LLMResponse(content="recovered"))
        fp = FailoverProvider(providers=[(bad, "m1"), (good, "m2")])
        resp = await fp.chat(messages=[])
        assert resp.content == "recovered"

    @pytest.mark.asyncio
    async def test_all_providers_fail(self):
        p1 = StubProvider(LLMResponse(content="err1", error=True))
        p2 = StubProvider(raise_exc=RuntimeError("err2"))
        fp = FailoverProvider(providers=[(p1, "m1"), (p2, "m2")])
        resp = await fp.chat(messages=[])
        assert resp.error is True

    @pytest.mark.asyncio
    async def test_cooldown_skips_provider(self):
        bad = StubProvider(raise_exc=RuntimeError("down"))
        good = StubProvider(LLMResponse(content="ok"))
        fp = FailoverProvider(providers=[(bad, "m1"), (good, "m2")], cooldown_seconds=60)

        # First call: bad fails, good succeeds
        resp1 = await fp.chat(messages=[])
        assert resp1.content == "ok"

        # Second call: bad should be skipped (cooled down)
        bad.call_count = 0
        good.call_count = 0
        resp2 = await fp.chat(messages=[])
        assert resp2.content == "ok"
        assert bad.call_count == 0  # Skipped!
        assert good.call_count == 1

    @pytest.mark.asyncio
    async def test_model_override(self):
        """When model is passed explicitly, it should be forwarded."""
        call_log = {}

        class TrackingProvider(LLMProvider):
            async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
                call_log["model"] = model
                return LLMResponse(content="ok")
            def get_default_model(self):
                return "default"

        fp = FailoverProvider(providers=[(TrackingProvider(), "fallback-model")])
        await fp.chat(messages=[], model="override-model")
        assert call_log["model"] == "override-model"

    @pytest.mark.asyncio
    async def test_no_model_uses_provider_default(self):
        call_log = {}

        class TrackingProvider(LLMProvider):
            async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
                call_log["model"] = model
                return LLMResponse(content="ok")
            def get_default_model(self):
                return "default"

        fp = FailoverProvider(providers=[(TrackingProvider(), "configured-model")])
        await fp.chat(messages=[])
        assert call_log["model"] == "configured-model"
