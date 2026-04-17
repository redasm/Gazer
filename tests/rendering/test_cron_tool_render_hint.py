"""Verify CronTool._list emits a TableBlock RenderHint inside a scope."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List

from src.rendering.types import RenderHint
from tools.base import RenderHintScope
from tools.cron_tool import CronTool


@dataclass
class _FakeJob:
    id: str
    name: str
    cron_expr: str
    enabled: bool
    agent_id: str


class _FakeScheduler:
    def __init__(self, jobs: List[_FakeJob]) -> None:
        self._jobs = jobs

    def list_jobs(self) -> List[_FakeJob]:
        return list(self._jobs)


class TestCronToolRenderHint:
    def test_list_emits_table_block_with_rows(self) -> None:
        jobs = [
            _FakeJob("j1", "daily-summary", "0 9 * * *", True, "main"),
            _FakeJob("j2", "weekly-report", "0 10 * * 1", False, "alt"),
        ]
        tool = CronTool(_FakeScheduler(jobs))

        with RenderHintScope() as scope:
            text = asyncio.run(tool.execute(action="list"))

        assert "j1: daily-summary" in text
        assert "j2: weekly-report" in text
        assert len(scope.hints) == 1

        hint = scope.hints[0]
        assert isinstance(hint, RenderHint)
        assert hint.component == "TableBlock"
        assert hint.data["columns"] == ["id", "name", "schedule", "status", "agent"]
        assert [r["id"] for r in hint.data["rows"]] == ["j1", "j2"]
        assert hint.data["rows"][0]["status"] == "enabled"
        assert hint.data["rows"][1]["status"] == "disabled"
        assert hint.fallback_text == text

    def test_list_empty_does_not_emit_hint(self) -> None:
        tool = CronTool(_FakeScheduler([]))

        with RenderHintScope() as scope:
            text = asyncio.run(tool.execute(action="list"))

        assert "No cron jobs" in text
        assert scope.hints == []

    def test_list_outside_scope_does_not_raise(self) -> None:
        """Legacy call path without a scope must still succeed silently."""
        jobs = [_FakeJob("j1", "x", "* * * * *", True, "main")]
        tool = CronTool(_FakeScheduler(jobs))
        text = asyncio.run(tool.execute(action="list"))
        assert "j1" in text
