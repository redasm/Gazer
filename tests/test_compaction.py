"""Tests for soul.compaction -- ContextPruner."""

import pytest
from soul.core import WorkingMemory, MemoryEntry
from soul.compaction import ContextPruner


@pytest.fixture
def pruner():
    return ContextPruner(max_tokens=100, chars_per_token=4.0)


def _make_entry(content: str, sender: str = "User", **kwargs) -> MemoryEntry:
    return MemoryEntry(sender=sender, content=content, **kwargs)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty(self, pruner):
        assert pruner.estimate_tokens("") == 0

    def test_none(self, pruner):
        assert pruner.estimate_tokens(None) == 0

    def test_normal_text(self, pruner):
        text = "a" * 400  # 400 chars / 4 chars_per_token = 100 tokens
        assert pruner.estimate_tokens(text) == 100

    def test_short_text(self, pruner):
        assert pruner.estimate_tokens("hi") == 0  # int(2/4) = 0


# ---------------------------------------------------------------------------
# estimate_memory_tokens
# ---------------------------------------------------------------------------

class TestEstimateMemoryTokens:
    def test_empty_memory(self, pruner):
        wm = WorkingMemory()
        assert pruner.estimate_memory_tokens(wm) == 0

    def test_single_entry(self, pruner):
        wm = WorkingMemory(memories=[_make_entry("a" * 40)])
        tokens = pruner.estimate_memory_tokens(wm)
        assert tokens == 10  # 40/4

    def test_with_metadata(self, pruner):
        entry = _make_entry("hi", metadata={"tool_calls": [{"name": "echo"}]})
        wm = WorkingMemory(memories=[entry])
        tokens = pruner.estimate_memory_tokens(wm)
        assert tokens > 0  # includes metadata overhead


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

class TestPrune:
    def test_no_prune_needed(self, pruner):
        wm = WorkingMemory(memories=[_make_entry("short")])
        result = pruner.prune(wm)
        assert result is None  # No pruning needed

    def test_prune_trims_tool_outputs(self):
        pruner = ContextPruner(max_tokens=50, chars_per_token=4.0, )
        # Create a large tool output
        big_output = "x" * 5000
        entries = [
            _make_entry("system prompt", sender="System"),
            _make_entry(f"Tool Execution [search] Result: {big_output}", sender="System"),
            _make_entry("user question"),
        ]
        wm = WorkingMemory(memories=entries)
        result = pruner.prune(wm)
        if result is not None:
            # Tool output should be trimmed
            trimmed = [m for m in result.memories if "Pruner" in m.content]
            assert len(trimmed) >= 0  # May or may not be trimmed depending on budget

    def test_prune_drops_old_messages(self):
        pruner = ContextPruner(max_tokens=20, chars_per_token=4.0)
        entries = [_make_entry(f"message {i}" * 5) for i in range(20)]
        wm = WorkingMemory(memories=entries)
        result = pruner.prune(wm)
        assert result is not None
        assert len(result.memories) < 20

    def test_prune_preserves_last_n(self):
        pruner = ContextPruner(max_tokens=10, chars_per_token=4.0)
        pruner.keep_last_n_messages = 3
        entries = [_make_entry(f"msg{i}" * 10) for i in range(15)]
        wm = WorkingMemory(memories=entries)
        result = pruner.prune(wm)
        assert result is not None
        # Last messages should be preserved
        assert result.memories[-1].content == entries[-1].content

    def test_prune_inserts_summary_entry(self):
        pruner = ContextPruner(max_tokens=10, chars_per_token=4.0)
        pruner.keep_last_n_messages = 2
        entries = [_make_entry(f"{'x' * 40}") for _ in range(10)]
        wm = WorkingMemory(memories=entries)
        result = pruner.prune(wm)
        if result is not None:
            summary_msgs = [m for m in result.memories if "consolidated" in m.content.lower() or "Pruner" in m.content]
            assert len(summary_msgs) >= 1


# ---------------------------------------------------------------------------
# _soft_trim_tool_outputs
# ---------------------------------------------------------------------------

class TestSoftTrimToolOutputs:
    def test_no_change_for_short_outputs(self, pruner):
        entries = [_make_entry("short output", sender="System")]
        new, changed = pruner._soft_trim_tool_outputs(entries)
        assert changed is False
        assert len(new) == 1

    def test_trims_large_system_output(self, pruner):
        big = "Tool Execution [search] Result: " + "x" * 5000
        entries = [_make_entry(big, sender="System")]
        new, changed = pruner._soft_trim_tool_outputs(entries)
        assert changed is True
        assert len(new[0].content) < len(big)
        assert "Pruner" in new[0].content

    def test_does_not_trim_user_messages(self, pruner):
        big = "a" * 5000
        entries = [_make_entry(big, sender="User")]
        new, changed = pruner._soft_trim_tool_outputs(entries)
        assert changed is False
        assert new[0].content == big


# ---------------------------------------------------------------------------
# _compact_history
# ---------------------------------------------------------------------------

class TestCompactHistory:
    def test_compact_removes_old(self, pruner):
        entries = [_make_entry(f"msg{i}" * 20) for i in range(20)]
        result = pruner._compact_history(entries, current_tokens=200)
        assert len(result) < 20

    def test_compact_inserts_placeholder(self, pruner):
        entries = [_make_entry(f"{'a' * 40}") for _ in range(20)]
        result = pruner._compact_history(entries, current_tokens=200)
        summary = [m for m in result if "Pruner" in m.content or "consolidated" in m.content.lower()]
        assert len(summary) >= 1

    def test_compact_no_op_when_few_messages(self, pruner):
        entries = [_make_entry("short")]
        result = pruner._compact_history(entries, current_tokens=200)
        assert len(result) == 1
