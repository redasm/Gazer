"""Tool registry for dynamic tool management with safety tiers.

Inspired by OpenClaw's allowlist / denylist model, the registry supports
per-request filtering so that untrusted callers can only access tools
within their allowed safety tier.
"""

import asyncio
import fnmatch
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from tools.base import CancellationToken, Tool, ToolSafetyTier
from runtime.config_manager import config as gazer_config
from runtime.rust_gate import push_tool_access_context

if TYPE_CHECKING:
    from plugins.hooks import HookRegistry

logger = logging.getLogger("ToolRegistry")

@dataclass(frozen=True)
class BudgetSettings:
    """Parsed budget configuration for the tool execution rate limiter."""
    enabled: bool = False
    max_calls: int = 120
    window_seconds: int = 60
    max_weight: float = 120.0
    group_caps: Dict[str, int] = field(default_factory=dict)
    group_weights: Dict[str, float] = field(default_factory=dict)
    tool_weights: Dict[str, float] = field(default_factory=dict)


_DEFAULT_ERROR_HINTS: Dict[str, str] = {
    "TOOL_NOT_FOUND": "Tool 名称不存在。先调用 tool definitions 获取可用工具列表，再重试。",
    "TOOL_NOT_PERMITTED": "工具被安全策略拦截。检查 security.tool_max_tier / owner 权限 / allowlist 配置。",
    "TOOL_PARAMS_INVALID": "参数不符合工具 schema。请根据工具 parameters 重新构建参数对象。",
    "TOOL_CIRCUIT_OPEN": "该工具近期连续失败触发熔断。等待冷却或改用替代工具路径。",
    "TOOL_BUDGET_EXCEEDED": "工具调用预算超限。降低调用频率或调整 security.tool_budget_* 配置。",
    "TOOL_CANCELLED": "操作已取消。必要时重新发起请求。",
    "TOOL_BLOCKED_BY_HOOK": "被插件 Hook 拦截。检查 plugins/hook 配置与日志。",
    "TOOL_EXECUTION_FAILED": "执行失败。检查依赖、权限、网络、以及工具日志；避免重复相同调用。",
}


@dataclass
class ToolPolicy:
    """Per-agent tool policy."""

    allow_names: Set[str] = field(default_factory=set)
    deny_names: Set[str] = field(default_factory=set)
    allow_providers: Set[str] = field(default_factory=set)
    deny_providers: Set[str] = field(default_factory=set)
    allow_model_providers: Set[str] = field(default_factory=set)
    deny_model_providers: Set[str] = field(default_factory=set)
    allow_model_names: Set[str] = field(default_factory=set)
    deny_model_names: Set[str] = field(default_factory=set)
    allow_model_selectors: Set[str] = field(default_factory=set)
    deny_model_selectors: Set[str] = field(default_factory=set)


def _normalize_policy_value(values: Any, *, lowercase: bool = False) -> Set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()
    normalized: Set[str] = set()
    for item in values:
        value = str(item).strip()
        if not value:
            continue
        normalized.add(value.lower() if lowercase else value)
    return normalized


def _normalize_model_selector_values(values: Any) -> Set[str]:
    selectors = _normalize_policy_value(values, lowercase=True)
    return {item for item in selectors if item not in {"/", "*"}}


def normalize_tool_policy(policy: Optional[Dict[str, Any]], groups: Optional[Dict[str, List[str]]] = None) -> ToolPolicy:
    """Normalize raw policy dict into ``ToolPolicy``."""
    if not policy:
        return ToolPolicy()
    groups = groups or {}

    allow_names = _normalize_policy_value(policy.get("allow_names"))
    deny_names = _normalize_policy_value(policy.get("deny_names"))
    allow_providers = _normalize_policy_value(policy.get("allow_providers"), lowercase=True)
    deny_providers = _normalize_policy_value(policy.get("deny_providers"), lowercase=True)
    allow_model_providers = _normalize_policy_value(
        policy.get("allow_model_providers"),
        lowercase=True,
    )
    deny_model_providers = _normalize_policy_value(
        policy.get("deny_model_providers"),
        lowercase=True,
    )
    allow_model_names = _normalize_policy_value(policy.get("allow_model_names"), lowercase=True)
    deny_model_names = _normalize_policy_value(policy.get("deny_model_names"), lowercase=True)
    allow_model_selectors = _normalize_model_selector_values(policy.get("allow_model_selectors"))
    deny_model_selectors = _normalize_model_selector_values(policy.get("deny_model_selectors"))

    for group_name in _normalize_policy_value(policy.get("allow_groups")):
        names = groups.get(group_name, [])
        if not names:
            logger.warning("Unknown/empty allow_group: %s", group_name)
        allow_names.update(_normalize_policy_value(names))

    for group_name in _normalize_policy_value(policy.get("deny_groups")):
        names = groups.get(group_name, [])
        if not names:
            logger.warning("Unknown/empty deny_group: %s", group_name)
        deny_names.update(_normalize_policy_value(names))

    return ToolPolicy(
        allow_names=allow_names,
        deny_names=deny_names,
        allow_providers=allow_providers,
        deny_providers=deny_providers,
        allow_model_providers=allow_model_providers,
        deny_model_providers=deny_model_providers,
        allow_model_names=allow_model_names,
        deny_model_names=deny_model_names,
        allow_model_selectors=allow_model_selectors,
        deny_model_selectors=deny_model_selectors,
    )


class ToolRegistry:
    """
    Registry for agent tools.
    
    Allows dynamic registration and execution of tools.
    Supports *safety-tier* filtering: callers can specify a maximum allowed
    tier so that untrusted sessions only see ``SAFE`` or ``STANDARD`` tools.
    """
    
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        # Per-name overrides: names in the denylist are never exposed.
        self._denylist: Set[str] = set()
        # If non-empty, only these tool names are exposed (allowlist wins).
        self._allowlist: Set[str] = set()
        # Hook registry (injected after init to avoid circular imports)
        self._hooks: Optional["HookRegistry"] = None
        # Per-tool failure tracker for lightweight circuit breaking.
        self._failure_state: Dict[str, Dict[str, float]] = {}
        # Rolling events for global tool-call budget.
        self._budget_events: List[Dict[str, float | str]] = []
        # Rolling governance rejection events (policy/tier/circuit/budget).
        self._rejection_events: deque[Dict[str, Any]] = deque(maxlen=300)

    @staticmethod
    def _error(code: str, message: str, *, trace_id: str = "", hint: str = "") -> str:
        """Standard tool error format.

        Keep the first line as `Error [CODE]: ...` so AgentLoop can parse `error_code`.
        Additional fields are appended on separate lines to stay human-readable.
        """
        head = f"Error [{code}]: {message}"
        if trace_id:
            head = f"{head} (trace_id={trace_id})"
        resolved_hint = str(hint or _DEFAULT_ERROR_HINTS.get(code, "")).strip()
        if not resolved_hint:
            return head
        return f"{head}\nHint: {resolved_hint}"

    @staticmethod
    def _new_trace_id() -> str:
        return f"trc_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    def _record_rejection_event(
        self,
        *,
        code: str,
        name: str,
        provider: str,
        reason: str,
        trace_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._rejection_events.append(
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

    def set_hook_registry(self, hooks: "HookRegistry") -> None:
        """Inject a HookRegistry for before/after tool call lifecycle hooks."""
        self._hooks = hooks

    @staticmethod
    def _read_int_config(key: str, default: int, minimum: int = 1) -> int:
        raw = gazer_config.get(key, default)
        if isinstance(raw, bool):
            return default
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= minimum else default

    def _circuit_settings(self) -> tuple[bool, int, int]:
        enabled = bool(gazer_config.get("security.tool_circuit_breaker_enabled", True))
        failures = self._read_int_config("security.tool_circuit_breaker_failures", 3, minimum=1)
        cooldown = self._read_int_config("security.tool_circuit_breaker_cooldown_seconds", 30, minimum=1)
        return enabled, failures, cooldown

    def _budget_settings(self) -> BudgetSettings:
        """Parse and return current budget configuration as a BudgetSettings dataclass."""
        enabled = bool(gazer_config.get("security.tool_budget_enabled", False))
        max_calls = self._read_int_config("security.tool_budget_max_calls", 120, minimum=1)
        window_seconds = self._read_int_config("security.tool_budget_window_seconds", 60, minimum=1)
        raw_max_weight = gazer_config.get("security.tool_budget_max_weight", float(max_calls))
        try:
            max_weight = float(raw_max_weight)
        except (TypeError, ValueError):
            max_weight = float(max_calls)
        max_weight = max(1.0, max_weight)
        raw_group_caps = gazer_config.get("security.tool_budget_max_calls_by_group", {})
        group_caps: Dict[str, int] = {}
        if isinstance(raw_group_caps, dict):
            for key, value in raw_group_caps.items():
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    group_caps[str(key).strip().lower()] = parsed
        raw_group_weights = gazer_config.get("security.tool_budget_weight_by_group", {})
        group_weights: Dict[str, float] = {}
        if isinstance(raw_group_weights, dict):
            for key, value in raw_group_weights.items():
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    group_weights[str(key).strip().lower()] = parsed
        raw_tool_weights = gazer_config.get("security.tool_budget_weight_by_tool", {})
        tool_weights: Dict[str, float] = {}
        if isinstance(raw_tool_weights, dict):
            for key, value in raw_tool_weights.items():
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    tool_weights[str(key).strip().lower()] = parsed
        return BudgetSettings(
            enabled=enabled,
            max_calls=max_calls,
            window_seconds=window_seconds,
            max_weight=max_weight,
            group_caps=group_caps,
            group_weights=group_weights,
            tool_weights=tool_weights,
        )

    def _trim_budget_events(self, *, window_seconds: int) -> None:
        if not self._budget_events:
            return
        cutoff = time.time() - float(window_seconds)
        self._budget_events = [
            item for item in self._budget_events if float(item.get("ts", 0.0)) >= cutoff
        ]

    def _resolve_budget_weight(
        self,
        *,
        tool_name: str,
        provider: str,
        group_weights: Dict[str, float],
        tool_weights: Dict[str, float],
    ) -> float:
        name_key = str(tool_name).strip().lower()
        provider_key = str(provider).strip().lower()
        if name_key in tool_weights:
            return max(0.01, float(tool_weights[name_key]))
        group_weight = float(group_weights.get(provider_key, 1.0))
        return max(0.01, group_weight)

    def _record_budget_usage(self, *, name: str, provider: str, weight: float) -> None:
        bs = self._budget_settings()
        if not bs.enabled:
            return
        self._trim_budget_events(window_seconds=bs.window_seconds)
        self._budget_events.append(
            {"ts": time.time(), "tool": name, "provider": provider, "weight": float(weight)}
        )

    def _budget_state(self) -> tuple[int, float, Dict[str, int]]:
        total_calls = len(self._budget_events)
        total_weight = 0.0
        by_group: Dict[str, int] = {}
        for item in self._budget_events:
            provider = str(item.get("provider", "core")).strip().lower() or "core"
            by_group[provider] = by_group.get(provider, 0) + 1
            try:
                total_weight += float(item.get("weight", 1.0))
            except (TypeError, ValueError):
                total_weight += 1.0
        return total_calls, round(total_weight, 4), by_group

    def get_budget_runtime_status(self) -> Dict[str, Any]:
        """Return current tool-budget runtime status for observability."""
        bs = self._budget_settings()
        self._trim_budget_events(window_seconds=bs.window_seconds)
        used_calls, used_weight, by_group = self._budget_state()
        group_usage = {
            key: {
                "used_calls": int(by_group.get(key, 0)),
                "cap_calls": int(bs.group_caps[key]) if key in bs.group_caps else None,
                "remaining_calls": (
                    max(0, int(bs.group_caps[key]) - int(by_group.get(key, 0)))
                    if key in bs.group_caps
                    else None
                ),
            }
            for key in sorted(set(by_group.keys()) | set(bs.group_caps.keys()))
        }
        return {
            "enabled": bool(bs.enabled),
            "window_seconds": int(bs.window_seconds),
            "max_calls": int(bs.max_calls),
            "used_calls": int(used_calls),
            "remaining_calls": max(0, int(bs.max_calls) - int(used_calls)),
            "max_weight": float(bs.max_weight),
            "used_weight": float(used_weight),
            "remaining_weight": round(max(0.0, float(bs.max_weight) - float(used_weight)), 4),
            "group_caps": {k: int(v) for k, v in sorted(bs.group_caps.items())},
            "group_usage": group_usage,
        }

    def get_recent_rejection_events(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Return most recent governance rejection events (newest first)."""
        safe_limit = max(1, min(int(limit), 500))
        items = list(self._rejection_events)
        return list(reversed(items[-safe_limit:]))

    def _is_budget_exceeded(self, *, name: str, provider: str) -> tuple[bool, str]:
        bs = self._budget_settings()
        if not bs.enabled:
            return False, ""
        self._trim_budget_events(window_seconds=bs.window_seconds)
        calls, used_weight, by_group = self._budget_state()
        if calls >= bs.max_calls:
            return True, "max_calls"
        provider_key = str(provider).strip().lower() or "core"
        if provider_key in bs.group_caps and by_group.get(provider_key, 0) >= int(bs.group_caps[provider_key]):
            return True, f"group_calls:{provider_key}"
        next_weight = self._resolve_budget_weight(
            tool_name=name,
            provider=provider_key,
            group_weights=bs.group_weights,
            tool_weights=bs.tool_weights,
        )
        if used_weight + next_weight > bs.max_weight:
            return True, "max_weight"
        return False, ""

    def _is_circuit_open(self, name: str) -> bool:
        state = self._failure_state.get(name)
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

    def _record_tool_outcome(self, name: str, result: str) -> None:
        enabled, threshold, cooldown = self._circuit_settings()
        if not enabled:
            return
        state = self._failure_state.setdefault(name, {"failures": 0.0, "open_until": 0.0})
        if str(result).startswith("Error"):
            failures = int(state.get("failures", 0.0)) + 1
            state["failures"] = float(failures)
            if failures >= threshold:
                state["open_until"] = time.time() + cooldown
        else:
            state["failures"] = 0.0
            state["open_until"] = 0.0

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool
    
    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    # ------------------------------------------------------------------
    # Allowlist / denylist (set at startup from config)
    # ------------------------------------------------------------------

    def set_allowlist(self, names: List[str]) -> None:
        """When set, *only* these tool names are available."""
        self._allowlist = set(names)
        logger.info(f"Tool allowlist set: {self._allowlist}")

    def set_denylist(self, names: List[str]) -> None:
        """These tool names are always hidden even if registered."""
        self._denylist = set(names)
        logger.info(f"Tool denylist set: {self._denylist}")

    # ------------------------------------------------------------------
    # Tier-aware queries
    # ------------------------------------------------------------------

    @staticmethod
    def _tier_order(tier: ToolSafetyTier) -> int:
        return {ToolSafetyTier.SAFE: 0, ToolSafetyTier.STANDARD: 1, ToolSafetyTier.PRIVILEGED: 2}.get(tier, 2)

    @staticmethod
    def _tool_provider(tool: Tool) -> str:
        provider = (tool.provider or "").strip().lower()
        if provider and provider != "core":
            return provider
        module = tool.__class__.__module__
        if ".tools." in module:
            return module.split(".tools.", 1)[1].split(".", 1)[0].strip().lower()
        return provider or "core"

    @staticmethod
    def _is_owner_sender(channel: str, sender_id: str) -> bool:
        ch = str(channel or "").strip()
        sid = str(sender_id or "").strip()
        if not sid:
            return False
        if sid == "owner":
            return True
        if not ch:
            return False
        try:
            from security.owner import get_owner_manager

            owner_mgr = get_owner_manager()
            return bool(owner_mgr and owner_mgr.is_owner_sender(ch, sid))
        except Exception as exc:
            logger.warning("Owner sender check failed: %s", exc, exc_info=True)
            return False

    @staticmethod
    def _has_sender_context(channel: str, sender_id: str) -> bool:
        return bool(str(channel or "").strip() or str(sender_id or "").strip())

    def _is_owner_only_tool(self, tool: Tool) -> bool:
        explicit_owner_only = bool(getattr(tool, "owner_only", False))
        if explicit_owner_only:
            return True
        return tool.safety_tier == ToolSafetyTier.PRIVILEGED

    @staticmethod
    def _normalize_model_context(model_provider: str, model_name: str) -> tuple[str, str]:
        provider = str(model_provider or "").strip().lower()
        model = str(model_name or "").strip().lower()
        if not provider and model:
            if "/" in model:
                provider, model = model.split("/", 1)
            elif ":" in model:
                provider_candidate, maybe_model = model.split(":", 1)
                if provider_candidate and maybe_model:
                    provider = provider_candidate
                    model = maybe_model
        return provider, model

    @staticmethod
    def _matches_model_selector(selector: str, *, model_provider: str, model_name: str) -> bool:
        raw = str(selector or "").strip().lower()
        if not raw:
            return False
        if "/" in raw:
            provider_pattern, model_pattern = raw.split("/", 1)
        else:
            provider_pattern, model_pattern = "*", raw
        provider_pattern = provider_pattern or "*"
        model_pattern = model_pattern or "*"
        return (
            fnmatch.fnmatch(model_provider, provider_pattern)
            and fnmatch.fnmatch(model_name, model_pattern)
        )

    def _is_allowed(
        self,
        name: str,
        max_tier: Optional[ToolSafetyTier] = None,
        policy: Optional[ToolPolicy] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> bool:
        """Check if tool *name* passes allowlist, denylist and tier filters."""
        decision = self.evaluate_tool_access(
            name,
            max_tier=max_tier,
            policy=policy,
            sender_id=sender_id,
            channel=channel,
            model_provider=model_provider,
            model_name=model_name,
        )
        return bool(decision.get("allowed", False))

    def evaluate_tool_access(
        self,
        name: str,
        *,
        max_tier: Optional[ToolSafetyTier] = None,
        policy: Optional[ToolPolicy] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> Dict[str, Any]:
        """Return structured access evaluation for a tool."""
        tool = self._tools.get(name)
        if tool is None:
            return {
                "tool": name,
                "exists": False,
                "allowed": False,
                "reason": "not_found",
            }

        provider = self._tool_provider(tool)
        resolved_model_provider, resolved_model_name = self._normalize_model_context(
            model_provider=model_provider,
            model_name=model_name,
        )
        has_model_context = bool(resolved_model_provider and resolved_model_name)
        rule_chain: List[Dict[str, Any]] = []

        def _append_rule(rule: str, allowed: bool, reason: str, details: Optional[Dict[str, Any]] = None) -> None:
            rule_chain.append(
                {
                    "rule": str(rule),
                    "allowed": bool(allowed),
                    "reason": str(reason),
                    "details": dict(details or {}),
                }
            )

        decision = {
            "tool": name,
            "exists": True,
            "provider": provider,
            "tier": tool.safety_tier.value,
            "model_context": {
                "provider": resolved_model_provider,
                "model": resolved_model_name,
                "available": has_model_context,
            },
            "allowed": True,
            "reason": "allowed",
            "checks": {
                "global_allowlist": True,
                "global_denylist": True,
                "policy_allow_names": True,
                "policy_deny_names": True,
                "policy_allow_providers": True,
                "policy_deny_providers": True,
                "policy_allow_model_providers": True,
                "policy_deny_model_providers": True,
                "policy_allow_model_names": True,
                "policy_deny_model_names": True,
                "policy_allow_model_selectors": True,
                "policy_deny_model_selectors": True,
                "owner_only": True,
                "tier": True,
            },
            "rule_chain": rule_chain,
        }

        if name in self._denylist:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_global_denylist"
            decision["checks"]["global_denylist"] = False
            _append_rule("global_denylist", False, "tool listed in global denylist")
            return decision
        _append_rule("global_denylist", True, "tool not in global denylist")
        if self._allowlist and name not in self._allowlist:
            decision["allowed"] = False
            decision["reason"] = "blocked_by_global_allowlist"
            decision["checks"]["global_allowlist"] = False
            _append_rule("global_allowlist", False, "tool missing in global allowlist")
            return decision
        _append_rule(
            "global_allowlist",
            True,
            "global allowlist empty or tool explicitly allowed",
        )

        owner_context_available = self._has_sender_context(channel=channel, sender_id=sender_id)
        owner_sender = (
            self._is_owner_sender(channel=channel, sender_id=sender_id)
            if owner_context_available
            else False
        )
        if self._is_owner_only_tool(tool):
            if owner_context_available and not owner_sender:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_owner_only"
                decision["checks"]["owner_only"] = False
                _append_rule(
                    "owner_only",
                    False,
                    "tool is owner-only and sender is not owner",
                    {
                        "owner_sender": owner_sender,
                        "sender_id": str(sender_id or "").strip(),
                        "channel": str(channel or "").strip(),
                    },
                )
                return decision
            if not owner_context_available:
                # Fail-closed: deny owner-only tools when sender context is
                # unavailable to prevent accidental privilege escalation.
                decision["allowed"] = False
                decision["reason"] = "blocked_by_owner_only_no_context"
                decision["checks"]["owner_only"] = False
                _append_rule(
                    "owner_only",
                    False,
                    "tool is owner-only; sender context unavailable, access denied (fail-closed)",
                )
                return decision
            _append_rule(
                "owner_only",
                True,
                "tool is owner-only and sender is owner",
                {
                    "owner_sender": owner_sender,
                    "context_available": owner_context_available,
                },
            )
        else:
            _append_rule("owner_only", True, "tool is not owner-only")

        if policy is not None:
            if name in policy.deny_names:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_deny_names"
                decision["checks"]["policy_deny_names"] = False
                _append_rule("policy_deny_names", False, "tool explicitly denied by policy")
                return decision
            _append_rule("policy_deny_names", True, "tool not denied by policy name rules")
            if provider in policy.deny_providers:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_deny_providers"
                decision["checks"]["policy_deny_providers"] = False
                _append_rule("policy_deny_providers", False, "tool provider denied by policy")
                return decision
            _append_rule("policy_deny_providers", True, "tool provider not denied by policy")
            if policy.allow_names and name not in policy.allow_names:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_allow_names"
                decision["checks"]["policy_allow_names"] = False
                _append_rule("policy_allow_names", False, "tool not in policy allow_names")
                return decision
            _append_rule(
                "policy_allow_names",
                True,
                "allow_names empty or tool matched allow_names",
            )
            if policy.allow_providers and provider not in policy.allow_providers:
                decision["allowed"] = False
                decision["reason"] = "blocked_by_policy_allow_providers"
                decision["checks"]["policy_allow_providers"] = False
                _append_rule("policy_allow_providers", False, "tool provider not in allow_providers")
                return decision
            _append_rule(
                "policy_allow_providers",
                True,
                "allow_providers empty or provider matched",
            )

            if has_model_context:
                if resolved_model_provider in policy.deny_model_providers:
                    decision["allowed"] = False
                    decision["reason"] = "blocked_by_policy_deny_model_providers"
                    decision["checks"]["policy_deny_model_providers"] = False
                    _append_rule("policy_deny_model_providers", False, "model provider denied by policy")
                    return decision
                _append_rule(
                    "policy_deny_model_providers",
                    True,
                    "model provider not denied by policy",
                )
                if policy.allow_model_providers and resolved_model_provider not in policy.allow_model_providers:
                    decision["allowed"] = False
                    decision["reason"] = "blocked_by_policy_allow_model_providers"
                    decision["checks"]["policy_allow_model_providers"] = False
                    _append_rule(
                        "policy_allow_model_providers",
                        False,
                        "model provider not in allow_model_providers",
                    )
                    return decision
                _append_rule(
                    "policy_allow_model_providers",
                    True,
                    "allow_model_providers empty or model provider matched",
                )

                if resolved_model_name in policy.deny_model_names:
                    decision["allowed"] = False
                    decision["reason"] = "blocked_by_policy_deny_model_names"
                    decision["checks"]["policy_deny_model_names"] = False
                    _append_rule("policy_deny_model_names", False, "model denied by policy")
                    return decision
                _append_rule("policy_deny_model_names", True, "model not denied by policy")
                if policy.allow_model_names and resolved_model_name not in policy.allow_model_names:
                    decision["allowed"] = False
                    decision["reason"] = "blocked_by_policy_allow_model_names"
                    decision["checks"]["policy_allow_model_names"] = False
                    _append_rule("policy_allow_model_names", False, "model not in allow_model_names")
                    return decision
                _append_rule(
                    "policy_allow_model_names",
                    True,
                    "allow_model_names empty or model matched",
                )

                for selector in sorted(policy.deny_model_selectors):
                    if self._matches_model_selector(
                        selector,
                        model_provider=resolved_model_provider,
                        model_name=resolved_model_name,
                    ):
                        decision["allowed"] = False
                        decision["reason"] = "blocked_by_policy_deny_model_selectors"
                        decision["checks"]["policy_deny_model_selectors"] = False
                        _append_rule(
                            "policy_deny_model_selectors",
                            False,
                            "model matched deny selector",
                            {"selector": selector},
                        )
                        return decision
                _append_rule(
                    "policy_deny_model_selectors",
                    True,
                    "model did not match deny selectors",
                )
                if policy.allow_model_selectors:
                    allow_hit = None
                    for selector in sorted(policy.allow_model_selectors):
                        if self._matches_model_selector(
                            selector,
                            model_provider=resolved_model_provider,
                            model_name=resolved_model_name,
                        ):
                            allow_hit = selector
                            break
                    if not allow_hit:
                        decision["allowed"] = False
                        decision["reason"] = "blocked_by_policy_allow_model_selectors"
                        decision["checks"]["policy_allow_model_selectors"] = False
                        _append_rule(
                            "policy_allow_model_selectors",
                            False,
                            "model did not match allow selectors",
                        )
                        return decision
                    _append_rule(
                        "policy_allow_model_selectors",
                        True,
                        "model matched allow selector",
                        {"selector": allow_hit},
                    )
                else:
                    _append_rule(
                        "policy_allow_model_selectors",
                        True,
                        "allow_model_selectors empty",
                    )
            else:
                _append_rule(
                    "policy_model_context",
                    True,
                    "model context unavailable; model-level rules skipped",
                    {
                        "allow_rules": bool(
                            policy.allow_model_providers
                            or policy.allow_model_names
                            or policy.allow_model_selectors
                        ),
                        "deny_rules": bool(
                            policy.deny_model_providers
                            or policy.deny_model_names
                            or policy.deny_model_selectors
                        ),
                    },
                )

        if max_tier is not None and self._tier_order(tool.safety_tier) > self._tier_order(max_tier):
            decision["allowed"] = False
            decision["reason"] = "blocked_by_tier"
            decision["checks"]["tier"] = False
            _append_rule(
                "tier",
                False,
                "tool tier exceeds requested max_tier",
                {"max_tier": max_tier.value, "tool_tier": tool.safety_tier.value},
            )
            return decision
        _append_rule(
            "tier",
            True,
            "tool tier allowed",
            {
                "max_tier": max_tier.value if isinstance(max_tier, ToolSafetyTier) else "",
                "tool_tier": tool.safety_tier.value,
            },
        )

        return decision

    def simulate_access(
        self,
        *,
        max_tier: Optional[ToolSafetyTier] = None,
        policy: Optional[ToolPolicy] = None,
        names: Optional[List[str]] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Simulate access for multiple tools."""
        tool_names = names if names is not None else list(self._tools.keys())
        return [
            self.evaluate_tool_access(
                name,
                max_tier=max_tier,
                policy=policy,
                sender_id=sender_id,
                channel=channel,
                model_provider=model_provider,
                model_name=model_name,
            )
            for name in tool_names
        ]

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
    
    def get_definitions(
        self,
        max_tier: Optional[ToolSafetyTier] = None,
        policy: Optional[ToolPolicy] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Get tool definitions in OpenAI format, filtered by tier."""
        return [
            tool.to_schema()
            for tool in self._tools.values()
            if self._is_allowed(
                tool.name,
                max_tier=max_tier,
                policy=policy,
                sender_id=sender_id,
                channel=channel,
                model_provider=model_provider,
                model_name=model_name,
            )
        ]

    async def execute(
        self,
        name: str,
        params: Dict[str, Any],
        *,
        max_tier: Optional[ToolSafetyTier] = None,
        policy: Optional[ToolPolicy] = None,
        cancel_token: Optional[CancellationToken] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> str:
        """
        Execute a tool by name with given parameters.
        
        Args:
            name: Tool name.
            params: Tool parameters.
            max_tier: If set, reject tools above this safety tier.
        
        Returns:
            Tool execution result as string.
        """
        trace_id = self._new_trace_id()
        tool = self._tools.get(name)
        if not tool:
            return self._error("TOOL_NOT_FOUND", f"Tool '{name}' not found", trace_id=trace_id)
        provider = self._tool_provider(tool)

        if not self._is_allowed(
            name,
            max_tier=max_tier,
            policy=policy,
            sender_id=sender_id,
            channel=channel,
            model_provider=model_provider,
            model_name=model_name,
        ):
            logger.warning(f"Tool '{name}' blocked by safety policy (tier={tool.safety_tier.value})")
            access = self.evaluate_tool_access(
                name,
                max_tier=max_tier,
                policy=policy,
                sender_id=sender_id,
                channel=channel,
                model_provider=model_provider,
                model_name=model_name,
            )
            reason = str(access.get("reason", "policy_or_tier"))
            self._record_rejection_event(
                code="TOOL_NOT_PERMITTED",
                name=name,
                provider=provider,
                reason=reason,
                trace_id=trace_id,
                metadata={
                    "tier": tool.safety_tier.value,
                    "max_tier": max_tier.value if isinstance(max_tier, ToolSafetyTier) else "",
                    "channel": str(channel or "").strip(),
                    "sender_id": str(sender_id or "").strip(),
                    "model_provider": str(model_provider or "").strip().lower(),
                    "model_name": str(model_name or "").strip().lower(),
                },
            )
            message = (
                f"Tool '{name}' is restricted to owner channels."
                if reason == "blocked_by_owner_only"
                else f"Tool '{name}' is not permitted for the current trust level."
            )
            return self._error(
                "TOOL_NOT_PERMITTED",
                message,
                trace_id=trace_id,
            )

        enabled, _threshold, _cooldown = self._circuit_settings()
        if enabled and self._is_circuit_open(name):
            self._record_rejection_event(
                code="TOOL_CIRCUIT_OPEN",
                name=name,
                provider=provider,
                reason="circuit_open",
                trace_id=trace_id,
            )
            return self._error(
                "TOOL_CIRCUIT_OPEN",
                f"Tool '{name}' is temporarily blocked after repeated failures.",
                trace_id=trace_id,
            )
        budget_exceeded, budget_reason = self._is_budget_exceeded(name=name, provider=provider)
        if budget_exceeded:
            status = self.get_budget_runtime_status()
            self._record_rejection_event(
                code="TOOL_BUDGET_EXCEEDED",
                name=name,
                provider=provider,
                reason=budget_reason or "budget_exceeded",
                trace_id=trace_id,
                metadata={
                    "used_calls": int(status.get("used_calls", 0)),
                    "max_calls": int(status.get("max_calls", 0)),
                    "used_weight": float(status.get("used_weight", 0.0)),
                    "max_weight": float(status.get("max_weight", 0.0)),
                },
            )
            return self._error(
                "TOOL_BUDGET_EXCEEDED",
                f"Tool execution budget exceeded for current rolling window ({budget_reason}).",
                trace_id=trace_id,
            )

        try:
            bs = self._budget_settings()
            budget_weight = self._resolve_budget_weight(
                tool_name=name,
                provider=provider,
                group_weights=bs.group_weights,
                tool_weights=bs.tool_weights,
            )
            if bs.enabled:
                self._record_budget_usage(name=name, provider=provider, weight=budget_weight)
            if cancel_token and cancel_token.is_cancelled:
                return self._error(
                    "TOOL_CANCELLED",
                    f"Operation cancelled before executing '{name}'.",
                    trace_id=trace_id,
                )
            errors = tool.validate_params(params)
            if errors:
                return self._error(
                    "TOOL_PARAMS_INVALID",
                    f"Invalid parameters for tool '{name}': " + "; ".join(errors),
                    trace_id=trace_id,
                )

            # --- Hook: before_tool_call ---
            effective_params = params
            if self._hooks:
                from plugins.hooks import HookAbort
                try:
                    effective_params = await self._hooks.run_before_tool_call(name, params)
                except HookAbort as abort:
                    return self._error(
                        "TOOL_BLOCKED_BY_HOOK",
                        f"Blocked by hook: {abort.reason}",
                        trace_id=trace_id,
                    )

            # Pass execution trust context to tools that need nested policy enforcement
            # (e.g. workflow tools that trigger additional tool calls).
            if isinstance(effective_params, dict):
                effective_params = dict(effective_params)
                effective_params.setdefault("_access_max_tier", max_tier)
                effective_params.setdefault("_access_policy", policy)
                effective_params.setdefault("_access_sender_id", str(sender_id or ""))
                effective_params.setdefault("_access_channel", str(channel or ""))

            with push_tool_access_context(channel=str(channel or ""), sender_id=str(sender_id or "")):
                result = await tool.execute(**effective_params)

            # --- Hook: after_tool_call ---
            if self._hooks:
                result = await self._hooks.run_after_tool_call(name, effective_params, result)

            self._record_tool_outcome(name, str(result))

            return result
        except asyncio.CancelledError:
            # Preserve cancellation semantics so upstream timeouts (e.g. asyncio.wait_for)
            # are surfaced as TimeoutError instead of being converted into a normal result.
            # Explicit user-triggered cancellation is still handled by the early
            # `cancel_token.is_cancelled` guard above.
            raise
        except Exception as e:
            # --- Hook: on_error ---
            if self._hooks:
                await self._hooks.run_on_error(name, e)
            logger.error(
                "Tool '%s' execution failed (trace_id=%s): %s",
                name,
                trace_id,
                e,
                exc_info=True,
            )
            error_result = self._error(
                "TOOL_EXECUTION_FAILED",
                f"Error executing {name}: {str(e)}",
                trace_id=trace_id,
            )
            self._record_tool_outcome(name, error_result)
            return error_result
    
    @property
    def tool_names(self) -> List[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools
