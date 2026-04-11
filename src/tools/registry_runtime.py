from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BudgetSettings:
    """Parsed budget configuration for the tool execution rate limiter."""

    enabled: bool = False
    max_calls: int = 120
    window_seconds: int = 60
    max_weight: float = 120.0
    group_caps: dict[str, int] = field(default_factory=dict)
    group_weights: dict[str, float] = field(default_factory=dict)
    tool_weights: dict[str, float] = field(default_factory=dict)


class ToolRegistryRuntimeState:
    """Mutable runtime state for tool-budgeting, circuit breaking, and rejections."""

    def __init__(self) -> None:
        self.failure_state: dict[str, dict[str, float]] = {}
        self.budget_events: list[dict[str, float | str]] = []
        self.rejection_events: deque[dict[str, Any]] = deque(maxlen=300)

    def record_rejection_event(
        self,
        *,
        code: str,
        name: str,
        provider: str,
        reason: str,
        trace_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.rejection_events.append(
            {
                "ts": time.time(),
                "code": str(code),
                "tool": str(name),
                "provider": str(provider or "core"),
                "reason": str(reason or ""),
                "trace_id": str(trace_id),
                "metadata": dict(metadata or {}),
            }
        )

    def recent_rejection_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        items = list(self.rejection_events)
        return list(reversed(items[-safe_limit:]))

    def trim_budget_events(self, *, window_seconds: int) -> None:
        if not self.budget_events:
            return
        cutoff = time.time() - float(window_seconds)
        self.budget_events = [
            item for item in self.budget_events if float(item.get("ts", 0.0)) >= cutoff
        ]

    @staticmethod
    def resolve_budget_weight(
        *,
        tool_name: str,
        provider: str,
        group_weights: dict[str, float],
        tool_weights: dict[str, float],
    ) -> float:
        name_key = str(tool_name).strip().lower()
        provider_key = str(provider).strip().lower()
        if name_key in tool_weights:
            return max(0.01, float(tool_weights[name_key]))
        group_weight = float(group_weights.get(provider_key, 1.0))
        return max(0.01, group_weight)

    def record_budget_usage(self, *, name: str, provider: str, weight: float, settings: BudgetSettings) -> None:
        if not settings.enabled:
            return
        self.trim_budget_events(window_seconds=settings.window_seconds)
        self.budget_events.append(
            {"ts": time.time(), "tool": name, "provider": provider, "weight": float(weight)}
        )

    def budget_state(self) -> tuple[int, float, dict[str, int]]:
        total_calls = len(self.budget_events)
        total_weight = 0.0
        by_group: dict[str, int] = {}
        for item in self.budget_events:
            provider = str(item.get("provider", "core")).strip().lower() or "core"
            by_group[provider] = by_group.get(provider, 0) + 1
            try:
                total_weight += float(item.get("weight", 1.0))
            except (TypeError, ValueError):
                total_weight += 1.0
        return total_calls, round(total_weight, 4), by_group

    def budget_runtime_status(self, settings: BudgetSettings) -> dict[str, Any]:
        self.trim_budget_events(window_seconds=settings.window_seconds)
        used_calls, used_weight, by_group = self.budget_state()
        group_usage = {
            key: {
                "used_calls": int(by_group.get(key, 0)),
                "cap_calls": int(settings.group_caps[key]) if key in settings.group_caps else None,
                "remaining_calls": (
                    max(0, int(settings.group_caps[key]) - int(by_group.get(key, 0)))
                    if key in settings.group_caps
                    else None
                ),
            }
            for key in sorted(set(by_group.keys()) | set(settings.group_caps.keys()))
        }
        return {
            "enabled": bool(settings.enabled),
            "window_seconds": int(settings.window_seconds),
            "max_calls": int(settings.max_calls),
            "used_calls": int(used_calls),
            "remaining_calls": max(0, int(settings.max_calls) - int(used_calls)),
            "max_weight": float(settings.max_weight),
            "used_weight": float(used_weight),
            "remaining_weight": round(max(0.0, float(settings.max_weight) - float(used_weight)), 4),
            "group_caps": {k: int(v) for k, v in sorted(settings.group_caps.items())},
            "group_usage": group_usage,
        }

    def is_budget_exceeded(self, *, name: str, provider: str, settings: BudgetSettings) -> tuple[bool, str]:
        if not settings.enabled:
            return False, ""
        self.trim_budget_events(window_seconds=settings.window_seconds)
        calls, used_weight, by_group = self.budget_state()
        if calls >= settings.max_calls:
            return True, "max_calls"
        provider_key = str(provider).strip().lower() or "core"
        if provider_key in settings.group_caps and by_group.get(provider_key, 0) >= int(settings.group_caps[provider_key]):
            return True, f"group_calls:{provider_key}"
        next_weight = self.resolve_budget_weight(
            tool_name=name,
            provider=provider_key,
            group_weights=settings.group_weights,
            tool_weights=settings.tool_weights,
        )
        if used_weight + next_weight > settings.max_weight:
            return True, "max_weight"
        return False, ""

    def is_circuit_open(self, name: str) -> bool:
        state = self.failure_state.get(name)
        if not state:
            return False
        open_until = float(state.get("open_until", 0.0))
        if open_until <= 0:
            return False
        now = time.time()
        if now >= open_until:
            state["open_until"] = 0.0
            state["failures"] = 0.0
            return False
        return True

    def record_tool_outcome(self, name: str, result: str, *, enabled: bool, threshold: int, cooldown: int) -> None:
        if not enabled:
            return
        state = self.failure_state.setdefault(name, {"failures": 0.0, "open_until": 0.0})
        if str(result).startswith("Error"):
            failures = int(state.get("failures", 0.0)) + 1
            state["failures"] = float(failures)
            if failures >= threshold:
                state["open_until"] = time.time() + cooldown
        else:
            state["failures"] = 0.0
            state["open_until"] = 0.0
