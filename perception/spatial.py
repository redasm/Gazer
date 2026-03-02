import asyncio
import base64
import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from runtime.config_manager import config
from perception.cloud_provider import resolve_openai_compatible_cloud_config

logger = logging.getLogger("GazerVision")

# Lazy imports -- cv2, mediapipe, numpy are only loaded when actually needed.
cv2 = None
mp = None
np = None


def _ensure_cv2():
    global cv2
    if cv2 is None:
        import cv2 as _cv2

        cv2 = _cv2


def _ensure_mediapipe():
    global mp
    if mp is None:
        import mediapipe as _mp

        mp = _mp


def _ensure_numpy():
    global np
    if np is None:
        import numpy as _np

        np = _np


class SpatialPerceiver:
    """视觉感知模块：支持 local/cloud/hybrid 路由。"""

    def __init__(self):
        self.cap = None
        self.is_running = False
        self.thread = None

        # Perception state (shared memory)
        self.face_detected = False
        self.last_distance = 0.0
        self.head_pose = (0, 0, 0)  # pitch, yaw, roll
        self._cloud_attention = 0.0
        self._cloud_last_ts = 0.0
        self._last_signal_source = "none"

        # Local inference
        self.mp_face_mesh = None
        self.face_mesh = None

        # Cloud inference
        self._cloud_step = None
        self._cloud_unavailable_reason = ""
        self._cloud_call_ts: list[float] = []
        self._last_cloud_call = 0.0

        self._reload_runtime_config()
        self._init_local_if_needed()
        self._init_cloud_if_needed()

    def _reload_runtime_config(self) -> None:
        spatial_cfg = config.get("perception.spatial", {})
        if not isinstance(spatial_cfg, dict):
            spatial_cfg = {}

        self.provider = str(spatial_cfg.get("provider", "local_mediapipe") or "local_mediapipe").strip()
        if self.provider not in {"local_mediapipe", "cloud_vision", "hybrid"}:
            self.provider = "local_mediapipe"

        self.route_mode = str(spatial_cfg.get("route_mode", "local_first") or "local_first").strip()
        if self.route_mode not in {"local_first", "cloud_first", "auto"}:
            self.route_mode = "local_first"

        cloud_cfg = spatial_cfg.get("cloud", {})
        self.cloud_cfg = cloud_cfg if isinstance(cloud_cfg, dict) else {}
        self.cloud_strict_required = bool(self.cloud_cfg.get("strict_required", False))

    def _init_local_if_needed(self) -> None:
        if self.provider not in {"local_mediapipe", "hybrid"}:
            return
        if self.face_mesh is not None:
            return
        try:
            _ensure_mediapipe()
            if hasattr(mp, "solutions"):
                self.mp_face_mesh = mp.solutions.face_mesh
            else:
                import mediapipe.python.solutions.face_mesh as mp_face_mesh

                self.mp_face_mesh = mp_face_mesh
            self.face_mesh = self.mp_face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            logger.info("MediaPipe FaceMesh initialized.")
        except Exception as exc:
            self.face_mesh = None
            logger.error(f"Failed to init MediaPipe: {exc}")

    def _init_cloud_if_needed(self) -> None:
        if self.provider not in {"cloud_vision", "hybrid"}:
            return

        resolved = resolve_openai_compatible_cloud_config(
            self.cloud_cfg,
            default_model="",
            require_base_url=False,
        )
        if not bool(resolved.get("enabled", False)):
            self._cloud_step = None
            reason = str(resolved.get("reason", "") or "disabled")
            self._cloud_unavailable_reason = f"Cloud vision unavailable: {reason}."
            if self.cloud_strict_required:
                raise RuntimeError(
                    f"Spatial cloud strict mode enabled but cloud vision is unavailable: {reason}"
                )
            if reason == "disabled":
                return
        api_key = str(resolved.get("api_key", "") or "").strip()
        base_url = str(resolved.get("base_url", "") or "").strip()
        model = str(resolved.get("model", "") or "").strip()

        # Allow borrowing fast_brain model configuration when cloud section omits fields.
        if not model or not api_key:
            try:
                from soul.models import ModelRegistry

                fb_key, fb_base, fb_model = ModelRegistry.resolve_model("fast_brain")
                api_key = api_key or str(fb_key or "")
                base_url = base_url or str(fb_base or "")
                model = model or str(fb_model or "")
            except Exception as exc:
                logger.warning(f"Failed to resolve fast_brain for cloud vision fallback: {exc}")

        if not model or not api_key:
            self._cloud_step = None
            self._cloud_unavailable_reason = "Cloud vision requires api_key + model."
            if self.cloud_strict_required:
                raise RuntimeError(self._cloud_unavailable_reason)
            return

        try:
            from soul.cognition import LLMCognitiveStep

            self._cloud_step = LLMCognitiveStep(
                name="SpatialCloudVision",
                model=model,
                api_key=api_key,
                base_url=base_url or None,
            )
            self._cloud_unavailable_reason = ""
            logger.info("Spatial cloud vision initialized.")
        except Exception as exc:
            self._cloud_step = None
            self._cloud_unavailable_reason = f"Cloud vision init failed: {exc}"
            logger.error(self._cloud_unavailable_reason)
            if self.cloud_strict_required:
                raise RuntimeError(self._cloud_unavailable_reason) from exc

    def start(self):
        """启动视觉感知循环"""
        if self.is_running:
            return

        self._reload_runtime_config()
        self._init_local_if_needed()
        self._init_cloud_if_needed()

        try:
            if self.provider in {"local_mediapipe", "cloud_vision", "hybrid"}:
                _ensure_cv2()
                cam_index = int(config.get("perception.camera_device_index", 0) or 0)
                self.cap = cv2.VideoCapture(cam_index)
                if not self.cap.isOpened():
                    logger.error("Could not open webcam (index=%s).", cam_index)
                    return
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            self.is_running = True
            self.thread = threading.Thread(target=self._update_loop, daemon=True)
            self.thread.start()
            logger.info(f"SpatialPerceiver started using provider={self.provider}, route={self.route_mode}")
        except Exception as exc:
            logger.error(f"Failed to start vision: {exc}")

    def stop(self):
        """停止感知"""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
        logger.info("SpatialPerceiver stopped.")

    def _update_loop(self):
        while self.is_running:
            if self.provider == "local_mediapipe":
                self._process_local()
            elif self.provider == "cloud_vision":
                self._process_cloud_only()
            elif self.provider == "hybrid":
                self._process_hybrid()
            else:
                time.sleep(0.2)

    def _capture_frame(self):
        if not self.cap:
            return None
        ret, frame = self.cap.read()
        if not ret:
            time.sleep(0.1)
            return None
        return frame

    def _process_local(self):
        frame = self._capture_frame()
        if frame is None:
            return
        self._process_local_frame(frame)
        time.sleep(0.033)

    def _process_cloud_only(self):
        frame = self._capture_frame()
        if frame is None:
            return
        self._process_cloud_frame(frame)
        time.sleep(0.1)

    def _process_hybrid(self):
        frame = self._capture_frame()
        if frame is None:
            return

        if self.route_mode in {"local_first", "auto"} and self.face_mesh is not None:
            self._process_local_frame(frame)

        should_call_cloud = False
        if self.route_mode == "cloud_first":
            should_call_cloud = True
        elif self.route_mode == "local_first":
            should_call_cloud = not self.face_detected
        elif self.route_mode == "auto":
            should_call_cloud = (not self.face_detected) or (self.get_attention_level() < 0.2)

        if should_call_cloud:
            self._process_cloud_frame(frame)

        time.sleep(0.05)

    def _process_local_frame(self, frame) -> None:
        image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image.flags.writeable = False

        if not self.face_mesh:
            return
        results = self.face_mesh.process(image)

        if results.multi_face_landmarks:
            self.face_detected = True
            self._last_signal_source = "local"
            face = results.multi_face_landmarks[0]
            left_cheek = face.landmark[234]
            right_cheek = face.landmark[454]
            pixel_width = abs(left_cheek.x - right_cheek.x) * 640
            if pixel_width > 0:
                distance = 84.0 / pixel_width
                self.last_distance = round(distance, 2)
        else:
            self.face_detected = False
            self.last_distance = 0.0

    def _allow_cloud_call(self) -> bool:
        now = time.time()
        self._cloud_call_ts = [ts for ts in self._cloud_call_ts if now - ts < 60]

        max_calls = int(self.cloud_cfg.get("max_calls_per_minute", 20) or 20)
        if len(self._cloud_call_ts) >= max(1, max_calls):
            return False

        per_call_cost = float(self.cloud_cfg.get("estimated_cost_per_call_usd", 0.001) or 0.001)
        max_cost = float(self.cloud_cfg.get("max_cost_per_minute_usd", 0.03) or 0.03)
        projected = (len(self._cloud_call_ts) + 1) * max(0.0, per_call_cost)
        if projected > max(0.0, max_cost):
            return False

        poll_interval = float(self.cloud_cfg.get("poll_interval_seconds", 1.5) or 1.5)
        if now - self._last_cloud_call < max(0.1, poll_interval):
            return False

        return True

    def _process_cloud_frame(self, frame) -> None:
        if self._cloud_step is None:
            return
        if not self._allow_cloud_call():
            return

        try:
            _, encoded = cv2.imencode(".jpg", frame)
            image_bytes = encoded.tobytes()
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            result = asyncio.run(self._analyze_cloud(b64))
            parsed = self._parse_cloud_payload(result)
            if parsed:
                self._apply_cloud_payload(parsed)
                now = time.time()
                self._cloud_call_ts.append(now)
                self._last_cloud_call = now
        except Exception as exc:
            logger.warning(f"Cloud vision call failed: {exc}")

    async def _analyze_cloud(self, image_b64: str) -> str:
        if self._cloud_step is None:
            return ""
        prompt = (
            "Analyze the image for user-presence signals. Return JSON only with keys: "
            "face_detected (bool), distance_m (number|null), attention (0..1)."
        )
        timeout_seconds = float(self.cloud_cfg.get("request_timeout_seconds", 15) or 15)
        coro = self._cloud_step.process_with_image(
            prompt=prompt,
            image_base64=image_b64,
            system_prompt="You are a perception analyzer. Return strict JSON only.",
        )
        return await asyncio.wait_for(coro, timeout=timeout_seconds)

    @staticmethod
    def _parse_cloud_payload(text: str) -> Optional[Dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except Exception:
                    return None
        return None

    def _apply_cloud_payload(self, payload: Dict[str, Any]) -> None:
        face_detected = payload.get("face_detected")
        if isinstance(face_detected, bool):
            self.face_detected = face_detected

        distance = payload.get("distance_m")
        if isinstance(distance, (int, float)):
            self.last_distance = max(0.0, min(float(distance), 10.0))

        attention = payload.get("attention")
        if isinstance(attention, (int, float)):
            self._cloud_attention = max(0.0, min(float(attention), 1.0))
            self._cloud_last_ts = time.time()
            self._last_signal_source = "cloud"

    def get_structured_state(self) -> Dict[str, Any]:
        """Structured observability payload for vision/automation pipelines."""
        return {
            "face_detected": bool(self.face_detected),
            "distance_m": float(self.last_distance or 0.0),
            "zone": self.get_interaction_zone(),
            "attention": float(self.get_attention_level()),
            "signal_source": self._last_signal_source,
            "provider": self.provider,
            "route_mode": self.route_mode,
            "camera_device_index": int(config.get("perception.camera_device_index", 0) or 0),
        }

    def get_user_distance(self) -> float:
        """获取当前用户距离 (米)"""
        return self.last_distance

    def get_interaction_zone(self) -> str:
        """根据距离划分交互区域"""
        d = self.get_user_distance()
        if not self.face_detected or d == 0:
            return "UNKNOWN"
        if d < 0.6:
            return "INTIMATE"
        if d < 1.2:
            return "SOCIAL"
        if d < 3.5:
            return "OBSERVING"
        return "FAR"

    def get_attention_level(self) -> float:
        """获取注意力水平"""
        if time.time() - self._cloud_last_ts <= 3.0:
            return self._cloud_attention

        if not self.face_detected:
            return 0.0

        zone = self.get_interaction_zone()
        if zone in ["INTIMATE", "SOCIAL"]:
            return 1.0
        if zone == "OBSERVING":
            return 0.5
        return 0.1


# Lazy initialization instead of module-level singleton
_spatial: Optional[SpatialPerceiver] = None


def get_spatial() -> SpatialPerceiver:
    """Return the shared SpatialPerceiver instance, creating it on first call."""
    global _spatial
    if _spatial is None:
        _spatial = SpatialPerceiver()
    return _spatial
