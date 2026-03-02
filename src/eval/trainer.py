"""Lightning-lite trainer interface for prompt/policy patch generation."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from eval.store import EvalStore
from runtime.resilience import classify_error_message


def _job_id() -> str:
    return f"train_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _release_id() -> str:
    return f"rel_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _sample_store_id() -> str:
    return f"sample_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _experiment_id() -> str:
    return f"exp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _pipeline_id() -> str:
    return f"pipe_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _safe_dataset_id(value: str) -> str:
    return str(value or "").replace("/", "_").replace("\\", "_")


class LightningLiteTrainer:
    """Rule-based trainer that emits prompt/policy/router patches from samples."""

    @staticmethod
    def _iter_tool_failures(item: Dict[str, Any]) -> List[Dict[str, str]]:
        failures: List[Dict[str, str]] = []
        events = item.get("events")
        if not isinstance(events, list):
            return failures
        for event in events:
            if not isinstance(event, dict):
                continue
            action = str(event.get("action", "")).strip().lower()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if action != "tool_result":
                continue
            status = str(payload.get("status", "")).strip().lower()
            if status in {"ok", "success"}:
                continue
            failures.append(
                {
                    "tool": str(payload.get("tool", "")).strip().lower(),
                    "error_code": str(payload.get("error_code", "")).strip().lower(),
                    "preview": str(payload.get("result_preview", "")).strip(),
                }
            )
        return failures

    def generate_patch(
        self,
        *,
        trajectory_samples: List[Dict[str, Any]],
        eval_samples: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt_rules: List[str] = []
        deny_tools: List[str] = []
        error_buckets: Dict[str, int] = {}
        tool_failure_count: Dict[str, int] = {}
        tool_error_code_count: Dict[str, int] = {}

        for item in trajectory_samples:
            output = str(item.get("assistant_output", ""))
            reason = classify_error_message(output)
            error_buckets[reason] = error_buckets.get(reason, 0) + 1

            feedback = str(item.get("feedback", "")).lower()
            if "unsafe" in feedback or "danger" in feedback:
                prompt_rules.append("Refuse unsafe or destructive requests and suggest safer alternatives.")
                deny_tools.extend(["exec"])
            if "tool" in feedback and "wrong" in feedback:
                prompt_rules.append("Before tool invocation, verify goal-tool alignment in one short sentence.")

            for failure in self._iter_tool_failures(item):
                tool_name = failure["tool"]
                if tool_name:
                    tool_failure_count[tool_name] = tool_failure_count.get(tool_name, 0) + 1
                code = failure["error_code"]
                if code:
                    tool_error_code_count[code] = tool_error_code_count.get(code, 0) + 1

        pass_rate_failures = 0
        for item in eval_samples:
            if not bool(item.get("passed", True)):
                pass_rate_failures += 1

        if pass_rate_failures > 0:
            prompt_rules.append("When uncertain, ask one clarifying question before proceeding.")
        if error_buckets.get("retryable", 0) > 0:
            prompt_rules.append("Prefer lightweight fallback plan when provider/network instability is detected.")
        if error_buckets.get("non_retryable", 0) > 0:
            prompt_rules.append("Do not repeat non-retryable actions; return explicit recovery steps.")
        if tool_failure_count:
            prompt_rules.append("For repeated tool failures, switch to an alternative tool or return a deterministic fallback.")
        if tool_error_code_count.get("tool_not_permitted", 0) > 0:
            prompt_rules.append("When policy denies a tool, stop retrying and explain allowed alternatives immediately.")

        unique_rules = sorted({rule.strip() for rule in prompt_rules if rule.strip()})
        unique_deny = sorted({name.strip() for name in deny_tools if name.strip()})

        router_strategy = "cost"
        router_template = "cost_first"
        if pass_rate_failures > 0 and (tool_failure_count or error_buckets.get("retryable", 0) > 0):
            router_strategy = "priority"
            router_template = "availability_first"
        elif pass_rate_failures > 0 or error_buckets.get("retryable", 0) > 0:
            router_strategy = "latency"
            router_template = "latency_first"
        router_budget: Dict[str, Any] = {"enabled": True}
        if pass_rate_failures > 0:
            router_budget["max_error_rate"] = 0.2
        if tool_failure_count:
            router_budget["prefer_healthy_provider"] = True

        return {
            "prompt_patch": {
                "strategy": "append_rules",
                "rules": unique_rules,
            },
            "policy_patch": {
                "security.tool_denylist.add": unique_deny,
                "security.tool_max_tier.suggested": "safe" if unique_deny else "standard",
            },
            "router_patch": {
                "strategy": router_strategy,
                "strategy_template": router_template,
                "budget": router_budget,
            },
            "training_summary": {
                "trajectory_count": len(trajectory_samples),
                "eval_count": len(eval_samples),
                "fail_count": pass_rate_failures,
                "error_buckets": error_buckets,
                "tool_failure_count": tool_failure_count,
                "tool_error_code_count": tool_error_code_count,
            },
        }


class TrainingJobManager(EvalStore):
    """Persist and run training jobs."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__(base_dir or (Path.home() / ".gazer" / "eval"))
        self._jobs = self._base / "training_jobs"
        self._releases = self._base / "training_releases.jsonl"
        self._bootstrap_runs = self._base / "bootstrap_runs.jsonl"
        self._sample_store = self._base / "sample_store"
        self._experiments = self._base / "training_experiments.jsonl"
        self._jobs.mkdir(parents=True, exist_ok=True)
        self._sample_store.mkdir(parents=True, exist_ok=True)
        self._trainer = LightningLiteTrainer()

    def _job_path(self, job_id: str) -> Path:
        safe = _safe_dataset_id(job_id)
        return self._jobs / f"{safe}.json"

    def _sample_store_path(self, store_id: str) -> Path:
        safe = _safe_dataset_id(store_id)
        return self._sample_store / f"{safe}.json"

    def create_job(
        self,
        *,
        dataset_id: str,
        trajectory_samples: List[Dict[str, Any]],
        eval_samples: List[Dict[str, Any]],
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        job = {
            "job_id": _job_id(),
            "dataset_id": dataset_id,
            "source": source,
            "status": "pending",
            "created_at": time.time(),
            "updated_at": time.time(),
            "input": {
                "trajectory_samples": trajectory_samples,
                "eval_samples": eval_samples,
            },
            "output": None,
            "metadata": metadata or {},
        }
        self._write_json(self._job_path(job["job_id"]), job)
        return job

    def create_sample_store(
        self,
        *,
        dataset_id: str,
        trajectory_samples: List[Dict[str, Any]],
        eval_samples: List[Dict[str, Any]],
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "store_id": _sample_store_id(),
            "dataset_id": str(dataset_id),
            "source": str(source or "manual"),
            "created_at": time.time(),
            "updated_at": time.time(),
            "trajectory_count": len(trajectory_samples),
            "eval_count": len(eval_samples),
            "trajectory_samples": list(trajectory_samples),
            "eval_samples": list(eval_samples),
            "metadata": metadata or {},
        }
        self._write_json(self._sample_store_path(payload["store_id"]), payload)
        return payload

    def get_sample_store(self, store_id: str) -> Optional[Dict[str, Any]]:
        path = self._sample_store_path(store_id)
        if not path.is_file():
            return None
        data = self._read_json(path)
        return data if isinstance(data, dict) else None

    def list_sample_stores(self, *, limit: int = 50, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        paths = sorted(
            self._sample_store.glob("*.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        dataset_key = str(dataset_id or "").strip()
        for path in paths:
            payload = self._read_json(path)
            if not isinstance(payload, dict):
                continue
            if dataset_key and str(payload.get("dataset_id", "")) != dataset_key:
                continue
            items.append(
                {
                    "store_id": payload.get("store_id"),
                    "dataset_id": payload.get("dataset_id"),
                    "source": payload.get("source"),
                    "trajectory_count": payload.get("trajectory_count", 0),
                    "eval_count": payload.get("eval_count", 0),
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                }
            )
            if len(items) >= limit:
                break
        return items

    def create_experiment(
        self,
        *,
        dataset_id: str,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        sample_store_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        experiment = {
            "experiment_id": _experiment_id(),
            "dataset_id": str(dataset_id),
            "name": str(name or "training_experiment"),
            "params": params or {},
            "sample_store_id": str(sample_store_id or ""),
            "status": "created",
            "runs": [],
            "created_at": time.time(),
            "updated_at": time.time(),
            "metadata": metadata or {},
        }
        items = self._read_jsonl(self._experiments)
        items.append(experiment)
        self._write_jsonl(self._experiments, items)
        return experiment

    def list_experiments(
        self,
        *,
        limit: int = 50,
        dataset_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self._read_jsonl(self._experiments)
        dataset_key = str(dataset_id or "").strip()
        if dataset_key:
            items = [item for item in items if str(item.get("dataset_id", "")) == dataset_key]
        items.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return items[:limit]

    def get_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        target = str(experiment_id or "").strip()
        if not target:
            return None
        for item in self._read_jsonl(self._experiments):
            if str(item.get("experiment_id", "")) == target:
                return item
        return None

    def append_experiment_run(
        self,
        *,
        experiment_id: str,
        job_id: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        target = str(experiment_id or "").strip()
        if not target:
            return None
        items = self._read_jsonl(self._experiments)
        updated_item: Optional[Dict[str, Any]] = None
        now = time.time()
        for idx, item in enumerate(items):
            if str(item.get("experiment_id", "")) != target:
                continue
            runs = list(item.get("runs", [])) if isinstance(item.get("runs", []), list) else []
            runs.append(
                {
                    "job_id": str(job_id),
                    "metrics": metrics or {},
                    "created_at": now,
                }
            )
            updated_item = dict(item)
            updated_item["runs"] = runs
            updated_item["status"] = "running" if not metrics else "completed"
            updated_item["updated_at"] = now
            items[idx] = updated_item
            break
        if updated_item is None:
            return None
        self._write_jsonl(self._experiments, items)
        return updated_item

    def compare_experiment_runs(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        experiment = self.get_experiment(experiment_id)
        if experiment is None:
            return None
        runs = list(experiment.get("runs", [])) if isinstance(experiment.get("runs", []), list) else []
        if not runs:
            return {
                "experiment_id": experiment_id,
                "run_count": 0,
                "best_job_id": "",
                "baseline_job_id": "",
                "delta": {},
            }
        baseline = runs[0]
        best = runs[0]
        for run in runs[1:]:
            run_metrics = run.get("metrics", {}) if isinstance(run.get("metrics"), dict) else {}
            best_metrics = best.get("metrics", {}) if isinstance(best.get("metrics"), dict) else {}
            run_score = float(run_metrics.get("score", 0.0) or 0.0)
            best_score = float(best_metrics.get("score", 0.0) or 0.0)
            if run_score > best_score:
                best = run
        base_metrics = baseline.get("metrics", {}) if isinstance(baseline.get("metrics"), dict) else {}
        best_metrics = best.get("metrics", {}) if isinstance(best.get("metrics"), dict) else {}
        return {
            "experiment_id": experiment_id,
            "run_count": len(runs),
            "best_job_id": best.get("job_id", ""),
            "baseline_job_id": baseline.get("job_id", ""),
            "delta": {
                "score": round(float(best_metrics.get("score", 0.0) or 0.0) - float(base_metrics.get("score", 0.0) or 0.0), 4),
                "fail_count": int(best_metrics.get("fail_count", 0) or 0) - int(base_metrics.get("fail_count", 0) or 0),
                "rule_count": int(best_metrics.get("rule_count", 0) or 0) - int(base_metrics.get("rule_count", 0) or 0),
            },
        }

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        path = self._job_path(job_id)
        if not path.is_file():
            return None
        payload = self._read_json(path)
        return payload if isinstance(payload, dict) else None

    def list_jobs(self, *, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        paths = sorted(
            self._jobs.glob("*.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        status_key = str(status or "").strip().lower()
        for path in paths:
            payload = self._read_json(path)
            if not isinstance(payload, dict):
                continue
            if status_key and str(payload.get("status", "")).strip().lower() != status_key:
                continue
            items.append(
                {
                    "job_id": payload.get("job_id"),
                    "dataset_id": payload.get("dataset_id"),
                    "source": payload.get("source"),
                    "status": payload.get("status"),
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                }
            )
            if len(items) >= limit:
                break
        return items

    def run_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.get_job(job_id)
        if not job:
            return None
        trajectory_samples = list((job.get("input") or {}).get("trajectory_samples", []))
        eval_samples = list((job.get("input") or {}).get("eval_samples", []))

        job["status"] = "running"
        job["updated_at"] = time.time()
        self._write_json(self._job_path(job_id), job)

        patch = self._trainer.generate_patch(
            trajectory_samples=trajectory_samples,
            eval_samples=eval_samples,
        )

        job["status"] = "completed"
        job["updated_at"] = time.time()
        job["output"] = patch
        self._write_json(self._job_path(job_id), job)
        return job

    def create_release(
        self,
        *,
        job_id: str,
        actor: str,
        note: str,
        before: Dict[str, Any],
        after: Dict[str, Any],
        dry_run: bool = False,
        rollout: Optional[Dict[str, Any]] = None,
        rollback_rule: Optional[Dict[str, Any]] = None,
        strategy_package: Optional[Dict[str, Any]] = None,
        status_override: Optional[str] = None,
        approval: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        rollout_cfg = rollout if isinstance(rollout, dict) else {}
        rollout_mode = str(rollout_cfg.get("mode", "direct")).strip().lower() or "direct"
        release_status = "dry_run" if dry_run else ("canary" if rollout_mode == "canary" else "published")
        status_key = str(status_override or "").strip().lower()
        if status_key in {"pending_approval", "canary", "published", "dry_run"}:
            release_status = status_key
        approval_payload = approval if isinstance(approval, dict) else {}
        default_approval = {
            "required": False,
            "state": "not_required",
            "approved": False,
            "approved_by": "",
            "approved_at": None,
            "note": "",
        }
        merged_approval = {**default_approval, **approval_payload}
        release = {
            "release_id": _release_id(),
            "job_id": job_id,
            "status": release_status,
            "actor": str(actor or "admin"),
            "note": str(note or ""),
            "created_at": time.time(),
            "updated_at": time.time(),
            "before": before,
            "after": after,
            "rollout": {
                "mode": rollout_mode,
                "percent": max(1, min(100, int(rollout_cfg.get("percent", 100) or 100))),
            },
            "rollback_rule": rollback_rule if isinstance(rollback_rule, dict) else {},
            "strategy_package": strategy_package if isinstance(strategy_package, dict) else {},
            "approval": merged_approval,
            "rolled_back_at": None,
            "rollback_actor": "",
            "rollback_note": "",
        }
        self._append_jsonl(self._releases, release)
        return release

    def mark_release_approved(
        self,
        *,
        release_id: str,
        actor: str,
        note: str = "",
        status: str = "published",
    ) -> Optional[Dict[str, Any]]:
        target = str(release_id or "").strip()
        if not target:
            return None
        target_status = str(status or "published").strip().lower() or "published"
        if target_status not in {"published", "canary"}:
            target_status = "published"
        items = self._read_jsonl(self._releases)
        found: Optional[Dict[str, Any]] = None
        now = time.time()
        for idx, item in enumerate(items):
            if str(item.get("release_id", "")) != target:
                continue
            updated = dict(item)
            approval = updated.get("approval") if isinstance(updated.get("approval"), dict) else {}
            approval_update = {
                "required": bool(approval.get("required", False)),
                "state": "approved",
                "approved": True,
                "approved_by": str(actor or "admin"),
                "approved_at": now,
                "note": str(note or ""),
            }
            updated["approval"] = {**approval, **approval_update}
            updated["status"] = target_status
            updated["updated_at"] = now
            updated["approved_at"] = now
            updated["approved_actor"] = str(actor or "admin")
            if note:
                updated["approved_note"] = str(note)
            items[idx] = updated
            found = updated
            break
        if found is None:
            return None
        self._write_jsonl(self._releases, items)
        return found

    def list_releases(self, *, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        items = self._read_jsonl(self._releases)
        if status:
            key = str(status).strip().lower()
            items = [item for item in items if str(item.get("status", "")).strip().lower() == key]
        items.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return items[:limit]

    def get_release(self, release_id: str) -> Optional[Dict[str, Any]]:
        target = str(release_id or "").strip()
        if not target:
            return None
        for item in self._read_jsonl(self._releases):
            if str(item.get("release_id", "")) == target:
                return item
        return None

    def mark_release_rolled_back(
        self,
        *,
        release_id: str,
        actor: str,
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        target = str(release_id or "").strip()
        if not target:
            return None
        items = self._read_jsonl(self._releases)
        found: Optional[Dict[str, Any]] = None
        now = time.time()
        for idx, item in enumerate(items):
            if str(item.get("release_id", "")) != target:
                continue
            updated = dict(item)
            updated["status"] = "rolled_back"
            updated["updated_at"] = now
            updated["rolled_back_at"] = now
            updated["rollback_actor"] = str(actor or "admin")
            updated["rollback_note"] = str(note or "")
            items[idx] = updated
            found = updated
            break
        if found is None:
            return None
        self._write_jsonl(self._releases, items)
        return found

    def mark_release_promoted(self, *, release_id: str, actor: str, note: str = "") -> Optional[Dict[str, Any]]:
        target = str(release_id or "").strip()
        if not target:
            return None
        items = self._read_jsonl(self._releases)
        found: Optional[Dict[str, Any]] = None
        now = time.time()
        for idx, item in enumerate(items):
            if str(item.get("release_id", "")) != target:
                continue
            updated = dict(item)
            updated["status"] = "published"
            updated["updated_at"] = now
            updated["promoted_at"] = now
            updated["promoted_actor"] = str(actor or "admin")
            if note:
                updated["promoted_note"] = str(note)
            items[idx] = updated
            found = updated
            break
        if found is None:
            return None
        self._write_jsonl(self._releases, items)
        return found

    @staticmethod
    def _normalize_change_set(change_set: Dict[str, Any]) -> Dict[str, Any]:
        payload = change_set if isinstance(change_set, dict) else {}
        files_raw = payload.get("files", [])
        files = [str(item).strip() for item in files_raw if str(item).strip()] if isinstance(files_raw, list) else []
        return {
            "change_id": str(payload.get("change_id") or _pipeline_id()),
            "title": str(payload.get("title", "")).strip(),
            "summary": str(payload.get("summary", "")).strip(),
            "files": files,
            "source_run_id": str(payload.get("source_run_id", "")).strip(),
            "diff_stats": payload.get("diff_stats", {}) if isinstance(payload.get("diff_stats", {}), dict) else {},
            "metadata": payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {},
        }

    @staticmethod
    def _eval_metrics(eval_samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(eval_samples)
        fail_count = 0
        for item in eval_samples:
            if not bool(item.get("passed", True)):
                fail_count += 1
        pass_count = max(0, total - fail_count)
        pass_rate = (pass_count / total) if total > 0 else 1.0
        return {
            "total": total,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pass_rate": round(pass_rate, 4),
        }

    def run_bootstrap_pipeline(
        self,
        *,
        dataset_id: str,
        change_set: Dict[str, Any],
        trajectory_samples: List[Dict[str, Any]],
        eval_samples: List[Dict[str, Any]],
        actor: str = "admin",
        note: str = "",
        dry_run: bool = False,
        rollout: Optional[Dict[str, Any]] = None,
        gate: Optional[Dict[str, Any]] = None,
        canary_health: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run self-bootstrapping pipeline.

        Flow:
        change_set -> run training job -> evaluate gate -> publish/canary release -> optional rollback.
        """
        started = time.time()
        normalized_change = self._normalize_change_set(change_set)
        if not normalized_change["title"]:
            raise ValueError("change_set.title is required")

        rollout_cfg = rollout if isinstance(rollout, dict) else {}
        gate_cfg = gate if isinstance(gate, dict) else {}
        min_pass_rate = float(gate_cfg.get("min_pass_rate", 0.6) or 0.6)
        max_fail_count = int(gate_cfg.get("max_fail_count", max(0, len(eval_samples) // 2)) or 0)
        auto_rollback_on_canary_fail = bool(gate_cfg.get("auto_rollback_on_canary_fail", True))
        actor_name = str(actor or "admin")

        pipeline = {
            "pipeline_id": _pipeline_id(),
            "dataset_id": str(dataset_id),
            "actor": actor_name,
            "note": str(note or ""),
            "status": "running",
            "created_at": started,
            "updated_at": started,
            "change_set": normalized_change,
            "job_id": "",
            "gate": {},
            "release_id": "",
            "release_status": "",
            "rollback_release_id": "",
            "duration_ms": 0,
        }

        job = self.create_job(
            dataset_id=str(dataset_id),
            trajectory_samples=list(trajectory_samples),
            eval_samples=list(eval_samples),
            source="bootstrap_pipeline",
            metadata={"change_set": normalized_change},
        )
        pipeline["job_id"] = str(job.get("job_id", ""))
        run_result = self.run_job(pipeline["job_id"])
        patch = (run_result or {}).get("output") if isinstance(run_result, dict) else None
        metrics = self._eval_metrics(eval_samples)
        gate_passed = (
            float(metrics.get("pass_rate", 0.0)) >= min_pass_rate
            and int(metrics.get("fail_count", 0)) <= max_fail_count
        )
        gate_payload = {
            "passed": bool(gate_passed),
            "min_pass_rate": min_pass_rate,
            "max_fail_count": max_fail_count,
            "metrics": metrics,
        }
        pipeline["gate"] = gate_payload
        if not gate_passed:
            pipeline["status"] = "gate_blocked"
            pipeline["updated_at"] = time.time()
            pipeline["duration_ms"] = int((pipeline["updated_at"] - started) * 1000)
            self._append_jsonl(self._bootstrap_runs, pipeline)
            return pipeline

        release = self.create_release(
            job_id=pipeline["job_id"],
            actor=actor_name,
            note=note or normalized_change.get("summary", ""),
            before={"change_set": normalized_change, "job_id": pipeline["job_id"]},
            after={"patch": patch or {}, "job_id": pipeline["job_id"]},
            dry_run=bool(dry_run),
            rollout=rollout_cfg,
            rollback_rule={
                "source": "bootstrap_pipeline",
                "change_id": normalized_change.get("change_id", ""),
                "auto_rollback_on_canary_fail": auto_rollback_on_canary_fail,
            },
            strategy_package={
                "version": "training_strategy_package_v1",
                "components": {
                    "prompt": dict((patch or {}).get("prompt_patch", {})),
                    "policy": dict((patch or {}).get("policy_patch", {})),
                    "router": dict((patch or {}).get("router_patch", {})),
                },
                "rollback_snapshot": {"change_set": normalized_change, "job_id": pipeline["job_id"]},
                "apply_snapshot": {"patch": patch or {}, "job_id": pipeline["job_id"]},
            },
        )
        release_id = str(release.get("release_id", ""))
        release_status = str(release.get("status", ""))
        pipeline["release_id"] = release_id
        pipeline["release_status"] = release_status

        if release_status == "canary":
            health = canary_health if isinstance(canary_health, dict) else {"passed": True, "reason": "not_provided"}
            passed = bool(health.get("passed", False))
            pipeline["canary_health"] = dict(health)
            if (not passed) and auto_rollback_on_canary_fail and not dry_run:
                rolled = self.mark_release_rolled_back(
                    release_id=release_id,
                    actor=actor_name,
                    note=f"auto_rollback: {health.get('reason', 'canary_failed')}",
                )
                if rolled:
                    pipeline["rollback_release_id"] = release_id
                    pipeline["status"] = "rolled_back"
                else:
                    pipeline["status"] = "canary_failed"
            else:
                pipeline["status"] = "canary"
        else:
            pipeline["status"] = "dry_run" if bool(dry_run) else "published"

        pipeline["updated_at"] = time.time()
        pipeline["duration_ms"] = int((pipeline["updated_at"] - started) * 1000)
        self._append_jsonl(self._bootstrap_runs, pipeline)
        return pipeline

    def list_bootstrap_runs(
        self,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self._read_jsonl(self._bootstrap_runs)
        status_key = str(status or "").strip().lower()
        if status_key:
            items = [item for item in items if str(item.get("status", "")).strip().lower() == status_key]
        items.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return items[:limit]

    def get_bootstrap_run(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        target = str(pipeline_id or "").strip()
        if not target:
            return None
        for item in self._read_jsonl(self._bootstrap_runs):
            if str(item.get("pipeline_id", "")).strip() == target:
                return item
        return None
