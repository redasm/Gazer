
import logging
from typing import Optional

import numpy as np
import sounddevice as sd
from runtime.config_manager import config

logger = logging.getLogger("perception.ear")


class Ear:
    """硬件采集层：负责与麦克风设备交互，仅输出音频数据。"""

    def __init__(self):
        self.last_sample_rate = 16000
        self.last_channels = 1

    def capture(self, duration: int = 5, sample_rate: int = 16000, channels: int = 1) -> np.ndarray:
        """Capture raw audio from the active microphone device."""
        self.last_sample_rate = int(sample_rate)
        self.last_channels = int(channels)
        device = config.get("asr.input_device", None)
        logger.info(
            "Ear capturing audio: duration=%ss, sample_rate=%s, device=%s",
            duration,
            sample_rate,
            device,
        )
        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            device=device,
        )
        sd.wait()
        if channels == 1:
            return recording.flatten()
        return np.asarray(recording, dtype=np.float32)

    def get_status(self) -> dict:
        """Best-effort microphone status for diagnostics."""
        try:
            devices = sd.query_devices()
            configured = config.get("asr.input_device", None)
            default_input = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else None
            return {
                "configured_input_device": configured,
                "default_input_device": default_input,
                "device_count": len(devices) if devices is not None else 0,
                "ok": True,
            }
        except Exception as exc:
            return {
                "configured_input_device": config.get("asr.input_device", None),
                "ok": False,
                "error": str(exc),
            }

    def close(self):
        """Release audio resources if needed (sounddevice is stateless here)."""
        return None


# Lazy initialization instead of module-level singleton
_digital_ear: Optional[Ear] = None


def get_ear() -> Ear:
    """Return the shared Ear instance, creating it on first call."""
    global _digital_ear
    if _digital_ear is None:
        _digital_ear = Ear()
    return _digital_ear
