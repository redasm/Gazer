from llm.prompt_cache import PromptSegmentCache


def test_prompt_cache_scope_isolation_prevents_cross_session_hit():
    cache = PromptSegmentCache(
        enabled=True,
        ttl_seconds=300,
        max_items=64,
        segment_policy="stable_prefix",
        scope_fields=["session_key", "channel", "sender_id"],
        sanitize_sensitive=True,
    )
    messages = [
        {"role": "system", "content": "You are Gazer."},
        {"role": "user", "content": "请总结项目现状"},
    ]

    first = cache.observe(
        messages=messages,
        tools=[],
        model="m1",
        scope={"session_key": "web:chat-1", "channel": "web", "sender_id": "owner-a"},
    )
    second = cache.observe(
        messages=messages,
        tools=[],
        model="m1",
        scope={"session_key": "web:chat-2", "channel": "web", "sender_id": "owner-b"},
    )
    third = cache.observe(
        messages=messages,
        tools=[],
        model="m1",
        scope={"session_key": "web:chat-1", "channel": "web", "sender_id": "owner-a"},
    )

    assert first["hit"] is False
    assert second["hit"] is False
    assert third["hit"] is True
    summary = cache.summary()
    assert summary["hits"] == 1
    assert summary["misses"] == 2


def test_prompt_cache_sanitizes_sensitive_material_before_keying():
    cache = PromptSegmentCache(
        enabled=True,
        ttl_seconds=300,
        max_items=64,
        segment_policy="full_prompt",
        scope_fields=["session_key"],
        sanitize_sensitive=True,
    )
    scope = {"session_key": "web:chat-1"}

    first = cache.observe(
        messages=[
            {"role": "system", "content": "You are Gazer."},
            {
                "role": "user",
                "content": "deploy with api_key=sk-AAAAAAAAAAAAAAAAAAAAAA and keep secret",
            },
        ],
        tools=[],
        model="m1",
        scope=scope,
    )
    second = cache.observe(
        messages=[
            {"role": "system", "content": "You are Gazer."},
            {
                "role": "user",
                "content": "deploy with api_key=sk-BBBBBBBBBBBBBBBBBBBBBB and keep secret",
            },
        ],
        tools=[],
        model="m1",
        scope=scope,
    )
    third = cache.observe(
        messages=[
            {"role": "system", "content": "You are Gazer."},
            {
                "role": "user",
                "content": {"prompt": "run", "api_key": "token-one"},
            },
        ],
        tools=[],
        model="m1",
        scope=scope,
    )
    fourth = cache.observe(
        messages=[
            {"role": "system", "content": "You are Gazer."},
            {
                "role": "user",
                "content": {"prompt": "run", "api_key": "token-two"},
            },
        ],
        tools=[],
        model="m1",
        scope=scope,
    )

    assert first["hit"] is False
    assert second["hit"] is True
    assert third["hit"] is False
    assert fourth["hit"] is True
