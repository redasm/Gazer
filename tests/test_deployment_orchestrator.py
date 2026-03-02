from typing import Any, Dict

import runtime.deployment_orchestrator as orchestrator_module


class _FakeConfig:
    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data

    def get(self, key_path: str, default=None):
        cur = self.data
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set_many(self, updates: Dict[str, Any]) -> None:
        for key_path, value in updates.items():
            cur = self.data
            parts = key_path.split(".")
            for part in parts[:-1]:
                if part not in cur or not isinstance(cur[part], dict):
                    cur[part] = {}
                cur = cur[part]
            cur[parts[-1]] = value


class _FakeRegistry:
    def __init__(self, targets: Dict[str, Dict[str, Any]]) -> None:
        self.targets = {k: dict(v) for k, v in targets.items()}

    def list_deployment_targets(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in self.targets.items()}

    def upsert_deployment_target(self, target_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        self.targets[target_id] = dict(cfg)
        return dict(cfg)


def _build_orchestrator(monkeypatch):
    cfg = _FakeConfig(
        {
            "models": {
                "router": {
                    "enabled": True,
                    "strategy": "priority",
                    "deployment_targets": ["t1", "t2", "t3"],
                    "deployment_orchestrator": {
                        "enabled": True,
                        "mode": "manual",
                        "target_ids": ["t1", "t2", "t3"],
                        "active_target": "t2",
                        "standby_targets": ["t1", "t3"],
                        "weights": {"t1": 0.9, "t2": 1.3, "t3": 0.5},
                        "canary": {"enabled": True, "target": "t3", "weight": 0.6},
                        "auto_failover": {"enabled": True, "cooldown_seconds": 30, "auto_rollback": True},
                    },
                }
            }
        }
    )
    registry = _FakeRegistry(
        {
            "t1": {"provider": "openai", "enabled": True},
            "t2": {"provider": "openai", "enabled": True},
            "t3": {"provider": "openai", "enabled": True},
        }
    )
    monkeypatch.setattr(orchestrator_module, "config", cfg)
    monkeypatch.setattr(orchestrator_module, "get_provider_registry", lambda: registry)
    return orchestrator_module.DeploymentOrchestrator(), cfg, registry


def test_apply_policy_updates_router_order_and_target_weights(monkeypatch):
    orchestrator, cfg, registry = _build_orchestrator(monkeypatch)
    status = orchestrator.apply_policy(reason="test_apply")

    assert cfg.get("models.router.deployment_targets") == ["t3", "t2", "t1"]
    assert float(registry.targets["t2"]["traffic_weight"]) >= 1.3
    assert float(registry.targets["t1"]["traffic_weight"]) >= 0.9
    assert status["computed_target_order"] == ["t3", "t2", "t1"]


def test_reconcile_auto_failover_switches_to_healthy_target(monkeypatch):
    orchestrator, cfg, _registry = _build_orchestrator(monkeypatch)
    cfg.set_many({"models.router.deployment_orchestrator.mode": "auto"})
    cfg.set_many({"models.router.deployment_orchestrator.canary.enabled": False})
    orchestrator.apply_policy(reason="before_reconcile")

    status = orchestrator.reconcile(
        [
            {"name": "t2", "healthy": False},
            {"name": "t1", "healthy": True},
            {"name": "t3", "healthy": True},
        ]
    )
    assert status["policy"]["active_target"] == "t1"
    assert status["router_targets"][0] == "t1"


def test_failover_then_rollback_restores_previous_active_target(monkeypatch):
    orchestrator, _cfg, _registry = _build_orchestrator(monkeypatch)
    orchestrator.apply_policy(reason="baseline")
    failed_over = orchestrator.failover(target_id="t1", reason="manual")
    assert failed_over["policy"]["active_target"] == "t1"

    rolled_back = orchestrator.rollback(reason="manual")
    assert rolled_back["policy"]["active_target"] == "t2"
    assert rolled_back["router_targets"][0] == "t3"
