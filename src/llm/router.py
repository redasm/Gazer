"""Dynamic LLM router with health-aware provider selection."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from collections import deque
from typing import Any, AsyncIterator, Dict, List, Optional

from llm.base import LLMProvider, LLMResponse
from llm.task_complexity import classify_task_complexity

logger = logging.getLogger("LLMRouter")

_ROUTER_STRATEGY_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "cost_first": {
        "strategy": "priority",
        "budget": {
            "enabled": True,
            "degrade_threshold_ratio": 0.35,
        },
        "outlier_ejection": {
            "enabled": True,
            "failure_threshold": 3,
            "cooldown_seconds": 30,
        },
    },
    "latency_first": {
        "strategy": "latency",
        "budget": {
            "enabled": False,
            "degrade_threshold_ratio": 0.15,
        },
        "outlier_ejection": {
            "enabled": True,
            "failure_threshold": 2,
            "cooldown_seconds": 20,
        },
    },
    "availability_first": {
        "strategy": "success_rate",
        "budget": {
            "enabled": False,
            "degrade_threshold_ratio": 0.2,
        },
        "outlier_ejection": {
            "enabled": True,
            "failure_threshold": 4,
            "cooldown_seconds": 45,
        },
    },
}


def list_router_strategy_templates() -> Dict[str, Dict[str, Any]]:
    """Return built-in router strategy templates."""
    return {name: dict(spec) for name, spec in _ROUTER_STRATEGY_TEMPLATES.items()}


def resolve_router_strategy_template(template: str) -> Dict[str, Any]:
    """Resolve a router strategy template into concrete strategy/policy fields."""
    key = str(template or "").strip().lower()
    if not key:
        raise ValueError("Router strategy template is required")
    spec = _ROUTER_STRATEGY_TEMPLATES.get(key)
    if not isinstance(spec, dict):
        raise ValueError(f"Unsupported router strategy template: {template}")
    return {
        "name": key,
        "strategy": str(spec.get("strategy", "priority")).strip().lower() or "priority",
        "budget": dict(spec.get("budget", {})),
        "outlier_ejection": dict(spec.get("outlier_ejection", {})),
    }


@dataclass
class ProviderRoute:
    name: str
    provider: LLMProvider
    default_model: str
    provider_name: str = ""
    target_type: str = "provider"
    health_url: str = ""
    enabled: bool = True
    capacity_rpm: int = 120
    cost_tier: str = "medium"
    latency_target_ms: float = 2000.0
    traffic_weight: float = 1.0
    calls: int = 0
    successes: int = 0
    failures: int = 0
    last_latency_ms: float = 0.0
    last_error: str = ""
    error_classes: Dict[str, int] = field(default_factory=dict)
    _latency_samples: deque = field(default_factory=lambda: deque(maxlen=200))
    _capacity_events: deque = field(default_factory=lambda: deque(maxlen=1000))
    consecutive_failures: int = 0
    ejected_until: float = 0.0
    last_probe_ok: Optional[bool] = None
    last_probe_error: str = ""
    last_probe_ts: float = 0.0
    updated_at: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        if self.calls == 0:
            return 1.0
        return self.successes / self.calls

    @property
    def p95_latency_ms(self) -> float:
        if not self._latency_samples:
            return 0.0
        ordered = sorted(float(item) for item in self._latency_samples)
        idx = int(0.95 * (len(ordered) - 1))
        return ordered[idx]


class RouterProvider(LLMProvider):
    """Route LLM calls across multiple providers using simple strategies."""

    _VALID_STRATEGIES = {"priority", "latency", "success_rate"}

    def __init__(
        self,
        routes: List[ProviderRoute],
        strategy: str = "priority",
        budget_policy: Optional[Dict[str, Any]] = None,
        outlier_policy: Optional[Dict[str, Any]] = None,
        complexity_policy: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not routes:
            raise ValueError("RouterProvider requires at least one route")
        super().__init__(api_key=None, api_base=None)
        self._routes = routes
        self._strategy = strategy if strategy in self._VALID_STRATEGIES else "priority"
        self._budget_events: List[Dict[str, Any]] = []
        self._budget_policy: Dict[str, Any] = {}
        self._outlier_policy: Dict[str, Any] = {}
        self._complexity_policy: Dict[str, Any] = {}
        self._last_complexity: Dict[str, Any] = {"level": "simple", "score": 0.0}
        self.set_budget_policy(budget_policy or {})
        self.set_outlier_policy(outlier_policy or {})
        self.set_complexity_policy(complexity_policy or {})

    def get_default_model(self) -> str:
        return self._routes[0].default_model

    def set_strategy(self, strategy: str) -> None:
        if strategy not in self._VALID_STRATEGIES:
            raise ValueError(f"Unsupported strategy: {strategy}")
        self._strategy = strategy

    def set_budget_policy(self, budget_policy: Dict[str, Any]) -> None:
        policy = budget_policy if isinstance(budget_policy, dict) else {}
        max_calls_raw = policy.get("max_calls", 120)
        max_cost_raw = policy.get("max_cost_usd", 2.0)
        window_raw = policy.get("window_seconds", 60)
        token_ratio_raw = policy.get("estimated_input_tokens_per_char", 0.25)
        provider_costs_raw = policy.get("provider_cost_per_1k_tokens", {})
        degrade_threshold_ratio_raw = policy.get("degrade_threshold_ratio", 0.2)
        try:
            max_calls = max(1, int(max_calls_raw))
        except (TypeError, ValueError):
            max_calls = 120
        try:
            max_cost = max(0.0, float(max_cost_raw))
        except (TypeError, ValueError):
            max_cost = 2.0
        try:
            window_seconds = max(10, int(window_raw))
        except (TypeError, ValueError):
            window_seconds = 60
        try:
            token_ratio = max(0.05, float(token_ratio_raw))
        except (TypeError, ValueError):
            token_ratio = 0.25

        provider_costs: Dict[str, float] = {}
        if isinstance(provider_costs_raw, dict):
            for key, value in provider_costs_raw.items():
                provider_name = str(key).strip()
                if not provider_name:
                    continue
                try:
                    provider_costs[provider_name] = max(0.0, float(value))
                except (TypeError, ValueError):
                    continue
        try:
            degrade_threshold_ratio = max(0.01, min(0.9, float(degrade_threshold_ratio_raw)))
        except (TypeError, ValueError):
            degrade_threshold_ratio = 0.2

        self._budget_policy = {
            "enabled": bool(policy.get("enabled", False)),
            "window_seconds": window_seconds,
            "max_calls": max_calls,
            "max_cost_usd": max_cost,
            "estimated_input_tokens_per_char": token_ratio,
            "provider_cost_per_1k_tokens": provider_costs,
            "degrade_threshold_ratio": degrade_threshold_ratio,
        }

    def set_outlier_policy(self, outlier_policy: Dict[str, Any]) -> None:
        policy = outlier_policy if isinstance(outlier_policy, dict) else {}
        threshold_raw = policy.get("failure_threshold", 3)
        cooldown_raw = policy.get("cooldown_seconds", 30)
        try:
            threshold = max(1, int(threshold_raw))
        except (TypeError, ValueError):
            threshold = 3
        try:
            cooldown_seconds = max(1, int(cooldown_raw))
        except (TypeError, ValueError):
            cooldown_seconds = 30
        self._outlier_policy = {
            "enabled": bool(policy.get("enabled", True)),
            "failure_threshold": threshold,
            "cooldown_seconds": cooldown_seconds,
        }

    def set_complexity_policy(self, complexity_policy: Dict[str, Any]) -> None:
        policy = complexity_policy if isinstance(complexity_policy, dict) else {}

        def _as_int(value: Any, default: int, minimum: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = default
            return parsed if parsed >= minimum else default

        def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = default
            if parsed < minimum:
                return minimum
            if parsed > maximum:
                return maximum
            return parsed

        marker_complex_terms_raw = policy.get("marker_complex_terms", [])
        marker_simple_terms_raw = policy.get("marker_simple_terms", [])
        marker_complex_terms = (
            [str(item).strip() for item in marker_complex_terms_raw if str(item).strip()]
            if isinstance(marker_complex_terms_raw, list)
            else []
        )
        marker_simple_terms = (
            [str(item).strip() for item in marker_simple_terms_raw if str(item).strip()]
            if isinstance(marker_simple_terms_raw, list)
            else []
        )
        weights_raw = policy.get("weights", {})
        weights = dict(weights_raw) if isinstance(weights_raw, dict) else {}

        self._complexity_policy = {
            "enabled": bool(policy.get("enabled", False)),
            "simple_prefer_cost": bool(policy.get("simple_prefer_cost", True)),
            "complex_prefer_success_rate": bool(policy.get("complex_prefer_success_rate", True)),
            "complex_threshold": _as_float(policy.get("complex_threshold", 0.52), 0.52, 0.05, 0.95),
            "message_chars_complex": _as_int(policy.get("message_chars_complex", 220), 220, 40),
            "history_messages_complex": _as_int(policy.get("history_messages_complex", 8), 8, 1),
            "line_breaks_complex": _as_int(policy.get("line_breaks_complex", 3), 3, 1),
            "list_lines_complex": _as_int(policy.get("list_lines_complex", 3), 3, 1),
            "tool_history_events_complex": _as_int(policy.get("tool_history_events_complex", 2), 2, 1),
            "context_window_tokens": _as_int(policy.get("context_window_tokens", 32000), 32000, 512),
            "chars_per_token_estimate": _as_float(
                policy.get("chars_per_token_estimate", 4.0),
                4.0,
                1.0,
                12.0,
            ),
            "marker_feature_enabled": bool(policy.get("marker_feature_enabled", False)),
            "marker_weight": _as_float(policy.get("marker_weight", 0.06), 0.06, 0.0, 0.2),
            "marker_complex_terms": marker_complex_terms,
            "marker_simple_terms": marker_simple_terms,
            "weights": weights,
        }

    def _trim_budget_events(self) -> None:
        if not self._budget_events:
            return
        window_seconds = int(self._budget_policy.get("window_seconds", 60))
        cutoff = time.time() - window_seconds
        self._budget_events = [item for item in self._budget_events if float(item.get("ts", 0.0)) >= cutoff]

    def _estimate_cost_usd(self, route_name: str, messages: List[Dict[str, Any]]) -> float:
        provider_costs = self._budget_policy.get("provider_cost_per_1k_tokens", {}) or {}
        cost_per_1k = float(provider_costs.get(route_name, 0.0))
        if cost_per_1k <= 0:
            return 0.0
        ratio = float(self._budget_policy.get("estimated_input_tokens_per_char", 0.25))
        total_chars = sum(len(str(item.get("content", "") or "")) for item in messages)
        est_tokens = max(1.0, total_chars * ratio)
        return (est_tokens / 1000.0) * cost_per_1k

    def _budget_state(self) -> Dict[str, Any]:
        self._trim_budget_events()
        used_calls = len(self._budget_events)
        used_cost = sum(float(item.get("cost_usd", 0.0)) for item in self._budget_events)
        max_calls = int(self._budget_policy.get("max_calls", 120))
        max_cost = float(self._budget_policy.get("max_cost_usd", 2.0))
        return {
            "enabled": bool(self._budget_policy.get("enabled", False)),
            "window_seconds": int(self._budget_policy.get("window_seconds", 60)),
            "max_calls": max_calls,
            "used_calls": used_calls,
            "remaining_calls": max(0, max_calls - used_calls),
            "max_cost_usd": round(max_cost, 6),
            "used_cost_usd": round(used_cost, 6),
            "remaining_cost_usd": round(max(0.0, max_cost - used_cost), 6),
        }

    def _budget_allows(self, *, route_name: str, est_cost_usd: float) -> bool:
        if not self._budget_policy.get("enabled", False):
            return True
        state = self._budget_state()
        if state["remaining_calls"] <= 0:
            return False
        if est_cost_usd > state["remaining_cost_usd"]:
            return False
        return True

    def _record_budget_usage(self, *, route_name: str, est_cost_usd: float) -> None:
        if not self._budget_policy.get("enabled", False):
            return
        self._budget_events.append(
            {
                "ts": time.time(),
                "route": route_name,
                "cost_usd": max(0.0, float(est_cost_usd)),
            }
        )

    def get_status(self) -> Dict[str, Any]:
        total_calls = sum(route.calls for route in self._routes)
        total_failures = sum(route.failures for route in self._routes)
        latency_samples = [route.last_latency_ms for route in self._routes if route.last_latency_ms > 0]
        avg_latency_ms = (sum(latency_samples) / len(latency_samples)) if latency_samples else 0.0
        return {
            "strategy": self._strategy,
            "total_calls": total_calls,
            "total_failures": total_failures,
            "avg_latency_ms": round(avg_latency_ms, 2),
            "budget": self._budget_state(),
            "budget_degrade_active": self._budget_degrade_active(),
            "outlier_ejection": dict(self._outlier_policy),
            "complexity_routing": {
                **dict(self._complexity_policy),
                "last": dict(self._last_complexity),
            },
            "providers": [
                {
                    "name": route.name,
                    "provider_name": route.provider_name or route.name,
                    "target_type": route.target_type,
                    "health_url": route.health_url,
                    "enabled": bool(route.enabled),
                    "model": route.default_model,
                    "capacity_rpm": route.capacity_rpm,
                    "cost_tier": route.cost_tier,
                    "latency_target_ms": route.latency_target_ms,
                    "traffic_weight": round(float(route.traffic_weight), 4),
                    "calls": route.calls,
                    "successes": route.successes,
                    "failures": route.failures,
                    "success_rate": round(route.success_rate, 4),
                    "last_latency_ms": round(route.last_latency_ms, 2),
                    "p95_latency_ms": round(route.p95_latency_ms, 2),
                    "error_classes": dict(route.error_classes),
                    "last_error": route.last_error,
                    "consecutive_failures": int(route.consecutive_failures),
                    "ejected": self._is_route_ejected(route),
                    "ejected_until": float(route.ejected_until),
                    "last_probe_ok": route.last_probe_ok,
                    "last_probe_error": route.last_probe_error,
                    "last_probe_ts": float(route.last_probe_ts),
                    "updated_at": route.updated_at,
                }
                for route in self._routes
            ],
        }

    @staticmethod
    def _cost_rank(cost_tier: str) -> int:
        key = str(cost_tier or "").strip().lower()
        if key == "low":
            return 1
        if key == "medium":
            return 2
        if key == "high":
            return 3
        return 2

    def _budget_degrade_active(self) -> bool:
        if not self._budget_policy.get("enabled", False):
            return False
        state = self._budget_state()
        max_calls = max(1, int(state.get("max_calls", 1)))
        max_cost = max(0.0001, float(state.get("max_cost_usd", 0.0001)))
        remaining_calls_ratio = float(state.get("remaining_calls", 0)) / float(max_calls)
        remaining_cost_ratio = float(state.get("remaining_cost_usd", 0.0)) / float(max_cost)
        threshold = float(self._budget_policy.get("degrade_threshold_ratio", 0.2))
        return min(remaining_calls_ratio, remaining_cost_ratio) <= threshold

    def _ordered_routes(self, *, messages: Optional[List[Dict[str, Any]]] = None) -> List[ProviderRoute]:
        if self._strategy == "priority":
            routes = sorted(
                self._routes,
                key=lambda route: -max(0.01, float(getattr(route, "traffic_weight", 1.0) or 1.0)),
            )
        elif self._strategy == "latency":
            routes = sorted(
                self._routes,
                key=lambda route: route.last_latency_ms if route.last_latency_ms > 0 else 1_000_000.0,
            )
        elif self._strategy == "success_rate":
            routes = sorted(self._routes, key=lambda route: route.success_rate, reverse=True)
        else:
            routes = list(self._routes)

        if self._budget_degrade_active():
            routes = sorted(
                routes,
                key=lambda route: (
                    self._cost_rank(route.cost_tier),
                    route.last_latency_ms if route.last_latency_ms > 0 else route.latency_target_ms,
                ),
            )
        if bool(self._complexity_policy.get("enabled", False)):
            total_calls = sum(int(route.calls) for route in self._routes)
            total_failures = sum(int(route.failures) for route in self._routes)
            recent_failure_rate = (
                (float(total_failures) / float(total_calls))
                if total_calls > 0
                else 0.0
            )
            complexity_policy = dict(self._complexity_policy)
            complexity_policy["recent_failure_rate"] = recent_failure_rate
            complexity = classify_task_complexity(messages or [], policy=complexity_policy)
            self._last_complexity = dict(complexity)
            level = str(complexity.get("level", "simple")).strip().lower()
            if level == "simple" and bool(self._complexity_policy.get("simple_prefer_cost", True)):
                routes = sorted(
                    routes,
                    key=lambda route: (
                        self._cost_rank(route.cost_tier),
                        -(route.success_rate),
                        route.last_latency_ms if route.last_latency_ms > 0 else route.latency_target_ms,
                    ),
                )
            elif level == "complex" and bool(
                self._complexity_policy.get("complex_prefer_success_rate", True)
            ):
                routes = sorted(
                    routes,
                    key=lambda route: (
                        -(route.success_rate),
                        self._cost_rank(route.cost_tier),
                        route.last_latency_ms if route.last_latency_ms > 0 else route.latency_target_ms,
                    ),
                )
        return routes

    def _is_route_ejected(self, route: ProviderRoute) -> bool:
        now = time.time()
        if float(route.ejected_until) <= 0:
            return False
        if now >= float(route.ejected_until):
            route.ejected_until = 0.0
            route.consecutive_failures = 0
            return False
        return True

    def _record_route_success(self, route: ProviderRoute) -> None:
        route.consecutive_failures = 0
        route.last_error = ""

    def _record_route_failure(self, route: ProviderRoute, *, error: str) -> None:
        route.consecutive_failures += 1
        if not bool(self._outlier_policy.get("enabled", True)):
            return
        threshold = int(self._outlier_policy.get("failure_threshold", 3) or 3)
        if route.consecutive_failures < threshold:
            return
        cooldown = int(self._outlier_policy.get("cooldown_seconds", 30) or 30)
        route.ejected_until = time.time() + float(cooldown)
        route.consecutive_failures = 0
        route.last_error = f"{error or 'provider_error'} | outlier_ejected({cooldown}s)"
        self._record_error(route, "outlier_ejected")

    async def probe_routes(
        self,
        *,
        active: bool = False,
        timeout_seconds: float = 3.0,
    ) -> List[Dict[str, Any]]:
        statuses: List[Dict[str, Any]] = []
        safe_timeout = max(0.5, float(timeout_seconds))
        for route in self._routes:
            ejected = self._is_route_ejected(route)
            healthy = bool(route.enabled) and not ejected
            reason = ""
            if not route.enabled:
                reason = "target_disabled"
            elif ejected:
                reason = "outlier_ejected"
            elif route.last_error:
                reason = route.last_error

            if active and healthy:
                try:
                    resp = await asyncio.wait_for(
                        route.provider.chat(
                            messages=[{"role": "user", "content": "health_probe"}],
                            tools=[],
                            model=route.default_model,
                            max_tokens=4,
                            temperature=0.0,
                        ),
                        timeout=safe_timeout,
                    )
                    healthy = not bool(resp.error)
                    reason = "" if healthy else (resp.content or "probe_error")
                    route.last_probe_ok = healthy
                    route.last_probe_error = "" if healthy else str(reason)
                    route.last_probe_ts = time.time()
                except Exception as exc:
                    healthy = False
                    reason = str(exc)
                    route.last_probe_ok = False
                    route.last_probe_error = reason
                    route.last_probe_ts = time.time()

            statuses.append(
                {
                    "name": route.name,
                    "provider_name": route.provider_name or route.name,
                    "target_type": route.target_type,
                    "enabled": bool(route.enabled),
                    "healthy": bool(healthy),
                    "reason": str(reason),
                    "ejected": bool(ejected),
                    "ejected_until": float(route.ejected_until),
                    "consecutive_failures": int(route.consecutive_failures),
                    "last_probe_ok": route.last_probe_ok,
                    "last_probe_error": route.last_probe_error,
                    "last_probe_ts": float(route.last_probe_ts),
                }
            )
        return statuses

    @staticmethod
    def _classify_error(value: str) -> str:
        text = str(value or "").lower()
        if not text:
            return "unknown"
        if "budget" in text:
            return "budget"
        if "timeout" in text or "timed out" in text:
            return "timeout"
        if "rate" in text and "limit" in text:
            return "rate_limit"
        if "auth" in text or "token" in text or "key" in text:
            return "auth"
        if "network" in text or "connection" in text:
            return "network"
        return "other"

    def _within_capacity(self, route: ProviderRoute) -> bool:
        now = time.time()
        window = 60.0
        while route._capacity_events and (now - route._capacity_events[0]) > window:
            route._capacity_events.popleft()
        limit = max(1, int(route.capacity_rpm))
        return len(route._capacity_events) < limit

    @staticmethod
    def _record_error(route: ProviderRoute, error: str) -> None:
        klass = RouterProvider._classify_error(error)
        route.error_classes[klass] = route.error_classes.get(klass, 0) + 1

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        last_error: Optional[str] = None
        budget_blocked = 0
        for route in self._ordered_routes(messages=messages):
            if not bool(route.enabled):
                route.last_error = "target_disabled"
                route.updated_at = time.time()
                continue
            if self._is_route_ejected(route):
                route.last_error = "outlier_ejected"
                route.updated_at = time.time()
                continue
            if not self._within_capacity(route):
                budget_blocked += 1
                route.last_error = "capacity_exceeded"
                self._record_error(route, route.last_error)
                route.updated_at = time.time()
                continue
            est_cost_usd = self._estimate_cost_usd(route.name, messages)
            if not self._budget_allows(route_name=route.name, est_cost_usd=est_cost_usd):
                budget_blocked += 1
                route.last_error = "budget_exceeded"
                self._record_error(route, route.last_error)
                route.updated_at = time.time()
                continue
            self._record_budget_usage(route_name=route.name, est_cost_usd=est_cost_usd)
            route.calls += 1
            route._capacity_events.append(time.time())
            started = time.perf_counter()
            try:
                use_model = model or route.default_model
                response = await route.provider.chat(
                    messages=messages,
                    tools=tools,
                    model=use_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                route.last_latency_ms = (time.perf_counter() - started) * 1000.0
                route._latency_samples.append(route.last_latency_ms)
                route.updated_at = time.time()
                if response.error:
                    route.failures += 1
                    route.last_error = response.content or "provider_error"
                    self._record_error(route, route.last_error)
                    self._record_route_failure(route, error=route.last_error)
                    last_error = route.last_error
                    continue
                route.successes += 1
                self._record_route_success(route)
                return response
            except Exception as exc:
                route.last_latency_ms = (time.perf_counter() - started) * 1000.0
                route._latency_samples.append(route.last_latency_ms)
                route.updated_at = time.time()
                route.failures += 1
                route.last_error = str(exc)
                self._record_error(route, route.last_error)
                self._record_route_failure(route, error=route.last_error)
                last_error = str(exc)
                logger.warning("Router provider '%s' failed: %s", route.name, exc)

        if budget_blocked == len(self._routes):
            return LLMResponse(
                content="All router providers blocked by budget policy.",
                finish_reason="error",
                error=True,
            )
        return LLMResponse(
            content=f"All router providers failed: {last_error or 'unknown error'}",
            finish_reason="error",
            error=True,
        )

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        budget_blocked = 0
        for route in self._ordered_routes(messages=messages):
            if not bool(route.enabled):
                route.last_error = "target_disabled"
                route.updated_at = time.time()
                continue
            if self._is_route_ejected(route):
                route.last_error = "outlier_ejected"
                route.updated_at = time.time()
                continue
            if not self._within_capacity(route):
                budget_blocked += 1
                route.last_error = "capacity_exceeded"
                self._record_error(route, route.last_error)
                route.updated_at = time.time()
                continue
            est_cost_usd = self._estimate_cost_usd(route.name, messages)
            if not self._budget_allows(route_name=route.name, est_cost_usd=est_cost_usd):
                budget_blocked += 1
                route.last_error = "budget_exceeded"
                self._record_error(route, route.last_error)
                route.updated_at = time.time()
                continue
            self._record_budget_usage(route_name=route.name, est_cost_usd=est_cost_usd)
            route.calls += 1
            route._capacity_events.append(time.time())
            started = time.perf_counter()
            try:
                use_model = model or route.default_model
                async for chunk in route.provider.stream_chat(
                    messages=messages,
                    tools=tools,
                    model=use_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    yield chunk
                route.successes += 1
                self._record_route_success(route)
                route.last_latency_ms = (time.perf_counter() - started) * 1000.0
                route._latency_samples.append(route.last_latency_ms)
                route.updated_at = time.time()
                return
            except Exception as exc:
                route.failures += 1
                route.last_error = str(exc)
                self._record_error(route, route.last_error)
                self._record_route_failure(route, error=route.last_error)
                route.last_latency_ms = (time.perf_counter() - started) * 1000.0
                route._latency_samples.append(route.last_latency_ms)
                route.updated_at = time.time()
                logger.warning("Router stream failed on '%s': %s", route.name, exc)
                continue

        if budget_blocked == len(self._routes):
            raise RuntimeError("All router providers blocked by budget policy.")
