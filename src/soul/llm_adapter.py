"""Unified LLM client adapter.

Provides a ``LLMClient`` protocol and a concrete ``AsyncOpenAIAdapter``
so that consumers (``SessionDistiller``, ``IdentityConstitution``,
``APOOptimizer``, etc.) depend on a lightweight interface rather than
coupling directly to OpenAI internals.
"""

from __future__ import annotations

import json as _json
import re
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal async LLM interface expected by soul subsystems."""

    async def call(self, prompt: str) -> str: ...

    async def call_structured(self, prompt: str, **kwargs: Any) -> Any: ...


class AsyncOpenAIAdapter:
    """Thin adapter on top of an ``AsyncOpenAI`` client instance.

    Parameters
    ----------
    openai_client:
        An ``openai.AsyncOpenAI`` instance (or compatible).
    model:
        Model identifier to pass in API calls.
    temperature:
        Default sampling temperature.
    """

    def __init__(
        self,
        openai_client: Any,
        model: str,
        *,
        temperature: float = 0.0,
    ) -> None:
        self._client = openai_client
        self._model = model
        self._temperature = temperature

    async def call(self, prompt: str) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
        )
        return resp.choices[0].message.content or ""

    async def call_structured(self, prompt: str, **_kwargs: Any) -> Any:
        raw = await self.call(prompt)
        text = raw.strip()
        # Strip markdown code-fence if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        # Extract first JSON object or array from the response
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if match:
            text = match.group(1)
        return _json.loads(text)
