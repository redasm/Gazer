"""Tests for soul.personality.identity_constitution — Issue-11 acceptance criteria.

Verifies:
  - ``_check_hard_bounds`` blocks extreme agreeableness/neuroticism values.
  - LLM soft check failure (exception) defaults to pass.
"""

import pytest

from soul.personality.identity_constitution import IdentityConstitution
from soul.personality.personality_vector import PersonalityVector


class FakeDictLLM:
    """Mock LLM returning a dict structure."""

    def __init__(self, response: dict) -> None:
        self._response = response
        self.call_count = 0

    async def call_structured(self, prompt: str) -> dict:
        self.call_count += 1
        return self._response


class FailingLLM:
    """Mock LLM that raises an Exception."""

    async def call_structured(self, prompt: str) -> dict:
        raise RuntimeError("API timeout")


class TestIdentityConstitution:
    @pytest.mark.asyncio
    async def test_hard_bounds_rejects_extreme_agreeableness(self) -> None:
        llm = FakeDictLLM({"pass": True})
        constitution = IdentityConstitution(llm_client=llm)

        before = PersonalityVector(agreeableness=0.5)
        # 0.9 exceeds the 0.85 max bound for agreeableness
        after = PersonalityVector(agreeableness=0.9)

        result = await constitution.validate(before, after)
        assert not result.passed
        assert "agreeableness" in result.reason
        assert result.violated_rule == "边界完整原则"
        assert llm.call_count == 0  # Should short-circuit before LLM call

    @pytest.mark.asyncio
    async def test_hard_bounds_rejects_extreme_neuroticism(self) -> None:
        llm = FakeDictLLM({"pass": True})
        constitution = IdentityConstitution(llm_client=llm)

        before = PersonalityVector(neuroticism=0.5)
        # 0.9 exceeds the 0.80 max bound for neuroticism
        after = PersonalityVector(neuroticism=0.9)

        result = await constitution.validate(before, after)
        assert not result.passed
        assert "neuroticism" in result.reason
        assert llm.call_count == 0

    @pytest.mark.asyncio
    async def test_soft_check_passes(self) -> None:
        llm = FakeDictLLM({"pass": True})
        constitution = IdentityConstitution(llm_client=llm)

        before = PersonalityVector(agreeableness=0.5)
        after = PersonalityVector(agreeableness=0.6)  # Within (0.1, 0.85)

        result = await constitution.validate(before, after)
        assert result.passed
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_soft_check_rejects(self) -> None:
        llm = FakeDictLLM({
            "pass": False,
            "reason": "AI过度妥协",
            "rule": "独立判断原则"
        })
        constitution = IdentityConstitution(llm_client=llm)

        before = PersonalityVector(agreeableness=0.5)
        after = PersonalityVector(agreeableness=0.8)  # Within hard bounds

        result = await constitution.validate(before, after)
        assert not result.passed
        assert result.reason == "AI过度妥协"
        assert result.violated_rule == "独立判断原则"
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_llm_failure_defaults_to_pass(self) -> None:
        llm = FailingLLM()
        constitution = IdentityConstitution(llm_client=llm)

        before = PersonalityVector(agreeableness=0.5)
        after = PersonalityVector(agreeableness=0.6)  # Within hard bounds

        result = await constitution.validate(before, after)
        # Must pass (fallback) if LLM crashes
        assert result.passed
