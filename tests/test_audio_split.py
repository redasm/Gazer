import numpy as np
import pytest

from perception.audio import GazerAudio, get_audio
from perception.ear import Ear


class _FakeEar:
    def __init__(self, payload=None):
        self.payload = np.array(payload if payload is not None else [0.0, 0.1, -0.1], dtype=np.float32)
        self.calls = []

    def capture(self, duration: int = 5, sample_rate: int = 16000, channels: int = 1):
        self.calls.append({"duration": duration, "sample_rate": sample_rate, "channels": channels})
        return self.payload


def test_ear_capture_uses_sounddevice(monkeypatch):
    rec_called = {"ok": False}
    wait_called = {"ok": False}

    def _fake_rec(frames, samplerate, channels, dtype, device=None):
        rec_called["ok"] = True
        assert frames == 16000
        assert samplerate == 16000
        assert channels == 1
        assert dtype == "float32"
        return np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

    def _fake_wait():
        wait_called["ok"] = True

    monkeypatch.setattr("perception.ear.sd.rec", _fake_rec)
    monkeypatch.setattr("perception.ear.sd.wait", _fake_wait)

    ear = Ear()
    data = ear.capture(duration=1, sample_rate=16000, channels=1)
    assert rec_called["ok"] is True
    assert wait_called["ok"] is True
    assert isinstance(data, np.ndarray)
    assert data.shape == (3,)


def test_audio_structured_local_route_uses_ear_capture():
    fake_ear = _FakeEar()
    audio = GazerAudio(ear=fake_ear)
    audio.provider = "whisper_local"
    audio.route_mode = "local_first"
    audio._local_model = object()
    audio._transcribe_local = lambda recording: "本地识别"

    payload = audio.record_and_transcribe_structured(duration=2, sample_rate=16000)
    assert payload["text"] == "本地识别"
    assert payload["source"] == "local_whisper"
    assert payload["fallback_used"] is False
    assert len(fake_ear.calls) == 1
    assert audio.get_last_asr_meta()["text"] == "本地识别"


def test_audio_structured_hybrid_fallback_to_cloud():
    fake_ear = _FakeEar()
    audio = GazerAudio(ear=fake_ear)
    audio.provider = "hybrid"
    audio.route_mode = "local_first"
    audio._local_model = object()
    audio._transcribe_local = lambda recording: ""
    audio._transcribe_cloud_with_fallback = lambda recording, sample_rate: "云端识别"
    audio.cloud_cfg = {"estimated_cost_per_call_usd": 0.003}

    payload = audio.record_and_transcribe_structured(duration=4, sample_rate=16000)
    assert payload["text"] == "云端识别"
    assert payload["source"] == "cloud_asr"
    assert payload["fallback_used"] is True
    assert payload["estimated_cost_usd"] == 0.003


@pytest.mark.asyncio
async def test_audio_async_speak_uses_cloud_provider_path(monkeypatch):
    fake_ear = _FakeEar()
    audio = GazerAudio(ear=fake_ear)
    audio.tts_provider = "cloud_openai_compatible"
    audio.voice_cfg = {"voice_id": "alloy"}
    audio.interrupt_event.clear()

    played = {"ok": False}

    async def _fake_synthesize(text, voice_id="alloy"):
        assert text == "hello"
        assert voice_id == "alloy"
        return ("pcm", np.array([0.0, 0.1, -0.1], dtype=np.float32))

    async def _fake_play(audio_np, sample_rate=24000):
        played["ok"] = True
        assert sample_rate == 24000
        assert len(audio_np) == 3

    monkeypatch.setattr(audio, "_synthesize_cloud_tts", _fake_synthesize)
    monkeypatch.setattr(audio, "_play_pcm_audio", _fake_play)

    await audio._async_speak("hello")
    assert played["ok"] is True


def test_get_audio_returns_singleton():
    a1 = get_audio()
    a2 = get_audio()
    assert a1 is a2
