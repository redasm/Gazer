"""Tests for src.rendering.types — RenderHint and MessageBlock contracts."""

from __future__ import annotations

import json

import pytest

from src.rendering.constants import (
    MAX_RENDER_HINT_DATA_BYTES,
    PROTOCOL_VERSION,
)
from src.rendering.types import RenderHint, RenderHintError


class TestRenderHint:
    def test_minimal_valid_hint(self) -> None:
        hint = RenderHint(
            component="WeatherCard",
            data={"city": "Beijing", "temp": 22},
            fallback_text="Beijing 22°C",
        )
        assert hint.component == "WeatherCard"
        assert hint.data == {"city": "Beijing", "temp": 22}
        assert hint.fallback_text == "Beijing 22°C"
        assert hint.version == PROTOCOL_VERSION

    def test_is_frozen(self) -> None:
        hint = RenderHint(component="X", data={}, fallback_text="x")
        with pytest.raises(Exception):
            hint.component = "Y"  # type: ignore[misc]

    def test_empty_component_rejected(self) -> None:
        with pytest.raises(RenderHintError, match="component"):
            RenderHint(component="", data={}, fallback_text="x")

    def test_whitespace_component_rejected(self) -> None:
        with pytest.raises(RenderHintError, match="component"):
            RenderHint(component="   ", data={}, fallback_text="x")

    def test_non_dict_data_rejected(self) -> None:
        with pytest.raises(RenderHintError, match="data"):
            RenderHint(component="X", data=[1, 2, 3], fallback_text="x")  # type: ignore[arg-type]

    def test_empty_fallback_rejected(self) -> None:
        with pytest.raises(RenderHintError, match="fallback_text"):
            RenderHint(component="X", data={}, fallback_text="")

    def test_non_json_serializable_data_rejected(self) -> None:
        class Unserializable:
            pass

        with pytest.raises(RenderHintError, match="JSON"):
            RenderHint(
                component="X",
                data={"obj": Unserializable()},
                fallback_text="x",
            )

    def test_data_exceeding_size_limit_rejected(self) -> None:
        oversized = {"blob": "a" * (MAX_RENDER_HINT_DATA_BYTES + 1)}
        with pytest.raises(RenderHintError, match="exceeds"):
            RenderHint(component="X", data=oversized, fallback_text="x")

    def test_to_dict_round_trip(self) -> None:
        hint = RenderHint(
            component="ChartBlock",
            data={"series": [1, 2, 3]},
            fallback_text="chart",
        )
        payload = hint.to_dict()
        assert json.loads(json.dumps(payload)) == payload
        assert payload["component"] == "ChartBlock"
        assert payload["version"] == PROTOCOL_VERSION

    def test_custom_version_preserved(self) -> None:
        hint = RenderHint(
            component="X", data={}, fallback_text="x", version="2.0-beta",
        )
        assert hint.version == "2.0-beta"

    def test_empty_version_rejected(self) -> None:
        with pytest.raises(RenderHintError, match="version"):
            RenderHint(component="X", data={}, fallback_text="x", version="")

    def test_unicode_fallback_allowed(self) -> None:
        hint = RenderHint(component="X", data={}, fallback_text="温度 22°C")
        assert hint.fallback_text == "温度 22°C"
