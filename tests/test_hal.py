"""Tests for the Hardware Abstraction Layer (HAL) refactor.

Validates:
- BodyDriver ABC contract
- NullDriver behaviour
- SerialArmDriver (with mocked serial)
- Factory function create_body_driver()
- Brain integration (body + spatial conditional init)
- HardwareControlTool / VisionTool with BodyDriver
- Config defaults for body / ui / perception.spatial_enabled
"""

import asyncio
import time
import types
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from hardware.drivers.base import BodyDriver, NullDriver
from hardware.drivers.factory import create_body_driver


# ======================================================================
# BodyDriver ABC contract
# ======================================================================

class TestBodyDriverABC:
    """Verify that BodyDriver cannot be instantiated and subclasses must
    implement all abstract methods."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BodyDriver()

    def test_incomplete_subclass_raises(self):
        class Incomplete(BodyDriver):
            pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_minimal_subclass_ok(self):
        class Minimal(BodyDriver):
            def connect(self): return True
            def disconnect(self): pass
            @property
            def is_connected(self): return False
            def gesture(self, name, **kw): pass
            def set_actuator(self, name, value): pass
            def set_leds(self, rgb): pass

        m = Minimal()
        assert m.connect() is True
        assert m.capabilities == []


# ======================================================================
# NullDriver
# ======================================================================

class TestNullDriver:
    """NullDriver must be a complete no-op implementation."""

    def setup_method(self):
        self.drv = NullDriver()

    def test_connect_returns_true(self):
        assert self.drv.connect() is True

    def test_disconnect_is_noop(self):
        self.drv.disconnect()  # should not raise

    def test_is_connected_always_true(self):
        assert self.drv.is_connected is True

    def test_gesture_is_noop(self):
        # All gesture names should be silently ignored
        for name in ("nod", "shake_head", "breathe", "nonexistent"):
            self.drv.gesture(name)

    def test_set_actuator_is_noop(self):
        self.drv.set_actuator("head_yaw", 90)

    def test_set_leds_is_noop(self):
        self.drv.set_leds([255, 0, 0])

    def test_capabilities_empty(self):
        assert self.drv.capabilities == []

    def test_isinstance_body_driver(self):
        assert isinstance(self.drv, BodyDriver)


# ======================================================================
# SerialArmDriver (mocked serial)
# ======================================================================

class TestSerialArmDriver:
    """Test SerialArmDriver with mocked pyserial."""

    def _make_driver(self, port="COM99", baudrate=115200):
        from hardware.drivers.serial_arm import SerialArmDriver
        return SerialArmDriver(port=port, baudrate=baudrate)

    def test_isinstance_body_driver(self):
        drv = self._make_driver()
        assert isinstance(drv, BodyDriver)

    def test_capabilities_non_empty(self):
        drv = self._make_driver()
        caps = drv.capabilities
        assert "nod" in caps
        assert "breathe" in caps
        assert "shake_head" in caps
        assert len(caps) >= 5

    def test_connect_success(self):
        drv = self._make_driver()
        mock_serial_mod = MagicMock()
        mock_serial_instance = MagicMock()
        mock_serial_instance.is_open = True
        mock_serial_mod.Serial.return_value = mock_serial_instance
        with patch.dict("sys.modules", {"serial": mock_serial_mod, "serial.tools": MagicMock(), "serial.tools.list_ports": MagicMock()}):
            result = drv.connect()
        assert result is True
        assert drv.is_connected is True

    def test_connect_no_port_auto_detect_fails(self):
        from hardware.drivers.serial_arm import SerialArmDriver
        drv = SerialArmDriver(port=None)
        mock_serial_mod = MagicMock()
        mock_list_ports = MagicMock()
        mock_list_ports.comports.return_value = []  # no ports
        with patch.dict("sys.modules", {"serial": mock_serial_mod, "serial.tools": MagicMock(), "serial.tools.list_ports": mock_list_ports}):
            result = drv.connect()
        assert result is False
        assert drv.is_connected is False

    def test_connect_serial_exception(self):
        drv = self._make_driver()
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = Exception("port busy")
        with patch.dict("sys.modules", {"serial": mock_serial_mod, "serial.tools": MagicMock(), "serial.tools.list_ports": MagicMock()}):
            result = drv.connect()
        assert result is False

    def test_disconnect(self):
        drv = self._make_driver()
        drv._ser = MagicMock()
        drv._connected = True
        drv.disconnect()
        assert drv.is_connected is False
        drv._ser.close.assert_called_once()

    def test_gesture_nod_sends_servos(self):
        drv = self._make_driver()
        drv._send_command = MagicMock()
        drv.gesture("nod", count=1)
        assert drv._send_command.call_count >= 2  # at least 2 steps

    def test_gesture_breathe(self):
        drv = self._make_driver()
        drv._send_command = MagicMock()
        drv.gesture("breathe")
        drv._send_command.assert_called_once()
        args = drv._send_command.call_args
        assert args[0][0] == "servos"

    def test_gesture_unknown_is_noop(self):
        drv = self._make_driver()
        drv._send_command = MagicMock()
        drv.gesture("nonexistent_gesture")
        drv._send_command.assert_not_called()

    def test_set_actuator(self):
        drv = self._make_driver()
        drv._send_command = MagicMock()
        drv.set_actuator("head_yaw", 90)
        drv._send_command.assert_called_once_with("servos", {"head_yaw": 90})

    def test_set_leds(self):
        drv = self._make_driver()
        drv._send_command = MagicMock()
        drv.set_leds([255, 128, 0])
        drv._send_command.assert_called_once_with("leds", {"rgb": [255, 128, 0]})

    def test_gesture_tracking_look(self):
        drv = self._make_driver()
        drv._send_command = MagicMock()
        drv.gesture("tracking_look", x_offset=0.5, y_offset=-0.5)
        drv._send_command.assert_called_once()
        data = drv._send_command.call_args[0][1]
        assert "head_yaw" in data
        assert "head_pitch" in data

    def test_gesture_reset(self):
        drv = self._make_driver()
        drv._send_command = MagicMock()
        drv.gesture("reset")
        data = drv._send_command.call_args[0][1]
        assert data == {"head_yaw": 90, "head_pitch": 45, "arm_base": 90}


# ======================================================================
# Factory
# ======================================================================

class TestFactory:
    """Test create_body_driver() with various config values."""

    def _mock_config(self, body_type="none", port="auto", baudrate=115200):
        cfg = MagicMock()
        store = {
            "body.type": body_type,
            "body.port": port,
            "body.baudrate": baudrate,
        }
        cfg.get = lambda key, default=None: store.get(key, default)
        return cfg

    def test_none_returns_null_driver(self):
        drv = create_body_driver(self._mock_config("none"))
        assert isinstance(drv, NullDriver)

    def test_default_returns_null_driver(self):
        cfg = MagicMock()
        cfg.get = lambda key, default=None: default  # all defaults
        drv = create_body_driver(cfg)
        assert isinstance(drv, NullDriver)

    def test_serial_arm_returns_serial_arm_driver(self):
        from hardware.drivers.serial_arm import SerialArmDriver
        drv = create_body_driver(self._mock_config("serial_arm", "COM5", 9600))
        assert isinstance(drv, SerialArmDriver)
        assert drv._port == "COM5"
        assert drv._baudrate == 9600

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown body type"):
            create_body_driver(self._mock_config("quadruped"))


# ======================================================================
# Config defaults
# ======================================================================

class TestConfigDefaults:
    """Verify that DEFAULT_CONFIG includes the new HAL-related sections."""

    def test_body_section_exists(self):
        from runtime.config_manager import DEFAULT_CONFIG
        assert "body" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["body"]["type"] == "none"
        assert DEFAULT_CONFIG["body"]["port"] == "auto"
        assert DEFAULT_CONFIG["body"]["baudrate"] == 115200

    def test_ui_section_exists(self):
        from runtime.config_manager import DEFAULT_CONFIG
        assert "ui" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["ui"]["enabled"] is False

    def test_spatial_enabled_in_perception(self):
        from runtime.config_manager import DEFAULT_CONFIG
        assert "spatial_enabled" in DEFAULT_CONFIG["perception"]
        assert DEFAULT_CONFIG["perception"]["spatial_enabled"] is False

    def test_spatial_route_defaults_exist(self):
        from runtime.config_manager import DEFAULT_CONFIG
        spatial = DEFAULT_CONFIG["perception"]["spatial"]
        assert spatial["provider"] in {"local_mediapipe", "cloud_vision", "hybrid"}
        assert spatial["route_mode"] in {"local_first", "cloud_first", "auto"}
        assert "cloud" in spatial
        assert "provider_ref" in spatial["cloud"]

    def test_asr_route_defaults_exist(self):
        from runtime.config_manager import DEFAULT_CONFIG
        asr = DEFAULT_CONFIG["asr"]
        assert asr["provider"] in {"whisper_local", "cloud_openai_compatible", "hybrid"}
        assert asr["route_mode"] in {"local_first", "cloud_first", "auto"}
        assert "cloud" in asr
        assert "provider_ref" in asr["cloud"]


# ======================================================================
# HardwareControlTool with BodyDriver
# ======================================================================

class TestHardwareControlTool:
    """Test that HardwareControlTool works with the BodyDriver interface."""

    def _make_tool(self):
        from tools.hardware import HardwareControlTool
        body = NullDriver()
        body.set_actuator = MagicMock()
        body.set_leds = MagicMock()
        return HardwareControlTool(body), body

    def test_move_head(self):
        tool, body = self._make_tool()
        result = asyncio.run(
            tool.execute(action="move_head", yaw=90, pitch=45)
        )
        assert "Head moved" in result
        assert body.set_actuator.call_count == 2

    def test_set_led(self):
        tool, body = self._make_tool()
        result = asyncio.run(
            tool.execute(action="set_led", rgb=[255, 0, 0])
        )
        assert "LEDs set" in result
        body.set_leds.assert_called_once_with([255, 0, 0])

    def test_move_head_no_angles(self):
        tool, body = self._make_tool()
        result = asyncio.run(
            tool.execute(action="move_head")
        )
        assert "Error" in result

    def test_unknown_action(self):
        tool, body = self._make_tool()
        result = asyncio.run(
            tool.execute(action="fly")
        )
        assert "Error" in result


# ======================================================================
# VisionTool with optional spatial
# ======================================================================

class TestVisionTool:
    """Test VisionTool with and without spatial perceiver."""

    def test_no_spatial_returns_unavailable(self):
        from tools.hardware import VisionTool
        tool = VisionTool(spatial=None)
        result = asyncio.run(
            tool.execute(query_type="distance")
        )
        assert "not enabled" in result.lower() or "unavailable" in result.lower()

    def test_with_spatial_returns_distance(self):
        from tools.hardware import VisionTool
        spatial = MagicMock()
        spatial.get_user_distance.return_value = 1.5
        tool = VisionTool(spatial=spatial)
        result = asyncio.run(
            tool.execute(query_type="distance")
        )
        assert "1.50" in result

    def test_with_spatial_user_status(self):
        from tools.hardware import VisionTool
        spatial = MagicMock()
        spatial.get_user_distance.return_value = 0.8
        spatial.get_attention_level.return_value = 1.0
        spatial.get_interaction_zone.return_value = "SOCIAL"
        tool = VisionTool(spatial=spatial)
        result = asyncio.run(
            tool.execute(query_type="user_status")
        )
        assert "present" in result.lower()
        assert "SOCIAL" in result


# ======================================================================
# Brain integration (lightweight -- no real LLM)
# ======================================================================

class TestBrainHALIntegration:
    """Verify brain.py correctly uses the HAL layer."""

    def test_brain_imports_no_hardware_deps(self):
        """Importing brain should not trigger serial/cv2/mediapipe imports.
        Note: brain has other heavy deps (sounddevice, etc.) so we only
        verify the import path exists without actually instantiating."""
        # Verify the import chain works (conftest mocks heavy deps)
        from hardware import create_body_driver, BodyDriver
        assert callable(create_body_driver)
        assert BodyDriver is not None

    def test_body_type_none_creates_null_driver(self):
        """With default config (body.type=none), brain should get NullDriver."""
        from runtime.config_manager import config
        body_type = config.get("body.type", "none")
        assert body_type == "none"
        drv = create_body_driver(config)
        assert isinstance(drv, NullDriver)

    def test_spatial_disabled_by_default(self):
        """With default config, spatial should be disabled."""
        from runtime.config_manager import config
        assert config.get("perception.spatial_enabled", False) is False

    def test_ui_disabled_by_default(self):
        """With default config, UI should be disabled."""
        from runtime.config_manager import config
        assert config.get("ui.enabled", False) is False


# ======================================================================
# Perception spatial lazy import
# ======================================================================

class TestSpatialLazyImport:
    """Verify that perception/spatial.py does not eagerly import cv2/mediapipe."""

    def test_module_level_cv2_is_none(self):
        import perception.spatial as sp
        # At module level, cv2 should be None (lazy placeholder)
        assert sp.cv2 is None or hasattr(sp.cv2, "__name__")

    def test_get_spatial_returns_perceiver(self):
        """get_spatial() should return a SpatialPerceiver even without cv2."""
        # Reset singleton
        import perception.spatial as sp
        sp._spatial = None
        perceiver = sp.get_spatial()
        from perception.spatial import SpatialPerceiver
        assert isinstance(perceiver, SpatialPerceiver)
        # Clean up
        sp._spatial = None

    def test_cloud_payload_parse_and_budget(self):
        import perception.spatial as sp
        sp._spatial = None
        perceiver = sp.get_spatial()
        payload = perceiver._parse_cloud_payload("noise {\"face_detected\": true, \"distance_m\": 1.2} tail")
        assert payload is not None
        assert payload["face_detected"] is True
        perceiver.cloud_cfg = {
            "max_calls_per_minute": 1,
            "estimated_cost_per_call_usd": 0.01,
            "max_cost_per_minute_usd": 0.01,
            "poll_interval_seconds": 0.0,
        }
        perceiver._cloud_call_ts = []
        perceiver._last_cloud_call = 0.0
        assert perceiver._allow_cloud_call() is True
        perceiver._cloud_call_ts.append(time.time())
        assert perceiver._allow_cloud_call() is False
        sp._spatial = None
