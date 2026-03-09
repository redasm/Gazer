"""Failover LLM provider -- tries multiple providers in order with cooldown.

When a provider returns an error, it is "cooled down" for a configurable
period and the next provider in the chain is tried.
"""

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from llm.base import LLMProvider, LLMResponse

logger = logging.getLogger("FailoverProvider")

DEFAULT_COOLDOWN_SECONDS = 60


class FailoverProvider(LLMProvider):
    """Wraps multiple ``LLMProvider`` instances with automatic failover.

    Usage::

        fp = FailoverProvider([
            (primary_provider, "primary_model"),
            (fallback_provider, "fallback_model"),
        ])
        response = await fp.chat(messages=msgs, tools=tools)
    """

    def __init__(
        self,
        providers: List[Tuple[LLMProvider, str]],
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        super().__init__()
        if not providers:
            raise ValueError("At least one provider is required")
        self._providers = providers  # List of (provider, default_model)
        self._cooldown = cooldown_seconds
        # Track cooldowns: index -> timestamp when cooldown expires
        self._cooled: Dict[int, float] = {}

    def get_default_model(self) -> str:
        return self._providers[0][1]

    def _is_cooled(self, idx: int) -> bool:
        expire = self._cooled.get(idx, 0)
        return time.monotonic() < expire

    def _cool_down(self, idx: int) -> None:
        self._cooled[idx] = time.monotonic() + self._cooldown
        provider_name = type(self._providers[idx][0]).__name__
        logger.warning(
            "Provider #%s (%s) cooled down for %ss", idx, provider_name, self._cooldown,
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        last_error: Optional[LLMResponse] = None

        for idx, (provider, default_model) in enumerate(self._providers):
            if self._is_cooled(idx):
                logger.debug("Skipping cooled-down provider #%s", idx)
                continue

            use_model = model or default_model
            try:
                response = await provider.chat(
                    messages=messages,
                    tools=tools,
                    model=use_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if response.error:
                    logger.warning("Provider #%s returned error: %s", idx, response.content)
                    self._cool_down(idx)
                    last_error = response
                    continue
                return response
            except Exception as exc:
                logger.warning("Provider #%s raised exception: %s", idx, exc)
                self._cool_down(idx)
                last_error = LLMResponse(
                    content=f"Error: {exc}",
                    finish_reason="error",
                    error=True,
                )

        # All providers exhausted
        if last_error:
            return last_error
        return LLMResponse(
            content="All LLM providers are unavailable.",
            finish_reason="error",
            error=True,
        )

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream from the first available provider."""
        for idx, (provider, default_model) in enumerate(self._providers):
            if self._is_cooled(idx):
                continue
            use_model = model or default_model
            try:
                had_content = False
                async for chunk in provider.stream_chat(
                    messages=messages,
                    tools=tools,
                    model=use_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    had_content = True
                    yield chunk
                if had_content:
                    return
            except Exception as exc:
                logger.warning("Stream from provider #%s failed: %s", idx, exc)
                self._cool_down(idx)

        yield "\n[All LLM providers are unavailable]"
