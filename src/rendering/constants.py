"""Rendering protocol constants.

Centralizes version identifiers, size limits, and guardrails used by
``RenderHint``/``MessageBlock`` pipelines. These values are part of the
public contract — change them only with a corresponding protocol bump.
"""

from __future__ import annotations

PROTOCOL_VERSION: str = "1.0"

MAX_BLOCKS_PER_MESSAGE: int = 20

MAX_RENDER_HINT_DATA_BYTES: int = 64 * 1024

MAX_FALLBACK_PREVIEW_CHARS: int = 200

BLOCK_TYPE_TEXT: str = "text"
BLOCK_TYPE_CODE: str = "code"
BLOCK_TYPE_RENDER: str = "render"
