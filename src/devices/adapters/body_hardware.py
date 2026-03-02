from __future__ import annotations

from typing import Any, Dict, List

from devices.models import NodeActionResult, NodeCapability, NodeInfo
from devices.registry import DeviceNode
from hardware.drivers.base import BodyDriver


class BodyHardwareNode(DeviceNode):
    """Expose physical body controls as a node-invoke compatible device node."""

    def __init__(
        self,
        *,
        body: BodyDriver,
        node_id: str = "body-main",
        label: str = "Physical Body",
        allow_connect_control: bool = True,
        spatial: Any = None,
        audio: Any = None,
        ui_queue: Any = None,
    ) -> None:
        self._body = body
        self._node_id = str(node_id or "body-main").strip() or "body-main"
        self._label = str(label or "Physical Body").strip() or "Physical Body"
        self._allow_connect_control = bool(allow_connect_control)
        self._spatial = spatial
        self._audio = audio
        self._ui_queue = ui_queue

    @property
    def node_id(self) -> str:
        return self._node_id

    def info(self) -> NodeInfo:
        capabilities: List[NodeCapability] = [
            NodeCapability(
                action="hardware.status",
                description="Get hardware connection status and supported gestures.",
                tier="safe",
            ),
            NodeCapability(
                action="hardware.move_head",
                description="Move head servos by yaw/pitch angles (0-180).",
                tier="privileged",
            ),
            NodeCapability(
                action="hardware.set_led",
                description="Set LED color with RGB values.",
                tier="privileged",
            ),
            NodeCapability(
                action="hardware.gesture",
                description="Trigger a named body gesture (e.g. nod/shake_head/breathe).",
                tier="privileged",
            ),
            NodeCapability(
                action="hardware.vision.distance",
                description="Get current user distance estimate from spatial perceiver.",
                tier="safe",
            ),
            NodeCapability(
                action="hardware.audio.transcribe",
                description="Capture microphone audio and return transcript.",
                tier="privileged",
            ),
            NodeCapability(
                action="hardware.audio.speak",
                description="Play a TTS utterance via current audio output pipeline.",
                tier="privileged",
            ),
            NodeCapability(
                action="hardware.display.message",
                description="Send a status message to connected UI/display channel.",
                tier="safe",
            ),
        ]
        if self._allow_connect_control:
            capabilities.extend(
                [
                    NodeCapability(
                        action="hardware.connect",
                        description="Connect to physical hardware driver.",
                        tier="privileged",
                    ),
                    NodeCapability(
                        action="hardware.disconnect",
                        description="Disconnect from physical hardware driver.",
                        tier="privileged",
                    ),
                ]
            )
        return NodeInfo(
            node_id=self._node_id,
            kind="hardware.body",
            label=self._label,
            online=bool(self._body.is_connected),
            capabilities=capabilities,
            metadata={
                "connected": bool(self._body.is_connected),
                "driver": self._body.__class__.__name__,
                "gestures": list(self._body.capabilities or []),
                "spatial_available": self._spatial is not None,
                "audio_available": self._audio is not None,
                "display_available": self._ui_queue is not None,
            },
        )

    async def invoke(self, action: str, args: Dict[str, Any]) -> NodeActionResult:
        args = args or {}
        if action == "hardware.status":
            return NodeActionResult(
                ok=True,
                message="Hardware status fetched.",
                data=self.info().metadata,
            )

        if action == "hardware.connect":
            if not self._allow_connect_control:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ACTION_DISABLED",
                    message="hardware.connect is disabled by configuration.",
                )
            ok = bool(self._body.connect())
            return NodeActionResult(
                ok=ok,
                code="" if ok else "HARDWARE_CONNECT_FAILED",
                message="Hardware connected." if ok else "Hardware connection failed.",
            )

        if action == "hardware.disconnect":
            if not self._allow_connect_control:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ACTION_DISABLED",
                    message="hardware.disconnect is disabled by configuration.",
                )
            self._body.disconnect()
            return NodeActionResult(ok=True, message="Hardware disconnected.")

        if action == "hardware.move_head":
            yaw = args.get("yaw")
            pitch = args.get("pitch")
            if yaw is None and pitch is None:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_REQUIRED",
                    message="At least one of 'yaw' or 'pitch' is required.",
                )
            try:
                if yaw is not None:
                    yaw_int = int(yaw)
                    if yaw_int < 0 or yaw_int > 180:
                        raise ValueError("yaw out of range")
                    self._body.set_actuator("head_yaw", yaw_int)
                if pitch is not None:
                    pitch_int = int(pitch)
                    if pitch_int < 0 or pitch_int > 180:
                        raise ValueError("pitch out of range")
                    self._body.set_actuator("head_pitch", pitch_int)
            except Exception as exc:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_MOVE_FAILED",
                    message=f"Failed to move head: {exc}",
                )
            return NodeActionResult(ok=True, message="Head movement command sent.")

        if action == "hardware.set_led":
            rgb = args.get("rgb")
            if not isinstance(rgb, list) or len(rgb) != 3:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_RGB_INVALID",
                    message="'rgb' must be an array of 3 integers.",
                )
            try:
                normalized = [max(0, min(255, int(v))) for v in rgb]
                self._body.set_leds(normalized)
            except Exception as exc:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_LED_FAILED",
                    message=f"Failed to set LED: {exc}",
                )
            return NodeActionResult(ok=True, message=f"LED updated: {normalized}.")

        if action == "hardware.gesture":
            name = str(args.get("name") or "").strip()
            if not name:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_NAME_REQUIRED",
                    message="Parameter 'name' is required for hardware.gesture.",
                )
            try:
                kwargs = args.get("kwargs")
                if not isinstance(kwargs, dict):
                    kwargs = {}
                self._body.gesture(name, **kwargs)
            except Exception as exc:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_GESTURE_FAILED",
                    message=f"Failed to execute gesture: {exc}",
                )
            return NodeActionResult(ok=True, message=f"Gesture executed: {name}.")

        if action == "hardware.vision.distance":
            if self._spatial is None:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_SPATIAL_UNAVAILABLE",
                    message="Spatial perceiver is not enabled.",
                )
            try:
                distance = self._spatial.get_user_distance()
            except Exception as exc:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_SPATIAL_FAILED",
                    message=f"Failed to query distance: {exc}",
                )
            return NodeActionResult(
                ok=True,
                message="Distance queried.",
                data={"distance_m": distance},
            )

        if action == "hardware.audio.transcribe":
            if self._audio is None:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_AUDIO_UNAVAILABLE",
                    message="Audio subsystem is unavailable.",
                )
            duration = args.get("duration", 3)
            sample_rate = args.get("sample_rate", 16000)
            try:
                payload = self._audio.record_and_transcribe_structured(
                    duration=int(duration), sample_rate=int(sample_rate)
                )
            except Exception as exc:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_AUDIO_TRANSCRIBE_FAILED",
                    message=f"Transcription failed: {exc}",
                )
            return NodeActionResult(
                ok=True,
                message="Audio transcribed.",
                data=payload if isinstance(payload, dict) else {"text": str(payload)},
            )

        if action == "hardware.audio.speak":
            if self._audio is None:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_AUDIO_UNAVAILABLE",
                    message="Audio subsystem is unavailable.",
                )
            text = str(args.get("text") or "").strip()
            if not text:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_TEXT_REQUIRED",
                    message="Parameter 'text' is required for hardware.audio.speak.",
                )
            try:
                self._audio.speak(text)
            except Exception as exc:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_AUDIO_SPEAK_FAILED",
                    message=f"Speak failed: {exc}",
                )
            return NodeActionResult(ok=True, message="Audio speak queued.")

        if action == "hardware.display.message":
            text = str(args.get("text") or "").strip()
            if not text:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_TEXT_REQUIRED",
                    message="Parameter 'text' is required for hardware.display.message.",
                )
            if self._ui_queue is None:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_DISPLAY_UNAVAILABLE",
                    message="Display/UI queue is unavailable.",
                )
            try:
                self._ui_queue.put({"type": "status", "data": text})
            except Exception as exc:
                return NodeActionResult(
                    ok=False,
                    code="HARDWARE_DISPLAY_FAILED",
                    message=f"Display update failed: {exc}",
                )
            return NodeActionResult(ok=True, message="Display message sent.")

        return NodeActionResult(
            ok=False,
            code="DEVICE_ACTION_UNSUPPORTED",
            message=f"Unsupported action: {action}",
        )
