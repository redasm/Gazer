import pytest

from llm.base import LLMProvider, LLMResponse
from llm.router import RouterProvider, ProviderRoute


class _StubProvider(LLMProvider):
    def __init__(self, *, ok: bool, name: str):
        super().__init__()
        self.ok = ok
        self.name = name
        self.calls = 0

    def get_default_model(self) -> str:
        return f"{self.name}-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.calls += 1
        if not self.ok:
            return LLMResponse(content=f"{self.name} failed", finish_reason="error", error=True)
        return LLMResponse(content=f"{self.name} ok", finish_reason="stop", error=False)


@pytest.mark.asyncio
async def test_router_falls_back_to_next_provider():
    p1 = _StubProvider(ok=False, name="p1")
    p2 = _StubProvider(ok=True, name="p2")
    router = RouterProvider(
        [
            ProviderRoute(name="p1", provider=p1, default_model=p1.get_default_model()),
            ProviderRoute(name="p2", provider=p2, default_model=p2.get_default_model()),
        ],
        strategy="priority",
    )

    resp = await router.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.error is False
    assert "p2 ok" in (resp.content or "")
    assert p1.calls == 1
    assert p2.calls == 1


def test_router_status_and_strategy_update():
    p = _StubProvider(ok=True, name="p")
    router = RouterProvider([ProviderRoute(name="p", provider=p, default_model="m")], strategy="priority")
    status = router.get_status()
    assert status["strategy"] == "priority"
    assert status["total_calls"] == 0
    assert status["total_failures"] == 0
    assert status["avg_latency_ms"] == 0.0
    router.set_strategy("latency")
    assert router.get_status()["strategy"] == "latency"


@pytest.mark.asyncio
async def test_router_budget_blocks_when_call_limit_exceeded():
    p = _StubProvider(ok=True, name="budgeted")
    router = RouterProvider(
        [ProviderRoute(name="budgeted", provider=p, default_model="m")],
        strategy="priority",
        budget_policy={"enabled": True, "window_seconds": 60, "max_calls": 1, "max_cost_usd": 10.0},
    )

    first = await router.chat(messages=[{"role": "user", "content": "hello"}])
    second = await router.chat(messages=[{"role": "user", "content": "world"}])

    assert first.error is False
    assert second.error is True
    assert "budget policy" in (second.content or "").lower()
    status = router.get_status()
    assert status["budget"]["enabled"] is True
    assert status["budget"]["used_calls"] == 1


@pytest.mark.asyncio
async def test_router_capacity_profile_blocks_when_rpm_exceeded():
    p = _StubProvider(ok=True, name="cap")
    router = RouterProvider(
        [ProviderRoute(name="cap", provider=p, default_model="m", capacity_rpm=1)],
        strategy="priority",
    )

    first = await router.chat(messages=[{"role": "user", "content": "first"}])
    second = await router.chat(messages=[{"role": "user", "content": "second"}])

    assert first.error is False
    assert second.error is True
    assert "failed" in (second.content or "").lower() or "blocked" in (second.content or "").lower()
    status = router.get_status()
    provider = status["providers"][0]
    assert provider["capacity_rpm"] == 1


@pytest.mark.asyncio
async def test_router_outlier_ejection_blocks_route_temporarily():
    p = _StubProvider(ok=False, name="unstable")
    router = RouterProvider(
        [ProviderRoute(name="unstable_t1", provider_name="unstable", provider=p, default_model="m")],
        strategy="priority",
        outlier_policy={"enabled": True, "failure_threshold": 2, "cooldown_seconds": 60},
    )

    first = await router.chat(messages=[{"role": "user", "content": "a"}])
    second = await router.chat(messages=[{"role": "user", "content": "b"}])
    third = await router.chat(messages=[{"role": "user", "content": "c"}])

    assert first.error is True
    assert second.error is True
    assert third.error is True
    assert p.calls == 2  # third call should be skipped due to ejection

    status = router.get_status()
    provider = status["providers"][0]
    assert provider["ejected"] is True
    assert provider["target_type"] == "provider"


@pytest.mark.asyncio
async def test_router_probe_routes_reports_target_health_fields():
    p = _StubProvider(ok=True, name="probe")
    router = RouterProvider(
        [
            ProviderRoute(
                name="probe_t1",
                provider_name="probe",
                target_type="gateway",
                provider=p,
                default_model="m",
            )
        ],
        strategy="priority",
    )

    probes = await router.probe_routes(active=False)
    assert len(probes) == 1
    assert probes[0]["name"] == "probe_t1"
    assert probes[0]["provider_name"] == "probe"
    assert probes[0]["target_type"] == "gateway"
    assert probes[0]["healthy"] is True
