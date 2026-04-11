from __future__ import annotations

import asyncio

from multi_agent.monitor_broadcast import fan_out_monitor_event


def test_fan_out_monitor_event_drops_oldest_when_queue_is_full() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)
    queue.put_nowait({"event": "old"})

    stale = fan_out_monitor_event([queue], {"event": "new"})

    assert stale == []
    assert queue.get_nowait()["event"] == "new"


def test_fan_out_monitor_event_marks_stale_queue_on_failure() -> None:
    class _BrokenQueue:
        def put_nowait(self, _item):
            raise RuntimeError("boom")

    broken = _BrokenQueue()
    stale = fan_out_monitor_event([broken], {"event": "new"})

    assert stale == [broken]
