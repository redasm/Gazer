"""Gazer rendering protocol.

Public entry point for the message rendering infrastructure. Import
types and utilities from here rather than from submodules so downstream
code is insulated from internal refactors.

Current stage: **P0-1** — foundation types, fence registry, and parser.
Subsequent stages add ``ToolResult.render`` adoption, the frontend
component registry, and the channel-aware ``RenderRouter``.
"""

from src.rendering.constants import (
    MAX_BLOCKS_PER_MESSAGE,
    MAX_FALLBACK_PREVIEW_CHARS,
    MAX_RENDER_HINT_DATA_BYTES,
    PROTOCOL_VERSION,
)
from src.rendering.fence_registry import (
    FENCE_COMPONENT_MAP,
    RENDERABLE_FENCES,
    is_renderable_fence,
    resolve_fence_component,
)
from src.rendering.parser import MessageParser
from src.rendering.types import (
    CodeBlock,
    MessageBlock,
    RenderBlock,
    RenderHint,
    RenderHintError,
    TextBlock,
)

__all__ = [
    "PROTOCOL_VERSION",
    "MAX_BLOCKS_PER_MESSAGE",
    "MAX_RENDER_HINT_DATA_BYTES",
    "MAX_FALLBACK_PREVIEW_CHARS",
    "RenderHint",
    "RenderHintError",
    "TextBlock",
    "CodeBlock",
    "RenderBlock",
    "MessageBlock",
    "FENCE_COMPONENT_MAP",
    "RENDERABLE_FENCES",
    "resolve_fence_component",
    "is_renderable_fence",
    "MessageParser",
]
