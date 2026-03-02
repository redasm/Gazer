"""Sliding-window rate limiter for message processing.

Prevents abuse by limiting the number of messages processed within a
configurable time window, per sender or globally.
"""

import logging
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("RateLimiter")


class RateLimiter:
    """Sliding-window rate limiter.

    Tracks message timestamps per key (e.g. sender_id or channel:chat_id)
    and rejects requests that exceed the configured rate.

    Parameters
    ----------
    max_requests : int
        Maximum requests allowed within ``window_seconds``.
    window_seconds : float
        Sliding window duration in seconds.
    """

    def __init__(self, max_requests: int = 20, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str = "__global__") -> bool:
        """Check if a request from *key* should be allowed.

        Returns True if within limits, False if rate-limited.
        """
        now = time.monotonic()
        window_start = now - self.window_seconds

        # Prune old timestamps
        timestamps = self._timestamps[key]
        self._timestamps[key] = [t for t in timestamps if t > window_start]

        if len(self._timestamps[key]) >= self.max_requests:
            return False

        self._timestamps[key].append(now)
        return True

    def remaining(self, key: str = "__global__") -> int:
        """Return the number of remaining allowed requests within the window."""
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = self._timestamps.get(key, [])
        active = [t for t in timestamps if t > window_start]
        return max(0, self.max_requests - len(active))

    def reset(self, key: Optional[str] = None) -> None:
        """Reset rate limit state for a key, or all keys if None."""
        if key is None:
            self._timestamps.clear()
        else:
            self._timestamps.pop(key, None)
