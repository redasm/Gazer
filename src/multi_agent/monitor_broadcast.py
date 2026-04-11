from __future__ import annotations

import asyncio
import copy
from typing import Any


def fan_out_monitor_event(
    subscribers: list[asyncio.Queue[dict[str, Any]]],
    envelope: dict[str, Any],
) -> list[asyncio.Queue[dict[str, Any]]]:
    """Broadcast a monitor event and return any stale queues to prune."""
    stale: list[asyncio.Queue[dict[str, Any]]] = []
    for queue in subscribers:
        try:
            queue.put_nowait(copy.deepcopy(envelope))
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(copy.deepcopy(envelope))
            except Exception:
                stale.append(queue)
        except Exception:
            stale.append(queue)
    return stale
