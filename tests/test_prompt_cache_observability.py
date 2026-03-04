from pathlib import Path
from types import SimpleNamespace

import pytest

import runtime.config_manager as config_manager
import tools.admin.api_facade as admin_api
from agent.loop import AgentLoop
from bus.queue import MessageBus
from llm.base import LLMResponse


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key_path: str, default=None):
        cur = self.data
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class _RetryProvider:
    def __init__(self):
        self.calls = 0

    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="network timeout",
                finish_reason="error",
                error=True,
                model=model or "dummy-model",
            )
        return LLMResponse(content="ok", model=model or "dummy-model")


@pytest.mark.asyncio
async def test_prompt_cache_tracks_hit_on_llm_retry(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        config_manager,
        "config",
        _FakeConfig(
            {
                "security": {
                    "llm_max_retries": 1,
                    "llm_retry_backoff_seconds": 0.0,
                    "retry_budget_total": 8,
                    "rate_limit_requests": 20,
                    "rate_limit_window": 60.0,
                },
                "models": {
                    "prompt_cache": {
                        "enabled": True,
                        "ttl_seconds": 300,
                        "max_items": 64,
                        "segment_policy": "stable_prefix",
                    }
                },
            }
        ),
    )

    provider = _RetryProvider()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
    )

    response = await loop._call_llm_with_retries(
        messages=[
            {"role": "system", "content": "You are Gazer."},
            {"role": "user", "content": "hello"},
        ],
        tools=[],
        model="dummy-model",
        call_name="test",
        retry_budget=loop._build_retry_budget(),
    )
    assert response.error is False
    assert response.content == "ok"

    summary = loop.prompt_cache.summary()
    assert summary["lookups"] == 2
    assert summary["hits"] == 1
    assert summary["misses"] == 1


@pytest.mark.asyncio
async def test_usage_stats_includes_prompt_cache(monkeypatch):
    monkeypatch.setattr(
        "tools.admin.system.get_usage_tracker",
        lambda: SimpleNamespace(summary=lambda: {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    )
    monkeypatch.setattr(
        "tools.admin.system.get_prompt_cache_tracker",
        lambda: SimpleNamespace(summary=lambda: {"enabled": True, "hits": 3, "lookups": 5}),
    )

    payload = await admin_api.get_usage_stats()
    assert payload["status"] == "ok"
    assert payload["usage"]["total_tokens"] == 15
    assert payload["prompt_cache"]["enabled"] is True
    assert payload["prompt_cache"]["hits"] == 3

