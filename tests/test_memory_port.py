"""Tests for soul.memory.memory_port — Issue-08 acceptance criteria.

Verifies:
  - ``InMemoryMemoryPort`` basic CRUD operations
  - ``MemoryPort`` ABC cannot be instantiated directly
"""

import pytest

from soul.memory.memory_port import InMemoryMemoryPort, MemoryPort


class TestMemoryPortABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            MemoryPort()  # type: ignore[abstract]


class TestInMemoryMemoryPort:
    @pytest.mark.asyncio
    async def test_store_and_query(self) -> None:
        port = InMemoryMemoryPort()
        await port.store("key1", {"data": "hello"})
        await port.store("key2", {"data": "world"})
        results = await port.query("anything", top_k=5)
        assert "key1" in results
        assert "key2" in results

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        port = InMemoryMemoryPort()
        await port.store("key1", {"data": "hello"})
        assert port.count() == 1
        deleted = await port.delete("key1")
        assert deleted is True
        assert port.count() == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self) -> None:
        port = InMemoryMemoryPort()
        deleted = await port.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_query_empty(self) -> None:
        port = InMemoryMemoryPort()
        results = await port.query("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_query_top_k(self) -> None:
        port = InMemoryMemoryPort()
        for i in range(10):
            await port.store(f"key{i}", {"i": i})
        results = await port.query("anything", top_k=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_get_helper(self) -> None:
        port = InMemoryMemoryPort()
        await port.store("key1", {"data": "hello"})
        record = port.get("key1")
        assert record is not None
        assert record["data"] == "hello"
        assert port.get("missing") is None

    @pytest.mark.asyncio
    async def test_query_with_affect_none(self) -> None:
        """current_affect=None should not raise."""
        port = InMemoryMemoryPort()
        await port.store("k", {"v": 1})
        results = await port.query("anything", current_affect=None)
        assert len(results) == 1
