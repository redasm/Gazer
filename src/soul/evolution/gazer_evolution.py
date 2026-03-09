"""Feedback-driven persona evolution.

Collects user feedback (positive / negative / correction) and periodically
uses LLM self-critique to refine Gazer's system prompt -- a lightweight
Automatic Prompt Optimization loop that requires no external training
framework.
"""

import json
import logging
import os
import re
import threading
import time
from difflib import SequenceMatcher
from datetime import datetime
from typing import List, Dict, Any, Optional

from eval.benchmark import EvalBenchmarkManager
from runtime.config_manager import config
from soul.evolution.apo_optimizer import APOOptimizer

logger = logging.getLogger("GazerEvolution")


def _default_state_path(filename: str) -> str:
    try:
        base_dir = str(
            config.get("memory.context_backend.data_dir", "data/openviking")
            or "data/openviking"
        )
    except Exception:
        base_dir = os.path.join("data", "openviking")
    return os.path.join(base_dir, filename)


FEEDBACK_PATH = _default_state_path("feedback.json")
HISTORY_PATH = _default_state_path("evolution_history.jsonl")


class GazerEvolution:
    """Feedback-driven persona evolution system.

    Collects user feedback and uses LLM self-critique to iteratively
    improve Gazer's system prompt.
    """

    def __init__(self, feedback_path: str = FEEDBACK_PATH, history_path: str = HISTORY_PATH):
        self.feedback_path = feedback_path
        self.history_path = history_path
        self._apo_optimizer: Optional["APOOptimizer"] = None  # lazy init
        # threading.Lock is intentional: protects sync file I/O only.
        # No awaits occur inside locked sections, so this is safe in async.
        self._file_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_auto_attempt_ts: float = 0.0
        self._last_auto_success_ts: float = 0.0
        self._auto_attempts: int = 0
        self._auto_successes: int = 0
        self._last_auto_reason: str = "never"
        self._last_gate_result: Dict[str, Any] = {
            "passed": None,
            "reason": "never",
            "similarity": 0.0,
            "length_ratio": 0.0,
            "checked_at": None,
        }
        self._last_pre_publish_eval: Dict[str, Any] = {
            "passed": None,
            "score": 0.0,
            "reason": "never",
            "checked_at": None,
        }
        os.makedirs(os.path.dirname(self.feedback_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)

    def _ensure_apo_optimizer(self) -> Optional[APOOptimizer]:
        """Lazily create an APOOptimizer backed by the slow-brain model."""
        if self._apo_optimizer is not None:
            return self._apo_optimizer
        try:
            from soul.models import ModelRegistry
            api_key, base_url, model_name, headers = ModelRegistry.resolve_model("slow_brain")
            if not api_key:
                logger.warning("No LLM API key available for APOOptimizer.")
                return None
            from openai import AsyncOpenAI
            from soul.llm_adapter import AsyncOpenAIAdapter
            client = AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=headers)

            self._apo_optimizer = APOOptimizer(
                llm_client=AsyncOpenAIAdapter(client, model_name, temperature=0.7),
                min_feedback_count=3,  # GazerEvolution already gates at 3 actionable
            )
            return self._apo_optimizer
        except Exception as exc:
            logger.error("Failed to create APOOptimizer: %s", exc)
            return None

    def collect_feedback(self, label: str, context: str, feedback_text: str = "") -> None:
        """Record a single piece of user feedback.

        Args:
            label: One of "positive", "negative", "correction".
            context: Where the feedback was triggered (e.g. "telegram_reply").
            feedback_text: Optional free-form text from the user.
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "label": label,
            "context": context,
            "feedback": feedback_text,
        }
        feedbacks = self._load_feedback()
        feedbacks.append(entry)
        self._save_feedback(feedbacks)
        logger.info("Feedback recorded: %s from %s", label, context)

    async def optimize_persona(self) -> bool:
        """Analyze accumulated feedback and refine the system prompt.

        Uses LLM self-critique: feeds the current prompt together with
        recent feedback to the slow-brain model and asks it to produce an
        improved version.

        Returns:
            True if the prompt was updated, False otherwise.
        """
        started_at = time.time()
        feedbacks = self._load_feedback()
        if not feedbacks:
            logger.info("No feedback collected yet, skipping optimization.")
            self._record_history_event(
                {
                    "event": "optimize_persona",
                    "attempted": False,
                    "updated": False,
                    "reason": "no_feedback",
                    "duration_ms": int((time.time() - started_at) * 1000),
                }
            )
            return False

        recent = feedbacks[-50:]
        negative_count = sum(1 for f in recent if f["label"] == "negative")
        correction_count = sum(1 for f in recent if f["label"] == "correction")

        if negative_count + correction_count < 3:
            logger.info("Not enough actionable feedback for optimization.")
            self._record_history_event(
                {
                    "event": "optimize_persona",
                    "attempted": False,
                    "updated": False,
                    "reason": "insufficient_actionable_feedback",
                    "feedback_total": len(recent),
                    "actionable_feedback": negative_count + correction_count,
                    "duration_ms": int((time.time() - started_at) * 1000),
                }
            )
            return False

        current_prompt = config.get("personality.system_prompt", "")

        try:
            # Delegate to APOOptimizer (Issue-07 reform)
            apo = self._ensure_apo_optimizer()
            if apo is None:
                self._record_history_event(
                    {
                        "event": "optimize_persona",
                        "attempted": True,
                        "updated": False,
                        "reason": "missing_api_key",
                        "duration_ms": int((time.time() - started_at) * 1000),
                    }
                )
                return False

            # Map feedback to APO format: [{"label": ..., "content": ...}]
            apo_feedback = [
                {"label": f.get("label", "positive"), "content": f.get("context", "")}
                for f in recent
            ]
            new_prompt = await apo.optimize_prompt(
                current_prompt=str(current_prompt or ""),
                feedback_batch=apo_feedback,
            )

            if not new_prompt:
                logger.info("APO returned None — insufficient feedback or error.")
                self._record_history_event(
                    {
                        "event": "optimize_persona",
                        "attempted": True,
                        "updated": False,
                        "reason": "apo_returned_none",
                        "duration_ms": int((time.time() - started_at) * 1000),
                    }
                )
                return False

            if new_prompt.startswith("```"):
                lines = new_prompt.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                new_prompt = "\n".join(lines).strip()

            gate_result = self._evaluate_publish_gate(
                current_prompt=str(current_prompt or ""),
                candidate_prompt=str(new_prompt or ""),
            )
            pre_publish_eval = self._evaluate_pre_publish(
                current_prompt=str(current_prompt or ""),
                candidate_prompt=str(new_prompt or ""),
                feedbacks=recent,
            )
            self._set_last_pre_publish_eval(pre_publish_eval)
            self._set_last_gate_result(gate_result)
            if not bool(gate_result.get("passed", False)):
                logger.warning(
                    "Evolution publish gate blocked update: reason=%s",
                    gate_result.get("reason", "blocked"),
                )
                self._record_history_event(
                    {
                        "event": "optimize_persona",
                        "attempted": True,
                        "updated": False,
                        "reason": str(gate_result.get("reason", "publish_gate_blocked")),
                        "gate": gate_result,
                        "pre_publish_eval": pre_publish_eval,
                        "duration_ms": int((time.time() - started_at) * 1000),
                    }
                )
                return False
            if not bool(pre_publish_eval.get("passed", False)) and bool(pre_publish_eval.get("block_on_fail", True)):
                logger.warning(
                    "Evolution pre-publish eval blocked update: reason=%s score=%.3f",
                    pre_publish_eval.get("reason", "failed"),
                    float(pre_publish_eval.get("score", 0.0)),
                )
                self._record_history_event(
                    {
                        "event": "optimize_persona",
                        "attempted": True,
                        "updated": False,
                        "reason": str(pre_publish_eval.get("reason", "pre_publish_eval_failed")),
                        "gate": gate_result,
                        "pre_publish_eval": pre_publish_eval,
                        "duration_ms": int((time.time() - started_at) * 1000),
                    }
                )
                return False

            config.set("personality.system_prompt", new_prompt)
            logger.info("System prompt updated via feedback optimization.")
            self._record_history_event(
                {
                    "event": "optimize_persona",
                    "attempted": True,
                    "updated": True,
                    "reason": "updated",
                    "gate": gate_result,
                    "pre_publish_eval": pre_publish_eval,
                    "feedback_total": len(recent),
                    "actionable_feedback": negative_count + correction_count,
                    "duration_ms": int((time.time() - started_at) * 1000),
                }
            )

            self._archive_feedback(feedbacks)
            return True

        except Exception as e:
            logger.error("Prompt optimization failed: %s", e)
            self._record_history_event(
                {
                    "event": "optimize_persona",
                    "attempted": True,
                    "updated": False,
                    "reason": "exception",
                    "error": str(e),
                    "duration_ms": int((time.time() - started_at) * 1000),
                }
            )
            return False

    async def maybe_auto_optimize(self, trigger: str = "feedback") -> Dict[str, Any]:
        """Conditionally run optimize_persona based on config thresholds."""
        settings = self._get_auto_optimize_settings()
        enabled = bool(settings.get("enabled", False))
        if not enabled:
            self._set_last_auto_reason("disabled")
            payload = {
                "enabled": False,
                "attempted": False,
                "updated": False,
                "reason": "disabled",
            }
            self._record_history_event({"event": "auto_optimize", **payload})
            return payload

        stats = self.get_feedback_stats()
        total = int(stats.get("total", 0))
        actionable = int(stats.get("negative", 0)) + int(stats.get("correction", 0))
        min_total = int(settings.get("min_feedback_total", 0))
        min_actionable = int(settings.get("min_actionable_feedback", 0))
        cooldown_seconds = int(settings.get("cooldown_seconds", 0))

        if total < min_total:
            self._set_last_auto_reason("insufficient_total_feedback")
            payload = {
                "enabled": True,
                "attempted": False,
                "updated": False,
                "reason": "insufficient_total_feedback",
                "total_feedback": total,
                "min_feedback_total": min_total,
                "actionable_feedback": actionable,
                "min_actionable_feedback": min_actionable,
            }
            self._record_history_event({"event": "auto_optimize", **payload})
            return payload
        if actionable < min_actionable:
            self._set_last_auto_reason("insufficient_actionable_feedback")
            payload = {
                "enabled": True,
                "attempted": False,
                "updated": False,
                "reason": "insufficient_actionable_feedback",
                "total_feedback": total,
                "min_feedback_total": min_total,
                "actionable_feedback": actionable,
                "min_actionable_feedback": min_actionable,
            }
            self._record_history_event({"event": "auto_optimize", **payload})
            return payload

        now = time.time()
        remaining = self._cooldown_remaining(now=now, cooldown_seconds=cooldown_seconds)
        if remaining > 0:
            self._set_last_auto_reason("cooldown_active")
            payload = {
                "enabled": True,
                "attempted": False,
                "updated": False,
                "reason": "cooldown_active",
                "cooldown_seconds": cooldown_seconds,
                "cooldown_remaining_seconds": remaining,
            }
            self._record_history_event({"event": "auto_optimize", **payload})
            return payload

        with self._state_lock:
            self._last_auto_attempt_ts = now
            self._auto_attempts += 1
        updated = await self.optimize_persona()
        with self._state_lock:
            if updated:
                self._last_auto_success_ts = time.time()
                self._auto_successes += 1
                self._last_auto_reason = "updated"
            else:
                self._last_auto_reason = "attempted_no_update"
        payload = {
            "enabled": True,
            "attempted": True,
            "updated": bool(updated),
            "reason": "updated" if updated else "attempted_no_update",
            "trigger": str(trigger or "feedback"),
        }
        self._record_history_event({"event": "auto_optimize", **payload})
        return payload

    def get_feedback_stats(self) -> Dict[str, Any]:
        """Return a summary of collected feedback."""
        feedbacks = self._load_feedback()
        stats: Dict[str, Any] = {
            "total": len(feedbacks),
            "positive": 0,
            "negative": 0,
            "correction": 0,
        }
        valid_labels = {"positive", "negative", "correction"}
        for f in feedbacks:
            label = f.get("label", "unknown")
            if label in valid_labels:
                stats[label] += 1
        return stats

    def get_auto_optimize_status(self) -> Dict[str, Any]:
        """Expose current auto-optimize state for diagnostics."""
        settings = self._get_auto_optimize_settings()
        cooldown_seconds = int(settings.get("cooldown_seconds", 0))
        now = time.time()
        with self._state_lock:
            last_attempt_ts = self._last_auto_attempt_ts
            last_success_ts = self._last_auto_success_ts
            attempts = self._auto_attempts
            successes = self._auto_successes
            last_reason = self._last_auto_reason
        return {
            "enabled": bool(settings.get("enabled", False)),
            "min_feedback_total": int(settings.get("min_feedback_total", 0)),
            "min_actionable_feedback": int(settings.get("min_actionable_feedback", 0)),
            "cooldown_seconds": cooldown_seconds,
            "cooldown_remaining_seconds": self._cooldown_remaining(
                now=now,
                cooldown_seconds=cooldown_seconds,
            ),
            "last_attempt_at": datetime.fromtimestamp(last_attempt_ts).isoformat() if last_attempt_ts else None,
            "last_success_at": datetime.fromtimestamp(last_success_ts).isoformat() if last_success_ts else None,
            "attempts": attempts,
            "successes": successes,
            "last_reason": last_reason,
            "publish_gate": self.get_publish_gate_status(),
            "pre_publish_eval": self.get_pre_publish_eval_status(),
            "recent_history": self.get_recent_history(limit=20),
        }

    def get_publish_gate_status(self) -> Dict[str, Any]:
        settings = self._get_publish_gate_settings()
        with self._state_lock:
            latest = dict(self._last_gate_result)
        return {
            "enabled": bool(settings.get("enabled", True)),
            "min_similarity": float(settings.get("min_similarity", 0.45)),
            "min_length_ratio": float(settings.get("min_length_ratio", 0.5)),
            "max_length_ratio": float(settings.get("max_length_ratio", 2.0)),
            "require_personality_name": bool(settings.get("require_personality_name", True)),
            "respect_release_gate": bool(settings.get("respect_release_gate", True)),
            "last": latest,
        }

    def get_pre_publish_eval_status(self) -> Dict[str, Any]:
        settings = self._get_pre_publish_eval_settings()
        with self._state_lock:
            latest = dict(self._last_pre_publish_eval)
        return {
            "enabled": bool(settings.get("enabled", True)),
            "min_score": float(settings.get("min_score", 0.55)),
            "block_on_fail": bool(settings.get("block_on_fail", True)),
            "set_release_gate_on_fail": bool(settings.get("set_release_gate_on_fail", True)),
            "last": latest,
        }

    def _summarize_feedback(self, feedbacks: List[Dict]) -> str:
        lines = []
        for f in feedbacks:
            label = f["label"]
            text = f.get("feedback", "")
            ctx = f.get("context", "")
            if text:
                lines.append(f"- [{label}] ({ctx}) {text}")
            else:
                lines.append(f"- [{label}] ({ctx})")
        return "\n".join(lines)

    def _load_feedback(self) -> List[Dict]:
        with self._file_lock:
            if not os.path.exists(self.feedback_path):
                return []
            try:
                with open(self.feedback_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load feedback file: %s", e)
                return []

    def _save_feedback(self, feedbacks: List[Dict]) -> None:
        with self._file_lock:
            try:
                with open(self.feedback_path, "w", encoding="utf-8") as f:
                    json.dump(feedbacks, f, ensure_ascii=False, indent=2)
            except OSError as e:
                logger.error("Failed to save feedback: %s", e)

    def _archive_feedback(self, feedbacks: List[Dict]) -> None:
        """Move consumed feedback to a dated archive file and clear active."""
        archive_dir = os.path.join(os.path.dirname(self.feedback_path), "feedback_archive")
        os.makedirs(archive_dir, exist_ok=True)
        archive_name = f"feedback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        archive_path = os.path.join(archive_dir, archive_name)
        try:
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(feedbacks, f, ensure_ascii=False, indent=2)
            self._save_feedback([])
            logger.info("Feedback archived to %s", archive_path)
        except OSError as e:
            logger.error("Failed to archive feedback: %s", e)

    def _get_auto_optimize_settings(self) -> Dict[str, Any]:
        raw = config.get("personality.evolution.auto_optimize", {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        enabled = bool(raw.get("enabled", False))
        min_feedback_total = self._to_int(raw.get("min_feedback_total"), default=6, minimum=1)
        min_actionable_feedback = self._to_int(raw.get("min_actionable_feedback"), default=3, minimum=0)
        cooldown_seconds = self._to_int(raw.get("cooldown_seconds"), default=1800, minimum=0)
        return {
            "enabled": enabled,
            "min_feedback_total": min_feedback_total,
            "min_actionable_feedback": min_actionable_feedback,
            "cooldown_seconds": cooldown_seconds,
        }

    def _cooldown_remaining(self, now: float, cooldown_seconds: int) -> int:
        if cooldown_seconds <= 0:
            return 0
        with self._state_lock:
            last_attempt_ts = self._last_auto_attempt_ts
        if not last_attempt_ts:
            return 0
        elapsed = now - last_attempt_ts
        if elapsed >= cooldown_seconds:
            return 0
        return max(0, int(cooldown_seconds - elapsed))

    def _set_last_auto_reason(self, reason: str) -> None:
        with self._state_lock:
            self._last_auto_reason = str(reason)

    def _set_last_gate_result(self, payload: Dict[str, Any]) -> None:
        with self._state_lock:
            self._last_gate_result = dict(payload)

    def _set_last_pre_publish_eval(self, payload: Dict[str, Any]) -> None:
        with self._state_lock:
            self._last_pre_publish_eval = dict(payload)

    def _get_publish_gate_settings(self) -> Dict[str, Any]:
        raw = config.get("personality.evolution.publish_gate", {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        min_similarity = self._to_float(raw.get("min_similarity"), default=0.45, minimum=0.0, maximum=1.0)
        min_length_ratio = self._to_float(raw.get("min_length_ratio"), default=0.5, minimum=0.1, maximum=10.0)
        max_length_ratio = self._to_float(raw.get("max_length_ratio"), default=2.0, minimum=0.1, maximum=20.0)
        if min_length_ratio > max_length_ratio:
            min_length_ratio, max_length_ratio = max_length_ratio, min_length_ratio
        return {
            "enabled": bool(raw.get("enabled", True)),
            "min_similarity": min_similarity,
            "min_length_ratio": min_length_ratio,
            "max_length_ratio": max_length_ratio,
            "require_personality_name": bool(raw.get("require_personality_name", True)),
            "respect_release_gate": bool(raw.get("respect_release_gate", True)),
        }

    def _evaluate_publish_gate(self, current_prompt: str, candidate_prompt: str) -> Dict[str, Any]:
        settings = self._get_publish_gate_settings()
        if not bool(settings.get("enabled", True)):
            return {
                "passed": True,
                "reason": "gate_disabled",
                "similarity": 1.0,
                "length_ratio": 1.0,
                "checked_at": datetime.now().isoformat(),
            }

        if bool(settings.get("respect_release_gate", True)) and self._is_release_gate_blocked():
            return {
                "passed": False,
                "reason": "release_gate_blocked",
                "similarity": 0.0,
                "length_ratio": 0.0,
                "checked_at": datetime.now().isoformat(),
            }

        old = str(current_prompt or "").strip()
        new = str(candidate_prompt or "").strip()
        if not new:
            return {
                "passed": False,
                "reason": "empty_candidate",
                "similarity": 0.0,
                "length_ratio": 0.0,
                "checked_at": datetime.now().isoformat(),
            }

        similarity = float(SequenceMatcher(a=old, b=new).ratio()) if old else 1.0
        old_len = max(1, len(old))
        new_len = len(new)
        length_ratio = float(new_len) / float(old_len)

        min_similarity = float(settings.get("min_similarity", 0.45))
        min_length_ratio = float(settings.get("min_length_ratio", 0.5))
        max_length_ratio = float(settings.get("max_length_ratio", 2.0))
        if similarity < min_similarity:
            return {
                "passed": False,
                "reason": "similarity_too_low",
                "similarity": similarity,
                "length_ratio": length_ratio,
                "checked_at": datetime.now().isoformat(),
            }
        if length_ratio < min_length_ratio:
            return {
                "passed": False,
                "reason": "candidate_too_short",
                "similarity": similarity,
                "length_ratio": length_ratio,
                "checked_at": datetime.now().isoformat(),
            }
        if length_ratio > max_length_ratio:
            return {
                "passed": False,
                "reason": "candidate_too_long",
                "similarity": similarity,
                "length_ratio": length_ratio,
                "checked_at": datetime.now().isoformat(),
            }

        if bool(settings.get("require_personality_name", True)):
            name = str(config.get("personality.name", "Gazer") or "Gazer").strip()
            if name:
                pattern = re.compile(re.escape(name), re.IGNORECASE)
                if not pattern.search(new):
                    return {
                        "passed": False,
                        "reason": "missing_personality_name",
                        "similarity": similarity,
                        "length_ratio": length_ratio,
                        "checked_at": datetime.now().isoformat(),
                    }

        return {
            "passed": True,
            "reason": "passed",
            "similarity": similarity,
            "length_ratio": length_ratio,
            "checked_at": datetime.now().isoformat(),
        }

    def _get_pre_publish_eval_settings(self) -> Dict[str, Any]:
        raw = config.get("personality.evolution.pre_publish_eval", {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "enabled": bool(raw.get("enabled", True)),
            "min_score": self._to_float(raw.get("min_score"), default=0.55, minimum=0.0, maximum=1.0),
            "block_on_fail": bool(raw.get("block_on_fail", True)),
            "set_release_gate_on_fail": bool(raw.get("set_release_gate_on_fail", True)),
        }

    def _evaluate_pre_publish(
        self,
        *,
        current_prompt: str,
        candidate_prompt: str,
        feedbacks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        settings = self._get_pre_publish_eval_settings()
        if not bool(settings.get("enabled", True)):
            return {
                "passed": True,
                "score": 1.0,
                "reason": "disabled",
                "block_on_fail": bool(settings.get("block_on_fail", True)),
                "checked_at": datetime.now().isoformat(),
            }

        old = str(current_prompt or "").strip()
        new = str(candidate_prompt or "").strip()
        if not new:
            score = 0.0
            passed = False
            reason = "empty_candidate"
        else:
            similarity = float(SequenceMatcher(a=old, b=new).ratio()) if old else 1.0
            old_len = max(1, len(old))
            len_ratio = float(len(new)) / float(old_len)
            len_stability = max(0.0, 1.0 - min(1.0, abs(len_ratio - 1.0)))
            actionable = sum(1 for item in feedbacks if str(item.get("label", "")).lower() in {"negative", "correction"})
            density = min(1.0, actionable / 5.0)
            score = round(0.6 * similarity + 0.25 * len_stability + 0.15 * density, 4)
            min_score = float(settings.get("min_score", 0.55))
            passed = score >= min_score
            reason = "passed" if passed else "score_below_threshold"

        payload = {
            "passed": bool(passed),
            "score": float(score),
            "reason": reason,
            "min_score": float(settings.get("min_score", 0.55)),
            "block_on_fail": bool(settings.get("block_on_fail", True)),
            "checked_at": datetime.now().isoformat(),
        }

        if not payload["passed"] and bool(settings.get("set_release_gate_on_fail", True)):
            try:
                EvalBenchmarkManager().set_release_gate_status(
                    blocked=True,
                    reason=f"evolution_pre_publish_eval:{payload['reason']}",
                    source="evolution.pre_publish_eval",
                    metadata={"score": payload["score"], "min_score": payload["min_score"]},
                )
            except Exception as exc:
                logger.debug("Failed to set release gate from pre-publish eval: %s", exc)
        return payload

    def _is_release_gate_blocked(self) -> bool:
        try:
            gate = EvalBenchmarkManager().get_release_gate_status()
            return bool((gate or {}).get("blocked", False))
        except Exception as exc:
            logger.debug("Release gate status check failed: %s", exc)
            return False

    def _record_history_event(self, payload: Dict[str, Any]) -> None:
        event = dict(payload or {})
        event.setdefault("timestamp", datetime.now().isoformat())
        max_records = self._to_int(config.get("personality.evolution.history.max_records", 300), default=300, minimum=50)
        with self._file_lock:
            records = self._load_history_records_locked()
            records.append(event)
            if len(records) > max_records:
                records = records[-max_records:]
            try:
                with open(self.history_path, "w", encoding="utf-8") as f:
                    for item in records:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.warning("Failed to persist evolution history: %s", exc)

    def get_recent_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        max_limit = max(1, min(int(limit or 20), 200))
        with self._file_lock:
            records = self._load_history_records_locked()
        return records[-max_limit:]

    def clear_history(self) -> int:
        with self._file_lock:
            records = self._load_history_records_locked()
            count = len(records)
            try:
                with open(self.history_path, "w", encoding="utf-8") as f:
                    f.write("")
            except OSError:
                pass
        return count

    def get_history_summary(self) -> Dict[str, Any]:
        with self._file_lock:
            records = self._load_history_records_locked()
        total = len(records)
        by_event: Dict[str, int] = {}
        by_reason: Dict[str, int] = {}
        updates = 0
        for item in records:
            event = str(item.get("event", "unknown"))
            reason = str(item.get("reason", "unknown"))
            by_event[event] = by_event.get(event, 0) + 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
            if bool(item.get("updated", False)):
                updates += 1
        return {
            "total": total,
            "updated": updates,
            "not_updated": max(0, total - updates),
            "by_event": by_event,
            "by_reason": by_reason,
        }

    def _load_history_records_locked(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.history_path):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = str(line or "").strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    if isinstance(item, dict):
                        out.append(item)
        except Exception:
            return []
        return out

    @staticmethod
    def _to_int(value: Any, default: int, minimum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, parsed)

    @staticmethod
    def _to_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        if parsed < minimum:
            parsed = minimum
        if parsed > maximum:
            parsed = maximum
        return parsed


# Lazy singleton
_evolution: Optional["GazerEvolution"] = None


def get_evolution() -> "GazerEvolution":
    """Return the singleton GazerEvolution, creating it on first access."""
    global _evolution
    if _evolution is None:
        _evolution = GazerEvolution()
    return _evolution
