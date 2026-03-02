"""DualBrain adapter — bridges the real LLMProvider interface to a
simpler generate()-style API used by the multi-agent system.

The existing Gazer codebase uses ``LLMProvider.chat(messages, tools, model)``
returning ``LLMResponse``.  The multi-agent design document assumes a
``dual_brain.fast_brain.generate(prompt)`` / ``slow_brain.generate(prompt)``
interface.  This module bridges the two without modifying any existing code.

The new ``generate(hint=...)`` / ``chat_with_tools(hint=...)`` methods
delegate routing to ``DualBrainRouter`` so callers declare intent rather
than picking a brain.  The legacy ``slow_generate`` / ``fast_generate``
remain for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from llm.base import LLMProvider, LLMResponse
from multi_agent.brain_router import (
    HINT_DEFAULT,
    HINT_DEEP,
    HINT_FAST,
    BrainHint,
    DualBrainRouter,
)

logger = logging.getLogger("multi_agent.DualBrain")


class DualBrain:
    """Wraps slow-brain and fast-brain LLM providers.

    Provides:
    - ``generate(hint=...)`` — hint-based routing via ``DualBrainRouter``
    - ``chat_with_tools(hint=...)`` — hint-based routing with tool support
    - ``slow_generate()`` / ``fast_generate()`` — legacy explicit calls
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
        self._router = DualBrainRouter(
            slow_provider=self._slow,
            fast_provider=self._fast,
            fast_model=self._fast_model,
        )

    @property
    def router(self) -> DualBrainRouter:
        return self._router

    # ------------------------------------------------------------------
    # Hint-based API (preferred)
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        system: str = "",
        hint: BrainHint = HINT_DEFAULT,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Text generation routed by *hint*."""
        return await self._router.generate(
            prompt=prompt,
            system=system,
            hint=hint,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        hint: BrainHint = HINT_DEFAULT,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Full chat with tool-call support, routed by *hint*."""
        return await self._router.chat_with_tools(
            messages=messages,
            tools=tools,
            hint=hint,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------
    # Legacy explicit fast/slow API (kept for backward compatibility)
    # ------------------------------------------------------------------

    async def slow_generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        return await self.generate(
            prompt=prompt,
            system=system,
            hint=HINT_DEEP,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def fast_generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        return await self.generate(
            prompt=prompt,
            system=system,
            hint=HINT_FAST,
            temperature=temperature,
            max_tokens=max_tokens,
        )
