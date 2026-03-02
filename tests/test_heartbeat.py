"""Tests for scheduler.heartbeat -- HeartbeatRunner."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock
from scheduler.heartbeat import HeartbeatRunner, HEARTBEAT_OK


@pytest.fixture
def runner(tmp_dir):
    callback = AsyncMock(return_value=HEARTBEAT_OK)
    return HeartbeatRunner(
        workspace=tmp_dir,
        run_callback=callback,
        interval_seconds=60,
    )


class TestHeartbeatRunner:
    def test_heartbeat_file_path(self, runner, tmp_dir):
        assert runner.heartbeat_file == tmp_dir / "HEARTBEAT.md"

    def test_load_checklist_missing(self, runner):
        assert runner._load_checklist() is None

    def test_load_checklist_exists(self, runner, tmp_dir):
        hb = tmp_dir / "HEARTBEAT.md"
        hb.write_text("- [ ] Check email\n- [ ] Check calendar", encoding="utf-8")
        content = runner._load_checklist()
        assert "Check email" in content

    def test_load_checklist_empty(self, runner, tmp_dir):
        hb = tmp_dir / "HEARTBEAT.md"
        hb.write_text("", encoding="utf-8")
        assert runner._load_checklist() is None

    @pytest.mark.asyncio
    async def test_tick_no_file(self, runner):
        await runner._tick()
        runner._run_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_with_file(self, runner, tmp_dir):
        hb = tmp_dir / "HEARTBEAT.md"
        hb.write_text("- [ ] Check email", encoding="utf-8")
        await runner._tick()
        runner._run_callback.assert_called_once()
        call_arg = runner._run_callback.call_args[0][0]
        assert "Heartbeat Check" in call_arg
        assert "Check email" in call_arg

    def test_stop(self, runner):
        runner._running = True
        runner.stop()
        assert runner._running is False
