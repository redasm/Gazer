"""Deployment orchestrator for router deployment targets."""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from runtime.config_manager import config
from runtime.provider_registry import get_provider_registry

logger = logging.getLogger("DeploymentOrchestrator")


@dataclass
class _RollbackSnapshot:
    policy: Dict[str, Any]
    deployment_targets_order: List[str]
    weights: Dict[str, float]
    reason: str
    ts: float


class DeploymentOrchestrator:
    """Policy orchestration for deployment targets (weights/failover/rollback)."""

    def __init__(self) -> None:
        self._last_probes: List[Dict[str, Any]] = []
        self._history: List[Dict[str, Any]] = []
        self._rollback_snapshot: Optional[_RollbackSnapshot] = None

    @staticmethod
    def _default_policy() -> Dict[str, Any]:
        return {
            "enabled": False,
            "mode": "manual",  # manual | auto
            "target_ids": [],
            "active_target": "",
            "standby_targets": [],
            "weights": {},
            "canary": {
                "enabled": False,
                "target": "",
                "weight": 0.1,
            },
            "auto_failover": {
                "enabled": True,
                "cooldown_seconds": 30,
                "auto_rollback": True,
            },
        }

    @staticmethod
    def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        out = copy.deepcopy(base if isinstance(base, dict) else {})
        if not isinstance(patch, dict):
            return out
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = DeploymentOrchestrator._deep_merge(out.get(key, {}), value)
            else:
                out[key] = copy.deepcopy(value)
        return out

    @staticmethod
    def _normalize_target_list(raw: Any) -> List[str]:
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    @staticmethod
    def _normalize_weights(raw: Any, valid_targets: List[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        candidate = raw if isinstance(raw, dict) else {}
        valid = set(valid_targets)
        for key, value in candidate.items():
            name = str(key).strip()
            if not name or name not in valid:
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = 1.0
            out[name] = max(0.01, parsed)
        for name in valid_targets:
            out.setdefault(name, 1.0)
        return out

    @staticmethod
    def _healthy_map_from_probes(probes: List[Dict[str, Any]]) -> Dict[str, bool]:
        out: Dict[str, bool] = {}
        for item in probes:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            out[name] = bool(item.get("healthy", False))
        return out

    @staticmethod
    def _sort_by_weight(items: List[str], weights: Dict[str, float]) -> List[str]:
        return sorted(
            items,
            key=lambda name: (-float(weights.get(name, 1.0)), name),
        )

    def _list_targets(self) -> Dict[str, Dict[str, Any]]:
        registry = get_provider_registry()
        if hasattr(registry, "list_deployment_targets"):
            data = registry.list_deployment_targets()
            if isinstance(data, dict):
                return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
        return {}

    def _get_router_order(self) -> List[str]:
        return self._normalize_target_list(config.get("models.router.deployment_targets", []))

    def _set_router_order(self, target_ids: List[str]) -> None:
        updates = {
            "models.router.enabled": True,
            "models.router.strategy": "priority",
            "models.router.deployment_targets": list(target_ids),
        }
        config.set_many(updates)

    def get_policy(self) -> Dict[str, Any]:
        raw = config.get("models.router.deployment_orchestrator", {}) or {}
        merged = self._deep_merge(self._default_policy(), raw if isinstance(raw, dict) else {})
        targets = self._list_targets()
        valid_targets = list(targets.keys())

        target_ids = self._normalize_target_list(merged.get("target_ids", []))
        if not target_ids:
            target_ids = self._get_router_order()
        if not target_ids:
            target_ids = valid_targets
        target_ids = [item for item in target_ids if item in targets]

        active_target = str(merged.get("active_target", "")).strip()
        if active_target and active_target not in target_ids:
            active_target = ""
        standby_targets = [
            item
            for item in self._normalize_target_list(merged.get("standby_targets", []))
            if item in target_ids and item != active_target
        ]
        weights = self._normalize_weights(merged.get("weights", {}), target_ids)

        canary_cfg = merged.get("canary", {}) if isinstance(merged.get("canary"), dict) else {}
        canary_target = str(canary_cfg.get("target", "")).strip()
        if canary_target and canary_target not in target_ids:
            canary_target = ""
        try:
            canary_weight = float(canary_cfg.get("weight", 0.1) or 0.1)
        except (TypeError, ValueError):
            canary_weight = 0.1
        canary_weight = max(0.0, min(1.0, canary_weight))

        failover_cfg = (
            merged.get("auto_failover", {})
            if isinstance(merged.get("auto_failover"), dict)
            else {}
        )
        try:
            cooldown_seconds = max(0, int(failover_cfg.get("cooldown_seconds", 30) or 30))
        except (TypeError, ValueError):
            cooldown_seconds = 30

        return {
            "enabled": bool(merged.get("enabled", False)),
            "mode": str(merged.get("mode", "manual") or "manual").strip().lower() or "manual",
            "target_ids": target_ids,
            "active_target": active_target,
            "standby_targets": standby_targets,
            "weights": weights,
            "canary": {
                "enabled": bool(canary_cfg.get("enabled", False)),
                "target": canary_target,
                "weight": canary_weight,
            },
            "auto_failover": {
                "enabled": bool(failover_cfg.get("enabled", True)),
                "cooldown_seconds": cooldown_seconds,
                "auto_rollback": bool(failover_cfg.get("auto_rollback", True)),
            },
        }

    def _save_policy(self, policy: Dict[str, Any]) -> None:
        config.set_many({"models.router.deployment_orchestrator": policy})

    def update_policy(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_policy()
        merged = self._deep_merge(current, patch if isinstance(patch, dict) else {})
        self._save_policy(merged)
        normalized = self.get_policy()
        self._append_history("policy_updated", {"patch": patch})
        return normalized

    def _append_history(self, action: str, payload: Dict[str, Any]) -> None:
        self._history.append(
            {
                "ts": time.time(),
                "action": str(action),
                "payload": copy.deepcopy(payload if isinstance(payload, dict) else {}),
            }
        )
        if len(self._history) > 200:
            self._history = self._history[-200:]

    def compute_target_order(
        self,
        policy: Optional[Dict[str, Any]] = None,
        *,
        healthy_map: Optional[Dict[str, bool]] = None,
    ) -> List[str]:
        current = policy if isinstance(policy, dict) else self.get_policy()
        target_ids = list(current.get("target_ids", []) or [])
        weights = dict(current.get("weights", {}) or {})
        active = str(current.get("active_target", "")).strip()
        canary_cfg = current.get("canary", {}) if isinstance(current.get("canary"), dict) else {}
        canary_enabled = bool(canary_cfg.get("enabled", False))
        canary_target = str(canary_cfg.get("target", "")).strip()
        canary_weight = float(canary_cfg.get("weight", 0.1) or 0.1)
        healthy = healthy_map if isinstance(healthy_map, dict) else {}

        ordered = self._sort_by_weight(target_ids, weights)
        if active and active in ordered:
            ordered.remove(active)
            ordered.insert(0, active)

        if canary_enabled and canary_target and canary_target in ordered:
            ordered.remove(canary_target)
            insert_index = 0 if canary_weight >= 0.5 else min(1, len(ordered))
            ordered.insert(insert_index, canary_target)

        if healthy:
            healthy_items = [item for item in ordered if healthy.get(item, True)]
            unhealthy_items = [item for item in ordered if not healthy.get(item, True)]
            ordered = healthy_items + unhealthy_items
        return ordered

    def apply_policy(
        self,
        *,
        reason: str = "manual_apply",
        capture_rollback: bool = True,
    ) -> Dict[str, Any]:
        policy = self.get_policy()
        target_ids = self.compute_target_order(policy)
        previous_order = self._get_router_order()
        weights = dict(policy.get("weights", {}) or {})
        effective_weights: Dict[str, float] = {}
        total = max(1, len(target_ids))
        for idx, target_id in enumerate(target_ids):
            base_weight = max(0.01, float(weights.get(target_id, 1.0)))
            # Encode explicit order into tiny deterministic bonuses.
            order_bonus = float(total - idx) / 1000.0
            effective_weights[target_id] = base_weight + order_bonus

        if capture_rollback:
            self._rollback_snapshot = _RollbackSnapshot(
                policy=policy,
                deployment_targets_order=previous_order,
                weights=weights,
                reason=reason,
                ts=time.time(),
            )

        self._set_router_order(target_ids)
        registry = get_provider_registry()
        targets = self._list_targets()
        for target_id, target_cfg in targets.items():
            next_cfg = dict(target_cfg)
            next_cfg["traffic_weight"] = float(effective_weights.get(target_id, weights.get(target_id, 1.0)))
            next_cfg["orchestrator_active"] = bool(target_id == str(policy.get("active_target", "")))
            try:
                registry.upsert_deployment_target(target_id, next_cfg)
            except Exception:
                logger.debug("Failed to upsert deployment target %s", target_id, exc_info=True)

        self._append_history(
            "policy_applied",
            {
                "reason": reason,
                "target_order": target_ids,
                "active_target": policy.get("active_target", ""),
            },
        )
        return self.get_status()

    def reconcile(self, probes: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._last_probes = list(probes or [])
        policy = self.get_policy()
        auto_cfg = policy.get("auto_failover", {}) if isinstance(policy.get("auto_failover"), dict) else {}
        if (
            policy.get("enabled", False)
            and str(policy.get("mode", "manual")).strip().lower() == "auto"
            and bool(auto_cfg.get("enabled", True))
        ):
            healthy_map = self._healthy_map_from_probes(self._last_probes)
            active_target = str(policy.get("active_target", "")).strip()
            if active_target and (healthy_map.get(active_target) is False):
                ordered = self.compute_target_order(policy, healthy_map=healthy_map)
                candidate = next(
                    (item for item in ordered if healthy_map.get(item, True)),
                    "",
                )
                if candidate and candidate != active_target:
                    self.failover(target_id=candidate, reason=f"active_unhealthy:{active_target}")
        return self.get_status()

    def failover(self, *, target_id: str, reason: str = "manual_failover") -> Dict[str, Any]:
        policy = self.get_policy()
        before_policy = copy.deepcopy(policy)
        before_order = list(self._get_router_order())
        before_weights = dict(before_policy.get("weights", {}) or {})
        target = str(target_id or "").strip()
        if not target:
            raise ValueError("target_id is required")
        if target not in list(policy.get("target_ids", []) or []):
            raise ValueError(f"unknown target_id: {target}")

        previous_active = str(policy.get("active_target", "")).strip()
        standby = [item for item in list(policy.get("standby_targets", []) or []) if item != target]
        if previous_active and previous_active != target and previous_active not in standby:
            standby.insert(0, previous_active)

        patch = {
            "active_target": target,
            "standby_targets": standby,
        }
        self._rollback_snapshot = _RollbackSnapshot(
            policy=before_policy,
            deployment_targets_order=before_order,
            weights=before_weights,
            reason=f"failover:{reason}",
            ts=time.time(),
        )
        new_policy = self.update_policy(patch)
        self._append_history(
            "failover",
            {"to": target, "from": previous_active, "reason": reason},
        )
        self._save_policy(new_policy)
        return self.apply_policy(reason=f"failover:{reason}", capture_rollback=False)

    def rollback(self, *, reason: str = "manual_rollback") -> Dict[str, Any]:
        snap = self._rollback_snapshot
        if snap is None:
            self._append_history("rollback_skipped", {"reason": reason})
            return self.get_status()

        self._save_policy(snap.policy)
        self._set_router_order(list(snap.deployment_targets_order))
        registry = get_provider_registry()
        targets = self._list_targets()
        for target_id, target_cfg in targets.items():
            next_cfg = dict(target_cfg)
            next_cfg["traffic_weight"] = float(snap.weights.get(target_id, 1.0))
            try:
                registry.upsert_deployment_target(target_id, next_cfg)
            except Exception:
                logger.debug("Failed to rollback target %s", target_id, exc_info=True)

        self._append_history(
            "rollback",
            {
                "reason": reason,
                "restore_order": list(snap.deployment_targets_order),
            },
        )
        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        policy = self.get_policy()
        healthy_map = self._healthy_map_from_probes(self._last_probes)
        target_order = self.compute_target_order(policy, healthy_map=healthy_map if healthy_map else None)
        return {
            "policy": policy,
            "router_targets": self._get_router_order(),
            "computed_target_order": target_order,
            "last_probes": list(self._last_probes),
            "history": list(self._history[-30:]),
            "rollback_available": self._rollback_snapshot is not None,
        }


_deployment_orchestrator_instance: Optional[DeploymentOrchestrator] = None


def get_deployment_orchestrator() -> DeploymentOrchestrator:
    global _deployment_orchestrator_instance
    if _deployment_orchestrator_instance is None:
        _deployment_orchestrator_instance = DeploymentOrchestrator()
    return _deployment_orchestrator_instance
