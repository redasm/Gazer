"""SerialArmDriver -- BodyDriver for the Gazer serial-controlled robotic arm."""

import json
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional

from hardware.drivers.base import BodyDriver

logger = logging.getLogger("GazerHardware")


class SerialArmDriver(BodyDriver):
    """Drives the Gazer robotic arm over a serial (USB) connection.

    Combines the former ``HardwareBridge`` (serial transport) and
    ``GazerGestures`` (gesture library) into a single BodyDriver.
    """

    # Supported gesture names
    GESTURES = ["nod", "shake_head", "greet", "shy", "breathe", "tracking_look", "reset"]

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115200,
        timeout: float = 1.0,
    ):
        self._port = port  # None means auto-detect
        self._baudrate = baudrate
        self._timeout = timeout
        self._ser = None  # serial.Serial instance (lazy)
        self._lock = threading.Lock()
        self._connected = False

    # ------------------------------------------------------------------
    # BodyDriver interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if self._connected and self._ser and self._ser.is_open:
            return True

        import serial
        import serial.tools.list_ports

        target = self._port if self._port and self._port != "auto" else self._find_port()
        if not target:
            logger.warning("No suitable hardware port found.")
            return False

        try:
            self._ser = serial.Serial(target, self._baudrate, timeout=self._timeout)
            self._connected = True
            logger.info(f"Connected to hardware on {target}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to hardware: {e}")
            return False

    def disconnect(self) -> None:
        self._connected = False
        if self._ser:
            self._ser.close()
            logger.info("Hardware connection closed.")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def gesture(self, name: str, **kwargs) -> None:
        dispatch = {
            "nod": self._gesture_nod,
            "shake_head": self._gesture_shake_head,
            "greet": self._gesture_greet,
            "shy": self._gesture_shy,
            "breathe": self._gesture_breathe,
            "tracking_look": self._gesture_tracking_look,
            "reset": self._gesture_reset,
        }
        handler = dispatch.get(name)
        if handler:
            handler(**kwargs)

    def set_actuator(self, name: str, value: Any) -> None:
        self._set_servos({name: int(value)})

    def set_leds(self, rgb: List[int]) -> None:
        self._send_command("leds", {"rgb": rgb})

    @property
    def capabilities(self) -> List[str]:
        return list(self.GESTURES)

    # ------------------------------------------------------------------
    # Serial transport (from former HardwareBridge)
    # ------------------------------------------------------------------

    @staticmethod
    def _find_port() -> Optional[str]:
        import serial.tools.list_ports

        for port in serial.tools.list_ports.comports():
            if any(kw in port.description for kw in ("USB", "UART", "CH340")):
                return port.device
        return None

    def _send_command(self, cmd_type: str, data: Dict[str, Any]) -> None:
        if not self._ser or not self._ser.is_open:
            return
        payload = {"type": cmd_type, "data": data, "ts": int(time.time())}
        try:
            with self._lock:
                msg = json.dumps(payload) + "\n"
                self._ser.write(msg.encode("utf-8"))
        except Exception as e:
            logger.error(f"Serial send error: {e}")
            self._connected = False

    def _set_servos(self, angles: Dict[str, int]) -> None:
        self._send_command("servos", angles)

    # ------------------------------------------------------------------
    # Gesture library (from former GazerGestures)
    # ------------------------------------------------------------------

    def _run_steps(self, steps):
        """Execute a list of (angles_dict, delay) tuples synchronously."""
        for angles, delay in steps:
            self._set_servos(angles)
            if delay > 0:
                time.sleep(delay)

    def _gesture_reset(self, **_kw) -> None:
        self._set_servos({"head_yaw": 90, "head_pitch": 45, "arm_base": 90})

    def _gesture_nod(self, count: int = 2, **_kw) -> None:
        steps = []
        for _ in range(count):
            steps.append(({"head_pitch": 60}, 0.3))
            steps.append(({"head_pitch": 45}, 0.3))
        self._run_steps(steps)

    def _gesture_shake_head(self, count: int = 2, **_kw) -> None:
        steps = []
        for _ in range(count):
            steps.append(({"head_yaw": 110}, 0.3))
            steps.append(({"head_yaw": 70}, 0.3))
        steps.append(({"head_yaw": 90}, 0.0))
        self._run_steps(steps)

    def _gesture_greet(self, **_kw) -> None:
        self._run_steps([
            ({"head_yaw": 100}, 0.2),
            ({"head_pitch": 60}, 0.5),
            ({"head_yaw": 90, "head_pitch": 45, "arm_base": 90}, 0.0),
        ])

    def _gesture_shy(self, **_kw) -> None:
        self._run_steps([
            ({"head_pitch": 30, "head_yaw": 110}, 1.0),
            ({"head_yaw": 90, "head_pitch": 45, "arm_base": 90}, 0.0),
        ])

    def _gesture_tracking_look(self, x_offset: float = 0.0, y_offset: float = 0.0, **_kw) -> None:
        target_yaw = int(90 - (x_offset * 30))
        target_pitch = int(45 + (y_offset * 20))
        self._set_servos({"head_yaw": target_yaw, "head_pitch": target_pitch})

    def _gesture_breathe(self, **_kw) -> None:
        t = time.time()
        offset = math.sin(t * (2 * math.pi / 4.0)) * 2
        self._set_servos({"head_pitch": int(45 + offset)})
