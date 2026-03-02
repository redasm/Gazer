"""Offline training bridge: trajectory export and trainer input adaptation."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from eval.store import EvalStore


def _export_id() -> str:
    return f"bridge_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _safe_dataset_id(value: str) -> str:
    return str(value or "").replace("/", "_").replace("\\", "_")


def _hash_payload(payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _feedback_score(item: Dict[str, Any]) -> float:
    label = str(item.get("label", "")).strip().lower()
    if label in {"good", "positive", "pass", "thumbs_up", "up"}:
        return 1.0
    if label in {"bad", "negative", "unsafe", "wrong", "fail", "thumbs_down", "down"}:
        return -1.0
    text = str(item.get("feedback", "")).strip().lower()
    if any(token in text for token in ("unsafe", "wrong", "bad", "fail", "error")):
        return -1.0
    if any(token in text for token in ("good", "great", "correct", "nice", "pass")):
        return 1.0
    return 0.0


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TrainingBridgeManager(EvalStore):
    """Persist trajectory->sample exports and compare export versions."""

    BRIDGE_VERSION = "offline-v1"
    SCHEMA_VERSION = "state-action-tool_result-reward_proxy.v1"

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__(base_dir or (Path.home() / ".gazer" / "eval"))
        self._exports = self._base / "training_bridge_exports"
        self._version_trace = self._base / "training_bridge_versions.jsonl"
        self._exports.mkdir(parents=True, exist_ok=True)

    def _export_path(self, export_id: str) -> Path:
        safe = _safe_dataset_id(export_id)
        return self._exports / f"{safe}.json"

    def _sample_from_trajectory(
        self,
        payload: Dict[str, Any],
        *,
        eval_hint: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        run_id = str(payload.get("run_id", "")).strip()
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
        metrics = final.get("metrics") if isinstance(final.get("metrics"), dict) else {}
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        feedback = payload.get("feedback") if isinstance(payload.get("feedback"), list) else []
        last_feedback = feedback[-1] if feedback and isinstance(feedback[-1], dict) else {}

        tool_calls: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        for idx, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            action = str(event.get("action", "")).strip().lower()
            raw = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            common = {
                "index": idx,
                "ts": event.get("ts"),
                "stage": str(event.get("stage", "")).strip(),
                "tool": str(raw.get("tool", "")).strip(),
                "tool_call_id": str(raw.get("tool_call_id", "")).strip(),
            }
            if action == "tool_call":
                tool_calls.append(
                    {
                        **common,
                        "args_hash": str(raw.get("args_hash", "")).strip(),
                        "args_preview": str(raw.get("args_preview", "")).strip(),
                    }
                )
            elif action == "tool_result":
                tool_results.append(
                    {
                        **common,
                        "status": str(raw.get("status", "")).strip().lower(),
                        "error_code": str(raw.get("error_code", "")).strip(),
                        "result_preview": str(raw.get("result_preview", "")).strip(),
                        "has_media": bool(raw.get("has_media", False)),
                    }
                )

        success_count = sum(1 for item in tool_results if item.get("status") in {"ok", "success"})
        error_count = sum(1 for item in tool_results if item.get("status") in {"error", "failed"})
        result_total = len(tool_results)
        success_rate = round(success_count / result_total, 4) if result_total > 0 else None
        eval_passed: Optional[bool] = None
        eval_score: Optional[float] = None
        persona_consistency_score: Optional[float] = None
        if isinstance(eval_hint, dict):
            if "passed" in eval_hint:
                eval_passed = bool(eval_hint.get("passed"))
            eval_score = _safe_float(eval_hint.get("composite_score", eval_hint.get("score")))
            persona_consistency_score = _safe_float(
                eval_hint.get("persona_consistency_score", eval_hint.get("consistency_score"))
            )

        final_status = str(final.get("status", "running")).strip().lower() or "running"
        return {
            "run_id": run_id,
            "state": {
                "session_key": str(meta.get("session_key", "")).strip(),
                "channel": str(meta.get("channel", "")).strip(),
                "chat_id": str(meta.get("chat_id", "")).strip(),
                "sender_id": str(meta.get("sender_id", "")).strip(),
                "user_content": str(meta.get("user_content", "")).strip(),
                "final_status": final_status,
                "event_count": len(events),
            },
            "action": {
                "tool_calls": tool_calls,
                "assistant_output": str(final.get("final_content", "")).strip(),
            },
            "tool_result": {
                "events": tool_results,
                "total_count": result_total,
                "success_count": success_count,
                "error_count": error_count,
            },
            "reward_proxy": {
                "feedback_score": _feedback_score(last_feedback),
                "feedback_label": str(last_feedback.get("label", "")).strip().lower(),
                "feedback_text": str(last_feedback.get("feedback", "")).strip(),
                "tool_success_rate": success_rate,
                "error_count": error_count,
                "has_terminal_error": bool(error_count > 0 or final_status in {"error", "llm_error", "incomplete"}),
                "eval_passed": eval_passed,
                "eval_score": eval_score,
                "persona_consistency_score": persona_consistency_score,
                "release_gate_alignment": eval_passed,
                "turn_latency_ms": _safe_float(metrics.get("turn_latency_ms")),
            },
        }

    @staticmethod
    def _offline_policy_eval(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_samples = len(samples)
        success_count = 0
        failure_types: Dict[str, int] = {}
        persona_scores: List[float] = []
        for item in samples:
            state = item.get("state") if isinstance(item.get("state"), dict) else {}
            reward = item.get("reward_proxy") if isinstance(item.get("reward_proxy"), dict) else {}
            tool_result = item.get("tool_result") if isinstance(item.get("tool_result"), dict) else {}
            final_status = str(state.get("final_status", "")).strip().lower()
            has_terminal_error = bool(reward.get("has_terminal_error", False))
            if final_status in {"done", "ok", "success", "completed"} and not has_terminal_error:
                success_count += 1

            events = tool_result.get("events") if isinstance(tool_result.get("events"), list) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                status = str(event.get("status", "")).strip().lower()
                if status not in {"error", "failed"}:
                    continue
                error_code = str(event.get("error_code", "")).strip() or "UNKNOWN_ERROR"
                failure_types[error_code] = int(failure_types.get(error_code, 0)) + 1

            persona = _safe_float(reward.get("persona_consistency_score"))
            if persona is not None:
                persona_scores.append(persona)

        sorted_failures = sorted(failure_types.items(), key=lambda item: (-item[1], item[0]))
        return {
            "trajectory_success_rate": round(success_count / max(1, total_samples), 4),
            "tool_failure_types": dict(sorted_failures),
            "top_tool_failure_types": [
                {"error_code": code, "count": count}
                for code, count in sorted_failures[:5]
            ],
            "persona_consistency_score_avg": (
                round(sum(persona_scores) / len(persona_scores), 4) if persona_scores else None
            ),
            "persona_consistency_coverage": round(len(persona_scores) / max(1, total_samples), 4),
        }

    @staticmethod
    def _summary_from_samples(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        rates = [
            float((item.get("reward_proxy") or {}).get("tool_success_rate"))
            for item in samples
            if (item.get("reward_proxy") or {}).get("tool_success_rate") is not None
        ]
        feedback_scores = [float((item.get("reward_proxy") or {}).get("feedback_score", 0.0)) for item in samples]
        terminal_error_count = sum(
            1 for item in samples if bool((item.get("reward_proxy") or {}).get("has_terminal_error", False))
        )
        eval_known = [
            bool((item.get("reward_proxy") or {}).get("eval_passed"))
            for item in samples
            if (item.get("reward_proxy") or {}).get("eval_passed") is not None
        ]
        policy_eval = TrainingBridgeManager._offline_policy_eval(samples)
        return {
            "sample_count": len(samples),
            "avg_tool_success_rate": round(sum(rates) / len(rates), 4) if rates else None,
            "avg_feedback_score": round(sum(feedback_scores) / len(feedback_scores), 4) if feedback_scores else 0.0,
            "terminal_error_rate": round(terminal_error_count / max(1, len(samples)), 4),
            "eval_pass_rate": round(sum(1 for x in eval_known if x) / len(eval_known), 4) if eval_known else None,
            "offline_policy_eval": policy_eval,
        }

    @staticmethod
    def _compact_export(payload: Dict[str, Any]) -> Dict[str, Any]:
        version_trace = payload.get("version_trace") if isinstance(payload.get("version_trace"), dict) else {}
        return {
            "export_id": payload.get("export_id"),
            "dataset_id": payload.get("dataset_id"),
            "source": payload.get("source"),
            "bridge_version": payload.get("bridge_version"),
            "schema_version": payload.get("schema_version"),
            "created_at": payload.get("created_at"),
            "trajectory_count": payload.get("trajectory_count", 0),
            "sample_count": payload.get("sample_count", 0),
            "fingerprint": payload.get("fingerprint", ""),
            "summary": payload.get("summary", {}),
            "version_trace": {
                "source_hash": version_trace.get("source_hash", ""),
                "source_run_ids": version_trace.get("source_run_ids", []),
                "release_gate": version_trace.get("release_gate", {}),
            },
            "metadata": payload.get("metadata", {}),
        }

    def create_export(
        self,
        *,
        dataset_id: str,
        trajectories: List[Dict[str, Any]],
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
        eval_by_run: Optional[Dict[str, Dict[str, Any]]] = None,
        release_gate: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        eval_map = eval_by_run if isinstance(eval_by_run, dict) else {}
        normalized = [item for item in trajectories if isinstance(item, dict)]
        normalized.sort(key=lambda item: str(item.get("run_id", "")))

        samples: List[Dict[str, Any]] = []
        run_ids: List[str] = []
        source_events: List[Dict[str, Any]] = []
        for item in normalized:
            run_id = str(item.get("run_id", "")).strip()
            if not run_id:
                continue
            run_ids.append(run_id)
            events = item.get("events") if isinstance(item.get("events"), list) else []
            source_events.append({"run_id": run_id, "event_count": len(events)})
            samples.append(self._sample_from_trajectory(item, eval_hint=eval_map.get(run_id)))

        summary = self._summary_from_samples(samples)
        fingerprint = _hash_payload(
            {
                "dataset_id": str(dataset_id),
                "schema_version": self.SCHEMA_VERSION,
                "samples": samples,
            }
        )
        source_hash = _hash_payload({"run_ids": run_ids, "events": source_events})
        export = {
            "export_id": _export_id(),
            "dataset_id": str(dataset_id),
            "source": str(source or "manual"),
            "bridge_version": self.BRIDGE_VERSION,
            "schema_version": self.SCHEMA_VERSION,
            "created_at": time.time(),
            "updated_at": time.time(),
            "trajectory_count": len(run_ids),
            "sample_count": len(samples),
            "fingerprint": fingerprint,
            "summary": summary,
            "version_trace": {
                "source_hash": source_hash,
                "source_run_ids": run_ids,
                "release_gate": release_gate if isinstance(release_gate, dict) else {},
            },
            "metadata": metadata or {},
            "samples": samples,
        }
        self._write_json(self._export_path(str(export["export_id"])), export)
        self._append_jsonl(
            self._version_trace,
            {
                "export_id": export["export_id"],
                "dataset_id": export["dataset_id"],
                "created_at": export["created_at"],
                "bridge_version": export["bridge_version"],
                "schema_version": export["schema_version"],
                "fingerprint": export["fingerprint"],
                "version_trace": export["version_trace"],
            },
        )
        return export

    def list_exports(self, *, limit: int = 50, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        paths = sorted(
            self._exports.glob("*.json"),
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
            items.append(self._compact_export(payload))
            if len(items) >= limit:
                break
        return items

    def get_export(self, export_id: str, *, include_samples: bool = False) -> Optional[Dict[str, Any]]:
        payload = self._read_json(self._export_path(export_id))
        if not payload:
            return None
        if include_samples:
            return payload
        compact = self._compact_export(payload)
        compact["sample_preview"] = list(payload.get("samples", []))[:5]
        return compact

    @staticmethod
    def _reward_metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
        samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        rates = [
            float((item.get("reward_proxy") or {}).get("tool_success_rate"))
            for item in samples
            if (item.get("reward_proxy") or {}).get("tool_success_rate") is not None
        ]
        feedback_scores = [float((item.get("reward_proxy") or {}).get("feedback_score", 0.0)) for item in samples]
        error_count = sum(
            1 for item in samples if bool((item.get("reward_proxy") or {}).get("has_terminal_error", False))
        )
        eval_known = [
            bool((item.get("reward_proxy") or {}).get("eval_passed"))
            for item in samples
            if (item.get("reward_proxy") or {}).get("eval_passed") is not None
        ]
        policy_eval = TrainingBridgeManager._offline_policy_eval(samples)
        return {
            "sample_count": len(samples),
            "avg_tool_success_rate": round(sum(rates) / len(rates), 4) if rates else None,
            "avg_feedback_score": round(sum(feedback_scores) / len(feedback_scores), 4) if feedback_scores else 0.0,
            "terminal_error_rate": round(error_count / max(1, len(samples)), 4),
            "eval_pass_rate": round(sum(1 for x in eval_known if x) / len(eval_known), 4) if eval_known else None,
            "trajectory_success_rate": policy_eval.get("trajectory_success_rate"),
            "persona_consistency_score_avg": policy_eval.get("persona_consistency_score_avg"),
            "persona_consistency_coverage": policy_eval.get("persona_consistency_coverage"),
            "tool_failure_types": policy_eval.get("tool_failure_types", {}),
        }

    def compare_exports(
        self,
        *,
        candidate_export_id: str,
        baseline_export_id: str,
    ) -> Optional[Dict[str, Any]]:
        candidate = self.get_export(candidate_export_id, include_samples=True)
        baseline = self.get_export(baseline_export_id, include_samples=True)
        if not candidate or not baseline:
            return None

        candidate_run_ids = [str(item.get("run_id", "")) for item in (candidate.get("samples") or [])]
        baseline_run_ids = [str(item.get("run_id", "")) for item in (baseline.get("samples") or [])]
        candidate_set = set(candidate_run_ids)
        baseline_set = set(baseline_run_ids)
        shared = sorted(candidate_set & baseline_set)
        added = sorted(candidate_set - baseline_set)
        removed = sorted(baseline_set - candidate_set)

        cand_metrics = self._reward_metrics(candidate)
        base_metrics = self._reward_metrics(baseline)

        def _delta(key: str) -> Optional[float]:
            left = cand_metrics.get(key)
            right = base_metrics.get(key)
            if left is None or right is None:
                return None
            return round(float(left) - float(right), 4)

        cand_failure_types = cand_metrics.get("tool_failure_types", {}) if isinstance(cand_metrics, dict) else {}
        base_failure_types = base_metrics.get("tool_failure_types", {}) if isinstance(base_metrics, dict) else {}
        failure_type_delta: Dict[str, int] = {}
        for code in sorted(set(cand_failure_types.keys()) | set(base_failure_types.keys())):
            left = int(cand_failure_types.get(code, 0))
            right = int(base_failure_types.get(code, 0))
            failure_type_delta[code] = left - right

        return {
            "dataset_id": candidate.get("dataset_id"),
            "candidate_export_id": candidate_export_id,
            "baseline_export_id": baseline_export_id,
            "sample_delta": int(candidate.get("sample_count", 0) or 0) - int(baseline.get("sample_count", 0) or 0),
            "shared_run_count": len(shared),
            "added_runs": added[:50],
            "removed_runs": removed[:50],
            "fingerprint_changed": str(candidate.get("fingerprint", "")) != str(baseline.get("fingerprint", "")),
            "candidate_metrics": cand_metrics,
            "baseline_metrics": base_metrics,
            "reward_proxy_delta": {
                "avg_tool_success_rate": _delta("avg_tool_success_rate"),
                "avg_feedback_score": _delta("avg_feedback_score"),
                "terminal_error_rate": _delta("terminal_error_rate"),
                "eval_pass_rate": _delta("eval_pass_rate"),
                "trajectory_success_rate": _delta("trajectory_success_rate"),
                "persona_consistency_score_avg": _delta("persona_consistency_score_avg"),
                "persona_consistency_coverage": _delta("persona_consistency_coverage"),
                "tool_failure_types": failure_type_delta,
            },
        }

    def compare_with_baseline(self, dataset_id: str, baseline_index: int = 1) -> Optional[Dict[str, Any]]:
        idx = max(1, int(baseline_index))
        history = self.list_exports(limit=max(2, idx + 1), dataset_id=dataset_id)
        if len(history) <= idx:
            return None
        candidate_id = str(history[0].get("export_id", ""))
        baseline_id = str(history[idx].get("export_id", ""))
        if not candidate_id or not baseline_id:
            return None
        return self.compare_exports(candidate_export_id=candidate_id, baseline_export_id=baseline_id)

    def to_training_inputs(self, export_id: str) -> Optional[Dict[str, Any]]:
        payload = self.get_export(export_id, include_samples=True)
        if not payload:
            return None
        samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        trajectory_samples: List[Dict[str, Any]] = []
        eval_samples: List[Dict[str, Any]] = []
        for item in samples:
            if not isinstance(item, dict):
                continue
            run_id = str(item.get("run_id", "")).strip()
            state = item.get("state") if isinstance(item.get("state"), dict) else {}
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            tool_result = item.get("tool_result") if isinstance(item.get("tool_result"), dict) else {}
            reward = item.get("reward_proxy") if isinstance(item.get("reward_proxy"), dict) else {}
            tool_events = tool_result.get("events") if isinstance(tool_result.get("events"), list) else []
            normalized_events = [
                {
                    "action": "tool_result",
                    "payload": {
                        "tool": evt.get("tool", ""),
                        "status": evt.get("status", ""),
                        "error_code": evt.get("error_code", ""),
                        "result_preview": evt.get("result_preview", ""),
                    },
                }
                for evt in tool_events
                if isinstance(evt, dict)
            ]
            trajectory_samples.append(
                {
                    "run_id": run_id,
                    "user_content": state.get("user_content", ""),
                    "assistant_output": action.get("assistant_output", ""),
                    "status": state.get("final_status", ""),
                    "feedback": reward.get("feedback_text", ""),
                    "events": normalized_events,
                }
            )
            eval_passed = reward.get("eval_passed")
            if eval_passed is None:
                eval_passed = not bool(reward.get("has_terminal_error", False))
            eval_samples.append(
                {
                    "run_id": run_id,
                    "passed": bool(eval_passed),
                    "score": reward.get("eval_score"),
                }
            )
        return {
            "dataset_id": str(payload.get("dataset_id", "")),
            "export_id": str(payload.get("export_id", "")),
            "trajectory_samples": trajectory_samples,
            "eval_samples": eval_samples,
        }
