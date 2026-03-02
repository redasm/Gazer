"""BrainHint-based dual-brain router.

Callers declare *what they need* via ``BrainHint``; the router decides
*which brain* to use based on a three-dimensional decision matrix:

    1. Latency constraint   (strict → fast brain, forced)
    2. Output quality need   (high / deep reasoning → slow brain)
    3. Reasoning depth       (1=reactive, 2=moderate, 3=planning/analysis)

Priority rules (highest first):
    - Any dimension forces fast brain  → fast brain
    - quality_critical OR depth >= 3   → slow brain
    - depth == 2                       → slow brain (load-aware in future)
    - Otherwise                        → fast brain
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from llm.base import LLMProvider, LLMResponse


@dataclass(frozen=True)
class BrainHint:
    """Caller declares intent; the router picks the brain.

    Callers only think about "what I need", never about "which model".
    """

    latency_critical: bool = False
    quality_critical: bool = False
    reasoning_depth: int = 1  # 1=reactive, 2=moderate, 3=deep/planning


# Convenience constants for common call-site patterns
HINT_FAST = BrainHint(latency_critical=True)
HINT_DEEP = BrainHint(quality_critical=True, reasoning_depth=3)
HINT_DEFAULT = BrainHint()


class DualBrainRouter:
    """Routes LLM calls to the appropriate brain based on ``BrainHint``.

    Wraps a slow (reasoning) provider and a fast (reactive) provider.
    All multi-agent code should go through this router rather than
    calling providers directly.
    """

    def __init__(
        self,
        slow_provider: LLMProvider,
        fast_provider: Optional[LLMProvider] = None,
        fast_model: Optional[str] = None,
    ) -> None:
        self._slow = slow_provider
        self._fast = fast_provider or slow_provider
        self._fast_model = fast_model

    def pick(self, hint: BrainHint) -> tuple[LLMProvider, Optional[str]]:
        """Return ``(provider, model_override)`` for the given hint."""
        if hint.latency_critical:
            return self._fast, self._fast_model

        if hint.quality_critical or hint.reasoning_depth >= 3:
            return self._slow, None

        if hint.reasoning_depth == 2:
            return self._slow, None

        return self._fast, self._fast_model

    # ------------------------------------------------------------------
    # High-level generate helpers
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        system: str = "",
        hint: BrainHint = HINT_DEFAULT,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Simple text generation routed by *hint*."""
        provider, model = self.pick(hint)
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await provider.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.content or ""

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        hint: BrainHint = HINT_DEFAULT,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Full chat with tool-call support, routed by *hint*."""
        provider, model = self.pick(hint)
        return await provider.chat(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
