import asyncio
from queue import Queue
from types import SimpleNamespace

from devices.adapters.body_hardware import BodyHardwareNode
from hardware.drivers.base import NullDriver


def test_body_hardware_node_info_includes_capabilities() -> None:
    node = BodyHardwareNode(body=NullDriver(), node_id="body-main", label="Body Node")
    payload = node.info().to_dict()
    actions = {item["action"] for item in payload["capabilities"]}
    assert "hardware.status" in actions
    assert "hardware.move_head" in actions
    assert "hardware.set_led" in actions
    assert payload["node_id"] == "body-main"


def test_body_hardware_node_move_head_and_led() -> None:
    node = BodyHardwareNode(body=NullDriver())
    ok_move = asyncio.run(node.invoke("hardware.move_head", {"yaw": 90, "pitch": 45}))
    assert ok_move.ok is True

    ok_led = asyncio.run(node.invoke("hardware.set_led", {"rgb": [255, 0, 0]}))
    assert ok_led.ok is True


def test_body_hardware_node_validates_args() -> None:
    node = BodyHardwareNode(body=NullDriver())
    bad_move = asyncio.run(node.invoke("hardware.move_head", {}))
    assert bad_move.ok is False
    assert bad_move.code == "DEVICE_ARG_REQUIRED"

    bad_led = asyncio.run(node.invoke("hardware.set_led", {"rgb": [1, 2]}))
    assert bad_led.ok is False
    assert bad_led.code == "DEVICE_ARG_RGB_INVALID"


def test_body_hardware_node_connect_disconnect_gate() -> None:
    node = BodyHardwareNode(body=NullDriver(), allow_connect_control=False)
    denied = asyncio.run(node.invoke("hardware.connect", {}))
    assert denied.ok is False
    assert denied.code == "DEVICE_ACTION_DISABLED"


def test_body_hardware_node_vision_audio_display_actions() -> None:
    spatial = SimpleNamespace(get_user_distance=lambda: 1.25)
    audio = SimpleNamespace(
        record_and_transcribe_structured=lambda duration=3, sample_rate=16000: {
            "text": "hello",
            "source": "mock",
        },
        speak=lambda text: None,
    )
    ui = Queue()
    node = BodyHardwareNode(body=NullDriver(), spatial=spatial, audio=audio, ui_queue=ui)

    vision = asyncio.run(node.invoke("hardware.vision.distance", {}))
    assert vision.ok is True
    assert vision.data["distance_m"] == 1.25

    asr = asyncio.run(node.invoke("hardware.audio.transcribe", {"duration": 1}))
    assert asr.ok is True
    assert asr.data["text"] == "hello"

    speak = asyncio.run(node.invoke("hardware.audio.speak", {"text": "hi"}))
    assert speak.ok is True

    display = asyncio.run(node.invoke("hardware.display.message", {"text": "status"}))
    assert display.ok is True
    assert not ui.empty()
