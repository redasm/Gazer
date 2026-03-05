"""Tests for tools.base -- Tool, CancellationToken."""

import asyncio
import pytest
from tools.base import Tool, CancellationToken


# Concrete tool for testing
class EchoTool(Tool):
    @property
    def name(self):
        return "echo"

    @property
    def description(self):
        return "Echoes input"

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 100},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["text"],
        }

    async def execute(self, text="", count=1, **kwargs):
        return text * count


class TestCancellationToken:
    def test_initial_state(self):
        token = CancellationToken()
        assert token.is_cancelled is False

    def test_cancel(self):
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled is True

    def test_raise_if_cancelled(self):
        token = CancellationToken()
        token.raise_if_cancelled()  # should not raise

        token.cancel()
        with pytest.raises(asyncio.CancelledError):
            token.raise_if_cancelled()

    @pytest.mark.asyncio
    async def test_wait(self):
        token = CancellationToken()

        async def cancel_later():
            await asyncio.sleep(0.05)
            token.cancel()

        asyncio.create_task(cancel_later())
        await asyncio.wait_for(token.wait(), timeout=2)
        assert token.is_cancelled is True





class TestTool:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Tool()

    def test_to_schema(self):
        tool = EchoTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "echo"
        assert "parameters" in schema["function"]

    def test_default_owner_only(self):
        tool = EchoTool()
        assert tool.owner_only is False



    @pytest.mark.asyncio
    async def test_execute(self):
        tool = EchoTool()
        result = await tool.execute(text="hi", count=3)
        assert result == "hihihi"

    def test_validate_params_valid(self):
        tool = EchoTool()
        errors = tool.validate_params({"text": "hello", "count": 3})
        assert errors == []

    def test_validate_params_missing_required(self):
        tool = EchoTool()
        errors = tool.validate_params({"count": 1})
        assert any("text" in e for e in errors)

    def test_validate_params_wrong_type(self):
        tool = EchoTool()
        errors = tool.validate_params({"text": 123})
        assert len(errors) > 0

    def test_validate_params_out_of_range(self):
        tool = EchoTool()
        errors = tool.validate_params({"text": "ok", "count": 100})
        assert any("maximum" in e or "<=" in e for e in errors)

    def test_validate_params_string_length(self):
        tool = EchoTool()
        errors = tool.validate_params({"text": ""})
        assert any("minLength" in e or "at least" in e for e in errors)
