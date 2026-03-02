from __future__ import annotations

import asyncio
import ctypes
import platform
import shutil
import subprocess
import struct
import uuid
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

from channels.media_utils import ensure_media_dir
from devices.models import NodeActionResult, NodeCapability, NodeInfo
from devices.registry import DeviceNode
from runtime.rust_gate import is_rust_allowed_for_current_context
from runtime.rust_sidecar import RustSidecarError

if TYPE_CHECKING:
    from perception.capture import CaptureManager
    from runtime.rust_sidecar import RustSidecarClient


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return length + chunk_type + data + struct.pack(">I", crc)


def _encode_png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """Encode an RGBA buffer (top-down, stride=width*4) into PNG bytes.

    Kept intentionally minimal to avoid optional dependencies (Pillow).
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid PNG dimensions: {width}x{height}")
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(f"Invalid RGBA buffer size: {len(rgba)} (expected {expected})")

    # PNG color type 6 (RGBA), 8-bit, no interlace.
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    stride = width * 4
    # Each scanline is prefixed by a filter byte (0 = None).
    raw = bytearray((stride + 1) * height)
    for y in range(height):
        row_off = y * (stride + 1)
        raw[row_off] = 0
        src_off = y * stride
        raw[row_off + 1 : row_off + 1 + stride] = rgba[src_off : src_off + stride]

    compressed = zlib.compress(bytes(raw), level=6)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def _capture_windows_screenshot_rgba() -> tuple[int, int, bytes]:
    """Return (width, height, RGBA bytes) for the virtual desktop on Windows."""
    # wintypes exists on all platforms, but WinDLL only works on Windows.
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    SRCCOPY = 0x00CC0020
    CAPTUREBLT = 0x40000000
    BI_RGB = 0
    DIB_RGB_COLORS = 0

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = wintypes.INT

    x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid virtual screen size: {width}x{height}")

    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise RuntimeError(f"GetDC failed (winerr={ctypes.get_last_error()})")

    hdc_mem = None
    hbmp = None
    old_obj = None
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            raise RuntimeError(f"CreateCompatibleDC failed (winerr={ctypes.get_last_error()})")
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        if not hbmp:
            raise RuntimeError(f"CreateCompatibleBitmap failed (winerr={ctypes.get_last_error()})")
        old_obj = gdi32.SelectObject(hdc_mem, hbmp)
        if not old_obj:
            raise RuntimeError(f"SelectObject failed (winerr={ctypes.get_last_error()})")
        if not gdi32.BitBlt(
            hdc_mem,
            0,
            0,
            width,
            height,
            hdc_screen,
            x,
            y,
            SRCCOPY | CAPTUREBLT,
        ):
            raise RuntimeError(f"BitBlt failed (winerr={ctypes.get_last_error()})")

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        buf_size = width * height * 4
        buf = (ctypes.c_ubyte * buf_size)()
        lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if lines != height:
            raise RuntimeError(
                f"GetDIBits failed (got {lines} lines, expected {height}, winerr={ctypes.get_last_error()})"
            )
        raw = bytearray(buf)

        # Windows returns BGRA; PNG wants RGBA.
        tmp = raw[0::4]
        raw[0::4] = raw[2::4]
        raw[2::4] = tmp
        return width, height, bytes(raw)
    finally:
        try:
            if old_obj and hdc_mem:
                gdi32.SelectObject(hdc_mem, old_obj)
        except Exception:
            pass
        try:
            if hbmp:
                gdi32.DeleteObject(hbmp)
        except Exception:
            pass
        try:
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
        except Exception:
            pass
        try:
            user32.ReleaseDC(None, hdc_screen)
        except Exception:
            pass


class LocalDesktopNode(DeviceNode):
    _RUST_ACTION_METHODS = {
        "screen.screenshot": "desktop.screen.screenshot",
        "input.mouse.click": "desktop.input.mouse.click",
        "input.keyboard.type": "desktop.input.keyboard.type",
        "input.keyboard.hotkey": "desktop.input.keyboard.hotkey",
    }

    def __init__(
        self,
        *,
        node_id: str = "local-desktop",
        label: str = "This Machine",
        capture_manager: Optional["CaptureManager"] = None,
        action_enabled: bool = True,
        backend: str = "python",
        rust_client: Optional["RustSidecarClient"] = None,
    ) -> None:
        self._node_id = node_id
        self._label = label
        self._capture = capture_manager
        self._action_enabled = action_enabled
        selected_backend = str(backend or "python").strip().lower()
        self._backend = selected_backend if selected_backend in {"python", "rust"} else "python"
        self._rust_client = rust_client
        if self._backend == "rust":
            self._screenshot_available = True
            self._screenshot_unavailable_reason = ""
        else:
            self._screenshot_available, self._screenshot_unavailable_reason = self._detect_screenshot_support()

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def backend(self) -> str:
        return self._backend

    def info(self) -> NodeInfo:
        capabilities: List[NodeCapability] = []
        observe_available, observe_unavailable_reason = self._get_observe_support()
        if observe_available:
            capabilities.append(
                NodeCapability(
                    action="screen.observe",
                    description="Analyze current screen using the configured perception pipeline.",
                    tier="safe",
                )
            )
        if self._screenshot_available:
            capabilities.append(
                NodeCapability(
                    action="screen.screenshot",
                    description="Capture a screenshot and return it as chat media.",
                    tier="safe",
                )
            )
        capabilities.append(
            NodeCapability(
                action="file.send",
                description="Send a local file path back to the user channel.",
                tier="safe",
            )
        )
        if self._action_enabled:
            capabilities.append(
                NodeCapability(
                    action="input.mouse.click",
                    description="Click the mouse at screen coordinates.",
                    tier="privileged",
                )
            )
            capabilities.append(
                NodeCapability(
                    action="input.keyboard.type",
                    description="Type text at the focused cursor location.",
                    tier="privileged",
                )
            )
            capabilities.append(
                NodeCapability(
                    action="input.keyboard.hotkey",
                    description="Press a keyboard chord such as ctrl+c.",
                    tier="privileged",
                )
            )
        return NodeInfo(
            node_id=self._node_id,
            kind="desktop.local",
            label=self._label,
            online=True,
            capabilities=capabilities,
            metadata={
                "backend": self._backend,
                "action_enabled": self._action_enabled,
                "capture_available": observe_available,
                "capture_unavailable_reason": observe_unavailable_reason,
                "screenshot_available": self._screenshot_available,
                "screenshot_unavailable_reason": self._screenshot_unavailable_reason,
            },
        )

    async def invoke(self, action: str, args: Dict[str, Any]) -> NodeActionResult:
        if action == "screen.observe":
            observe_available, observe_unavailable_reason = self._get_observe_support()
            if not observe_available:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_CAPTURE_UNAVAILABLE",
                    message=observe_unavailable_reason or "Screen capture is not available.",
                )
            query = str(
                args.get("query")
                or "Describe the active window and what the user is doing."
            )
            payload = await self._get_structured_observation_payload(query=query)
            summary = str(payload.get("summary", "") or "").strip() or "Observation captured."
            return NodeActionResult(
                ok=True,
                message=summary,
                data={"observation": payload},
            )

        if action == "screen.screenshot":
            if self._backend == "rust" and is_rust_allowed_for_current_context():
                return await self._invoke_rust_action(action=action, args=args)
            if not self._screenshot_available:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_SCREENSHOT_UNAVAILABLE",
                    message=self._screenshot_unavailable_reason
                    or "Screen screenshot capability is unavailable on this node.",
                )
            ok, result = self._capture_screenshot_to_file()
            if not ok:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_SCREENSHOT_FAILED",
                    message=result,
                )
            return NodeActionResult(
                ok=True,
                message="Screenshot captured.",
                data={"media_path": result},
            )

        if action == "file.send":
            file_path = str(args.get("path") or "").strip()
            if not file_path:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_PATH_REQUIRED",
                    message="Parameter 'path' is required.",
                )
            path_obj = Path(file_path)
            if not path_obj.is_file():
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_FILE_NOT_FOUND",
                    message=f"File not found: {file_path}",
                )
            return NodeActionResult(
                ok=True,
                message="File prepared for sending.",
                data={"media_path": str(path_obj)},
            )

        if not self._action_enabled and action.startswith("input."):
            return NodeActionResult(
                ok=False,
                code="DEVICE_ACTION_DISABLED",
                message="Desktop input actions are disabled by configuration.",
            )

        if (
            self._backend == "rust"
            and action in self._RUST_ACTION_METHODS
            and is_rust_allowed_for_current_context()
        ):
            return await self._invoke_rust_action(action=action, args=args)

        if action == "input.mouse.click":
            try:
                import pyautogui
            except ImportError:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_DEPENDENCY_MISSING",
                    message="pyautogui is not installed.",
                )
            visible = bool(args.get("target_visible", True))
            if not visible:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_TARGET_NOT_VISIBLE",
                    message="Target is not visible. Refusing click.",
                )
            interactable = bool(args.get("target_interactable", True))
            if not interactable:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_TARGET_NOT_INTERACTABLE",
                    message="Target is not interactable. Refusing click.",
                )
            try:
                confidence = float(args.get("target_confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            try:
                min_confidence = float(args.get("min_target_confidence", 0.45))
            except (TypeError, ValueError):
                min_confidence = 0.45
            if confidence < min_confidence:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_TARGET_LOW_CONFIDENCE",
                    message=(
                        "Target confidence is too low for click "
                        f"(confidence={confidence:.2f}, min={min_confidence:.2f})."
                    ),
                )
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
                screen_size = pyautogui.size()
                width = int(getattr(screen_size, "width", 0) or 0)
                height = int(getattr(screen_size, "height", 0) or 0)
            except Exception:
                width = 0
                height = 0
            if width > 0 and height > 0 and (x >= width or y >= height):
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ACTION_OUT_OF_BOUNDS",
                    message=f"Click coordinates out of bounds: ({x}, {y}) not in {width}x{height}.",
                )
            button = str(args.get("button") or "left")
            verify_after = bool(args.get("verify_after", False))
            rollback_on_failure = bool(args.get("rollback_on_failure", False))
            before_frame = await self._grab_verification_frame() if verify_after else None
            pyautogui.click(x, y, button=button)
            if verify_after:
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
                                pyautogui.press(rollback_hotkey)
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
            return NodeActionResult(ok=True, message=f"Clicked ({x}, {y}) with {button} button.")

        if action == "input.keyboard.type":
            try:
                import pyautogui
            except ImportError:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_DEPENDENCY_MISSING",
                    message="pyautogui is not installed.",
                )
            text = str(args.get("text") or "")
            if not text:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_TEXT_REQUIRED",
                    message="Parameter 'text' is required.",
                )
            pyautogui.write(text, interval=0.02)
            preview = f"{text[:80]}{'...' if len(text) > 80 else ''}"
            return NodeActionResult(ok=True, message=f"Typed: {preview}")

        if action == "input.keyboard.hotkey":
            try:
                import pyautogui
            except ImportError:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_DEPENDENCY_MISSING",
                    message="pyautogui is not installed.",
                )
            keys_raw = args.get("keys") or []
            if not isinstance(keys_raw, list) or not keys_raw:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_ARG_KEYS_INVALID",
                    message="Parameter 'keys' must be a non-empty array.",
                )
            keys = [str(item) for item in keys_raw]
            pyautogui.hotkey(*keys)
            return NodeActionResult(ok=True, message=f"Pressed: {'+'.join(keys)}")

        return NodeActionResult(
            ok=False,
            code="DEVICE_ACTION_UNSUPPORTED",
            message=f"Unsupported action: {action}",
        )

    async def _invoke_rust_action(self, *, action: str, args: Dict[str, Any]) -> NodeActionResult:
        method = self._RUST_ACTION_METHODS.get(action, "")
        if not method:
            return NodeActionResult(
                ok=False,
                code="DEVICE_ACTION_UNSUPPORTED",
                message=f"Unsupported action: {action}",
            )
        if self._rust_client is None:
            return NodeActionResult(
                ok=False,
                code="RUST_SIDECAR_UNAVAILABLE",
                message="Rust backend selected but rust sidecar client is unavailable.",
            )
        try:
            result = await self._rust_client.rpc(method=method, params=dict(args or {}))
        except RustSidecarError as exc:
            message = exc.message
            if exc.trace_id:
                message = f"{message} (trace_id={exc.trace_id})"
            return NodeActionResult(
                ok=False,
                code=exc.mapped_code,
                message=message,
            )
        except Exception as exc:
            return NodeActionResult(
                ok=False,
                code="DEVICE_INVOKE_EXCEPTION",
                message=f"Rust sidecar invocation failed: {exc}",
            )

        result_payload = result if isinstance(result, dict) else {}
        if action == "screen.screenshot":
            media_path = (
                str(result_payload.get("media_path", "") or result_payload.get("path", "")).strip()
            )
            if not media_path:
                return NodeActionResult(
                    ok=False,
                    code="DEVICE_SCREENSHOT_FAILED",
                    message="Rust sidecar screenshot response missing media_path.",
                )
            return NodeActionResult(
                ok=True,
                message=str(result_payload.get("message", "Screenshot captured.") or "Screenshot captured."),
                data={"media_path": media_path},
            )

        default_messages = {
            "input.mouse.click": "Mouse click completed.",
            "input.keyboard.type": "Keyboard input completed.",
            "input.keyboard.hotkey": "Hotkey completed.",
        }
        message = str(result_payload.get("message", default_messages.get(action, "Action completed.")))
        data = result_payload.get("data", {})
        safe_data = data if isinstance(data, dict) else {}
        return NodeActionResult(
            ok=True,
            message=message,
            data=safe_data,
        )

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

    @staticmethod
    def _normalize_observation_payload(payload: Any, *, query: str) -> Optional[Dict[str, Any]]:
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
                "source_id": str(frame_raw.get("source_id", "local") or "local"),
                "timestamp": str(frame_raw.get("timestamp", "")),
                "width": int(frame_raw.get("width", 0) or 0),
                "height": int(frame_raw.get("height", 0) or 0),
            },
            "elements": elements,
        }

    @staticmethod
    def _fallback_observation_payload(*, summary: str, query: str) -> Dict[str, Any]:
        text = str(summary or "").strip() or "Observation captured."
        return {
            "summary": text,
            "query": str(query or "").strip(),
            "frame": {
                "source_type": "screen",
                "source_id": "local",
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

    def _capture_screenshot_to_file(self) -> Tuple[bool, str]:
        """Capture screenshot via OS-native APIs/commands.

        This mirrors OpenClaw's node-side capability model: screenshot capture
        belongs to the execution node and should use platform capabilities
        instead of perception pipeline dependencies.
        """
        media_dir = ensure_media_dir()
        output_path = (media_dir / f"screenshot_{uuid.uuid4().hex[:12]}.png").resolve()

        system = platform.system().lower()
        try:
            if system == "windows":
                width, height, rgba = _capture_windows_screenshot_rgba()
                png = _encode_png_rgba(width, height, rgba)
                output_path.write_bytes(png)
            elif system == "darwin":
                proc = subprocess.run(
                    ["screencapture", "-x", str(output_path)],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    check=False,
                )
                if proc.returncode != 0:
                    msg = (proc.stderr or proc.stdout or "screencapture failed").strip()
                    return False, f"Could not capture screen (macOS): {msg}"
            else:
                linux_cmds = [
                    ["grim", str(output_path)],
                    ["gnome-screenshot", "-f", str(output_path)],
                    ["scrot", str(output_path)],
                ]
                last_err = ""
                for cmd in linux_cmds:
                    try:
                        proc = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=12,
                            check=False,
                        )
                    except FileNotFoundError:
                        continue
                    if proc.returncode == 0:
                        break
                    last_err = (proc.stderr or proc.stdout or "").strip()
                else:
                    if not last_err:
                        last_err = "No supported screenshot command found (grim/gnome-screenshot/scrot)."
                    return False, f"Could not capture screen (Linux): {last_err}"

            if not output_path.is_file() or output_path.stat().st_size <= 0:
                return False, (
                    "Could not capture screen. Check OS screen-capture permission and "
                    "ensure Gazer runs in an interactive desktop session."
                )
            return True, str(output_path)
        except Exception as exc:
            return False, f"Could not capture screen: {exc}"

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

    def _get_observe_support(self) -> Tuple[bool, str]:
        if self._capture is None:
            return False, "Screen capture is not available."
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

    @staticmethod
    def _detect_screenshot_support() -> Tuple[bool, str]:
        system = platform.system().lower()
        if system == "windows":
            # Windows screenshot support uses WinAPI via ctypes (no external deps).
            return True, ""
        if system == "darwin":
            if shutil.which("screencapture"):
                return True, ""
            return False, "Screenshot unavailable: macOS 'screencapture' command is missing."
        # Linux/other unix-like
        linux_cmds = ("grim", "gnome-screenshot", "scrot")
        for cmd in linux_cmds:
            if shutil.which(cmd):
                return True, ""
        return False, (
            "Screenshot unavailable: install one of grim/gnome-screenshot/scrot, "
            "and run in an interactive desktop session."
        )
