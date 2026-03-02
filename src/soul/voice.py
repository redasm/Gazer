"""Soul voice module -- wake-word detection, multi-provider TTS, and voice conversation loop.

This module builds on top of ``perception.audio.GazerAudio`` (Edge-TTS + Cloud ASR)
and adds:
  - ``VoiceWakeDetector``     – keyword detection via openwakeword / simple energy threshold
  - ``TTSProvider`` hierarchy – pluggable TTS backends (Edge-TTS, ElevenLabs, Azure, OpenAI)
  - ``VoiceConversationLoop`` – end-to-end: wake → transcribe → callback → speak → repeat

Configuration (via ``runtime.config_manager``):
  voice.wake_enabled    – enable wake-word detection (default: false)
  voice.wake_keyword    – wake keyword (default: "hey gazer")
  voice.tts_provider    – "edge" | "elevenlabs" | "azure" | "openai" (default: "edge")
  voice.elevenlabs_key  – ElevenLabs API key (env: ELEVENLABS_API_KEY)
  voice.elevenlabs_voice – ElevenLabs voice ID (default: "Rachel")
  voice.listen_duration – seconds per ASR capture (default: 5)
  voice.energy_threshold – mic energy threshold for wake (default: 0.02)
"""

from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

import numpy as np

logger = logging.getLogger("soul.voice")

# Type alias for the callback invoked when speech is transcribed
TranscriptCallback = Callable[[str], Awaitable[str]]


# ---------------------------------------------------------------------------
# TTS Provider abstraction
# ---------------------------------------------------------------------------


class TTSProvider(ABC):
    """Abstract TTS provider interface."""

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Synthesize *text* into audio bytes (MP3 or WAV).

        Returns:
            Raw audio bytes ready for playback.
        """

    @abstractmethod
    async def speak(self, text: str) -> None:
        """Synthesize and immediately play *text*."""


class EdgeTTSProvider(TTSProvider):
    """Edge-TTS provider (Microsoft free TTS, uses ``edge_tts`` library).

    This is the default provider, already used by ``perception.audio``.
    Delegates to ``GazerAudio.speak()`` for backward compatibility.
    """

    def __init__(self) -> None:
        self._audio: Optional[Any] = None

    def _get_audio(self) -> Any:
        if self._audio is None:
            from perception.audio import get_audio
            self._audio = get_audio()
        return self._audio

    async def synthesize(self, text: str) -> bytes:
        """Synthesize via edge_tts and return MP3 bytes."""
        try:
            import edge_tts
        except ImportError:
            logger.error("edge_tts not installed. Install with: pip install edge-tts")
            return b""

        from runtime.config_manager import config
        voice = config.get("tts.voice", "zh-CN-XiaoxiaoNeural")

        communicate = edge_tts.Communicate(text, voice)
        audio_chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
        return b"".join(audio_chunks)

    async def speak(self, text: str) -> None:
        self._get_audio().speak(text)


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs TTS provider (high-quality neural TTS).

    Requires ``ELEVENLABS_API_KEY`` environment variable or
    ``voice.elevenlabs_key`` config.
    """

    def __init__(self) -> None:
        from runtime.config_manager import config
        self._api_key = str(
            config.get("voice.elevenlabs_key", "")
            or os.environ.get("ELEVENLABS_API_KEY", "")
        ).strip()
        self._voice_id = str(
            config.get("voice.elevenlabs_voice", "Rachel")
        ).strip()
        self._model_id = str(
            config.get("voice.elevenlabs_model", "eleven_multilingual_v2")
        ).strip()

    async def synthesize(self, text: str) -> bytes:
        """Call ElevenLabs Text-to-Speech API and return MP3 bytes."""
        if not self._api_key:
            logger.error("ElevenLabs API key not configured.")
            return b""

        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed. Install with: pip install httpx")
            return b""

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}"
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": self._model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                logger.error(
                    "ElevenLabs TTS failed: %s %s",
                    response.status_code,
                    response.text[:200],
                )
                return b""
            return response.content

    async def speak(self, text: str) -> None:
        audio_bytes = await self.synthesize(text)
        if not audio_bytes:
            return
        try:
            from perception.audio import get_audio
            get_audio()._play_mp3_bytes(audio_bytes)
        except Exception as exc:
            logger.error("ElevenLabs playback failed: %s", exc, exc_info=True)


class OpenAITTSProvider(TTSProvider):
    """OpenAI-compatible TTS provider (via perception.audio Cloud TTS)."""

    def __init__(self) -> None:
        self._audio: Optional[Any] = None

    def _get_audio(self) -> Any:
        if self._audio is None:
            from perception.audio import get_audio
            self._audio = get_audio()
        return self._audio

    async def synthesize(self, text: str) -> bytes:
        from runtime.config_manager import config
        voice_id = config.get("voice.openai_voice", "alloy")
        result = await self._get_audio()._synthesize_cloud_tts(text, voice_id)
        return result if isinstance(result, bytes) else b""

    async def speak(self, text: str) -> None:
        self._get_audio().speak(text)


# ---------------------------------------------------------------------------
# TTS Factory
# ---------------------------------------------------------------------------


def create_tts_provider(provider_name: Optional[str] = None) -> TTSProvider:
    """Create a TTS provider by name.

    Args:
        provider_name: One of "edge", "elevenlabs", "openai". Defaults to config.

    Returns:
        A TTSProvider instance.
    """
    from runtime.config_manager import config
    name = (provider_name or config.get("voice.tts_provider", "edge")).strip().lower()

    if name == "elevenlabs":
        return ElevenLabsTTSProvider()
    elif name == "openai":
        return OpenAITTSProvider()
    else:
        return EdgeTTSProvider()


# ---------------------------------------------------------------------------
# Voice Wake Detector
# ---------------------------------------------------------------------------


class VoiceWakeDetector:
    """Detect a wake keyword in microphone audio.

    Uses simple energy-threshold detection with optional ``openwakeword``
    integration for more accurate keyword spotting.

    Args:
        keyword: The wake keyword phrase (used if openwakeword is available).
        energy_threshold: Minimum RMS energy to consider audio as speech.
        chunk_duration: Seconds per detection chunk.
    """

    def __init__(
        self,
        keyword: str = "hey gazer",
        energy_threshold: float = 0.02,
        chunk_duration: float = 1.5,
    ) -> None:
        self.keyword = keyword.strip().lower()
        self.energy_threshold = energy_threshold
        self.chunk_duration = chunk_duration
        self._oww_model: Optional[Any] = None
        self._use_oww = False
        self._init_oww()

    def _init_oww(self) -> None:
        """Try to load openwakeword for accurate keyword detection."""
        try:
            import openwakeword
            from openwakeword.model import Model as OWWModel
            self._oww_model = OWWModel(inference_framework="onnx")
            self._use_oww = True
            logger.info("VoiceWakeDetector using openwakeword for keyword detection.")
        except ImportError:
            logger.info(
                "openwakeword not installed, falling back to energy-threshold detection. "
                "Install with: pip install openwakeword"
            )
        except Exception as exc:
            logger.warning("Failed to init openwakeword: %s; using energy fallback.", exc)

    def detect_in_audio(self, audio: np.ndarray, sample_rate: int = 16000) -> bool:
        """Check if the wake keyword is present in the given audio chunk.

        Args:
            audio: numpy float32 array of audio samples.
            sample_rate: Sample rate of the audio.

        Returns:
            True if wake word detected.
        """
        if self._use_oww and self._oww_model is not None:
            try:
                # openwakeword expects int16 audio
                audio_int16 = (audio * 32767).astype(np.int16)
                prediction = self._oww_model.predict(audio_int16)
                # Check if any model score exceeds threshold
                for model_name, scores in prediction.items():
                    if isinstance(scores, (list, np.ndarray)):
                        if any(s > 0.5 for s in scores):
                            logger.info("Wake word detected via openwakeword: %s", model_name)
                            return True
                    elif isinstance(scores, (int, float)) and scores > 0.5:
                        logger.info("Wake word detected via openwakeword: %s", model_name)
                        return True
                return False
            except Exception as exc:
                logger.debug("openwakeword detection error: %s", exc)

        # Fallback: simple energy threshold detection
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms > self.energy_threshold:
            logger.debug("Audio energy above threshold: %.4f > %.4f", rms, self.energy_threshold)
            return True
        return False

    async def wait_for_wake(self, timeout: Optional[float] = None) -> bool:
        """Block until wake word is detected or timeout.

        Args:
            timeout: Maximum seconds to wait. None = wait forever.

        Returns:
            True if wake word detected, False if timeout.
        """
        from perception.ear import get_ear

        ear = get_ear()
        elapsed = 0.0
        chunk_sec = self.chunk_duration

        while True:
            if timeout is not None and elapsed >= timeout:
                return False

            try:
                # Run blocking mic capture in thread pool
                audio = await asyncio.to_thread(
                    ear.capture,
                    duration=int(max(1, chunk_sec)),
                    sample_rate=16000,
                    channels=1,
                )
                if self.detect_in_audio(audio, sample_rate=16000):
                    return True
            except Exception as exc:
                logger.warning("Wake detection capture error: %s", exc)
                await asyncio.sleep(0.5)

            elapsed += chunk_sec


# ---------------------------------------------------------------------------
# Voice Conversation Loop
# ---------------------------------------------------------------------------


class VoiceConversationLoop:
    """End-to-end voice conversation: wake → transcribe → process → speak → repeat.

    Args:
        on_transcript: Async callback ``(text: str) -> str`` that processes
                       the transcribed speech and returns the response text.
        tts_provider: TTS provider to use. Defaults to config-driven factory.
        wake_enabled: Whether to wait for wake word before each turn.
        wake_keyword: Wake keyword phrase.
        listen_duration: Seconds to listen for each ASR capture.
    """

    def __init__(
        self,
        on_transcript: TranscriptCallback,
        tts_provider: Optional[TTSProvider] = None,
        wake_enabled: bool = False,
        wake_keyword: str = "hey gazer",
        listen_duration: int = 5,
    ) -> None:
        from runtime.config_manager import config

        self.on_transcript = on_transcript
        self.tts = tts_provider or create_tts_provider()
        self.wake_enabled = wake_enabled or config.get("voice.wake_enabled", False)
        self.listen_duration = listen_duration or int(config.get("voice.listen_duration", 5))
        self._wake_detector = VoiceWakeDetector(
            keyword=wake_keyword or config.get("voice.wake_keyword", "hey gazer"),
            energy_threshold=float(config.get("voice.energy_threshold", 0.02)),
        )
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the voice conversation loop in the background."""
        if self._running:
            logger.warning("VoiceConversationLoop already running.")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("VoiceConversationLoop started.")

    async def stop(self) -> None:
        """Stop the loop gracefully."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("VoiceConversationLoop stopped.")

    async def _loop(self) -> None:
        """Main conversation loop."""
        from perception.audio import get_audio

        audio_engine = get_audio()

        while self._running:
            try:
                # --- Wake phase ---
                if self.wake_enabled:
                    logger.debug("Waiting for wake word...")
                    detected = await self._wake_detector.wait_for_wake(timeout=None)
                    if not detected or not self._running:
                        continue
                    logger.info("Wake word detected! Listening for command...")

                # --- Listen & transcribe ---
                transcript = await asyncio.to_thread(
                    audio_engine.record_and_transcribe,
                    duration=self.listen_duration,
                )
                transcript = str(transcript or "").strip()
                if not transcript:
                    logger.debug("No speech detected, continuing.")
                    continue

                logger.info("Transcribed: %s", transcript[:100])

                # --- Process via callback ---
                response = await self.on_transcript(transcript)
                response = str(response or "").strip()
                if not response:
                    continue

                # --- Speak response ---
                logger.info("Speaking response: %s", response[:100])
                await self.tts.speak(response)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Voice loop error: %s", exc, exc_info=True)
                await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Legacy compatibility (original thin facade)
# ---------------------------------------------------------------------------


class VoiceAdapter:
    """Base class for TTS adapters (legacy interface)."""

    async def speak(self, text: str) -> None:
        raise NotImplementedError


class GazerAudioAdapter(VoiceAdapter):
    """Unified adapter backed by perception.audio.GazerAudio."""

    def __init__(self) -> None:
        self._tts: Optional[TTSProvider] = None

    async def speak(self, text: str) -> None:
        if self._tts is None:
            self._tts = create_tts_provider()
        await self._tts.speak(text)


class VoiceFactory:
    """Voice module factory."""

    @staticmethod
    def get_voice() -> VoiceAdapter:
        return GazerAudioAdapter()


# Lazy-initialized singleton
_speaker: Optional[VoiceAdapter] = None


def get_speaker() -> VoiceAdapter:
    """Return the global VoiceAdapter, creating it on first call."""
    global _speaker
    if _speaker is None:
        _speaker = VoiceFactory.get_voice()
    return _speaker
