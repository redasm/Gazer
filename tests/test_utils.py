"""Tests for runtime.utils -- atomic_write_json."""

import json
import os
from runtime.utils import atomic_write_json


class TestAtomicWriteJson:
    def test_basic_write(self, tmp_dir):
        path = str(tmp_dir / "test.json")
        data = {"key": "value", "num": 42}
        atomic_write_json(path, data)
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_dir):
        path = str(tmp_dir / "sub" / "deep" / "test.json")
        atomic_write_json(path, {"nested": True})
        assert os.path.exists(path)

    def test_unicode_content(self, tmp_dir):
        path = str(tmp_dir / "unicode.json")
        data = {"name": "小明", "greeting": "你好世界"}
        atomic_write_json(path, data)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["name"] == "小明"

    def test_overwrite_existing(self, tmp_dir):
        path = str(tmp_dir / "overwrite.json")
        atomic_write_json(path, {"v": 1})
        atomic_write_json(path, {"v": 2})
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["v"] == 2

    def test_no_tmp_file_left_on_success(self, tmp_dir):
        path = str(tmp_dir / "clean.json")
        atomic_write_json(path, {"ok": True})
        files = os.listdir(str(tmp_dir))
        assert len(files) == 1
        assert files[0] == "clean.json"
