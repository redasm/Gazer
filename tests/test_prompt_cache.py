from llm.prompt_cache import PromptSegmentCache


def test_prompt_cache_stable_prefix_hits_when_tail_changes():
    cache = PromptSegmentCache(
        enabled=True,
        ttl_seconds=300,
        max_items=32,
        segment_policy="stable_prefix",
    )
    base_messages = [
        {"role": "system", "content": "You are Gazer."},
        {"role": "user", "content": "Summarize this repo."},
    ]
    first = cache.observe(messages=base_messages, tools=[], model="m1")
    assert first["hit"] is False

    with_tool_tail = [
        *base_messages,
        {"role": "assistant", "content": "I will call tools.", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "name": "list_dir", "tool_call_id": "1", "content": "src\nweb\ntests"},
    ]
    second = cache.observe(messages=with_tool_tail, tools=[], model="m1")
    assert second["hit"] is True

    summary = cache.summary()
    assert summary["lookups"] == 2
    assert summary["hits"] == 1
    assert summary["misses"] == 1
    assert summary["estimated_saved_prompt_tokens"] > 0


def test_prompt_cache_full_prompt_misses_when_tail_changes():
    cache = PromptSegmentCache(
        enabled=True,
        ttl_seconds=300,
        max_items=32,
        segment_policy="full_prompt",
    )
    base_messages = [
        {"role": "system", "content": "You are Gazer."},
        {"role": "user", "content": "Summarize this repo."},
    ]
    cache.observe(messages=base_messages, tools=[], model="m1")

    with_tool_tail = [
        *base_messages,
        {"role": "assistant", "content": "I will call tools.", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "name": "list_dir", "tool_call_id": "1", "content": "src\nweb\ntests"},
    ]
    second = cache.observe(messages=with_tool_tail, tools=[], model="m1")
    assert second["hit"] is False

    summary = cache.summary()
    assert summary["lookups"] == 2
    assert summary["hits"] == 0
    assert summary["misses"] == 2

