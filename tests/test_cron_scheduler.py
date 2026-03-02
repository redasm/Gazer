"""Tests for scheduler.cron -- CronScheduler, _cron_matches."""

import asyncio
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from scheduler.cron import CronScheduler, CronJob, _cron_matches, _field_matches


class TestCronMatches:
    def test_wildcard(self):
        dt = datetime(2026, 2, 8, 10, 30)
        assert _cron_matches("* * * * *", dt) is True

    def test_specific_minute(self):
        dt = datetime(2026, 2, 8, 10, 30)
        assert _cron_matches("30 * * * *", dt) is True
        assert _cron_matches("15 * * * *", dt) is False

    def test_specific_hour_minute(self):
        dt = datetime(2026, 2, 8, 14, 0)
        assert _cron_matches("0 14 * * *", dt) is True
        assert _cron_matches("0 15 * * *", dt) is False

    def test_step_values(self):
        dt = datetime(2026, 2, 8, 10, 0)
        assert _cron_matches("*/10 * * * *", dt) is True
        dt2 = datetime(2026, 2, 8, 10, 7)
        assert _cron_matches("*/10 * * * *", dt2) is False

    def test_range(self):
        dt = datetime(2026, 2, 8, 10, 0)
        assert _cron_matches("0 9-17 * * *", dt) is True
        dt_night = datetime(2026, 2, 8, 23, 0)
        assert _cron_matches("0 9-17 * * *", dt_night) is False

    def test_comma_separated(self):
        dt = datetime(2026, 2, 8, 10, 15)
        assert _cron_matches("15,30,45 * * * *", dt) is True
        dt2 = datetime(2026, 2, 8, 10, 20)
        assert _cron_matches("15,30,45 * * * *", dt2) is False

    def test_invalid_fields(self):
        assert _cron_matches("* * *", datetime.now()) is False  # Only 3 fields


class TestFieldMatches:
    def test_star(self):
        assert _field_matches("*", 5, 0, 59) is True

    def test_exact(self):
        assert _field_matches("5", 5, 0, 59) is True
        assert _field_matches("5", 6, 0, 59) is False

    def test_step(self):
        assert _field_matches("*/15", 0, 0, 59) is True
        assert _field_matches("*/15", 15, 0, 59) is True
        assert _field_matches("*/15", 7, 0, 59) is False

    def test_range(self):
        assert _field_matches("1-5", 3, 0, 6) is True
        assert _field_matches("1-5", 6, 0, 6) is False


class TestCronJob:
    def test_defaults(self):
        job = CronJob(name="test")
        assert job.enabled is True
        assert job.one_shot is False
        assert job.cron_expr == "0 * * * *"


class TestCronScheduler:
    @pytest.fixture
    def scheduler(self, tmp_dir):
        results = []

        async def callback(job):
            results.append(job.id)
            return "ok"

        store = tmp_dir / "jobs.json"
        sched = CronScheduler(run_callback=callback, store_path=store)
        sched._results = results  # Attach for test inspection
        return sched

    def test_add_and_list(self, scheduler):
        job = CronJob(name="hourly", cron_expr="0 * * * *", message="check")
        scheduler.add(job)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].name == "hourly"

    def test_remove(self, scheduler):
        job = CronJob(name="to_remove")
        scheduler.add(job)
        assert scheduler.remove(job.id) is True
        assert len(scheduler.list_jobs()) == 0

    def test_remove_nonexistent(self, scheduler):
        assert scheduler.remove("nonexistent") is False

    def test_edit(self, scheduler):
        job = CronJob(name="orig")
        scheduler.add(job)
        updated = scheduler.edit(job.id, name="edited")
        assert updated.name == "edited"

    def test_edit_nonexistent(self, scheduler):
        assert scheduler.edit("nope", name="x") is None

    def test_get(self, scheduler):
        job = CronJob(name="findme")
        scheduler.add(job)
        assert scheduler.get(job.id).name == "findme"
        assert scheduler.get("missing") is None

    def test_persistence(self, tmp_dir):
        store = tmp_dir / "jobs.json"

        async def noop(job):
            return None

        s1 = CronScheduler(run_callback=noop, store_path=store)
        s1.add(CronJob(name="persist_test", message="hi"))
        s1.save()

        s2 = CronScheduler(run_callback=noop, store_path=store)
        s2.load()
        assert len(s2.list_jobs()) == 1
        assert s2.list_jobs()[0].name == "persist_test"

    @pytest.mark.asyncio
    async def test_force_run(self, scheduler):
        job = CronJob(name="forced")
        scheduler.add(job)
        result = await scheduler.force_run(job.id)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_force_run_missing(self, scheduler):
        result = await scheduler.force_run("missing")
        assert result is None

    def test_stop(self, scheduler):
        scheduler._running = True
        scheduler.stop()
        assert scheduler._running is False
