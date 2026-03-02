import collections

import pytest

from tools.admin import workflows as admin_api


def test_consume_satellite_frame_budget_enforces_limit() -> None:
    state = {"frames": collections.deque(), "total_bytes": 0}
    ok1 = admin_api._consume_satellite_frame_budget(
        state=state,
        size_bytes=100,
        now_ts=1.0,
        window_seconds=1.0,
        max_bytes_per_window=200,
    )
    ok2 = admin_api._consume_satellite_frame_budget(
        state=state,
        size_bytes=120,
        now_ts=1.1,
        window_seconds=1.0,
        max_bytes_per_window=200,
    )
    assert ok1 is True
    assert ok2 is False

    # Outside window, budget is released.
    ok3 = admin_api._consume_satellite_frame_budget(
        state=state,
        size_bytes=120,
        now_ts=2.5,
        window_seconds=1.0,
        max_bytes_per_window=200,
    )
    assert ok3 is True


@pytest.mark.asyncio
async def test_satellite_session_status_endpoint(monkeypatch):
    class _Meta:
        def __init__(self):
            self.version = "1"
            self.authenticated = True
            self.connected_at = 100.0
            self.last_heartbeat_ts = 110.0
            self.client_ip = "127.0.0.1"

    class _Mgr:
        backend = "rust"

        async def prune_stale_sessions(self):
            return None

        def get_runtime_status(self):
            return {"backend": "rust", "online_nodes": 1}

        def list_nodes(self):
            return {"sat-01": _Meta()}

    monkeypatch.setattr(admin_api, "SATELLITE_SESSION_MANAGER", _Mgr())
    payload = await admin_api.get_satellite_session_status()
    assert payload["status"] == "ok"
    assert payload["backend"] == "rust"
    assert payload["manager"]["online_nodes"] == 1
    assert payload["nodes"]["sat-01"]["authenticated"] is True
