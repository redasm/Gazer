"""Tests for rendering.fence_registry."""

from __future__ import annotations

import pytest

from rendering.fence_registry import (
    FENCE_COMPONENT_MAP,
    RENDERABLE_FENCES,
    is_renderable_fence,
    resolve_fence_component,
)


class TestFenceRegistry:
    @pytest.mark.parametrize("lang,expected", [
        ("chart", "ChartBlock"),
        ("options", "OptionsBlock"),
        ("table", "TableBlock"),
        ("timeline", "TimelineBlock"),
        ("mermaid", "MermaidBlock"),
    ])
    def test_registered_fences_resolve(self, lang: str, expected: str) -> None:
        assert resolve_fence_component(lang) == expected
        assert is_renderable_fence(lang) is True

    @pytest.mark.parametrize("lang", ["CHART", "Chart", "  chart  "])
    def test_case_and_whitespace_insensitive(self, lang: str) -> None:
        assert resolve_fence_component(lang) == "ChartBlock"

    @pytest.mark.parametrize("lang", ["python", "js", "unknown", "", "foo.bar"])
    def test_unregistered_returns_none(self, lang: str) -> None:
        assert resolve_fence_component(lang) is None
        assert is_renderable_fence(lang) is False

    def test_non_string_input_returns_none(self) -> None:
        assert resolve_fence_component(None) is None  # type: ignore[arg-type]
        assert resolve_fence_component(123) is None  # type: ignore[arg-type]

    def test_map_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            FENCE_COMPONENT_MAP["new"] = "NewBlock"  # type: ignore[index]

    def test_renderable_set_matches_map(self) -> None:
        assert RENDERABLE_FENCES == frozenset(FENCE_COMPONENT_MAP.keys())
