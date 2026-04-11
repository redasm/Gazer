from __future__ import annotations

from types import SimpleNamespace

from runtime.brain import GazerBrain


class _CaptureManager:
    def __init__(self) -> None:
        self.sync_stop_calls = 0

    async def stop(self) -> None:
        raise AssertionError("async stop should not run without an event loop")

    def stop_sync(self) -> None:
        self.sync_stop_calls += 1


def test_brain_stop_falls_back_to_sync_capture_cleanup(monkeypatch) -> None:
    capture_manager = _CaptureManager()
    brain = object.__new__(GazerBrain)
    brain.is_running = True
    brain.spatial = None
    brain.body = SimpleNamespace(disconnect=lambda: None)
    brain.capture_manager = capture_manager
    brain.cron_scheduler = None
    brain.heartbeat_runner = None
    brain._gmail_push_manager = None
    brain._agent_task = None
    brain._cron_task = None
    brain._heartbeat_task = None
    brain.agent = SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr("runtime.brain.asyncio.get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))

    brain.stop()

    assert capture_manager.sync_stop_calls == 1
