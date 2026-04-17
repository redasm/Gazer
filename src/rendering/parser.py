"""Message parser: LLM raw text + tool render hints → ``MessageBlock[]``.

The parser is pure and synchronous. It has no knowledge of channels and
performs no I/O. Keeping it this way lets it run on the hot path of
every outbound message without amortized cost.

Parsing rules (documented in REFACTOR_MESSAGE_RENDERING.md §4.1):

1. Markdown fenced code blocks (``` ```lang\\n...\\n``` ```) are
   recognized.
2. Fences whose language is registered in :mod:`fence_registry` become
   :class:`RenderBlock` s — their body is parsed as JSON. Invalid JSON
   degrades gracefully to :class:`CodeBlock`.
3. Unknown fences become :class:`CodeBlock` s verbatim.
4. Text between / around fences becomes :class:`TextBlock` s (stripped,
   empties discarded).
5. All :class:`RenderHint` s are appended after the parsed blocks,
   preserving tool-call order.

A hard cap of :data:`MAX_BLOCKS_PER_MESSAGE` is enforced to protect the
frontend from runaway messages.
"""

from __future__ import annotations

import json
import re
from typing import Sequence

from rendering.constants import (
    MAX_BLOCKS_PER_MESSAGE,
    MAX_FALLBACK_PREVIEW_CHARS,
)
from rendering.fence_registry import resolve_fence_component
from rendering.types import MessageBlock, RenderHint

_FENCE_RE = re.compile(r"```(\w+)[ \t]*\n([\s\S]*?)```", re.MULTILINE)


class MessageParser:
    """Stateless parser — one instance can be reused across messages."""

    def parse(
        self,
        raw_text: str,
        render_hints: Sequence[RenderHint] = (),
    ) -> list[MessageBlock]:
        blocks: list[MessageBlock] = []
        text = raw_text or ""
        last_end = 0

        for match in _FENCE_RE.finditer(text):
            lang = match.group(1)
            content = match.group(2)
            start = match.start()

            if start > last_end:
                leading = text[last_end:start].strip()
                if leading:
                    blocks.append({"type": "text", "markdown": leading})

            component = resolve_fence_component(lang)
            if component is not None:
                try:
                    data = json.loads(content)
                except (json.JSONDecodeError, ValueError):
                    blocks.append({
                        "type": "code",
                        "lang": lang,
                        "code": content,
                    })
                else:
                    if not isinstance(data, dict):
                        blocks.append({
                            "type": "code",
                            "lang": lang,
                            "code": content,
                        })
                    else:
                        blocks.append({
                            "type": "render",
                            "component": component,
                            "data": data,
                            "fallback": content[:MAX_FALLBACK_PREVIEW_CHARS],
                        })
            else:
                blocks.append({
                    "type": "code",
                    "lang": lang,
                    "code": content,
                })

            last_end = match.end()

        tail = text[last_end:].strip()
        if tail:
            blocks.append({"type": "text", "markdown": tail})

        for hint in render_hints:
            blocks.append({
                "type": "render",
                "component": hint.component,
                "data": hint.data,
                "fallback": hint.fallback_text,
            })

        if len(blocks) > MAX_BLOCKS_PER_MESSAGE:
            truncated = blocks[:MAX_BLOCKS_PER_MESSAGE - 1]
            dropped = len(blocks) - len(truncated)
            truncated.append({
                "type": "text",
                "markdown": f"_(已省略 {dropped} 个渲染块)_",
            })
            blocks = truncated

        return blocks


__all__ = ["MessageParser"]
