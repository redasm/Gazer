"""Tests for the ToolResult + emit_render_hint side-channel in tools.base."""

from __future__ import annotations

import pytest

from rendering.types import RenderHint
from tools.base import (
    RenderHintScope,
    ToolResult,
    emit_render_hint,
    pop_render_hints,
)


class TestToolResult:
    def test_defaults(self) -> None:
        r = ToolResult(success=True, text="ok")
        assert r.success is True
        assert r.text == "ok"
        assert r.render is None
        assert r.error is None
        assert r.metadata == {}

    def test_with_render_hint(self) -> None:
        hint = RenderHint(component="X", data={}, fallback_text="x")
        r = ToolResult(success=True, text="ok", render=hint)
        assert r.render is hint


class TestRenderHintScope:
    def test_emit_outside_scope_returns_false_and_never_raises(self) -> None:
        hint = RenderHint(component="X", data={}, fallback_text="x")
        assert emit_render_hint(hint) is False

    def test_scope_collects_hints(self) -> None:
        hint = RenderHint(component="X", data={}, fallback_text="x")
        with RenderHintScope() as scope:
            assert emit_render_hint(hint) is True
            assert emit_render_hint(hint) is True
        assert len(scope.hints) == 2

    def test_scope_restores_on_exit(self) -> None:
        hint = RenderHint(component="X", data={}, fallback_text="x")
        with RenderHintScope():
            emit_render_hint(hint)
        # after exit, no active sink
        assert emit_render_hint(hint) is False

    def test_pop_drains_current_scope(self) -> None:
        hint = RenderHint(component="X", data={}, fallback_text="x")
        with RenderHintScope() as scope:
            emit_render_hint(hint)
            emit_render_hint(hint)
            drained = pop_render_hints()
            assert len(drained) == 2
            # sink now empty but scope still active
            assert emit_render_hint(hint) is True
            assert len(pop_render_hints()) == 1
        assert scope.hints == []

    def test_nested_scopes_isolated(self) -> None:
        inner_hint = RenderHint(component="inner", data={}, fallback_text="i")
        outer_hint = RenderHint(component="outer", data={}, fallback_text="o")
        with RenderHintScope() as outer:
            emit_render_hint(outer_hint)
            with RenderHintScope() as inner:
                emit_render_hint(inner_hint)
            assert [h.component for h in inner.hints] == ["inner"]
            emit_render_hint(outer_hint)
        assert [h.component for h in outer.hints] == ["outer", "outer"]

    def test_emit_rejects_non_renderhint(self) -> None:
        with RenderHintScope():
            with pytest.raises(TypeError):
                emit_render_hint("not a hint")  # type: ignore[arg-type]
