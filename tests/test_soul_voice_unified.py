import pytest

from soul import voice


@pytest.mark.asyncio
async def test_voice_factory_routes_to_gazer_audio(monkeypatch):
    called = {"text": None}

    class _FakeAudio:
        def speak(self, text: str):
            called["text"] = text

    monkeypatch.setattr("perception.audio.get_audio", lambda: _FakeAudio())
    adapter = voice.VoiceFactory.get_voice()
    await adapter.speak("hello")
    assert called["text"] == "hello"

