"""Tests for multi_agent.communication.Blackboard."""

import pytest

from multi_agent.communication import Blackboard


@pytest.fixture
def bb():
    return Blackboard(session_id="test-session")


@pytest.mark.asyncio
class TestBlackboardReadWrite:
    async def test_write_and_read(self, bb: Blackboard):
        ref = await bb.write("key1", "value1", agent_id="w1")
        assert "test-session" in ref
        assert "results" in ref

        val = await bb.read("key1")
        assert val == "value1"

    async def test_read_missing_key(self, bb: Blackboard):
        val = await bb.read("nonexistent")
        assert val is None

    async def test_namespace_isolation(self, bb: Blackboard):
        await bb.write("k", "val-results", agent_id="w1", namespace="results")
        await bb.write("k", "val-knowledge", agent_id="w1", namespace="knowledge")

        assert await bb.read("k", namespace="results") == "val-results"
        assert await bb.read("k", namespace="knowledge") == "val-knowledge"


@pytest.mark.asyncio
class TestBlackboardContext:
    async def test_write_and_read_context(self, bb: Blackboard):
        await bb.write_context("plan", "do stuff")
        val = await bb.read_context("plan")
        assert val == "do stuff"


@pytest.mark.asyncio
class TestBlackboardSearch:
    async def test_fallback_search_substring(self, bb: Blackboard):
        await bb.write("analysis-1", "quantum computing results", agent_id="w1")
        await bb.write("analysis-2", "neural network training", agent_id="w2")

        results = await bb.search("quantum")
        assert len(results) >= 1
        assert any("quantum" in str(r.get("value", "")) for r in results)

    async def test_fallback_search_by_key(self, bb: Blackboard):
        await bb.write("my-report", "some content", agent_id="w1")
        results = await bb.search("my-report")
        assert len(results) >= 1

    async def test_search_limit(self, bb: Blackboard):
        for i in range(10):
            await bb.write(f"item-{i}", f"data {i}", agent_id="w1")
        results = await bb.search("data", limit=3)
        assert len(results) <= 3


@pytest.mark.asyncio
class TestBlackboardGetAll:
    async def test_get_all(self, bb: Blackboard):
        await bb.write("a", "1", agent_id="w1")
        await bb.write("b", "2", agent_id="w2")
        all_results = bb.get_all("results")
        assert all_results == {"a": "1", "b": "2"}

    async def test_get_all_empty_namespace(self, bb: Blackboard):
        all_results = bb.get_all("coordination")
        assert all_results == {}
