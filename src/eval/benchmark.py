"""Feedback-linked benchmark dataset builder and scorer."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from eval.store import EvalStore

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
_ERROR_MARKERS = (
    "error:",
    "sorry, i couldn't",
    "timed out",
    "failed",
    "not permitted",
)


def _tokenize(text: str) -> set[str]:
    return set(token.lower() for token in _TOKEN_RE.findall(str(text or "")))


def _jaccard(a: str, b: str) -> float:
    sa = _tokenize(a)
    sb = _tokenize(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _looks_like_error(text: str) -> bool:
    content = str(text or "").strip().lower()
    if not content:
        return True
    return any(marker in content for marker in _ERROR_MARKERS)


class EvalBenchmarkManager(EvalStore):
    """Persist benchmark datasets and run simple regression scoring."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__(base_dir or (Path.home() / ".gazer" / "eval"))

    def _release_gate_path(self) -> Path:
        return self._base / "release_gate.json"

    def _gate_streak_path(self) -> Path:
        return self._base / "gate_streaks.json"

    def _optimization_tasks_path(self) -> Path:
        return self._base / "optimization_tasks.jsonl"

    def build_dataset(
        self,
        *,
        name: str,
        samples: List[Dict[str, Any]],
        source: str = "trajectory_feedback",
    ) -> Dict[str, Any]:
        ts = int(time.time())
        dataset_id = f"{name.strip().lower().replace(' ', '_')}_{ts}"
        normalized_samples: List[Dict[str, Any]] = []
        for item in samples:
            normalized_samples.append(
                {
                    "run_id": str(item.get("run_id", "")),
                    "label": str(item.get("label", "")).strip().lower(),
                    "user_input": str(item.get("user_content", "")),
                    "reference_output": str(item.get("assistant_output", "")),
                    "feedback": str(item.get("feedback", "")),
                    "context": str(item.get("context", "")),
                    "status": str(item.get("status", "")),
                }
            )

        payload = {
            "id": dataset_id,
            "name": name,
            "source": source,
            "created_at": time.time(),
            "sample_count": len(normalized_samples),
            "samples": normalized_samples,
        }
        self._write_json(self._dataset_path(dataset_id), payload)
        return payload

    def list_datasets(self, limit: int = 50) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for payload in self._list_dataset_payloads(limit=limit):
            items.append(
                {
                    "id": payload.get("id"),
                    "name": payload.get("name"),
                    "source": payload.get("source"),
                    "created_at": payload.get("created_at"),
                    "sample_count": payload.get("sample_count", 0),
                }
            )
        return items

    def run_dataset(
        self,
        dataset_id: str,
        *,
        outputs: Optional[Dict[str, str]] = None,
        gate: Optional[Dict[str, float]] = None,
    ) -> Optional[Dict[str, Any]]:
        dataset = self.get_dataset(dataset_id)
        if not dataset:
            return None

        output_map = outputs or {}
        results: List[Dict[str, Any]] = []
        positive_scores: List[float] = []
        negative_scores: List[float] = []
        error_count = 0
        pass_count = 0

        for sample in dataset.get("samples", []):
            run_id = str(sample.get("run_id", ""))
            label = str(sample.get("label", "")).strip().lower()
            ref_output = str(sample.get("reference_output", ""))
            candidate_output = str(output_map.get(run_id, ref_output))
            similarity = _jaccard(candidate_output, ref_output)
            has_error = _looks_like_error(candidate_output)

            if label == "positive":
                pass_flag = similarity >= 0.5 and not has_error
                positive_scores.append(similarity)
            elif label == "negative":
                pass_flag = similarity <= 0.5 and not has_error
                negative_scores.append(similarity)
            else:
                pass_flag = not has_error

            if has_error:
                error_count += 1
            if pass_flag:
                pass_count += 1

            results.append(
                {
                    "run_id": run_id,
                    "label": label,
                    "similarity": round(similarity, 4),
                    "has_error": has_error,
                    "passed": pass_flag,
                }
            )

        total = len(results)
        pass_rate = (pass_count / total) if total else 0.0
        error_rate = (error_count / total) if total else 0.0
        pos_sim = (sum(positive_scores) / len(positive_scores)) if positive_scores else 0.0
        neg_sim = (sum(negative_scores) / len(negative_scores)) if negative_scores else 0.0
        composite_score = max(0.0, min(1.0, 0.5 * pos_sim + 0.3 * (1.0 - neg_sim) + 0.2 * (1.0 - error_rate)))
        gate_payload = gate or {}
        min_composite = float(gate_payload.get("min_composite_score", 0.0))
        min_pass_rate = float(gate_payload.get("min_pass_rate", 0.0))
        max_error_rate = float(gate_payload.get("max_error_rate", 1.0))
        gate_passed = (
            composite_score >= min_composite
            and pass_rate >= min_pass_rate
            and error_rate <= max_error_rate
        )
        gate_fail_reasons: List[str] = []
        if composite_score < min_composite:
            gate_fail_reasons.append("composite_score_below_threshold")
        if pass_rate < min_pass_rate:
            gate_fail_reasons.append("pass_rate_below_threshold")
        if error_rate > max_error_rate:
            gate_fail_reasons.append("error_rate_above_threshold")

        report = {
            "dataset_id": dataset_id,
            "created_at": time.time(),
            "sample_count": total,
            "pass_count": pass_count,
            "pass_rate": round(pass_rate, 4),
            "error_rate": round(error_rate, 4),
            "positive_similarity": round(pos_sim, 4),
            "negative_similarity": round(neg_sim, 4),
            "composite_score": round(composite_score, 4),
            "quality_gate": {
                "min_composite_score": round(min_composite, 4),
                "min_pass_rate": round(min_pass_rate, 4),
                "max_error_rate": round(max_error_rate, 4),
                "passed": gate_passed,
                "blocked": not gate_passed,
                "reasons": gate_fail_reasons,
            },
            "results": results,
        }

        self._append_jsonl(self._run_path(dataset_id), report)

        return report

    def evaluate_gate(
        self,
        dataset_id: str,
        *,
        gate: Dict[str, float],
        run_index: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate a quality gate against historical run at index."""
        runs = self._read_jsonl(self._run_path(dataset_id))
        if not runs:
            return None
        runs.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        if run_index < 0 or run_index >= len(runs):
            return None
        base = runs[run_index]
        score = float(base.get("composite_score", 0.0))
        pass_rate = float(base.get("pass_rate", 0.0))
        error_rate = float(base.get("error_rate", 0.0))
        min_composite = float(gate.get("min_composite_score", 0.0))
        min_pass = float(gate.get("min_pass_rate", 0.0))
        max_error = float(gate.get("max_error_rate", 1.0))
        passed = score >= min_composite and pass_rate >= min_pass and error_rate <= max_error
        reasons: List[str] = []
        if score < min_composite:
            reasons.append("composite_score_below_threshold")
        if pass_rate < min_pass:
            reasons.append("pass_rate_below_threshold")
        if error_rate > max_error:
            reasons.append("error_rate_above_threshold")
        return {
            "dataset_id": dataset_id,
            "run_index": run_index,
            "run_created_at": base.get("created_at"),
            "metrics": {
                "composite_score": round(score, 4),
                "pass_rate": round(pass_rate, 4),
                "error_rate": round(error_rate, 4),
            },
            "gate": {
                "min_composite_score": round(min_composite, 4),
                "min_pass_rate": round(min_pass, 4),
                "max_error_rate": round(max_error, 4),
                "passed": passed,
                "blocked": not passed,
                "reasons": reasons,
            },
        }

    def list_runs(self, dataset_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """List persisted run reports for a dataset."""
        entries = self._read_jsonl(self._run_path(dataset_id))
        entries.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        out: List[Dict[str, Any]] = []
        for item in entries[:limit]:
            out.append(
                {
                    "dataset_id": item.get("dataset_id"),
                    "created_at": item.get("created_at"),
                    "sample_count": item.get("sample_count", 0),
                    "pass_rate": item.get("pass_rate", 0.0),
                    "error_rate": item.get("error_rate", 0.0),
                    "composite_score": item.get("composite_score", 0.0),
                    "gate_passed": bool((item.get("quality_gate") or {}).get("passed", True)),
                }
            )
        return out

    def get_latest_run(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        runs = self._read_jsonl(self._run_path(dataset_id))
        if not runs:
            return None
        runs.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return runs[0]

    def compare_with_baseline(
        self,
        dataset_id: str,
        *,
        baseline_index: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Compare latest run with a baseline run by index in history."""
        runs = self._read_jsonl(self._run_path(dataset_id))
        if len(runs) < baseline_index + 1:
            return None
        runs.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        current = runs[0]
        baseline = runs[baseline_index]

        cur_score = float(current.get("composite_score", 0.0))
        base_score = float(baseline.get("composite_score", 0.0))
        cur_pass = float(current.get("pass_rate", 0.0))
        base_pass = float(baseline.get("pass_rate", 0.0))
        cur_err = float(current.get("error_rate", 0.0))
        base_err = float(baseline.get("error_rate", 0.0))

        return {
            "dataset_id": dataset_id,
            "current": {
                "created_at": current.get("created_at"),
                "composite_score": round(cur_score, 4),
                "pass_rate": round(cur_pass, 4),
                "error_rate": round(cur_err, 4),
            },
            "baseline": {
                "created_at": baseline.get("created_at"),
                "composite_score": round(base_score, 4),
                "pass_rate": round(base_pass, 4),
                "error_rate": round(base_err, 4),
            },
            "delta": {
                "composite_score": round(cur_score - base_score, 4),
                "pass_rate": round(cur_pass - base_pass, 4),
                "error_rate": round(cur_err - base_err, 4),
            },
        }

    def get_release_gate_status(self) -> Dict[str, Any]:
        """Return persisted release gate status."""
        path = self._release_gate_path()
        if not path.is_file():
            return {
                "blocked": False,
                "reason": "",
                "source": "",
                "updated_at": 0.0,
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid release gate payload")
            return {
                "blocked": bool(payload.get("blocked", False)),
                "reason": str(payload.get("reason", "")),
                "source": str(payload.get("source", "")),
                "updated_at": float(payload.get("updated_at", 0.0)),
                "metadata": payload.get("metadata", {}),
            }
        except Exception:
            return {
                "blocked": False,
                "reason": "",
                "source": "",
                "updated_at": 0.0,
            }

    def set_release_gate_status(
        self,
        *,
        blocked: bool,
        reason: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist release gate status."""
        payload = {
            "blocked": bool(blocked),
            "reason": str(reason or ""),
            "source": str(source or ""),
            "updated_at": time.time(),
            "metadata": metadata or {},
        }
        self._write_json(self._release_gate_path(), payload)
        return payload

    def register_gate_result(
        self,
        dataset_id: str,
        report: Dict[str, Any],
        *,
        fail_streak_threshold: int = 2,
    ) -> Dict[str, Any]:
        """Track consecutive gate failures and create optimization tasks when needed."""
        threshold = max(1, int(fail_streak_threshold))
        quality_gate = report.get("quality_gate", {}) if isinstance(report, dict) else {}
        blocked = bool(quality_gate.get("blocked", False))

        streaks = self._read_json(self._gate_streak_path(), fallback={"datasets": {}})
        datasets = streaks.setdefault("datasets", {})
        ds_state = datasets.setdefault(dataset_id, {"fail_streak": 0, "last_updated": 0.0})
        current_streak = int(ds_state.get("fail_streak", 0))
        if blocked:
            current_streak += 1
        else:
            current_streak = 0
        ds_state["fail_streak"] = current_streak
        ds_state["last_updated"] = time.time()
        self._gate_streak_path().write_text(
            json.dumps(streaks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task: Optional[Dict[str, Any]] = None
        if blocked and current_streak >= threshold:
            task = {
                "task_id": f"opt_{dataset_id}_{int(time.time())}",
                "dataset_id": dataset_id,
                "created_at": time.time(),
                "status": "open",
                "priority": "high" if current_streak >= threshold + 1 else "medium",
                "fail_streak": current_streak,
                "gate_reasons": list(quality_gate.get("reasons", []) or []),
                "metrics": {
                    "composite_score": report.get("composite_score"),
                    "pass_rate": report.get("pass_rate"),
                    "error_rate": report.get("error_rate"),
                },
                "recommendations": [
                    "Review failed trajectories and cluster root causes.",
                    "Add targeted prompts/tool constraints for top failure cluster.",
                    "Re-run benchmark and verify gate recovery before release.",
                ],
            }
            with open(self._optimization_tasks_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(task, ensure_ascii=False) + "\n")

        return {
            "dataset_id": dataset_id,
            "blocked": blocked,
            "fail_streak": current_streak,
            "threshold": threshold,
            "task_created": task is not None,
            "task": task,
        }

    def get_gate_streaks(
        self,
        *,
        limit: int = 20,
        dataset_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        payload = self._read_json(self._gate_streak_path(), fallback={"datasets": {}})
        datasets = payload.get("datasets", {}) if isinstance(payload, dict) else {}
        items: List[Dict[str, Any]] = []
        for did, state in datasets.items():
            if dataset_id and str(did) != str(dataset_id):
                continue
            state_dict = state if isinstance(state, dict) else {}
            items.append(
                {
                    "dataset_id": str(did),
                    "fail_streak": int(state_dict.get("fail_streak", 0)),
                    "last_updated": float(state_dict.get("last_updated", 0.0)),
                }
            )
        items.sort(key=lambda item: (int(item.get("fail_streak", 0)), float(item.get("last_updated", 0.0))), reverse=True)
        safe_limit = max(1, int(limit))
        return items[:safe_limit]

    def list_optimization_tasks(
        self,
        *,
        limit: int = 50,
        status: Optional[str] = None,
        dataset_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self._read_jsonl(self._optimization_tasks_path())
        if status:
            key = str(status).strip().lower()
            items = [item for item in items if str(item.get("status", "")).strip().lower() == key]
        if dataset_id:
            did = str(dataset_id).strip()
            items = [item for item in items if str(item.get("dataset_id", "")) == did]
        items.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return items[:limit]

    def set_optimization_task_status(
        self,
        *,
        task_id: str,
        status: str,
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        items = self._read_jsonl(self._optimization_tasks_path())
        if not items:
            return None
        target_id = str(task_id).strip()
        target: Optional[Dict[str, Any]] = None
        for item in items:
            if str(item.get("task_id", "")) == target_id:
                item["status"] = str(status).strip().lower() or "open"
                item["updated_at"] = time.time()
                if note:
                    item["note"] = str(note)
                target = item
                break
        if target is None:
            return None
        with open(self._optimization_tasks_path(), "w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        return target
