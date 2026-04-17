"""Tests for src.rendering.parser — MessageParser behavior.

Covers all must-have cases from REFACTOR_MESSAGE_RENDERING.md §4.1
plus additional edge cases to lock in the contract.
"""

from __future__ import annotations

import json

import pytest

from src.rendering.constants import MAX_BLOCKS_PER_MESSAGE
from src.rendering.parser import MessageParser
from src.rendering.types import RenderHint


@pytest.fixture
def parser() -> MessageParser:
    return MessageParser()


class TestMessageParser:
    def test_plain_text_becomes_single_text_block(self, parser: MessageParser) -> None:
        blocks = parser.parse("hello world")
        assert blocks == [{"type": "text", "markdown": "hello world"}]

    def test_empty_string_returns_empty_list(self, parser: MessageParser) -> None:
        assert parser.parse("") == []

    def test_whitespace_only_returns_empty_list(self, parser: MessageParser) -> None:
        assert parser.parse("   \n\t\n   ") == []

    def test_registered_fence_becomes_render_block(self, parser: MessageParser) -> None:
        text = '```chart\n{"type": "line", "series": [1,2,3]}\n```'
        blocks = parser.parse(text)
        assert len(blocks) == 1
        block = blocks[0]
        assert block["type"] == "render"
        assert block["component"] == "ChartBlock"
        assert block["data"] == {"type": "line", "series": [1, 2, 3]}
        assert "fallback" in block

    def test_unregistered_fence_becomes_code_block(self, parser: MessageParser) -> None:
        text = '```python\nprint("hi")\n```'
        blocks = parser.parse(text)
        assert blocks == [
            {"type": "code", "lang": "python", "code": 'print("hi")\n'}
        ]

    def test_invalid_json_in_fence_degrades_to_code_block(self, parser: MessageParser) -> None:
        text = '```chart\n{not valid json\n```'
        blocks = parser.parse(text)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "code"
        assert blocks[0]["lang"] == "chart"

    def test_non_object_json_in_fence_degrades_to_code_block(
        self, parser: MessageParser,
    ) -> None:
        text = "```chart\n[1, 2, 3]\n```"
        blocks = parser.parse(text)
        assert blocks[0]["type"] == "code"

    def test_text_before_and_after_fence_preserved(self, parser: MessageParser) -> None:
        text = 'intro\n```chart\n{"x": 1}\n```\noutro'
        blocks = parser.parse(text)
        assert len(blocks) == 3
        assert blocks[0] == {"type": "text", "markdown": "intro"}
        assert blocks[1]["type"] == "render"
        assert blocks[2] == {"type": "text", "markdown": "outro"}

    def test_multiple_fences_in_single_message(self, parser: MessageParser) -> None:
        text = (
            '```chart\n{"a": 1}\n```\n'
            'middle\n'
            '```options\n{"items": []}\n```'
        )
        blocks = parser.parse(text)
        types = [b["type"] for b in blocks]
        assert types == ["render", "text", "render"]
        assert blocks[0]["component"] == "ChartBlock"
        assert blocks[2]["component"] == "OptionsBlock"

    def test_render_hints_appended_after_fence_blocks(self, parser: MessageParser) -> None:
        hint = RenderHint(
            component="WeatherCard",
            data={"city": "Tokyo", "temp": 18},
            fallback_text="Tokyo 18°C",
        )
        text = '```chart\n{"a": 1}\n```'
        blocks = parser.parse(text, render_hints=[hint])
        assert len(blocks) == 2
        assert blocks[0]["component"] == "ChartBlock"
        assert blocks[1]["component"] == "WeatherCard"
        assert blocks[1]["fallback"] == "Tokyo 18°C"

    def test_empty_raw_text_with_only_hints(self, parser: MessageParser) -> None:
        hint = RenderHint(
            component="AgentTaskCard",
            data={"task": "do x", "status": "running"},
            fallback_text="doing x",
        )
        blocks = parser.parse("", render_hints=[hint])
        assert len(blocks) == 1
        assert blocks[0]["type"] == "render"
        assert blocks[0]["component"] == "AgentTaskCard"

    def test_hints_preserve_order(self, parser: MessageParser) -> None:
        hints = [
            RenderHint(component="A", data={}, fallback_text="a"),
            RenderHint(component="B", data={}, fallback_text="b"),
            RenderHint(component="C", data={}, fallback_text="c"),
        ]
        blocks = parser.parse("", render_hints=hints)
        assert [b["component"] for b in blocks] == ["A", "B", "C"]

    def test_malicious_fence_with_traversal_rejected(self, parser: MessageParser) -> None:
        """Unknown / malformed fence languages never escalate to renderable components."""
        text = '```../../etc/passwd\n{"x":1}\n```'
        blocks = parser.parse(text)
        # The fence regex requires \w+ for lang; a path-like lang does not match.
        # The whole payload stays as inert text — never a render block.
        assert all(b["type"] != "render" for b in blocks)

    def test_suspicious_but_valid_word_fence_still_unregistered(
        self, parser: MessageParser,
    ) -> None:
        text = '```eval\n{"cmd": "rm -rf"}\n```'
        blocks = parser.parse(text)
        assert blocks[0]["type"] == "code"

    def test_fence_with_trailing_whitespace_on_lang(self, parser: MessageParser) -> None:
        text = '```chart   \n{"a": 1}\n```'
        blocks = parser.parse(text)
        assert blocks[0]["type"] == "render"
        assert blocks[0]["component"] == "ChartBlock"

    def test_nested_fence_ignored_inside_outer_fence(self, parser: MessageParser) -> None:
        text = '```python\nprint("```nested```")\n```'
        blocks = parser.parse(text)
        assert blocks[0]["type"] == "code"
        assert blocks[0]["lang"] == "python"

    def test_fallback_preview_truncated(self, parser: MessageParser) -> None:
        long_json = json.dumps({"s": "x" * 500})
        text = f"```chart\n{long_json}\n```"
        blocks = parser.parse(text)
        assert blocks[0]["type"] == "render"
        assert len(blocks[0]["fallback"]) <= 200

    def test_excessive_blocks_are_truncated(self, parser: MessageParser) -> None:
        hints = [
            RenderHint(component=f"C{i}", data={}, fallback_text=f"c{i}")
            for i in range(MAX_BLOCKS_PER_MESSAGE + 5)
        ]
        blocks = parser.parse("", render_hints=hints)
        assert len(blocks) == MAX_BLOCKS_PER_MESSAGE
        assert blocks[-1]["type"] == "text"
        assert "省略" in blocks[-1]["markdown"]

    def test_none_input_handled(self, parser: MessageParser) -> None:
        assert parser.parse(None) == []  # type: ignore[arg-type]

    def test_parser_is_stateless(self, parser: MessageParser) -> None:
        first = parser.parse("hello")
        second = parser.parse("hello")
        assert first == second
