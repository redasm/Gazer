import pytest

from llm.base import LLMProvider, LLMResponse
from llm.router import ProviderRoute, RouterProvider


class _StubProvider(LLMProvider):
    def __init__(self, *, name: str):
        super().__init__()
        self.name = name
        self.calls = 0

    def get_default_model(self) -> str:
        return f"{self.name}-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.calls += 1
        return LLMResponse(content=f"{self.name} ok", finish_reason="stop", error=False)


@pytest.mark.asyncio
async def test_router_complexity_simple_prefers_lower_cost():
    low = _StubProvider(name="low")
    high = _StubProvider(name="high")
    router = RouterProvider(
        [
            ProviderRoute(
                name="high_route",
                provider=high,
                default_model="high-model",
                cost_tier="high",
                calls=10,
                successes=10,
            ),
            ProviderRoute(
                name="low_route",
                provider=low,
                default_model="low-model",
                cost_tier="low",
                calls=10,
                successes=4,
            ),
        ],
        strategy="priority",
        complexity_policy={"enabled": True, "simple_prefer_cost": True},
    )

    out = await router.chat(messages=[{"role": "user", "content": "hello"}])
    assert out.error is False
    assert "low ok" in (out.content or "")
    assert low.calls == 1
    assert high.calls == 0
    status = router.get_status()
    breakdown = status["complexity_routing"]["last"]["feature_breakdown"]
    assert isinstance(breakdown, dict)
    assert "message_size" in breakdown


@pytest.mark.asyncio
async def test_router_complexity_complex_prefers_higher_success_rate():
    low = _StubProvider(name="low")
    high = _StubProvider(name="high")
    router = RouterProvider(
        [
            ProviderRoute(
                name="low_route",
                provider=low,
                default_model="low-model",
                cost_tier="low",
                calls=10,
                successes=4,
            ),
            ProviderRoute(
                name="high_route",
                provider=high,
                default_model="high-model",
                cost_tier="high",
                calls=10,
                successes=9,
            ),
        ],
        strategy="priority",
        complexity_policy={"enabled": True, "complex_prefer_success_rate": True},
    )

    out = await router.chat(
        messages=[
            {"role": "user", "content": "上一轮先确认目标"},
            {
                "role": "assistant",
                "content": "先读取配置",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "read_file"}}],
            },
            {"role": "tool", "content": "配置读取完成"},
            {"role": "assistant", "content": "已完成预检查"},
            {
                "role": "user",
                "content": (
                    "请继续完成这个迁移任务，并在每一步说明风险和回滚策略：\n"
                    "1. 先梳理当前架构与依赖\n"
                    "2. 再制定迁移计划和验证步骤\n"
                    "3. 最后给出上线和回滚清单"
                ),
            }
        ]
    )
    assert out.error is False
    assert "high ok" in (out.content or "")
    assert high.calls == 1
    assert low.calls == 0

    status = router.get_status()
    assert status["complexity_routing"]["enabled"] is True
    assert status["complexity_routing"]["last"]["level"] == "complex"
    breakdown = status["complexity_routing"]["last"]["feature_breakdown"]
    assert breakdown["structure_density"]["contribution"] > 0
    assert breakdown["tool_need"]["contribution"] > 0
    assert status["complexity_routing"]["last"]["signals"]["marker_feature_enabled"] is False

