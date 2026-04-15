import logging
import os
import queue
import re
import tempfile
import time
import wave
import numpy as np
import asyncio
from typing import Any, Optional

logger = logging.getLogger("GazerAudio")

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - optional dependency
    sd = None
try:
    import edge_tts
except Exception:  # pragma: no cover - optional dependency
    edge_tts = None
try:
    import httpx
except Exception:  # pragma: no cover - optional dependency
    httpx = None
try:
    import pygame
except Exception:  # pragma: no cover - optional dependency
    pygame = None

from runtime.config_manager import config
from perception.ear import Ear, get_ear
from perception.cloud_provider import resolve_openai_compatible_cloud_config

class GazerAudio:
    """
    音频感知与输出层
    使用 Faster-Whisper 进行 ASR
    使用 Edge-TTS (SSML + Raw PCM) 进行语音合成与口型同步
    """
    def __init__(self, model_size="tiny", device="cpu", compute_type="int8", ear: Optional[Ear] = None):
        asr_cfg = config.get("asr", {}) or {}
        if not isinstance(asr_cfg, dict):
            asr_cfg = {}
        self.provider = str(asr_cfg.get("provider", "whisper_local") or "whisper_local").strip()
        if self.provider not in {"whisper_local", "cloud_openai_compatible", "hybrid"}:
            self.provider = "whisper_local"
        self.route_mode = str(asr_cfg.get("route_mode", "local_first") or "local_first").strip()
        if self.route_mode not in {"local_first", "cloud_first", "auto"}:
            self.route_mode = "local_first"
        self.cloud_cfg = asr_cfg.get("cloud", {}) if isinstance(asr_cfg.get("cloud"), dict) else {}
        self.cloud_strict_required = bool(self.cloud_cfg.get("strict_required", False))

        self.model_size = str(asr_cfg.get("model_size", model_size) or model_size)
        self._local_model = None
        self._cloud_calls: list[float] = []
        self._last_asr_meta: dict = {}
        self.ear = ear or get_ear()

        if self.provider in {"whisper_local", "hybrid"}:
            try:
                from faster_whisper import WhisperModel

                logger.info(f"Loading Whisper model: {self.model_size}...")
                self._local_model = WhisperModel(self.model_size, device=device, compute_type=compute_type)
            except ImportError:
                logger.warning("Faster-Whisper not installed.")

        # Keep compatibility: Brain checks `audio.asr_model` to enable wake-word wiring.
        self.asr_model = self._local_model
        if self.asr_model is None and self.provider in {"cloud_openai_compatible", "hybrid"}:
            self.asr_model = object()
        if self.provider in {"cloud_openai_compatible", "hybrid"} and self.cloud_strict_required:
            resolved = resolve_openai_compatible_cloud_config(
                self.cloud_cfg,
                default_model="gpt-4o-mini-transcribe",
                require_base_url=True,
            )
            if not bool(resolved.get("enabled", False)):
                reason = str(resolved.get("reason", "unavailable"))
                raise RuntimeError(f"ASR cloud strict mode enabled but cloud ASR is unavailable: {reason}")

        self.audio_queue = queue.Queue()
        self.ui_queue = None  # 用于发送口型数据
        self.is_recording = False
        self.interrupt_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop = None
        voice_cfg = config.get("voice", {}) or {}
        if not isinstance(voice_cfg, dict):
            voice_cfg = {}
        self.voice_cfg = voice_cfg
        self.tts_provider = str(voice_cfg.get("provider", "edge-tts") or "edge-tts").strip()
        self.tts_cloud_cfg = voice_cfg.get("cloud", {}) if isinstance(voice_cfg.get("cloud"), dict) else {}

    def set_queues(self, ui_queue):
        self.ui_queue = ui_queue

    def _audio_callback(self, indata, frames, time, status):
        """麦克风采集回调"""
        if status:
            logger.warning(status)
        self.audio_queue.put(indata.copy())

    def record_and_transcribe(self, duration=5, sample_rate=16000) -> str:
        """记录指定时长的音频并转译"""
        result = self.record_and_transcribe_structured(duration=duration, sample_rate=sample_rate)
        return str(result.get("text", "") or "")

    def record_and_transcribe_structured(self, duration=5, sample_rate=16000) -> dict:
        """Record + transcribe with structured metadata for routing observability."""
        logger.info(f"Recording for {duration} seconds...")
        recording = self.ear.capture(duration=duration, sample_rate=sample_rate, channels=1)

        outcome = {
            "text": "",
            "source": "none",
            "provider": self.provider,
            "route_mode": self.route_mode,
            "fallback_used": False,
            "estimated_cost_usd": 0.0,
        }

        if self.provider == "whisper_local":
            text = self._transcribe_local(recording)
            outcome.update({"text": text, "source": "local_whisper"})
            self._last_asr_meta = dict(outcome)
            return outcome

        if self.provider == "cloud_openai_compatible":
            text = self._transcribe_cloud_with_fallback(recording, sample_rate)
            outcome.update(
                {
                    "text": text,
                    "source": "cloud_asr" if text else "none",
                    "estimated_cost_usd": float(
                        self.cloud_cfg.get("estimated_cost_per_call_usd", 0.002) or 0.002
                    )
                    if text
                    else 0.0,
                }
            )
            self._last_asr_meta = dict(outcome)
            return outcome

        # hybrid
        text, source, fallback_used = self._transcribe_hybrid(
            recording, sample_rate, duration=duration, return_meta=True
        )
        outcome.update(
            {
                "text": text,
                "source": source,
                "fallback_used": bool(fallback_used),
                "estimated_cost_usd": float(
                    self.cloud_cfg.get("estimated_cost_per_call_usd", 0.002) or 0.002
                )
                if source == "cloud_asr"
                else 0.0,
            }
        )
        self._last_asr_meta = dict(outcome)
        return outcome

    def get_last_asr_meta(self) -> dict:
        """Return structured metadata of the latest ASR attempt."""
        return dict(self._last_asr_meta)

    def _transcribe_hybrid(
        self, recording, sample_rate: int, duration: int, return_meta: bool = False
    ):
        if self.route_mode == "cloud_first":
            text = self._transcribe_cloud_with_fallback(recording, sample_rate)
            if text:
                return (text, "cloud_asr", False) if return_meta else text
            fallback = self._transcribe_local(recording)
            return (fallback, "local_whisper" if fallback else "none", True) if return_meta else fallback
        if self.route_mode == "auto":
            # auto: 短语音优先本地，长语音优先云端。
            if duration <= 3:
                text = self._transcribe_local(recording)
                if text:
                    return (text, "local_whisper", False) if return_meta else text
                fallback = self._transcribe_cloud_with_fallback(recording, sample_rate)
                return (fallback, "cloud_asr" if fallback else "none", True) if return_meta else fallback
            text = self._transcribe_cloud_with_fallback(recording, sample_rate)
            if text:
                return (text, "cloud_asr", False) if return_meta else text
            fallback = self._transcribe_local(recording)
            return (fallback, "local_whisper" if fallback else "none", True) if return_meta else fallback
        # local_first
        text = self._transcribe_local(recording)
        if text:
            return (text, "local_whisper", False) if return_meta else text
        fallback = self._transcribe_cloud_with_fallback(recording, sample_rate)
        return (fallback, "cloud_asr" if fallback else "none", True) if return_meta else fallback

    def _transcribe_local(self, recording) -> str:
        if not self._local_model:
            return ""
        try:
            segments, _ = self._local_model.transcribe(recording, beam_size=5)
            text = "".join([segment.text for segment in segments]).strip()
            logger.info(f"Transcribed(local): {text}")
            return text
        except Exception as exc:
            logger.warning(f"Local ASR failed: {exc}")
            return ""

    def _transcribe_cloud_with_fallback(self, recording, sample_rate: int) -> str:
        wav_path: Optional[str] = None
        try:
            wav_path = self._save_temp_wav(recording, sample_rate)
            return self._transcribe_cloud_wav(wav_path)
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    def _save_temp_wav(self, recording, sample_rate: int) -> str:
        float_audio = np.asarray(recording, dtype=np.float32)
        int16_audio = np.clip(float_audio * 32767.0, -32768.0, 32767.0).astype(np.int16)
        fd, path = tempfile.mkstemp(prefix="gazer_asr_", suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(int16_audio.tobytes())
        return path

    def _allow_cloud_call(self) -> bool:
        now = time.time()
        self._cloud_calls = [ts for ts in self._cloud_calls if now - ts < 60]
        max_calls = int(self.cloud_cfg.get("max_calls_per_minute", 20) or 20)
        if len(self._cloud_calls) >= max(1, max_calls):
            return False
        per_call_cost = float(self.cloud_cfg.get("estimated_cost_per_call_usd", 0.002) or 0.002)
        max_cost = float(self.cloud_cfg.get("max_cost_per_minute_usd", 0.05) or 0.05)
        projected_cost = (len(self._cloud_calls) + 1) * max(0.0, per_call_cost)
        return projected_cost <= max(0.0, max_cost)

    def _transcribe_cloud_wav(self, wav_path: str) -> str:
        if not self._allow_cloud_call():
            logger.info("Cloud ASR skipped due to budget/rate policy.")
            return ""
        if httpx is None:
            logger.warning("httpx is not installed; cloud ASR unavailable.")
            return ""

        resolved = resolve_openai_compatible_cloud_config(
            self.cloud_cfg,
            default_model="gpt-4o-mini-transcribe",
            require_base_url=True,
        )
        if not bool(resolved.get("enabled", False)):
            if self.cloud_strict_required:
                reason = str(resolved.get("reason", "unavailable"))
                raise RuntimeError(
                    f"ASR cloud strict mode enabled but cloud ASR is unavailable: {reason}"
                )
            return ""

        api_key = str(resolved.get("api_key", "") or "").strip()
        base_url = str(resolved.get("base_url", "") or "").strip().rstrip("/")
        model = str(resolved.get("model", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe").strip()
        timeout_seconds = float(self.cloud_cfg.get("request_timeout_seconds", 20) or 20)
        if not api_key or not base_url:
            return ""

        endpoint = f"{base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            with open(wav_path, "rb") as f:
                files = {"file": (os.path.basename(wav_path), f, "audio/wav")}
                data = {"model": model}
                resp = httpx.post(endpoint, headers=headers, files=files, data=data, timeout=timeout_seconds)
            if resp.status_code >= 400:
                logger.warning(f"Cloud ASR failed: {resp.status_code} {resp.text[:200]}")
                return ""
            payload = resp.json() if resp.text else {}
            text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
            if text:
                self._cloud_calls.append(time.time())
                logger.info(f"Transcribed(cloud): {text}")
            return text
        except Exception as exc:
            logger.warning(f"Cloud ASR request failed: {exc}")
            return ""

    def speak(self, text: str) -> None:
        """语音输出入口"""
        logger.info(f"Gazer Speaking: {text}")
        voice_cfg = config.get("voice", {}) or {}
        if isinstance(voice_cfg, dict):
            self.voice_cfg = voice_cfg
            self.tts_provider = str(voice_cfg.get("provider", "edge-tts") or "edge-tts").strip()
            self.tts_cloud_cfg = voice_cfg.get("cloud", {}) if isinstance(voice_cfg.get("cloud"), dict) else {}
        
        # Signal interrupt; the running speak task will check and clear it
        self.interrupt_event.set()

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_speak(text), self._loop)
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_speak(text))
            except RuntimeError:
                asyncio.run(self._async_speak(text))

    def _parse_emotion(self, text: str):
        """解析情感标签 [happy] Hello -> style='cheerful'"""
        match = re.search(r"^\[(\w+)\]\s*(.*)", text)
        if match:
            tag = match.group(1).lower()
            content = match.group(2)
            style_map = {
                "happy": "cheerful",
                "excited": "cheerful",
                "sad": "sad",
                "angry": "angry",
                "fear": "terrified",
                "whisper": "whispering",
            }
            return style_map.get(tag), content
        return None, text

    def _construct_ssml(self, text: str, voice: str, style: str = None) -> str:
        """构建 SSML"""
        if not style:
            return text # Edge-TTS 自动处理纯文本
            
        return f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='zh-CN'>
    <voice name='{voice}'>
        <mstts:express-as style='{style}'>
            {text}
        </mstts:express-as>
    </voice>
</speak>"""

    async def _async_speak(self, text: str):
        """异步生成并播放 (Raw PCM + Amplitude Broadcasting)"""
        # Clear interrupt from the previous speak call before starting
        self.interrupt_event.clear()
        voice = str(self.voice_cfg.get("voice_id", "zh-CN-XiaoxiaoNeural") or "zh-CN-XiaoxiaoNeural")
        style, clean_text = self._parse_emotion(text)
        if self.tts_provider == "cloud_openai_compatible":
            strict_tts = bool(self.tts_cloud_cfg.get("strict_required", False))
            retry_count_raw = self.tts_cloud_cfg.get("retry_count", 1)
            try:
                retry_count = max(0, min(int(retry_count_raw), 5))
            except Exception:
                retry_count = 1
            last_error = ""
            response: Optional[tuple[str, Any]] = None
            for _ in range(retry_count + 1):
                try:
                    response = await self._synthesize_cloud_tts(clean_text, voice_id=voice)
                except Exception as exc:
                    last_error = str(exc)
                    response = None
                if response is not None:
                    break
            if response is None:
                last_error = "cloud_tts_unavailable"
                if strict_tts:
                    logger.error("Cloud TTS strict mode enabled; speech aborted: %s", last_error)
                    return
            else:
                fmt, payload = response
                if fmt == "pcm":
                    await self._play_pcm_audio(payload, sample_rate=24000)
                    return
                if fmt == "mp3":
                    ok = await self._play_mp3_bytes(payload)
                    if ok:
                        return
                    last_error = "cloud_tts_mp3_playback_failed"

            if strict_tts:
                logger.error("Cloud TTS strict mode enabled; fallback disabled.")
                return
            if bool(self.tts_cloud_cfg.get("fallback_to_edge", True)):
                logger.warning(f"Cloud TTS failed ({last_error}), fallback to Edge TTS.")
            else:
                logger.warning(f"Cloud TTS failed ({last_error}), fallback disabled.")
                return

        if edge_tts is None:
            logger.warning("edge-tts not installed; TTS unavailable.")
            return

        ssml_text = self._construct_ssml(clean_text, voice, style)
        try:
            communicate = edge_tts.Communicate(ssml_text, voice)
            audio_data = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.extend(chunk["data"])
            audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            await self._play_pcm_audio(audio_np, sample_rate=24000)
        except Exception as e:
            logger.error(f"TTS Playback failed: {e}")

    async def _play_pcm_audio(self, audio_np: np.ndarray, sample_rate: int = 24000) -> None:
        """Play PCM float32 audio and emit amplitude frames to UI."""
        if audio_np is None or len(audio_np) <= 0:
            return
        if sd is None:
            logger.warning("sounddevice not installed; audio playback unavailable.")
            return

        def callback(outdata, frames, time_info, status):
            if status:
                logger.warning(f"Audio output status: {status}")

            chunk_size = len(outdata)
            nonlocal current_idx
            if current_idx + chunk_size > len(audio_np):
                remaining = len(audio_np) - current_idx
                outdata[:remaining, 0] = audio_np[current_idx:]
                outdata[remaining:, 0] = 0
                current_idx += remaining
                raise sd.CallbackStop
            chunk = audio_np[current_idx : current_idx + chunk_size]
            outdata[:, 0] = chunk
            current_idx += chunk_size
            amplitude = float(np.max(np.abs(chunk)))
            if self.ui_queue:
                self.ui_queue.put({"type": "audio_amplitude", "amplitude": amplitude})

        current_idx = 0
        with sd.OutputStream(channels=1, samplerate=sample_rate, callback=callback):
            while current_idx < len(audio_np):
                if self.interrupt_event.is_set():
                    break
                await asyncio.sleep(0.1)
        if self.ui_queue:
            self.ui_queue.put({"type": "audio_amplitude", "amplitude": 0})

    async def _play_mp3_bytes(self, audio_bytes: bytes) -> bool:
        """Play mp3 bytes through pygame mixer if available."""
        if not audio_bytes:
            return False
        if pygame is None:
            logger.warning("pygame not installed; cannot play cloud mp3 TTS.")
            return False
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="gazer_tts_", suffix=".mp3")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._play_audio_file_blocking, tmp_path)
            return True
        except Exception as exc:
            logger.warning(f"Cloud mp3 playback failed: {exc}")
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _play_audio_file_blocking(file_path: str) -> None:
        if pygame is None:
            return
        pygame.mixer.init()
        try:
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        finally:
            pygame.mixer.quit()

    async def _synthesize_cloud_tts(self, text: str, voice_id: str = "alloy") -> Optional[tuple[str, Any]]:
        """Call OpenAI-compatible audio speech endpoint."""
        if httpx is None:
            logger.warning("httpx not installed; cloud TTS unavailable.")
            return None
        resolved = resolve_openai_compatible_cloud_config(
            self.tts_cloud_cfg,
            default_model="gpt-4o-mini-tts",
            require_base_url=True,
        )
        if not bool(resolved.get("enabled", False)):
            reason = str(resolved.get("reason", "unavailable"))
            if bool(self.tts_cloud_cfg.get("strict_required", False)):
                raise RuntimeError(f"Cloud TTS strict mode enabled but provider unavailable: {reason}")
            logger.warning(f"Cloud TTS disabled: {reason}")
            return None
        base_url = str(resolved.get("base_url", "") or "").strip().rstrip("/")
        api_key = str(resolved.get("api_key", "") or "").strip()
        model = str(resolved.get("model", "gpt-4o-mini-tts") or "gpt-4o-mini-tts").strip()
        timeout_seconds = float(self.tts_cloud_cfg.get("request_timeout_seconds", 20) or 20)
        response_format = str(self.tts_cloud_cfg.get("response_format", "pcm") or "pcm").strip().lower()
        if response_format not in {"pcm", "mp3"}:
            response_format = "pcm"
        endpoint = f"{base_url}/audio/speech"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "input": text,
            "voice": str(voice_id or "alloy"),
            "response_format": response_format,
        }
        try:
            resp = httpx.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
            if resp.status_code >= 400:
                logger.warning(f"Cloud TTS failed: {resp.status_code} {resp.text[:200]}")
                return None
            raw = resp.content or b""
            if not raw:
                return None
            if response_format == "mp3":
                return ("mp3", raw)
            return ("pcm", np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0)
        except Exception as exc:
            logger.warning(f"Cloud TTS request failed: {exc}")
            return None


_shared_audio: Optional[GazerAudio] = None


def get_audio() -> GazerAudio:
    """Return shared GazerAudio instance for runtime-wide voice consistency."""
    global _shared_audio
    if _shared_audio is None:
        _shared_audio = GazerAudio()
    return _shared_audio
