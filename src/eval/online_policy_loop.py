"""Online policy learning loop built from bridge exports and release gate checks."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from eval.benchmark import EvalBenchmarkManager
from eval.offpolicy_estimator import OffPolicyEstimator
from eval.store import EvalStore
from eval.training_bridge import TrainingBridgeManager
from eval.trainer import TrainingJobManager


def _candidate_id() -> str:
    return f"opc_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class OnlinePolicyLoopManager(EvalStore):
    """Persist online policy candidates and enforce review+gate before publish."""

    VERSION = "online-policy-loop.v1"

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__(base_dir or (Path.home() / ".gazer" / "eval"))
        self._candidates = self._base / "online_policy_candidates"
        self._candidates.mkdir(parents=True, exist_ok=True)

    def _candidate_path(self, candidate_id: str) -> Path:
        safe = self._safe_id(candidate_id)
        return self._candidates / f"{safe}.json"

    def _write_candidate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["updated_at"] = time.time()
        self._write_json(self._candidate_path(str(payload.get("candidate_id", ""))), payload)
        return payload

    @staticmethod
    def _compact(payload: Dict[str, Any]) -> Dict[str, Any]:
        gate_check = payload.get("gate_check") if isinstance(payload.get("gate_check"), dict) else {}
        review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
        publish = payload.get("publish") if isinstance(payload.get("publish"), dict) else {}
        return {
            "candidate_id": payload.get("candidate_id"),
            "dataset_id": payload.get("dataset_id"),
            "export_id": payload.get("export_id"),
            "job_id": payload.get("job_id"),
            "status": payload.get("status"),
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "source": payload.get("source"),
            "summary": payload.get("summary", {}),
            "review": {
                "state": review.get("state", "pending"),
                "approved": bool(review.get("approved", False)),
                "reviewed_by": review.get("reviewed_by", ""),
                "reviewed_at": review.get("reviewed_at"),
            },
            "gate_check": {
                "passed": gate_check.get("passed"),
                "reasons": list(gate_check.get("reasons", []) or []),
                "checked_at": gate_check.get("checked_at"),
            },
            "publish": {
                "state": publish.get("state", "not_published"),
                "release_id": publish.get("release_id", ""),
                "published_by": publish.get("published_by", ""),
                "published_at": publish.get("published_at"),
            },
            "offpolicy_eval": {
                "state": str(
                    (payload.get("offpolicy_eval") or {}).get("state", "not_evaluated")
                ).strip()
                if isinstance(payload.get("offpolicy_eval"), dict)
                else "not_evaluated",
                "method": str(
                    (payload.get("offpolicy_eval") or {}).get("method", "")
                ).strip()
                if isinstance(payload.get("offpolicy_eval"), dict)
                else "",
                "expected_reward_delta": (
                    ((payload.get("offpolicy_eval") or {}).get("reward_estimate") or {}).get(
                        "expected_reward_delta"
                    )
                    if isinstance(payload.get("offpolicy_eval"), dict)
                    else None
                ),
                "sample_coverage": (
                    ((payload.get("offpolicy_eval") or {}).get("coverage") or {}).get("sample_coverage")
                    if isinstance(payload.get("offpolicy_eval"), dict)
                    else None
                ),
                "updated_at": (payload.get("offpolicy_eval") or {}).get("generated_at")
                if isinstance(payload.get("offpolicy_eval"), dict)
                else None,
            },
            "metadata": payload.get("metadata", {}),
        }

    def create_candidate(
        self,
        *,
        dataset_id: str,
        export_id: str,
        job_id: str,
        patch: Dict[str, Any],
        summary: Dict[str, Any],
        release_gate: Optional[Dict[str, Any]] = None,
        source: str = "training_bridge",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        payload = {
            "candidate_id": _candidate_id(),
            "dataset_id": str(dataset_id),
            "export_id": str(export_id),
            "job_id": str(job_id),
            "status": "pending_review",
            "source": str(source or "training_bridge"),
            "created_at": now,
            "updated_at": now,
            "summary": summary if isinstance(summary, dict) else {},
            "patch": patch if isinstance(patch, dict) else {},
            "release_gate_snapshot": release_gate if isinstance(release_gate, dict) else {},
            "review": {
                "state": "pending",
                "approved": False,
                "reviewed_by": "",
                "reviewed_at": None,
                "note": "",
            },
            "gate_check": {
                "passed": None,
                "reasons": [],
                "checked_at": None,
                "thresholds": {},
                "metrics": {},
            },
            "publish": {
                "state": "not_published",
                "release_id": "",
                "published_by": "",
                "published_at": None,
                "note": "",
                "dry_run": False,
            },
            "metadata": metadata or {},
            "offpolicy_eval": {
                "state": "not_evaluated",
                "method": "",
                "generated_at": None,
                "reward_estimate": {},
                "risk_interval": {},
                "coverage": {},
                "context": {},
            },
        }
        return self._write_candidate(payload)

    def create_candidate_from_bridge(
        self,
        *,
        bridge_manager: TrainingBridgeManager,
        training_manager: TrainingJobManager,
        eval_manager: Optional[EvalBenchmarkManager] = None,
        dataset_id: Optional[str] = None,
        export_id: Optional[str] = None,
        source: str = "online_policy_loop",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target_export_id = str(export_id or "").strip()
        if not target_export_id:
            dataset_key = str(dataset_id or "").strip()
            latest = bridge_manager.list_exports(limit=1, dataset_id=dataset_key or None)
            if not latest:
                raise ValueError("training bridge export not found")
            target_export_id = str(latest[0].get("export_id", "")).strip()
        if not target_export_id:
            raise ValueError("export_id is required")

        adapted = bridge_manager.to_training_inputs(target_export_id)
        if not isinstance(adapted, dict):
            raise ValueError("training bridge export not found")

        target_dataset = str(dataset_id or adapted.get("dataset_id", "")).strip() or "online_policy"
        job = training_manager.create_job(
            dataset_id=target_dataset,
            trajectory_samples=list(adapted.get("trajectory_samples", [])),
            eval_samples=list(adapted.get("eval_samples", [])),
            source=str(source or "online_policy_loop"),
            metadata={
                "export_id": target_export_id,
                "dataset_id": target_dataset,
                **(metadata or {}),
            },
        )
        completed = training_manager.run_job(str(job.get("job_id", "")))
        if not isinstance(completed, dict) or not isinstance(completed.get("output"), dict):
            raise ValueError("failed to build policy candidate from training job")

        export_payload = bridge_manager.get_export(target_export_id, include_samples=False) or {}
        gate_snapshot = eval_manager.get_release_gate_status() if eval_manager is not None else {}
        return self.create_candidate(
            dataset_id=target_dataset,
            export_id=target_export_id,
            job_id=str(completed.get("job_id", "")),
            patch=dict(completed.get("output", {})),
            summary=dict(export_payload.get("summary", {})) if isinstance(export_payload, dict) else {},
            release_gate=gate_snapshot if isinstance(gate_snapshot, dict) else {},
            source=str(source or "online_policy_loop"),
            metadata={
                "adapted_trajectory_count": len(adapted.get("trajectory_samples", [])),
                "adapted_eval_count": len(adapted.get("eval_samples", [])),
                **(metadata or {}),
            },
        )

    def list_candidates(
        self,
        *,
        limit: int = 50,
        dataset_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        dataset_key = str(dataset_id or "").strip()
        status_key = str(status or "").strip().lower()
        for path in self._candidates.glob("*.json"):
            payload = self._read_json(path)
            if not isinstance(payload, dict):
                continue
            if dataset_key and str(payload.get("dataset_id", "")) != dataset_key:
                continue
            if status_key and str(payload.get("status", "")).strip().lower() != status_key:
                continue
            items.append(self._compact(payload))
        items.sort(
            key=lambda item: float(item.get("created_at", 0.0) or 0.0),
            reverse=True,
        )
        return items[: max(1, int(limit))]

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        payload = self._read_json(self._candidate_path(candidate_id))
        return payload if isinstance(payload, dict) and payload else None

    def run_gate_check(
        self,
        *,
        candidate_id: str,
        gate_status: Optional[Dict[str, Any]] = None,
        require_release_gate_open: bool = True,
        min_eval_pass_rate: float = 0.55,
        min_trajectory_success_rate: float = 0.6,
        max_terminal_error_rate: float = 0.4,
    ) -> Dict[str, Any]:
        payload = self.get_candidate(candidate_id)
        if payload is None:
            raise ValueError("candidate not found")
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        offline = summary.get("offline_policy_eval") if isinstance(summary.get("offline_policy_eval"), dict) else {}

        eval_pass_rate = _safe_float(summary.get("eval_pass_rate"))
        trajectory_success_rate = _safe_float(offline.get("trajectory_success_rate"))
        terminal_error_rate = _safe_float(summary.get("terminal_error_rate"))
        gate = gate_status if isinstance(gate_status, dict) else {}
        release_gate_blocked = bool(gate.get("blocked", False))

        reasons: List[str] = []
        if require_release_gate_open and release_gate_blocked:
            reasons.append("release_gate_blocked")
        if eval_pass_rate is None or eval_pass_rate < float(min_eval_pass_rate):
            reasons.append("eval_pass_rate_below_threshold")
        if trajectory_success_rate is None or trajectory_success_rate < float(min_trajectory_success_rate):
            reasons.append("trajectory_success_rate_below_threshold")
        if terminal_error_rate is None or terminal_error_rate > float(max_terminal_error_rate):
            reasons.append("terminal_error_rate_above_threshold")

        check = {
            "passed": len(reasons) == 0,
            "reasons": reasons,
            "checked_at": time.time(),
            "thresholds": {
                "require_release_gate_open": bool(require_release_gate_open),
                "min_eval_pass_rate": float(min_eval_pass_rate),
                "min_trajectory_success_rate": float(min_trajectory_success_rate),
                "max_terminal_error_rate": float(max_terminal_error_rate),
            },
            "metrics": {
                "release_gate_blocked": release_gate_blocked,
                "eval_pass_rate": eval_pass_rate,
                "trajectory_success_rate": trajectory_success_rate,
                "terminal_error_rate": terminal_error_rate,
            },
        }
        payload["gate_check"] = check
        return self._write_candidate(payload)

    def review_candidate(
        self,
        *,
        candidate_id: str,
        approved: bool,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        payload = self.get_candidate(candidate_id)
        if payload is None:
            raise ValueError("candidate not found")
        review = {
            "state": "approved" if approved else "rejected",
            "approved": bool(approved),
            "reviewed_by": str(reviewer or "admin"),
            "reviewed_at": time.time(),
            "note": str(note or ""),
        }
        payload["review"] = review
        payload["status"] = "approved" if approved else "rejected"
        return self._write_candidate(payload)

    def mark_published(
        self,
        *,
        candidate_id: str,
        actor: str,
        note: str = "",
        release_id: str = "",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        payload = self.get_candidate(candidate_id)
        if payload is None:
            raise ValueError("candidate not found")
        review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
        gate_check = payload.get("gate_check") if isinstance(payload.get("gate_check"), dict) else {}
        if not bool(review.get("approved", False)):
            raise ValueError("candidate is not approved")
        if not bool(gate_check.get("passed", False)):
            raise ValueError("candidate gate check has not passed")
        payload["publish"] = {
            "state": "dry_run" if dry_run else "published",
            "release_id": str(release_id or ""),
            "published_by": str(actor or "admin"),
            "published_at": time.time(),
            "note": str(note or ""),
            "dry_run": bool(dry_run),
        }
        payload["status"] = "dry_run" if dry_run else "published"
        return self._write_candidate(payload)

    def run_offpolicy_eval(
        self,
        *,
        candidate_id: str,
        bridge_manager: TrainingBridgeManager,
        baseline_export_id: Optional[str] = None,
        baseline_index: int = 1,
        bootstrap_rounds: int = 300,
        min_reward_threshold: float = 0.6,
        min_samples_for_confidence: int = 20,
    ) -> Dict[str, Any]:
        payload = self.get_candidate(candidate_id)
        if payload is None:
            raise ValueError("candidate not found")

        export_id = str(payload.get("export_id", "")).strip()
        if not export_id:
            raise ValueError("candidate export_id is missing")
        candidate_export = bridge_manager.get_export(export_id, include_samples=True)
        if not isinstance(candidate_export, dict):
            raise ValueError("candidate export not found")

        dataset_id = str(payload.get("dataset_id", "")).strip()
        baseline_target = str(baseline_export_id or "").strip()
        if not baseline_target:
            idx = max(1, int(baseline_index))
            history = bridge_manager.list_exports(limit=max(2, idx + 1), dataset_id=dataset_id or None)
            history_ids = [str(item.get("export_id", "")).strip() for item in history if str(item.get("export_id", "")).strip()]
            if export_id in history_ids:
                current_idx = history_ids.index(export_id)
                target_idx = current_idx + idx
                if target_idx < len(history_ids):
                    baseline_target = history_ids[target_idx]
            elif len(history_ids) > idx:
                baseline_target = history_ids[idx]

        baseline_export = (
            bridge_manager.get_export(baseline_target, include_samples=True)
            if baseline_target
            else None
        )
        if not isinstance(baseline_export, dict):
            baseline_export = None
            baseline_target = ""

        compare = (
            bridge_manager.compare_exports(
                candidate_export_id=export_id,
                baseline_export_id=baseline_target,
            )
            if baseline_target
            else None
        )

        estimator = OffPolicyEstimator(
            bootstrap_rounds=bootstrap_rounds,
            min_reward_threshold=min_reward_threshold,
            min_samples_for_confidence=min_samples_for_confidence,
        )
        report = estimator.estimate_from_exports(
            candidate_export=candidate_export,
            baseline_export=baseline_export,
            compare=compare if isinstance(compare, dict) else None,
            context={
                "candidate_id": str(payload.get("candidate_id", "")),
                "dataset_id": dataset_id,
                "export_id": export_id,
                "baseline_index": int(max(1, int(baseline_index))),
            },
        )
        payload["offpolicy_eval"] = report
        return self._write_candidate(payload)
