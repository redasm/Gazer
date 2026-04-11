"""Tool registry for dynamic tool management with owner-only / allow-deny policy.

    from its provider to map generic requests to local executions.
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from tools.base import CancellationToken, Tool
from tools.registry_access import evaluate_tool_access_decision
from tools.registry_definitions import list_tool_definitions
from tools.registry_execute import evaluate_pre_execution_block, prepare_execution_context, run_tool_pipeline
from tools.registry_errors import (
    DEFAULT_TOOL_ERROR_HINTS,
    format_tool_error,
    new_tool_trace_id,
)
from tools.registry_policy import ToolPolicy, normalize_tool_policy
from tools.registry_runtime import BudgetSettings, ToolRegistryRuntimeState
from runtime.config_manager import config as gazer_config

if TYPE_CHECKING:
    from plugins.hooks import HookRegistry

logger = logging.getLogger("ToolRegistry")

_DEFAULT_ERROR_HINTS: Dict[str, str] = DEFAULT_TOOL_ERROR_HINTS


class ToolRegistry:
    """
    Registry for agent tools.
    
    Allows dynamic registration and execution of tools.
    """
    
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        # Per-name overrides: names in the denylist are never exposed.
        self._denylist: Set[str] = set()
        # If non-empty, only these tool names are exposed (allowlist wins).
        self._allowlist: Set[str] = set()
        # Hook registry (injected after init to avoid circular imports)
        self._hooks: Optional["HookRegistry"] = None
        self._runtime_state = ToolRegistryRuntimeState()

    @staticmethod
    def _error(code: str, message: str, *, trace_id: str = "", hint: str = "") -> str:
        """Standard tool error format.

        Keep the first line as `Error [CODE]: ...` so AgentLoop can parse `error_code`.
        Additional fields are appended on separate lines to stay human-readable.
        """
        return format_tool_error(code, message, trace_id=trace_id, hint=hint)

    @staticmethod
    def _new_trace_id() -> str:
        return new_tool_trace_id()

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
        self._runtime_state.record_rejection_event(
            code=code,
            name=name,
            provider=provider,
            reason=reason,
            trace_id=trace_id,
            metadata=metadata,
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

    def _record_budget_usage(self, *, name: str, provider: str, weight: float) -> None:
        bs = self._budget_settings()
        self._runtime_state.record_budget_usage(name=name, provider=provider, weight=weight, settings=bs)

    def get_budget_runtime_status(self) -> Dict[str, Any]:
        """Return current tool-budget runtime status for observability."""
        bs = self._budget_settings()
        return self._runtime_state.budget_runtime_status(bs)

    def get_recent_rejection_events(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Return most recent governance rejection events (newest first)."""
        safe_limit = max(1, min(int(limit), 500))
        return self._runtime_state.recent_rejection_events(limit=safe_limit)

    def _is_budget_exceeded(self, *, name: str, provider: str) -> tuple[bool, str]:
        bs = self._budget_settings()
        return self._runtime_state.is_budget_exceeded(name=name, provider=provider, settings=bs)

    def _is_circuit_open(self, name: str) -> bool:
        return self._runtime_state.is_circuit_open(name)

    def _record_tool_outcome(self, name: str, result: str) -> None:
        enabled, threshold, cooldown = self._circuit_settings()
        self._runtime_state.record_tool_outcome(
            name,
            result,
            enabled=enabled,
            threshold=threshold,
            cooldown=cooldown,
        )

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
        logger.info("Tool allowlist set: %s", self._allowlist)

    def set_denylist(self, names: List[str]) -> None:
        """These tool names are always hidden even if registered."""
        self._denylist = set(names)
        logger.info("Tool denylist set: %s", self._denylist)

    # ------------------------------------------------------------------
    # Tier-aware queries
    # ------------------------------------------------------------------

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
        return False


    def _is_allowed(
        self,
        name: str,
        policy: Optional[ToolPolicy] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> bool:
        """Check if tool *name* passes allowlist, denylist and tier filters."""
        decision = self.evaluate_tool_access(
            name,
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
        policy: Optional[ToolPolicy] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> Dict[str, Any]:
        """Return structured access evaluation for a tool."""
        tool = self._tools.get(name)
        owner_context_available = self._has_sender_context(channel=channel, sender_id=sender_id)
        return evaluate_tool_access_decision(
            name=name,
            tool=tool,
            denylist=self._denylist,
            allowlist=self._allowlist,
            policy=policy,
            sender_id=sender_id,
            channel=channel,
            model_provider=model_provider,
            model_name=model_name,
            provider=self._tool_provider(tool) if tool is not None else "",
            owner_context_available=owner_context_available,
            owner_sender=self._is_owner_sender(channel=channel, sender_id=sender_id) if owner_context_available else False,
            owner_only=self._is_owner_only_tool(tool) if tool is not None else False,
        )

    def simulate_access(
        self,
        *,
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
        policy: Optional[ToolPolicy] = None,
        sender_id: str = "",
        channel: str = "",
        model_provider: str = "",
        model_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Get tool definitions in OpenAI format, filtered by policy."""
        return list_tool_definitions(
            self._tools,
            is_allowed=self._is_allowed,
            policy=policy,
            sender_id=sender_id,
            channel=channel,
            model_provider=model_provider,
            model_name=model_name,
        )

    async def execute(
        self,
        name: str,
        params: Dict[str, Any],
        *,
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

        Returns:
            Tool execution result as string.
        """
        trace_id = self._new_trace_id()
        tool = self._tools.get(name)
        if not tool:
            return self._error("TOOL_NOT_FOUND", f"Tool '{name}' not found", trace_id=trace_id)
        provider = self._tool_provider(tool)

        access = self.evaluate_tool_access(
            name,
            policy=policy,
            sender_id=sender_id,
            channel=channel,
            model_provider=model_provider,
            model_name=model_name,
        )
        enabled, _threshold, _cooldown = self._circuit_settings()
        budget_exceeded, budget_reason = self._is_budget_exceeded(name=name, provider=provider)
        precheck_error = evaluate_pre_execution_block(
            access_allowed=bool(access.get("allowed", False)),
            access_reason=str(access.get("reason", "policy")),
            tool_owner_only=bool(tool.owner_only),
            trace_id=trace_id,
            name=name,
            provider=provider,
            channel=channel,
            sender_id=sender_id,
            model_provider=model_provider,
            model_name=model_name,
            circuit_open=bool(enabled and self._is_circuit_open(name)),
            budget_exceeded=bool(budget_exceeded),
            budget_reason=str(budget_reason or ""),
            budget_status=self.get_budget_runtime_status() if budget_exceeded else None,
            record_rejection_event=self._record_rejection_event,
            error_builder=self._error,
        )
        if precheck_error:
            logger.warning("Tool '%s' blocked by pre-execution guard (owner_only=%s)", name, tool.owner_only)
            return precheck_error

        try:
            bs = self._budget_settings()
            prep_error, _budget_weight = prepare_execution_context(
                tool=tool,
                name=name,
                params=params,
                cancel_token=cancel_token,
                trace_id=trace_id,
                budget_settings=bs,
                resolve_budget_weight=lambda **kwargs: self._runtime_state.resolve_budget_weight(
                    tool_name=kwargs["tool_name"],
                    provider=provider,
                    group_weights=kwargs["group_weights"],
                    tool_weights=kwargs["tool_weights"],
                ),
                record_budget_usage=lambda **kwargs: self._record_budget_usage(
                    name=kwargs["name"],
                    provider=provider,
                    weight=kwargs["weight"],
                ),
                error_builder=self._error,
            )
            if prep_error:
                return prep_error

            owner_context_available = self._has_sender_context(channel=channel, sender_id=sender_id)
            owner_sender = self._is_owner_sender(channel=channel, sender_id=sender_id) if owner_context_available else False
            try:
                result = await run_tool_pipeline(
                    tool=tool,
                    name=name,
                    params=params,
                    hooks=self._hooks,
                    policy=policy,
                    sender_id=sender_id,
                    channel=channel,
                    sender_is_owner=owner_sender,
                )
            except Exception as hook_exc:
                from plugins.hooks import HookAbort

                if isinstance(hook_exc, HookAbort):
                    return self._error(
                        "TOOL_BLOCKED_BY_HOOK",
                        f"Blocked by hook: {hook_exc.reason}",
                        trace_id=trace_id,
                    )
                raise

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
