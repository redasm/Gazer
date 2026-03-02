"""Tests for memory.openviking_bootstrap."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory.openviking_bootstrap import ensure_openviking_ready, load_openviking_settings


class _FakeConfig:
    def __init__(self, data: dict):
        self._data = data

    def get(self, key_path: str, default=None):
        cur = self._data
        for key in key_path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return default
        return cur


def test_load_openviking_settings_parses_defaults(tmp_path: Path):
    cfg = _FakeConfig(
        {
            "memory": {
                "context_backend": {
                    "enabled": True,
                    "mode": "openviking",
                    "data_dir": str(tmp_path / "ov"),
                    "config_file": "",
                    "session_prefix": "gazer",
                    "default_user": "owner",
                }
            }
        }
    )
    settings = load_openviking_settings(cfg)
    assert settings.enabled is True
    assert settings.mode == "openviking"
    assert settings.data_dir == (tmp_path / "ov").resolve()
    assert settings.session_prefix == "gazer"
    assert settings.default_user == "owner"


def test_load_openviking_settings_rejects_invalid_mode():
    cfg = _FakeConfig(
        {
            "memory": {
                "context_backend": {
                    "enabled": True,
                    "mode": "local",
                    "data_dir": "data/openviking",
                }
            }
        }
    )
    with pytest.raises(RuntimeError, match="Invalid memory.context_backend.mode"):
        load_openviking_settings(cfg)


def test_ensure_openviking_ready_when_disabled_does_not_import(monkeypatch):
    cfg = _FakeConfig(
        {
            "memory": {
                "context_backend": {
                    "enabled": False,
                    "mode": "openviking",
                    "data_dir": "data/openviking",
                }
            }
        }
    )
    called = {"count": 0}

    def _boom(_name: str):
        called["count"] += 1
        raise AssertionError("import should not be called when disabled")

    monkeypatch.setattr("memory.openviking_bootstrap.importlib.import_module", _boom)
    settings = ensure_openviking_ready(cfg)
    assert settings.enabled is False
    assert called["count"] == 0


def test_ensure_openviking_ready_requires_installed_package(monkeypatch):
    cfg = _FakeConfig(
        {
            "memory": {
                "context_backend": {
                    "enabled": True,
                    "mode": "openviking",
                    "data_dir": "data/openviking",
                }
            }
        }
    )

    def _raise_module_not_found(_name: str):
        raise ModuleNotFoundError("No module named 'openviking'")

    monkeypatch.setattr(
        "memory.openviking_bootstrap.importlib.import_module",
        _raise_module_not_found,
    )
    with pytest.raises(RuntimeError, match="package 'openviking' is unavailable"):
        ensure_openviking_ready(cfg)


def test_ensure_openviking_ready_requires_existing_config_file(monkeypatch, tmp_path: Path):
    cfg = _FakeConfig(
        {
            "memory": {
                "context_backend": {
                    "enabled": True,
                    "mode": "openviking",
                    "data_dir": str(tmp_path / "data"),
                    "config_file": str(tmp_path / "missing_ov.conf"),
                }
            }
        }
    )

    monkeypatch.setattr(
        "memory.openviking_bootstrap.importlib.import_module",
        lambda _name: SimpleNamespace(__name__="openviking"),
    )

    with pytest.raises(RuntimeError, match="config_file is configured but file does not exist"):
        ensure_openviking_ready(cfg)


def test_ensure_openviking_ready_sets_env_and_creates_data_dir(monkeypatch, tmp_path: Path):
    ov_conf = tmp_path / "ov.conf"
    ov_conf.write_text("{}", encoding="utf-8")
    data_dir = tmp_path / "viking_data"
    cfg = _FakeConfig(
        {
            "memory": {
                "context_backend": {
                    "enabled": True,
                    "mode": "openviking",
                    "data_dir": str(data_dir),
                    "config_file": str(ov_conf),
                    "session_prefix": "branch",
                    "default_user": "tester",
                }
            }
        }
    )

    monkeypatch.setattr(
        "memory.openviking_bootstrap.importlib.import_module",
        lambda _name: SimpleNamespace(__name__="openviking"),
    )
    monkeypatch.delenv("OPENVIKING_CONFIG_FILE", raising=False)

    settings = ensure_openviking_ready(cfg)

    assert settings.enabled is True
    assert settings.mode == "openviking"
    assert settings.data_dir == data_dir.resolve()
    assert settings.session_prefix == "branch"
    assert settings.default_user == "tester"
    assert data_dir.is_dir()
    assert Path(str(os.getenv("OPENVIKING_CONFIG_FILE", ""))).resolve() == ov_conf.resolve()
