"""Tests for runtime.rate_limiter -- RateLimiter."""

import time
from unittest.mock import patch
from runtime.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allow_within_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        assert rl.allow("user1") is True
        assert rl.allow("user1") is True
        assert rl.allow("user1") is True
        assert rl.allow("user1") is False  # 4th should be blocked

    def test_different_keys_independent(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        assert rl.allow("a") is True
        assert rl.allow("b") is True
        assert rl.allow("a") is False
        assert rl.allow("b") is False

    def test_remaining(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        assert rl.remaining("k") == 5
        rl.allow("k")
        assert rl.remaining("k") == 4
        rl.allow("k")
        assert rl.remaining("k") == 3

    def test_reset_specific_key(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        rl.allow("k1")
        rl.allow("k2")
        rl.reset("k1")
        assert rl.remaining("k1") == 2
        assert rl.remaining("k2") == 1

    def test_reset_all(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        rl.allow("k1")
        rl.allow("k2")
        rl.reset()
        assert rl.remaining("k1") == 2
        assert rl.remaining("k2") == 2

    def test_default_key(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        assert rl.allow() is True
        assert rl.allow() is False
