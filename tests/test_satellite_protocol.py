import pytest

from devices.satellite_protocol import (
    FRAME_TYPE_HELLO,
    FRAME_TYPE_INVOKE_RESULT,
    SatelliteProtocolError,
    ensure_frame,
    ensure_hello,
    ensure_invoke_result,
)


def test_ensure_frame_rejects_unknown_type() -> None:
    with pytest.raises(SatelliteProtocolError):
        ensure_frame({"type": "unknown"})


def test_ensure_hello_requires_node_and_token() -> None:
    with pytest.raises(SatelliteProtocolError):
        ensure_hello({"type": FRAME_TYPE_HELLO, "node_id": "", "token": ""})


def test_ensure_invoke_result_valid_payload() -> None:
    result = ensure_invoke_result(
        {
            "type": FRAME_TYPE_INVOKE_RESULT,
            "request_id": "req-1",
            "ok": True,
            "message": "done",
            "data": {"k": "v"},
        }
    )
    assert result["request_id"] == "req-1"
    assert result["ok"] is True
    assert result["data"]["k"] == "v"
