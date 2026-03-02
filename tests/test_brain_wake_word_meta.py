import asyncio

import pytest

from runtime import brain as brain_module


class _FakeConfig:
    def get(self, key, default=None):
        if key == "wake_word.keyword":
            return "gazer"
        return default


class _FakeBus:
    def __init__(self):
        self.messages = []

    async def publish_inbound(self, msg):
        self.messages.append(msg)


class _FakeAgent:
    def __init__(self):
        self.bus = _FakeBus()


class _FakeAudio:
    def __init__(self):
        self.asr_model = object()
        self.record_and_transcribe = self._record_and_transcribe

    def _record_and_transcribe(self, *args, **kwargs):
        return "hello gazer"

    def get_last_asr_meta(self):
        return {
            "source": "cloud_asr",
            "provider": "hybrid",
            "fallback_used": True,
            "estimated_cost_usd": 0.002,
        }


@pytest.mark.asyncio
async def test_setup_wake_word_includes_asr_meta(monkeypatch):
    monkeypatch.setattr(brain_module, "config", _FakeConfig())
    brain = brain_module.GazerBrain.__new__(brain_module.GazerBrain)
    brain.audio = _FakeAudio()
    brain.agent = _FakeAgent()

    brain._setup_wake_word()
    text = brain.audio.record_and_transcribe(duration=1)
    assert "gazer" in text

    await asyncio.sleep(0)
    assert len(brain.agent.bus.messages) == 1
    msg = brain.agent.bus.messages[0]
    assert msg.metadata["source"] == "wake_word"
    assert msg.metadata["asr"]["source"] == "cloud_asr"
    assert msg.metadata["asr"]["fallback_used"] is True

