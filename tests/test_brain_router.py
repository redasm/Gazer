"""Tests for multi_agent.brain_router — BrainHint routing logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from multi_agent.brain_router import (
    HINT_DEFAULT,
    HINT_DEEP,
    HINT_FAST,
    BrainHint,
    DualBrainRouter,
)


@pytest.fixture
def slow():
    p = AsyncMock()
    p.chat = AsyncMock()
    return p


@pytest.fixture
def fast():
    p = AsyncMock()
    p.chat = AsyncMock()
    return p


@pytest.fixture
def router(slow, fast):
    return DualBrainRouter(slow_provider=slow, fast_provider=fast, fast_model="fast-v1")


class TestPick:
    def test_latency_critical_picks_fast(self, router, fast):
        provider, model = router.pick(BrainHint(latency_critical=True))
        assert provider is fast
        assert model == "fast-v1"

    def test_quality_critical_picks_slow(self, router, slow):
        provider, model = router.pick(BrainHint(quality_critical=True))
        assert provider is slow
        assert model is None

    def test_depth_3_picks_slow(self, router, slow):
        provider, model = router.pick(BrainHint(reasoning_depth=3))
        assert provider is slow

    def test_depth_2_picks_slow(self, router, slow):
        provider, model = router.pick(BrainHint(reasoning_depth=2))
        assert provider is slow

    def test_default_picks_fast(self, router, fast):
        provider, model = router.pick(BrainHint())
        assert provider is fast
        assert model == "fast-v1"

    def test_latency_overrides_quality(self, router, fast):
        """latency_critical takes highest priority even with quality_critical."""
        provider, model = router.pick(BrainHint(latency_critical=True, quality_critical=True))
        assert provider is fast

    def test_convenience_hints(self, router, fast, slow):
        p1, _ = router.pick(HINT_FAST)
        assert p1 is fast

        p2, _ = router.pick(HINT_DEEP)
        assert p2 is slow

        p3, _ = router.pick(HINT_DEFAULT)
        assert p3 is fast


class TestBrainHintImmutability:
    def test_frozen(self):
        h = BrainHint()
        with pytest.raises(AttributeError):
            h.latency_critical = True


@pytest.mark.asyncio
class TestGenerate:
    async def test_generate_routes_correctly(self, router, slow, fast):
        fast.chat = AsyncMock(return_value=MagicMock(content="fast answer"))
        slow.chat = AsyncMock(return_value=MagicMock(content="slow answer"))

        result_fast = await router.generate("hello", hint=HINT_FAST)
        assert result_fast == "fast answer"
        fast.chat.assert_awaited_once()

        result_slow = await router.generate("analyze this", hint=HINT_DEEP)
        assert result_slow == "slow answer"
        slow.chat.assert_awaited_once()

    async def test_chat_with_tools_routes(self, router, slow, fast):
        resp = MagicMock(content="tool result", tool_calls=[], has_tool_calls=False)
        fast.chat = AsyncMock(return_value=resp)

        result = await router.chat_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            hint=HINT_FAST,
        )
        assert result.content == "tool result"
        fast.chat.assert_awaited_once()
