"""Rendering protocol type definitions.

Defines the channel-agnostic data contract shared between tools, the
agent loop, render adapters, and the frontend:

- :class:`RenderHint` — a tool's semantic rendering intent.
- :data:`MessageBlock` — the union of discrete message fragments
  (text / code / render) produced by :class:`MessageParser`.

Only primitive JSON-serializable data is allowed. Backend construction
must guarantee ``json.dumps`` compatibility; the helpers in this module
validate it eagerly so problems surface at tool-author level rather than
at WebSocket send time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, Union

from rendering.constants import (
    MAX_RENDER_HINT_DATA_BYTES,
    PROTOCOL_VERSION,
)


class RenderHintError(ValueError):
    """Raised when a :class:`RenderHint` violates the protocol contract."""


@dataclass(frozen=True)
class RenderHint:
    """Channel-agnostic rendering intent returned by a tool.

    Attributes:
        component: Frontend registry key (e.g. ``"WeatherCard"``,
            ``"ChartBlock"``). Must be non-empty.
        data: Props forwarded to the component. Must be JSON serializable
            and stay within :data:`MAX_RENDER_HINT_DATA_BYTES` after
            serialization.
        fallback_text: Plain-text degradation used by CLI/SMS channels,
            screen readers, and LLM follow-up reasoning. Must be
            non-empty; empty values break accessibility and low-capability
            channels.
        version: Protocol version — defaults to the current
            :data:`PROTOCOL_VERSION`.
    """

    component: str
    data: dict[str, Any]
    fallback_text: str
    version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.component, str) or not self.component.strip():
            raise RenderHintError("component must be a non-empty string")
        if not isinstance(self.data, dict):
            raise RenderHintError("data must be a dict")
        if not isinstance(self.fallback_text, str) or not self.fallback_text:
            raise RenderHintError("fallback_text must be a non-empty string")
        if not isinstance(self.version, str) or not self.version:
            raise RenderHintError("version must be a non-empty string")

        try:
            serialized = json.dumps(self.data, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise RenderHintError(f"data is not JSON-serializable: {exc}") from exc

        if len(serialized.encode("utf-8")) > MAX_RENDER_HINT_DATA_BYTES:
            raise RenderHintError(
                f"data exceeds {MAX_RENDER_HINT_DATA_BYTES} bytes after serialization"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation suitable for JSON transport."""
        return {
            "component": self.component,
            "data": self.data,
            "fallback_text": self.fallback_text,
            "version": self.version,
        }


class TextBlock(TypedDict):
    type: Literal["text"]
    markdown: str


class CodeBlock(TypedDict):
    type: Literal["code"]
    lang: str
    code: str


class RenderBlock(TypedDict):
    type: Literal["render"]
    component: str
    data: dict[str, Any]
    fallback: str


MessageBlock = Union[TextBlock, CodeBlock, RenderBlock]


__all__ = [
    "RenderHint",
    "RenderHintError",
    "TextBlock",
    "CodeBlock",
    "RenderBlock",
    "MessageBlock",
]
