"""Tests for native coding tool operations (fuzzy matching, parameter aliases)."""

from pathlib import Path

import pytest

from tools.coding import EditFileTool
from tools.coding_impl.native_ops import (
    AmbiguousMatchError,
    NoMatchError,
    apply_edit,
)


def test_apply_edit_exact_match() -> None:
    content = "hello world\nfoo bar\n"
    result = apply_edit(content, "foo bar", "baz qux")
    assert result.match_type == "exact"
    assert "baz qux" in result.new_content
    assert "foo bar" not in result.new_content


def test_apply_edit_fuzzy_whitespace_match() -> None:
    content = "value = 1    +    2\n"
    result = apply_edit(content, "value = 1 + 2", "value = 3")
    assert result.match_type == "fuzzy"
    assert "value = 3" in result.new_content


def test_apply_edit_fuzzy_smart_quotes() -> None:
    content = "say \u201chello\u201d\n"
    result = apply_edit(content, 'say "hello"', 'say "world"')
    assert result.match_type == "fuzzy"
    assert 'say "world"' in result.new_content


def test_apply_edit_ambiguous_raises() -> None:
    content = "alpha\nalpha\n"
    with pytest.raises(AmbiguousMatchError) as exc_info:
        apply_edit(content, "alpha", "beta")
    assert exc_info.value.count == 2


def test_apply_edit_no_match_raises() -> None:
    content = "hello world\n"
    with pytest.raises(NoMatchError):
        apply_edit(content, "does not exist", "replacement")


@pytest.mark.asyncio
async def test_edit_file_fuzzy_match(tmp_path: Path) -> None:
    target = tmp_path / "fuzzy.txt"
    target.write_text("value = 1    +    2\n", encoding="utf-8")

    tool = EditFileTool(tmp_path)
    result = await tool.execute(
        path="fuzzy.txt",
        old_text="value = 1 + 2",
        new_text="value = 3",
    )

    assert "replaced 1 occurrence" in result
    assert "value = 3" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_file_supports_claude_style_param_aliases(tmp_path: Path) -> None:
    target = tmp_path / "alias.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    tool = EditFileTool(tmp_path)
    result = await tool.execute(
        file_path="alias.txt",
        old_string="beta",
        new_string="gamma",
    )

    assert "replaced 1 occurrence" in result
    assert "gamma" in target.read_text(encoding="utf-8")
