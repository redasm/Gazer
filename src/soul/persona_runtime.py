"""Persona runtime signals, correction strategy, and mental-process versioning."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from eval.store import EvalStore


def _signal_id() -> str:
    return f"psig_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _version_id() -> str:
    return f"pver_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _safe_id(value: str) -> str:
    return str(value or "").replace("/", "_").replace("\\", "_")


class PersonaRuntimeManager(EvalStore):
    """Manage runtime persona signals and mental-process version snapshots."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        super().__init__(base_dir or (Path.home() / ".gazer" / "eval" / "persona_runtime"))
        self._signals = self._base / "runtime_signals.jsonl"
        self._versions = self._base / "mental_process_versions.jsonl"

    @staticmethod
    def _payload_hash(payload: Dict[str, Any]) -> str:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _contains_any(haystack: str, tokens: List[str]) -> bool:
        text = haystack.lower()
        return any(token.lower() in text for token in tokens if token)

    def assess_output(
        self,
        *,
        content: str,
        source: str = "agent_loop",
        run_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = str(content or "").strip()
        violations: List[str] = []
        hints: List[str] = []

        unsafe_tokens = [
            "sure, done",
            "sure done",
            "i will do that unsafe",
            "i can do the unsafe action",
            "当然可以做这个危险",
            "没问题我来做危险操作",
        ]
        drift_tokens = [
            "i am just a generic ai",
            "i have no identity",
            "我只是一个通用ai",
            "我没有身份",
        ]
        if not text:
            violations.append("empty_output")
            hints.append("Return a concise answer that keeps persona identity and safety stance.")
        if self._contains_any(text, unsafe_tokens):
            violations.append("unsafe_compliance")
            hints.append("Refuse unsafe intent and provide safer alternatives.")
        if self._contains_any(text, drift_tokens):
            violations.append("identity_drift")
            hints.append("Reassert identity as Gazer and keep companion tone.")

        level = "healthy"
        if "unsafe_compliance" in violations:
            level = "critical"
        elif violations:
            level = "warning"

        drift_score = 0.0
        if "identity_drift" in violations:
            drift_score += 0.45
        if "empty_output" in violations:
            drift_score += 0.2
        if "unsafe_compliance" in violations:
            drift_score += 0.65
        drift_score = round(min(1.0, drift_score), 4)

        return {
            "signal_id": _signal_id(),
            "created_at": time.time(),
            "source": str(source or "agent_loop"),
            "run_id": str(run_id or ""),
            "level": level,
            "drift_score": drift_score,
            "violation_count": len(violations),
            "violations": violations,
            "hints": hints,
            "metadata": metadata or {},
        }

    def assess_eval_report(
        self,
        *,
        report: Dict[str, Any],
        dataset_id: str,
        warning_score: float,
        critical_score: float,
    ) -> Dict[str, Any]:
        score = float(report.get("consistency_score", 0.0) or 0.0)
        failed_samples: List[str] = []
        for item in list(report.get("results", []) or []):
            if not isinstance(item, dict):
                continue
            if not bool(item.get("passed", False)):
                failed_samples.append(str(item.get("sample_id", "")).strip())
        failed_samples = [sid for sid in failed_samples if sid]
        if score < float(critical_score):
            level = "critical"
        elif score < float(warning_score):
            level = "warning"
        else:
            level = "healthy"

        return {
            "signal_id": _signal_id(),
            "created_at": time.time(),
            "source": "persona_eval",
            "dataset_id": str(dataset_id or ""),
            "level": level,
            "drift_score": round(max(0.0, min(1.0, 1.0 - score)), 4),
            "violation_count": len(failed_samples),
            "violations": failed_samples,
            "hints": (
                ["Raise score above threshold and tighten identity/safety wording."]
                if level in {"warning", "critical"}
                else []
            ),
            "metrics": {
                "consistency_score": round(score, 4),
                "warning_score": round(float(warning_score), 4),
                "critical_score": round(float(critical_score), 4),
                "auto_passed": bool(report.get("auto_passed", False)),
            },
        }

    def apply_correction(
        self,
        *,
        content: str,
        strategy: str = "rewrite",
        language: str = "zh",
        violations: Optional[List[str]] = None,
    ) -> str:
        text = str(content or "").strip()
        mode = str(strategy or "rewrite").strip().lower()
        violation_set = {str(item).strip().lower() for item in (violations or []) if str(item).strip()}

        if mode == "degrade":
            if language.lower().startswith("en"):
                return "I cannot continue with that phrasing. I can help with a safer, clear alternative."
            return "该表述不符合人格与安全约束。我可以继续提供更安全且清晰的替代方案。"

        # rewrite (default)
        rewritten = text
        identity_prefix = "I am Gazer, your AI companion." if language.lower().startswith("en") else "我是 Gazer，你的 AI 伙伴。"
        identity_needed = False
        if "identity_drift" in violation_set or self._contains_any(
            rewritten,
            ["i am just a generic ai", "i have no identity", "我只是一个通用ai", "我没有身份"],
        ):
            identity_needed = True
            rewritten = f"{identity_prefix} {rewritten}".strip()
        unsafe_map = {
            "sure, done": "I can't assist with unsafe actions, but I can suggest a safer path.",
            "sure done": "I can't assist with unsafe actions, but I can suggest a safer path.",
            "i will do that unsafe": "I can't assist with unsafe actions, but I can suggest a safer path.",
            "i can do the unsafe action": "I can't assist with unsafe actions, but I can suggest a safer path.",
            "当然可以做这个危险": "我不能协助危险操作，但可以给出更安全的替代方案。",
            "没问题我来做危险操作": "我不能协助危险操作，但可以给出更安全的替代方案。",
        }
        lower = rewritten.lower()
        for key, val in unsafe_map.items():
            if key in lower:
                rewritten = val if not identity_needed else f"{identity_prefix} {val}"
                break
        return rewritten.strip()

    def resolve_ab_strategy(
        self,
        *,
        ab_config: Optional[Dict[str, Any]],
        assignment_key: str,
        violations: Optional[List[str]],
        default_strategy: str = "rewrite",
    ) -> Dict[str, Any]:
        cfg = ab_config if isinstance(ab_config, dict) else {}
        if not bool(cfg.get("enabled", False)):
            return {
                "enabled": False,
                "profile": "",
                "strategy": str(default_strategy or "rewrite"),
                "reason": "ab_disabled",
            }
        profiles = cfg.get("profiles", {})
        if not isinstance(profiles, dict) or not profiles:
            return {
                "enabled": False,
                "profile": "",
                "strategy": str(default_strategy or "rewrite"),
                "reason": "ab_profiles_missing",
            }
        profile_names = sorted(str(name).strip() for name in profiles.keys() if str(name).strip())
        if not profile_names:
            return {
                "enabled": False,
                "profile": "",
                "strategy": str(default_strategy or "rewrite"),
                "reason": "ab_profiles_empty",
            }

        forced_profile = str(cfg.get("force_profile", "")).strip()
        if forced_profile and forced_profile in profiles:
            profile_name = forced_profile
            select_reason = "forced_profile"
        else:
            stable_key = str(assignment_key or "default")
            bucket = int(hashlib.sha256(stable_key.encode("utf-8")).hexdigest(), 16) % len(profile_names)
            profile_name = profile_names[bucket]
            select_reason = "hash_bucket"

        profile = profiles.get(profile_name, {})
        if not isinstance(profile, dict):
            profile = {}
        violation_map = profile.get("violation_strategy", {})
        if not isinstance(violation_map, dict):
            violation_map = {}

        strategy = str(profile.get("default_strategy", default_strategy)).strip().lower() or "rewrite"
        for violation in (violations or []):
            key = str(violation).strip()
            if key in violation_map:
                strategy = str(violation_map.get(key, strategy)).strip().lower() or strategy
                break

        if strategy not in {"rewrite", "degrade"}:
            strategy = str(default_strategy or "rewrite").strip().lower() or "rewrite"
        return {
            "enabled": True,
            "profile": profile_name,
            "strategy": strategy,
            "reason": select_reason,
        }

    def process_output(
        self,
        *,
        content: str,
        source: str = "agent_loop",
        run_id: str = "",
        language: str = "zh",
        auto_correct_enabled: bool = False,
        strategy: str = "rewrite",
        trigger_levels: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        retain: int = 500,
        ab_config: Optional[Dict[str, Any]] = None,
        assignment_key: str = "",
    ) -> Dict[str, Any]:
        signal = self.assess_output(content=content, source=source, run_id=run_id, metadata=metadata)
        levels = {
            str(item).strip().lower()
            for item in (trigger_levels or ["critical"])
            if str(item).strip()
        }
        corrected = str(content or "")
        correction_applied = False
        applied_strategy = str(strategy or "rewrite")
        if auto_correct_enabled and str(signal.get("level", "")).lower() in levels:
            ab_decision = self.resolve_ab_strategy(
                ab_config=ab_config,
                assignment_key=str(assignment_key or run_id or source or "default"),
                violations=list(signal.get("violations", [])),
                default_strategy=applied_strategy,
            )
            if bool(ab_decision.get("enabled", False)):
                signal["ab_profile"] = str(ab_decision.get("profile", ""))
                signal["ab_reason"] = str(ab_decision.get("reason", ""))
            applied_strategy = str(ab_decision.get("strategy", applied_strategy)).strip().lower() or applied_strategy
            corrected = self.apply_correction(
                content=corrected,
                strategy=applied_strategy,
                language=language,
                violations=list(signal.get("violations", [])),
            )
            correction_applied = corrected != str(content or "")
        signal["correction_applied"] = correction_applied
        signal["correction_strategy"] = str(applied_strategy or "rewrite")
        self.record_signal(signal, retain=retain)
        return {
            "final_content": corrected,
            "signal": signal,
        }

    def record_signal(self, signal: Dict[str, Any], *, retain: int = 500) -> Dict[str, Any]:
        payload = dict(signal or {})
        payload.setdefault("signal_id", _signal_id())
        payload.setdefault("created_at", time.time())
        self._append_jsonl(self._signals, payload)
        cap = max(50, min(int(retain or 500), 5000))
        items = self._read_jsonl(self._signals)
        if len(items) > cap:
            items = sorted(items, key=lambda item: float(item.get("created_at", 0.0)))[-cap:]
            self._write_jsonl(self._signals, items)
        return payload

    def list_signals(
        self,
        *,
        limit: int = 100,
        level: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self._read_jsonl(self._signals)
        level_key = str(level or "").strip().lower()
        source_key = str(source or "").strip().lower()
        if level_key:
            items = [item for item in items if str(item.get("level", "")).strip().lower() == level_key]
        if source_key:
            items = [item for item in items if str(item.get("source", "")).strip().lower() == source_key]
        items.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return items[: max(1, min(limit, 500))]

    def get_latest_signal(self, *, source: Optional[str] = None) -> Optional[Dict[str, Any]]:
        items = self.list_signals(limit=1, source=source)
        return items[0] if items else None

    def create_mental_process_version(
        self,
        *,
        mental_process: Dict[str, Any],
        actor: str,
        note: str = "",
        source: str = "manual_update",
        related_version_id: str = "",
    ) -> Dict[str, Any]:
        payload = mental_process if isinstance(mental_process, dict) else {}
        states = payload.get("states", [])
        version = {
            "version_id": _version_id(),
            "created_at": time.time(),
            "actor": str(actor or "admin"),
            "note": str(note or ""),
            "source": str(source or "manual_update"),
            "related_version_id": str(related_version_id or ""),
            "state_count": len(states) if isinstance(states, list) else 0,
            "hash": self._payload_hash(payload),
            "mental_process": payload,
        }
        self._append_jsonl(self._versions, version)
        return version

    def list_mental_process_versions(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        items = self._read_jsonl(self._versions)
        items.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        out: List[Dict[str, Any]] = []
        for item in items[: max(1, min(limit, 500))]:
            out.append(
                {
                    "version_id": item.get("version_id"),
                    "created_at": item.get("created_at"),
                    "actor": item.get("actor"),
                    "note": item.get("note"),
                    "source": item.get("source"),
                    "related_version_id": item.get("related_version_id"),
                    "state_count": item.get("state_count", 0),
                    "hash": item.get("hash", ""),
                }
            )
        return out

    def get_mental_process_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        target = _safe_id(version_id)
        if not target:
            return None
        for item in self._read_jsonl(self._versions):
            if str(item.get("version_id", "")).strip() == target:
                return item
        return None

    @staticmethod
    def _collect_diff_paths(before: Any, after: Any, prefix: str = "") -> List[str]:
        if type(before) is not type(after):
            return [prefix or "$"]
        if isinstance(before, dict):
            keys = sorted(set(before.keys()) | set(after.keys()))
            changed: List[str] = []
            for key in keys:
                child = f"{prefix}.{key}" if prefix else str(key)
                if key not in before or key not in after:
                    changed.append(child)
                    continue
                changed.extend(PersonaRuntimeManager._collect_diff_paths(before[key], after[key], child))
            return changed
        if isinstance(before, list):
            changed = []
            max_len = max(len(before), len(after))
            for idx in range(max_len):
                child = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
                if idx >= len(before) or idx >= len(after):
                    changed.append(child)
                    continue
                changed.extend(PersonaRuntimeManager._collect_diff_paths(before[idx], after[idx], child))
            return changed
        if before != after:
            return [prefix or "$"]
        return []

    def diff_mental_process_versions(self, *, from_version_id: str, to_version_id: str) -> Optional[Dict[str, Any]]:
        left = self.get_mental_process_version(from_version_id)
        right = self.get_mental_process_version(to_version_id)
        if left is None or right is None:
            return None
        before = left.get("mental_process", {})
        if not isinstance(before, dict):
            before = {}
        after = right.get("mental_process", {})
        if not isinstance(after, dict):
            after = {}
        changed_paths = self._collect_diff_paths(before, after)
        return {
            "from_version_id": str(left.get("version_id", "")),
            "to_version_id": str(right.get("version_id", "")),
            "from_hash": str(left.get("hash", "")),
            "to_hash": str(right.get("hash", "")),
            "changed": bool(changed_paths),
            "changed_path_count": len(changed_paths),
            "changed_paths": changed_paths[:500],
            "from_state_count": int(left.get("state_count", 0) or 0),
            "to_state_count": int(right.get("state_count", 0) or 0),
            "state_count_delta": int(right.get("state_count", 0) or 0) - int(left.get("state_count", 0) or 0),
        }

    def replay_mental_process_versions(
        self,
        *,
        limit: int = 50,
        start_version_id: str = "",
    ) -> List[Dict[str, Any]]:
        items = self._read_jsonl(self._versions)
        items.sort(key=lambda item: float(item.get("created_at", 0.0)))
        start_id = str(start_version_id or "").strip()
        if start_id:
            start_index = 0
            for idx, item in enumerate(items):
                if str(item.get("version_id", "")) == start_id:
                    start_index = idx
                    break
            items = items[start_index:]

        out: List[Dict[str, Any]] = []
        prev_version_id: str = ""
        for item in items:
            version_id = str(item.get("version_id", ""))
            diff = None
            if prev_version_id:
                diff = self.diff_mental_process_versions(
                    from_version_id=prev_version_id,
                    to_version_id=version_id,
                )
            out.append(
                {
                    "version_id": version_id,
                    "created_at": item.get("created_at"),
                    "actor": item.get("actor"),
                    "note": item.get("note"),
                    "source": item.get("source"),
                    "related_version_id": item.get("related_version_id"),
                    "state_count": int(item.get("state_count", 0) or 0),
                    "hash": item.get("hash", ""),
                    "diff": diff,
                }
            )
            prev_version_id = version_id
        cap = max(1, min(int(limit), 500))
        return out[-cap:]

    def find_fast_rollback_target(self, *, current_mental_process: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current_payload = current_mental_process if isinstance(current_mental_process, dict) else {}
        current_hash = self._payload_hash(current_payload)
        for item in reversed(self._read_jsonl(self._versions)):
            version_hash = str(item.get("hash", ""))
            if version_hash and version_hash != current_hash:
                return item
        return None


_PERSONA_RUNTIME_MANAGER: Optional[PersonaRuntimeManager] = None


def get_persona_runtime_manager() -> PersonaRuntimeManager:
    global _PERSONA_RUNTIME_MANAGER
    if _PERSONA_RUNTIME_MANAGER is None:
        _PERSONA_RUNTIME_MANAGER = PersonaRuntimeManager()
    return _PERSONA_RUNTIME_MANAGER
