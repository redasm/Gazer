"""Token usage / latency / cost accumulator for the agent loop."""

from __future__ import annotations

import collections
import time
from typing import Any, Dict


class UsageTracker:
    """Accumulates LLM token usage across the session.

    Tracks totals, per-model breakdown, daily buckets, and latency.
    """

    def __init__(self) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.request_count: int = 0
        self._start_ts: float = time.time()
        # Per-model breakdown: {model_name: {prompt, completion, total, requests, cost, latencies}}
        self._by_model: Dict[str, Dict[str, Any]] = {}
        # Daily buckets: {"YYYY-MM-DD": {input, output, cache, requests, cost}}
        self._daily: Dict[str, Dict[str, Any]] = {}
        # Latency tracking
        self._latencies: collections.deque = collections.deque(maxlen=1000)
        # Today's date for fast comparison
        self._today: str = time.strftime("%Y-%m-%d")
        # Today-only counters
        self._today_input: int = 0
        self._today_output: int = 0
        self._today_requests: int = 0
        self._today_cost: float = 0.0

    def _ensure_today(self) -> str:
        """Roll over today counters if date changed."""
        now = time.strftime("%Y-%m-%d")
        if now != self._today:
            self._today = now
            self._today_input = 0
            self._today_output = 0
            self._today_requests = 0
            self._today_cost = 0.0
        return now

    def add(self, usage: dict, *, model: str = "", latency_ms: float = 0.0, cost_usd: float = 0.0) -> None:
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        total = int(usage.get("total_tokens", 0) or 0)
        cache = int(usage.get("cache_read_tokens", 0) or usage.get("cached_tokens", 0) or 0)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.request_count += 1

        # Today
        today = self._ensure_today()
        self._today_input += prompt
        self._today_output += completion
        self._today_requests += 1
        self._today_cost += cost_usd

        # Daily bucket
        bucket = self._daily.setdefault(today, {
            "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0,
            "requests": 0, "cost_usd": 0.0,
        })
        bucket["input_tokens"] += prompt
        bucket["output_tokens"] += completion
        bucket["cache_tokens"] += cache
        bucket["requests"] += 1
        bucket["cost_usd"] += cost_usd

        # Per-model
        if model:
            m = self._by_model.setdefault(model, {
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "requests": 0, "cost_usd": 0.0, "latencies": collections.deque(maxlen=1000),
            })
            m["prompt_tokens"] += prompt
            m["completion_tokens"] += completion
            m["total_tokens"] += total
            m["requests"] += 1
            m["cost_usd"] += cost_usd
            if latency_ms > 0:
                m["latencies"].append(latency_ms)

        # Latency
        if latency_ms > 0:
            self._latencies.append(latency_ms)

    def summary(self) -> dict:
        self._ensure_today()
        avg_latency = (
            sum(self._latencies) / len(self._latencies)
            if self._latencies else 0.0
        )
        # Build per-model summary (strip latency arrays)
        by_model = {}
        for name, data in self._by_model.items():
            lats = data.get("latencies", [])
            by_model[name] = {
                "prompt_tokens": data["prompt_tokens"],
                "completion_tokens": data["completion_tokens"],
                "total_tokens": data["total_tokens"],
                "requests": data["requests"],
                "cost_usd": round(data["cost_usd"], 6),
                "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else 0.0,
            }
        # Build daily trend
        daily = []
        for date in sorted(self._daily.keys()):
            b = self._daily[date]
            daily.append({
                "date": date,
                "input_tokens": b["input_tokens"],
                "output_tokens": b["output_tokens"],
                "cache_tokens": b["cache_tokens"],
                "requests": b["requests"],
                "cost_usd": round(b["cost_usd"], 6),
            })
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "requests": self.request_count,
            "avg_latency_ms": round(avg_latency, 2),
            "today_input_tokens": self._today_input,
            "today_output_tokens": self._today_output,
            "today_total_tokens": self._today_input + self._today_output,
            "today_requests": self._today_requests,
            "today_cost_usd": round(self._today_cost, 6),
            "by_model": by_model,
            "daily": daily,
        }


__all__ = ["UsageTracker"]
