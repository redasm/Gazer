from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

from channels.media_utils import save_media
from devices.models import NodeActionResult, NodeCapability, NodeInfo
from devices.registry import DeviceNode

if TYPE_CHECKING:
    from devices.satellite_session import SatelliteSessionManager
    from perception.capture import CaptureManager


REMOTE_CAPABILITIES: Dict[str, NodeCapability] = {
    "screen.observe": NodeCapability(
        action="screen.observe",
        description="Analyze the latest remote screen frame with current perception pipeline.",
        tier="safe",
    ),
    "screen.screenshot": NodeCapability(
        action="screen.screenshot",
        description="Request a remote screenshot and return it as chat media.",
        tier="safe",
    ),
    "file.send": NodeCapability(
        action="file.send",
        description="Send a remote file path to the user channel (if supported by remote node).",
        tier="safe",
    ),
    "input.mouse.click": NodeCapability(
        action="input.mouse.click",
        description="Click the remote mouse at screen coordinates.",
        tier="privileged",
    ),
    "input.keyboard.type": NodeCapability(
        action="input.keyboard.type",
        description="Type text on remote node.",
        tier="privileged",
    ),
    "input.keyboard.hotkey": NodeCapability(
        action="input.keyboard.hotkey",
        description="Press a remote keyboard hotkey chord.",
        tier="privileged",
    ),
}


class RemoteSatelliteNode(DeviceNode):
    def __init__(
        self,
        *,
        node_id: str,
        label: Optional[str] = None,
        session_manager: "SatelliteSessionManager",
        capture_manager: Optional["CaptureManager"] = None,
        allow_actions: Optional[List[str]] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._node_id = node_id
        self._label = label or f"Satellite {node_id}"
        self._sessions = session_manager
        self._capture = capture_manager
        allowed = [str(item).strip() for item in (allow_actions or []) if str(item).strip()]
        self._allow_actions = set(allowed) if allowed else set(REMOTE_CAPABILITIES.keys())
        self._timeout = timeout_seconds

    @property
    def node_id(self) -> str:
        return self._node_id

    def info(self) -> NodeInfo:
        observe_available, observe_unavailable_reason = self._get_observe_support()
        capabilities = [
            REMOTE_CAPABILITIES[action]
            for action in sorted(self._allow_actions)
            if action in REMOTE_CAPABILITIES
            and not (action == "screen.observe" and not observe_available)
        ]
        return NodeInfo(
            node_id=self._node_id,
            kind="desktop.remote",
            label=self._label,
            online=self._sessions.is_online(self._node_id),
            capabilities=capabilities,
            metadata={
                "transport": "satellite_ws",
                "transport_backend": getattr(self._sessions, "backend", "python"),
                "timeout_seconds": self._timeout,
                "capture_available": observe_available,
                "capture_unavailable_reason": observe_unavailable_reason,
            },
        )

    async def invoke(self, action: str, args: Dict[str, Any]) -> NodeActionResult:
        if action not in self._allow_actions:
            return NodeActionResult(
                ok=False,
                code="DEVICE_ACTION_NOT_ALLOWED",
                message=f"Action '{action}' is not allowed for node '{self._node_id}'.",
            )

        if action == "screen.observe":
            observe_available, observe_unavailable_reason = self._get_observe_support()
            if not observe_available:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_CAPTURE_UNAVAILABLE",
                    message=observe_unavailable_reason or "Remote capture pipeline is not available.",
                )
            query = str(args.get("query") or "Describe the current remote desktop state.")
            payload = await self._get_structured_observation_payload(query=query)
            summary = str(payload.get("summary", "") or "").strip() or "Observation captured."
            return NodeActionResult(ok=True, message=summary, data={"observation": payload})

        verify_after = False
        rollback_on_failure = False
        before_frame = None
        if action == "input.mouse.click":
            try:
                x = int(args.get("x"))
                y = int(args.get("y"))
            except Exception:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_COORDINATES_INVALID",
                    message="Mouse click requires integer x/y coordinates.",
                )
            if x < 0 or y < 0:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ACTION_OUT_OF_BOUNDS",
                    message=f"Click coordinates out of bounds: ({x}, {y}).",
                )
            try:
                screen_width = int(args.get("screen_width", 0) or 0)
                screen_height = int(args.get("screen_height", 0) or 0)
            except Exception:
                screen_width = 0
                screen_height = 0
            if screen_width > 0 and screen_height > 0 and (x >= screen_width or y >= screen_height):
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ACTION_OUT_OF_BOUNDS",
                    message=(
                        f"Click coordinates out of bounds: ({x}, {y}) "
                        f"not in {screen_width}x{screen_height}."
                    ),
                )
            verify_after = bool(args.get("verify_after", False))
            rollback_on_failure = bool(args.get("rollback_on_failure", False))
            before_frame = await self._grab_verification_frame() if verify_after else None

        result = await self._sessions.send_invoke(
            node_id=self._node_id,
            action=action,
            args=args,
            timeout_seconds=self._timeout,
        )
        if not result.ok:
            return result
        media_b64 = str(result.data.get("media_b64", "")).strip() if result.data else ""
        media_format = str(result.data.get("media_format", "png")).strip().lower() if result.data else "png"
        if media_b64:
            try:
                binary = base64.b64decode(media_b64.encode("utf-8"), validate=True)
                media_path = save_media(binary, ext=f".{media_format or 'png'}", prefix="satellite")
                result.data["media_path"] = str(media_path)
            except Exception:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_REMOTE_MEDIA_INVALID",
                    message="Remote screenshot payload is invalid.",
                )
        if action == "input.mouse.click" and verify_after:
            try:
                settle_seconds = float(args.get("verify_settle_seconds", 0.35))
            except (TypeError, ValueError):
                settle_seconds = 0.35
            await asyncio.sleep(max(0.0, settle_seconds))
            after_frame = await self._grab_verification_frame()
            changed_ratio = self._estimate_frame_change_ratio(before_frame, after_frame)
            if changed_ratio <= 0.0:
                if rollback_on_failure:
                    rollback_hotkey = str(args.get("rollback_hotkey", "esc") or "esc").strip().lower()
                    if rollback_hotkey:
                        try:
                            await self._sessions.send_invoke(
                                node_id=self._node_id,
                                action="input.keyboard.hotkey",
                                args={"keys": [rollback_hotkey]},
                                timeout_seconds=self._timeout,
                            )
                        except Exception:
                            pass
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ACTION_POST_VERIFY_FAILED",
                    message=(
                        "Click post-verification failed: screen state did not change. "
                        f"(delta={changed_ratio:.4f})"
                    ),
                    data={"delta_ratio": changed_ratio},
                )
        return result

    def _get_observe_support(self) -> Tuple[bool, str]:
        if self._capture is None:
            return False, "Remote capture pipeline is not available."
        probe = getattr(self._capture, "get_observe_capability", None)
        if not callable(probe):
            return (
                False,
                "Screen perception capability probe is missing on capture manager.",
            )
        try:
            result = probe()
            if isinstance(result, tuple) and len(result) >= 2:
                return bool(result[0]), str(result[1] or "")
            if isinstance(result, bool):
                return result, "" if result else "Screen perception is unavailable."
            return False, "Screen perception capability probe returned an invalid value."
        except Exception as exc:
            return False, f"Screen perception probe failed: {exc}"

    async def _get_structured_observation_payload(self, *, query: str) -> Dict[str, Any]:
        structured_loader = getattr(self._capture, "get_latest_observation_structured", None)
        if callable(structured_loader):
            try:
                payload = await structured_loader(query=query)
                normalized = self._normalize_observation_payload(payload, query=query)
                if normalized is not None:
                    return normalized
            except Exception:
                pass

        summary = await self._capture.get_latest_observation(query=query)
        return self._fallback_observation_payload(summary=summary, query=query)

    def _normalize_observation_payload(self, payload: Any, *, query: str) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        summary = str(payload.get("summary", "") or payload.get("message", "") or "").strip()
        if not summary:
            summary = str(payload.get("observation", "") or "").strip()
        elements_raw = payload.get("elements", [])
        elements: List[Dict[str, Any]] = []
        if isinstance(elements_raw, list):
            for item in elements_raw:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "") or "").strip()
                elem_type = str(item.get("type", "text_block") or "text_block").strip()
                coords = item.get("coordinates", {})
                if not isinstance(coords, dict):
                    coords = {}
                confidence_raw = item.get("confidence", 0.35)
                try:
                    confidence = float(confidence_raw)
                except (TypeError, ValueError):
                    confidence = 0.35
                elements.append(
                    {
                        "type": elem_type or "text_block",
                        "text": text,
                        "coordinates": {
                            "x": int(coords.get("x", 0) or 0),
                            "y": int(coords.get("y", 0) or 0),
                            "width": int(coords.get("width", 0) or 0),
                            "height": int(coords.get("height", 0) or 0),
                        },
                        "confidence": max(0.0, min(1.0, confidence)),
                    }
                )
        if not elements:
            elements.append(
                {
                    "type": "screen_summary",
                    "text": summary or "Observation captured.",
                    "coordinates": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "confidence": 0.35,
                }
            )

        frame_raw = payload.get("frame", {})
        if not isinstance(frame_raw, dict):
            frame_raw = {}
        return {
            "summary": summary or "Observation captured.",
            "query": str(payload.get("query", query) or query),
            "frame": {
                "source_type": str(frame_raw.get("source_type", "screen") or "screen"),
                "source_id": str(frame_raw.get("source_id", self._node_id) or self._node_id),
                "timestamp": str(frame_raw.get("timestamp", "")),
                "width": int(frame_raw.get("width", 0) or 0),
                "height": int(frame_raw.get("height", 0) or 0),
            },
            "elements": elements,
        }

    def _fallback_observation_payload(self, *, summary: str, query: str) -> Dict[str, Any]:
        text = str(summary or "").strip() or "Observation captured."
        return {
            "summary": text,
            "query": str(query or "").strip(),
            "frame": {
                "source_type": "screen",
                "source_id": self._node_id,
                "timestamp": "",
                "width": 0,
                "height": 0,
            },
            "elements": [
                {
                    "type": "screen_summary",
                    "text": text,
                    "coordinates": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "confidence": 0.35,
                }
            ],
        }

    async def _grab_verification_frame(self):
        if self._capture is None:
            return None
        grab = getattr(self._capture, "_grab_frame", None)
        if not callable(grab):
            return None
        try:
            return await grab()
        except Exception:
            return None

    @staticmethod
    def _estimate_frame_change_ratio(before_frame, after_frame) -> float:
        if before_frame is None or after_frame is None:
            return 1.0
        before_image = getattr(before_frame, "image", None)
        after_image = getattr(after_frame, "image", None)
        if before_image is None or after_image is None:
            return 1.0
        try:
            from PIL import ImageChops
        except Exception:
            return 1.0
        try:
            if before_image.size != after_image.size:
                return 1.0
            diff = ImageChops.difference(before_image.convert("RGB"), after_image.convert("RGB"))
            histogram = diff.histogram()
            if not histogram:
                return 0.0
            total = sum(
                value * (index % 256)
                for index, value in enumerate(histogram)
            )
            width, height = before_image.size
            max_total = max(1, width * height * 3 * 255)
            ratio = float(total) / float(max_total)
            return max(0.0, min(1.0, ratio))
        except Exception:
            return 1.0
