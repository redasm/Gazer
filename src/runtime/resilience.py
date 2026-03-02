"""Shared resilience primitives: retry budget and error classification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetryBudget:
    """Token bucket used to limit retries across a single run."""

    total: int
    remaining: int

    @classmethod
    def from_total(cls, total: int) -> "RetryBudget":
        safe_total = max(0, int(total))
        return cls(total=safe_total, remaining=safe_total)

    def consume(self, amount: int = 1) -> bool:
        use = max(1, int(amount))
        if self.remaining < use:
            return False
        self.remaining -= use
        return True


def classify_error_message(message: str) -> str:
    """Classify an error string into retryability buckets."""
    text = str(message or "").lower()

    non_retryable_markers = (
        "invalid parameter",
        "invalid arguments",
        "schema",
        "forbidden",
        "unauthorized",
        "permission denied",
        "not found",
        "dependency",
        "blocked by",
        "tool '",
        "must be",
    )
    transient_markers = (
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "network",
    )

    if any(marker in text for marker in non_retryable_markers):
        return "non_retryable"
    if any(marker in text for marker in transient_markers):
        return "retryable"
    return "unknown"
