"""Talk Mode — continuous voice conversation state machine.

States:
    IDLE → [wake word / button] → LISTENING → [VAD silence] → PROCESSING
    PROCESSING → [TTS done] → LISTENING
    LISTENING → [timeout] → IDLE
    Any state → [user speech during TTS] → interrupt TTS → LISTENING

Config (voice.talk_mode.*):
    timeout_seconds: 30       # Silence timeout before returning to IDLE
    vad_silence_ms: 1200      # VAD silence threshold for end-of-utterance
    interrupt_on_speech: true  # Allow user to interrupt TTS playback
"""

import asyncio
import enum
import hashlib
import inspect
import logging
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from runtime.config_manager import config

logger = logging.getLogger("TalkMode")


class TalkState(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


class TalkModeController:
    """Manages the Talk Mode lifecycle for continuous voice conversation."""

    def __init__(
        self,
        on_utterance: Optional[Callable[[str], Coroutine[Any, Any, str]]] = None,
        on_tts: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
        on_state_change: Optional[Callable[[TalkState], Any]] = None,
    ) -> None:
        self._state = TalkState.IDLE
        self._on_utterance = on_utterance  # async (text) -> response_text
        self._on_tts = on_tts              # async (text) -> None (plays audio)
        self._on_state_change = on_state_change

        # Config
        self._timeout = float(config.get("voice.talk_mode.timeout_seconds", 30))
        self._vad_silence_ms = int(config.get("voice.talk_mode.vad_silence_ms", 1200))
        self._interrupt_on_speech = bool(config.get("voice.talk_mode.interrupt_on_speech", True))

        self._last_activity: float = 0.0
        self._tts_task: Optional[asyncio.Task] = None
        self._running = False
        self._cancel_tts = asyncio.Event()

    @property
    def state(self) -> TalkState:
        return self._state

    async def _set_state(self, new_state: TalkState) -> None:
        if self._state != new_state:
            old = self._state
            self._state = new_state
            logger.info("TalkMode: %s → %s", old.value, new_state.value)
            if self._on_state_change:
                try:
                    result = self._on_state_change(new_state)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def activate(self) -> None:
        """Transition from IDLE to LISTENING (triggered by wake word or button)."""
        if self._state == TalkState.IDLE:
            self._running = True
            self._last_activity = time.time()
            await self._set_state(TalkState.LISTENING)
            # Start timeout watcher
            asyncio.create_task(self._timeout_watcher())

    async def deactivate(self) -> None:
        """Force return to IDLE."""
        self._running = False
        self._cancel_tts.set()
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        await self._set_state(TalkState.IDLE)

    async def on_speech_detected(self) -> None:
        """Called when VAD detects user is speaking.

        If currently in SPEAKING state and interrupt is enabled, cancel TTS.
        """
        self._last_activity = time.time()
        if self._state == TalkState.SPEAKING and self._interrupt_on_speech:
            logger.info("TalkMode: user speech detected, interrupting TTS")
            self._cancel_tts.set()
            if self._tts_task and not self._tts_task.done():
                self._tts_task.cancel()
            await self._set_state(TalkState.LISTENING)

    async def on_utterance_complete(self, transcribed_text: str) -> None:
        """Called when VAD detects end of utterance (silence after speech).

        Transitions: LISTENING → PROCESSING → SPEAKING → LISTENING
        """
        if self._state != TalkState.LISTENING:
            return
        if not transcribed_text.strip():
            return

        self._last_activity = time.time()
        await self._set_state(TalkState.PROCESSING)

        # Get agent response
        response_text = ""
        if self._on_utterance:
            try:
                response_text = await self._on_utterance(transcribed_text)
            except Exception as exc:
                logger.error("TalkMode utterance handler error: %s", exc)
                response_text = ""

        if not response_text:
            await self._set_state(TalkState.LISTENING)
            return

        # Play TTS
        await self._set_state(TalkState.SPEAKING)
        self._cancel_tts.clear()
        if self._on_tts:
            try:
                self._tts_task = asyncio.create_task(self._on_tts(response_text))
                await self._tts_task
            except asyncio.CancelledError:
                logger.info("TalkMode: TTS cancelled (interrupted)")
            except Exception as exc:
                logger.error("TalkMode TTS error: %s", exc)

        # Back to listening (unless deactivated or timed out)
        if self._running and self._state == TalkState.SPEAKING:
            self._last_activity = time.time()
            await self._set_state(TalkState.LISTENING)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _timeout_watcher(self) -> None:
        """Background task: return to IDLE if no activity for timeout_seconds."""
        while self._running and self._state != TalkState.IDLE:
            await asyncio.sleep(1.0)
            elapsed = time.time() - self._last_activity
            if elapsed > self._timeout and self._state in (TalkState.LISTENING,):
                logger.info("TalkMode: timeout (%.0fs), returning to IDLE", elapsed)
                await self.deactivate()
                return

    def to_dict(self) -> dict:
        """Serialize current state for API/WS."""
        return {
            "state": self._state.value,
            "running": self._running,
            "timeout_seconds": self._timeout,
            "last_activity": self._last_activity,
        }


# ---------------------------------------------------------------------------
# TTS Cache — avoids re-synthesizing identical text
# ---------------------------------------------------------------------------

class TTSCache:
    """Hash-based disk cache for TTS audio output."""

    def __init__(self, cache_dir: Optional[str] = None, max_entries: int = 500) -> None:
        if cache_dir is None:
            cache_dir = str(config.get("voice.tts.cache_dir", "data/tts_cache"))
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_entries = max_entries

    def _key(self, text: str, voice: str = "") -> str:
        h = hashlib.sha256(f"{voice}:{text}".encode("utf-8")).hexdigest()[:16]
        return h

    def get(self, text: str, voice: str = "") -> Optional[Path]:
        """Return cached audio path if it exists."""
        key = self._key(text, voice)
        for ext in (".mp3", ".wav", ".opus"):
            path = self._dir / f"{key}{ext}"
            if path.exists():
                return path
        return None

    def put(self, text: str, audio_data: bytes, ext: str = ".mp3", voice: str = "") -> Path:
        """Store audio data in cache. Returns the cache path."""
        key = self._key(text, voice)
        path = self._dir / f"{key}{ext}"
        path.write_bytes(audio_data)
        self._evict_if_needed()
        return path

    def _evict_if_needed(self) -> None:
        """Remove oldest entries if cache exceeds max size."""
        files = sorted(self._dir.iterdir(), key=lambda p: p.stat().st_mtime)
        while len(files) > self._max_entries:
            oldest = files.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass
