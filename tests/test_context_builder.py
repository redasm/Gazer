"""Tests for agent.context -- ContextBuilder."""

import pytest
import base64
from pathlib import Path
from agent.context import ContextBuilder


class TestBuildSystemPrompt:
    @pytest.fixture
    def builder(self, tmp_dir):
        return ContextBuilder(workspace=tmp_dir)

    def test_contains_identity(self, builder):
        prompt = builder.build_system_prompt()
        assert "Gazer" in prompt
        assert "embodied AI companion" in prompt

    def test_contains_workspace(self, builder, tmp_dir):
        prompt = builder.build_system_prompt()
        assert str(tmp_dir.resolve()) in prompt


class TestBuildMessages:
    @pytest.fixture
    def builder(self, tmp_dir):
        return ContextBuilder(workspace=tmp_dir)

    def test_basic_structure(self, builder):
        msgs = builder.build_messages(
            history=[],
            current_message="hello",
        )
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "hello"

    def test_history_preserved(self, builder):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
        ]
        msgs = builder.build_messages(history=history, current_message="new")
        # system + 2 history + 1 current
        assert len(msgs) == 4
        assert msgs[1]["content"] == "hi"
        assert msgs[2]["content"] == "hey"

    def test_channel_info_injected(self, builder):
        msgs = builder.build_messages(
            history=[],
            current_message="x",
            channel="telegram",
            chat_id="123",
        )
        system = msgs[0]["content"]
        assert "telegram" in system
        assert "123" in system

    def test_no_channel_info_when_absent(self, builder):
        msgs = builder.build_messages(history=[], current_message="x")
        system = msgs[0]["content"]
        assert "Current Session" not in system


class TestBuildUserContent:
    @pytest.fixture
    def builder(self, tmp_dir):
        return ContextBuilder(workspace=tmp_dir)

    def test_text_only(self, builder):
        result = builder._build_user_content("hello", None)
        assert result == "hello"

    def test_empty_media(self, builder):
        result = builder._build_user_content("hello", [])
        assert result == "hello"

    def test_image_media(self, builder, tmp_dir):
        img = tmp_dir / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        result = builder._build_user_content("describe", [str(img)])
        assert isinstance(result, list)
        assert any(item.get("type") == "image_url" for item in result)
        assert any(item.get("type") == "text" for item in result)

    def test_nonexistent_media_ignored(self, builder):
        result = builder._build_user_content("hello", ["/no/such/file.png"])
        assert result == "hello"

    def test_non_image_media_ignored(self, builder, tmp_dir):
        txt = tmp_dir / "notes.txt"
        txt.write_text("not an image")
        result = builder._build_user_content("hello", [str(txt)])
        assert result == "hello"


class TestHelperMethods:
    @pytest.fixture
    def builder(self, tmp_dir):
        return ContextBuilder(workspace=tmp_dir)

    def test_add_tool_result(self, builder):
        msgs = [{"role": "user", "content": "do it"}]
        builder.add_tool_result(msgs, "tc_1", "echo", "done")
        assert len(msgs) == 2
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["tool_call_id"] == "tc_1"
        assert msgs[1]["name"] == "echo"
        assert msgs[1]["content"] == "done"

    def test_add_assistant_message(self, builder):
        msgs = []
        builder.add_assistant_message(msgs, "hi")
        assert msgs[0] == {"role": "assistant", "content": "hi"}

    def test_add_assistant_message_with_tool_calls(self, builder):
        msgs = []
        tc = [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
        builder.add_assistant_message(msgs, None, tool_calls=tc)
        assert msgs[0]["tool_calls"] == tc
        assert msgs[0]["content"] == ""
