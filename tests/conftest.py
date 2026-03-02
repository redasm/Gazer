"""Shared pytest fixtures for Gazer test suite."""

import os
import sys
import shutil
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# Ensure project root is on sys.path so all imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Mock heavy native dependencies that are not installed in test environments
# ---------------------------------------------------------------------------
if "faiss" not in sys.modules:
    sys.modules["faiss"] = MagicMock()

# Watchdog needs a real base class for FileSystemEventHandler so subclassing works
if "watchdog" not in sys.modules:
    _watchdog = ModuleType("watchdog")
    sys.modules["watchdog"] = _watchdog

    _watchdog_events = ModuleType("watchdog.events")

    class _FakeFileSystemEventHandler:
        pass

    class _FakeFileSystemEvent:
        def __init__(self, src_path, is_directory=False):
            self.src_path = src_path
            self.is_directory = is_directory

    _watchdog_events.FileSystemEventHandler = _FakeFileSystemEventHandler
    _watchdog_events.FileSystemEvent = _FakeFileSystemEvent
    sys.modules["watchdog.events"] = _watchdog_events

    _watchdog_observers = ModuleType("watchdog.observers")
    _watchdog_observers.Observer = MagicMock()
    sys.modules["watchdog.observers"] = _watchdog_observers

# Set working directory to project root for tests that depend on relative paths
os.chdir(PROJECT_ROOT)


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory that is auto-cleaned."""
    yield tmp_path


@pytest.fixture
def tmp_config_file(tmp_path):
    """Provide a temporary YAML config file path."""
    return str(tmp_path / "settings.yaml")


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory base directory."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    return str(mem_dir)
