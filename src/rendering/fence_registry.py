"""Fence-block → frontend component mapping.

LLM output frequently carries structured payloads inside Markdown fenced
code blocks (e.g. ```chart ... ```). :class:`MessageParser` consults
this registry to decide whether a fence should become a
:class:`RenderBlock` or degrade to a plain :class:`CodeBlock`.

The mapping is intentionally narrow and curated. Adding a fence type
here is a protocol decision: the corresponding frontend component must
exist and validate its payload. Unknown fences always fall back to code
blocks — never throw, never drop the message.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

FENCE_COMPONENT_MAP: Mapping[str, str] = MappingProxyType({
    "chart": "ChartBlock",
    "options": "OptionsBlock",
    "table": "TableBlock",
    "timeline": "TimelineBlock",
    "mermaid": "MermaidBlock",
})

RENDERABLE_FENCES = frozenset(FENCE_COMPONENT_MAP.keys())


def resolve_fence_component(lang: str) -> str | None:
    """Return the registered component key for ``lang``, or ``None``.

    Matching is case-insensitive on the fence language tag.
    """
    if not isinstance(lang, str):
        return None
    return FENCE_COMPONENT_MAP.get(lang.strip().lower())


def is_renderable_fence(lang: str) -> bool:
    """Return True iff ``lang`` maps to a renderable component."""
    return resolve_fence_component(lang) is not None


__all__ = [
    "FENCE_COMPONENT_MAP",
    "RENDERABLE_FENCES",
    "resolve_fence_component",
    "is_renderable_fence",
]
