"""AgentLoop mixin: Tool Policy.

Extracted from loop.py to reduce file size.
Contains 14 methods.
"""

from __future__ import annotations

from agent.constants import *  # noqa: F403
from tools.base import ToolSafetyTier
from tools.registry import ToolPolicy, normalize_tool_policy
from bus.events import InboundMessage
import logging
import time
logger = logging.getLogger('AgentLoop')

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # Add type imports as needed


def _lazy_get_owner_manager():
    from security.owner import get_owner_manager as _fn
    return _fn()

def _lazy_get_persona_runtime_manager():
    from soul.persona_runtime import get_persona_runtime_manager as _fn
    return _fn()

def _lazy_evaluate_persona_tool_policy_linkage(*args, **kwargs):
    from agent.persona_tool_policy import evaluate_persona_tool_policy_linkage as _fn
    return _fn(*args, **kwargs)

def _lazy_apply_tool_policy_pipeline_steps(*args, **kwargs):
    from agent.tool_policy_pipeline import apply_tool_policy_pipeline_steps as _fn
    return _fn(*args, **kwargs)

def _lazy_merge_tool_policy_constraints(*args, **kwargs):
    from agent.tool_policy_pipeline import merge_tool_policy_constraints as _fn
    return _fn(*args, **kwargs)

# Module-level aliases used throughout this mixin
get_owner_manager = _lazy_get_owner_manager
get_persona_runtime_manager = _lazy_get_persona_runtime_manager
evaluate_persona_tool_policy_linkage = _lazy_evaluate_persona_tool_policy_linkage
apply_tool_policy_pipeline_steps = _lazy_apply_tool_policy_pipeline_steps
merge_tool_policy_constraints = _lazy_merge_tool_policy_constraints


class ToolPolicyMixin:
    """Mixin providing tool policy functionality."""

    @staticmethod
    def _is_release_gate_enforced() -> bool:
        from runtime.config_manager import config as _cfg

        return bool(_cfg.get("security.release_gate_enforcement", True))

    @staticmethod
    def _is_release_gate_owner_bypass_enabled() -> bool:
        from runtime.config_manager import config as _cfg

        return bool(_cfg.get("security.release_gate_owner_bypass", False))

    def _release_gate_block_message(
        self,
        *,
        tool_name: str,
        tier: ToolSafetyTier,
        gate_reason: str,
    ) -> str:
        reason = gate_reason or "quality gate blocked"
        return (
            "Error [TOOL_RELEASE_GATE_BLOCKED]: "
            f"Release gate is active and blocked by '{reason}'. "
            f"Tool '{tool_name}' (tier={tier.value}) is disabled until gate is unblocked. "
            f"(trace_id={self._new_trace_id()})\n"
            "Hint: Unblock the release gate or use a lower-tier alternative tool."
        )

    def _release_gate_limits_tier(self, *, sender_id: str, channel: str) -> bool:
        if not self._is_release_gate_enforced():
            return False
        gate = self._eval_benchmark_manager.get_release_gate_status()
        if not bool(gate.get("blocked", False)):
            return False
        if self._is_release_gate_owner_bypass_enabled():
            owner_mgr = get_owner_manager()
            if owner_mgr and owner_mgr.is_owner_sender(channel, sender_id):
                return False
        return True

    def _check_release_gate_for_tool(self, *, tool_name: str, sender_id: str, channel: str) -> Optional[str]:
        if not self._is_release_gate_enforced():
            return None

        gate = self._eval_benchmark_manager.get_release_gate_status()
        if not bool(gate.get("blocked", False)):
            return None

        if self._is_release_gate_owner_bypass_enabled():
            owner_mgr = get_owner_manager()
            if owner_mgr and owner_mgr.is_owner_sender(channel, sender_id):
                return None

        tool = self.tools.get(tool_name)
        if tool is None:
            return None
        if tool.safety_tier == ToolSafetyTier.SAFE:
            return None

        gate_reason = str(gate.get("reason", "")).strip()
        message = self._release_gate_block_message(
            tool_name=tool_name,
            tier=tool.safety_tier,
            gate_reason=gate_reason,
        )
        logger.warning(
            "Release gate blocked tool execution: tool=%s tier=%s reason=%s",
            tool_name,
            tool.safety_tier.value,
            gate_reason,
        )
        return message

    def _get_tool_governance_limits(self) -> tuple[int, int]:
        """Return (max_tool_calls_per_turn, max_parallel_tool_calls)."""
        from runtime.config_manager import config as _cfg

        raw_turn_limit = _cfg.get("security.max_tool_calls_per_turn", DEFAULT_MAX_TOOL_CALLS_PER_TURN)
        raw_parallel_limit = _cfg.get("security.max_parallel_tool_calls", DEFAULT_MAX_PARALLEL_TOOL_CALLS)
        try:
            turn_limit = int(raw_turn_limit)
        except (TypeError, ValueError):
            turn_limit = DEFAULT_MAX_TOOL_CALLS_PER_TURN
        try:
            parallel_limit = int(raw_parallel_limit)
        except (TypeError, ValueError):
            parallel_limit = DEFAULT_MAX_PARALLEL_TOOL_CALLS

        provider_agents_defaults = self._resolve_active_provider_agents_defaults()
        raw_provider_parallel_limit = (
            provider_agents_defaults.get("maxConcurrent")
            if isinstance(provider_agents_defaults, dict)
            else None
        )
        if raw_provider_parallel_limit is not None:
            try:
                provider_parallel_limit = int(raw_provider_parallel_limit)
            except (TypeError, ValueError):
                provider_parallel_limit = 0
            if provider_parallel_limit > 0:
                parallel_limit = provider_parallel_limit

        turn_limit = min(max(turn_limit, 1), 200)
        parallel_limit = min(max(parallel_limit, 1), 64)
        return turn_limit, parallel_limit

    @staticmethod
    def _get_parallel_tool_lane_limits() -> Dict[str, int]:
        from runtime.config_manager import config as _cfg

        raw = _cfg.get("security.parallel_tool_lane_limits", {}) or {}
        merged = dict(DEFAULT_PARALLEL_TOOL_LANE_LIMITS)
        if isinstance(raw, dict):
            for key in DEFAULT_PARALLEL_TOOL_LANE_LIMITS.keys():
                if key not in raw:
                    continue
                try:
                    value = int(raw.get(key))
                except (TypeError, ValueError):
                    continue
                merged[key] = min(max(value, 1), 32)
        return merged

    def _current_tool_policy_model_context(self) -> tuple[str, str]:
        provider = str(self._tool_policy_model_provider or "").strip().lower()
        model = str(self._tool_policy_model_name or "").strip().lower()
        if not provider:
            provider = self._resolve_llm_provider_key(self._active_provider_override or self.provider)
        if not model:
            model = str(self._active_model_override or self.model or "").strip().lower()
        return provider, model

    def _resolve_tool_max_tier(self, msg: InboundMessage) -> ToolSafetyTier:
        """Resolve per-message tool tier.

        Owner messages always get ``PRIVILEGED`` access.
        Non-owner messages follow ``security.tool_max_tier``.
        """
        if msg.sender_id == "owner" or get_owner_manager().is_owner_sender(msg.channel, msg.sender_id):
            return ToolSafetyTier.PRIVILEGED

        from runtime.config_manager import config as _cfg

        configured = str(_cfg.get("security.tool_max_tier", "standard")).strip().lower()
        tier = _TIER_MAP.get(configured)
        if tier is None:
            logger.warning("Invalid security.tool_max_tier=%r, falling back to 'standard'", configured)
            tier = ToolSafetyTier.STANDARD
        return self._apply_persona_signal_tier_guard(base_tier=tier, msg=msg)

    def _apply_persona_signal_tier_guard(self, *, base_tier: ToolSafetyTier, msg: InboundMessage) -> ToolSafetyTier:
        from runtime.config_manager import config as _cfg

        runtime_cfg = _cfg.get("personality.runtime", {}) or {}
        if not isinstance(runtime_cfg, dict):
            return base_tier
        guard_cfg = runtime_cfg.get("tool_tier_guard", {}) or {}
        if not isinstance(guard_cfg, dict):
            guard_cfg = {}
        if not bool(guard_cfg.get("enabled", True)):
            return base_tier
        levels_raw = guard_cfg.get("trigger_levels", ["critical"])
        levels = {
            str(item).strip().lower()
            for item in levels_raw
            if str(item).strip()
        } if isinstance(levels_raw, list) else {"critical"}
        if not levels:
            levels = {"critical"}
        high_risk_raw = guard_cfg.get("high_risk_levels", list(levels))
        high_risk_levels = {
            str(item).strip().lower()
            for item in high_risk_raw
            if str(item).strip()
        } if isinstance(high_risk_raw, list) else set(levels)
        if not high_risk_levels:
            high_risk_levels = set(levels)
        sources_raw = guard_cfg.get("sources", ["agent_loop", "persona_eval"])
        source_filter = {
            str(item).strip().lower()
            for item in sources_raw
            if str(item).strip()
        } if isinstance(sources_raw, list) else set()
        window_raw = guard_cfg.get("window_seconds", 1800)
        try:
            window_seconds = max(0, int(window_raw))
        except (TypeError, ValueError):
            window_seconds = 1800
        downgrade_by_level_raw = guard_cfg.get("downgrade_by_level", {})
        downgrade_by_level = (
            {
                str(key).strip().lower(): str(value).strip().lower()
                for key, value in downgrade_by_level_raw.items()
            }
            if isinstance(downgrade_by_level_raw, dict)
            else {}
        )
        downgrade_key_default = str(guard_cfg.get("downgrade_to", "safe")).strip().lower() or "safe"

        manager = get_persona_runtime_manager()
        signal = manager.get_latest_signal() if hasattr(manager, "get_latest_signal") else None
        if not isinstance(signal, dict):
            return base_tier
        signal_level = str(signal.get("level", "")).strip().lower()
        if signal_level not in levels:
            return base_tier
        if signal_level not in high_risk_levels:
            return base_tier
        signal_source = str(signal.get("source", "")).strip().lower()
        if source_filter and signal_source and signal_source not in source_filter:
            return base_tier
        created_at = signal.get("created_at", 0.0)
        try:
            created_at_ts = float(created_at)
        except (TypeError, ValueError):
            created_at_ts = 0.0
        if window_seconds > 0 and created_at_ts > 0 and (time.time() - created_at_ts) > float(window_seconds):
            return base_tier
        downgrade_key = str(downgrade_by_level.get(signal_level, downgrade_key_default)).strip().lower() or "safe"
        downgrade_tier = _TIER_MAP.get(downgrade_key, ToolSafetyTier.SAFE)

        rank = {
            ToolSafetyTier.SAFE: 0,
            ToolSafetyTier.STANDARD: 1,
            ToolSafetyTier.PRIVILEGED: 2,
        }
        if rank[downgrade_tier] > rank[base_tier]:
            return base_tier
        if downgrade_tier != base_tier:
            logger.info(
                "Persona runtime signal downgraded tool tier: %s -> %s (level=%s source=%s sender=%s channel=%s)",
                base_tier.value,
                downgrade_tier.value,
                signal_level,
                signal_source,
                msg.sender_id,
                msg.channel,
            )
        return downgrade_tier

    def _resolve_tool_policy(self) -> ToolPolicy:
        """Build effective tool policy for this loop."""
        from runtime.config_manager import config as _cfg

        groups_raw = _cfg.get("security.tool_groups", {})
        groups = groups_raw if isinstance(groups_raw, dict) else {}
        base = normalize_tool_policy(self._tool_policy_raw, groups)
        allowed_from_agents: set[str] = set()
        deny_from_agents: set[str] = set()
        if hasattr(self.context, "get_agents_tool_policy_overlay"):
            try:
                overlay = self.context.get_agents_tool_policy_overlay()
            except Exception:
                logger.debug("Failed to load AGENTS.md tool policy overlay", exc_info=True)
                overlay = {}
            if isinstance(overlay, dict):
                allowed_from_agents = {
                    str(item).strip()
                    for item in (overlay.get("allowed_tools", []) or [])
                    if str(item).strip()
                }
                deny_from_agents = {
                    str(item).strip()
                    for item in (overlay.get("deny_tools", []) or [])
                    if str(item).strip()
                }
        pipeline_steps: List[Dict[str, Any]] = [
            {
                "label": "agents_md_overlay",
                "overlay": {
                    "allow_names": allowed_from_agents,
                    "deny_names": deny_from_agents,
                },
            }
        ]
        runtime_cfg = _cfg.get("personality.runtime", {}) or {}
        if not isinstance(runtime_cfg, dict):
            runtime_cfg = {}
        manager = get_persona_runtime_manager()
        signal = manager.get_latest_signal() if hasattr(manager, "get_latest_signal") else None
        linkage = evaluate_persona_tool_policy_linkage(runtime_cfg=runtime_cfg, signal=signal)
        self._persona_tool_policy_linkage_status = linkage
        overlay = linkage.get("policy_overlay", {}) if isinstance(linkage.get("policy_overlay"), dict) else {}
        pipeline_steps.append(
            {
                "label": "persona_runtime_overlay",
                "overlay": {
                    "allow_names": {
                        str(item).strip()
                        for item in (overlay.get("allow_names", []) or [])
                        if str(item).strip()
                    },
                    "deny_names": {
                        str(item).strip()
                        for item in (overlay.get("deny_names", []) or [])
                        if str(item).strip()
                    },
                    "allow_providers": {
                        str(item).strip()
                        for item in (overlay.get("allow_providers", []) or [])
                        if str(item).strip()
                    },
                    "deny_providers": {
                        str(item).strip()
                        for item in (overlay.get("deny_providers", []) or [])
                        if str(item).strip()
                    },
                    "allow_model_providers": {
                        str(item).strip().lower()
                        for item in (overlay.get("allow_model_providers", []) or [])
                        if str(item).strip()
                    },
                    "deny_model_providers": {
                        str(item).strip().lower()
                        for item in (overlay.get("deny_model_providers", []) or [])
                        if str(item).strip()
                    },
                    "allow_model_names": {
                        str(item).strip().lower()
                        for item in (overlay.get("allow_model_names", []) or [])
                        if str(item).strip()
                    },
                    "deny_model_names": {
                        str(item).strip().lower()
                        for item in (overlay.get("deny_model_names", []) or [])
                        if str(item).strip()
                    },
                    "allow_model_selectors": {
                        str(item).strip().lower()
                        for item in (overlay.get("allow_model_selectors", []) or [])
                        if str(item).strip()
                    },
                    "deny_model_selectors": {
                        str(item).strip().lower()
                        for item in (overlay.get("deny_model_selectors", []) or [])
                        if str(item).strip()
                    },
                },
            }
        )
        resolved, diagnostics = apply_tool_policy_pipeline_steps(base=base, steps=pipeline_steps)
        base_counts = {
            "allow_names": len(base.allow_names),
            "deny_names": len(base.deny_names),
            "allow_providers": len(base.allow_providers),
            "deny_providers": len(base.deny_providers),
            "allow_model_providers": len(base.allow_model_providers),
            "deny_model_providers": len(base.deny_model_providers),
            "allow_model_names": len(base.allow_model_names),
            "deny_model_names": len(base.deny_model_names),
            "allow_model_selectors": len(base.allow_model_selectors),
            "deny_model_selectors": len(base.deny_model_selectors),
        }
        final_counts = {
            "allow_names": len(resolved.allow_names),
            "deny_names": len(resolved.deny_names),
            "allow_providers": len(resolved.allow_providers),
            "deny_providers": len(resolved.deny_providers),
            "allow_model_providers": len(resolved.allow_model_providers),
            "deny_model_providers": len(resolved.deny_model_providers),
            "allow_model_names": len(resolved.allow_model_names),
            "deny_model_names": len(resolved.deny_model_names),
            "allow_model_selectors": len(resolved.allow_model_selectors),
            "deny_model_selectors": len(resolved.deny_model_selectors),
        }
        self._tool_policy_pipeline_status = {
            "reason": "ok",
            "steps": diagnostics,
            "base_counts": base_counts,
            "final_counts": final_counts,
            "evaluated_at": time.time(),
        }
        return resolved

    @staticmethod
    def _merge_tool_policy(
        base: ToolPolicy,
        *,
        allow_names: Optional[set[str]] = None,
        deny_names: Optional[set[str]] = None,
        allow_providers: Optional[set[str]] = None,
        deny_providers: Optional[set[str]] = None,
        allow_model_providers: Optional[set[str]] = None,
        deny_model_providers: Optional[set[str]] = None,
        allow_model_names: Optional[set[str]] = None,
        deny_model_names: Optional[set[str]] = None,
        allow_model_selectors: Optional[set[str]] = None,
        deny_model_selectors: Optional[set[str]] = None,
    ) -> ToolPolicy:
        return merge_tool_policy_constraints(
            base,
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

    def get_persona_tool_policy_linkage_status(self) -> Dict[str, Any]:
        """Return latest persona-signal-driven tool policy linkage status."""
        snapshot = self._persona_tool_policy_linkage_status
        if not isinstance(snapshot, dict) or str(snapshot.get("reason", "")) == "not_evaluated":
            _ = self._resolve_tool_policy()
            snapshot = self._persona_tool_policy_linkage_status
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def get_tool_policy_pipeline_status(self) -> Dict[str, Any]:
        """Return latest tool-policy pipeline status for observability."""
        snapshot = self._tool_policy_pipeline_status
        if not isinstance(snapshot, dict) or str(snapshot.get("reason", "")) == "not_evaluated":
            _ = self._resolve_tool_policy()
            snapshot = self._tool_policy_pipeline_status
        return dict(snapshot) if isinstance(snapshot, dict) else {}

