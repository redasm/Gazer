"""Automatic eval benchmark dataset builder.

Builds eval sample sets from training-bridge exports without manual annotation.
Three strategies are combined:

  1. Positive cases  — trajectories with high tool-success rates and positive feedback.
  2. Negative cases  — trajectories with terminal errors or failed gate evaluations.
  3. Tool-contract cases — (input, expected_status) pairs inferred from tool-result events.

Also provides ``build_recall_query_set_from_skills`` which mirrors the hermes-style
automatic query-set generation for memory recall regression tests.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_\u4e00-\u9fff]+")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tok(text: str) -> List[str]:
    """Return lower-cased token list from text."""
    return [t.lower() for t in _TOKEN_RE.findall(str(text or ""))]


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reward(sample: Dict[str, Any]) -> Dict[str, Any]:
    rp = sample.get("reward_proxy")
    return rp if isinstance(rp, dict) else {}


def _state(sample: Dict[str, Any]) -> Dict[str, Any]:
    st = sample.get("state")
    return st if isinstance(st, dict) else {}


def _action(sample: Dict[str, Any]) -> Dict[str, Any]:
    ac = sample.get("action")
    return ac if isinstance(ac, dict) else {}


def _tool_result(sample: Dict[str, Any]) -> Dict[str, Any]:
    tr = sample.get("tool_result")
    return tr if isinstance(tr, dict) else {}


def _tool_events(sample: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = _tool_result(sample).get("events")
    return list(events) if isinstance(events, list) else []


def _auto_sample_id() -> str:
    return f"auto_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class DatasetAutoBuilder:
    """Builds eval benchmark datasets automatically from bridge-export samples.

    All ``build_*`` methods accept a list of bridge-export samples (the format
    produced by ``TrainingBridgeManager.get_export(include_samples=True)``).
    They return lists of dicts compatible with ``EvalBenchmarkManager.build_dataset()``.
    """

    # --- Strategy 1: positive cases ---

    def build_positive_from_trajectories(
        self,
        samples: List[Dict[str, Any]],
        *,
        min_tool_success_rate: float = 0.75,
        min_feedback_score: float = 0.0,
        max_samples: int = 100,
    ) -> List[Dict[str, Any]]:
        """Extract positive-labelled eval cases from high-quality trajectories."""
        results: List[Dict[str, Any]] = []
        for item in samples:
            if not isinstance(item, dict):
                continue
            rp = _reward(item)
            tsr = _safe_float(rp.get("tool_success_rate"))
            fb = _safe_float(rp.get("feedback_score")) or 0.0
            has_terminal = bool(rp.get("has_terminal_error", False))
            if has_terminal:
                continue
            if tsr is not None and tsr < min_tool_success_rate:
                continue
            if fb < min_feedback_score:
                continue
            st = _state(item)
            ac = _action(item)
            results.append(
                {
                    "run_id": str(item.get("run_id", _auto_sample_id())),
                    "label": "positive",
                    "user_content": str(st.get("user_content", "")),
                    "assistant_output": str(ac.get("assistant_output", "")),
                    "feedback": str(rp.get("feedback_text", "")),
                    "context": str(st.get("channel", "")),
                    "status": str(st.get("final_status", "done")),
                }
            )
            if len(results) >= max_samples:
                break
        return results

    # --- Strategy 2: negative cases ---

    def build_negative_from_failures(
        self,
        samples: List[Dict[str, Any]],
        *,
        max_samples: int = 50,
    ) -> List[Dict[str, Any]]:
        """Extract negative-labelled eval cases from failed trajectories."""
        results: List[Dict[str, Any]] = []
        for item in samples:
            if not isinstance(item, dict):
                continue
            rp = _reward(item)
            has_terminal = bool(rp.get("has_terminal_error", False))
            eval_passed = rp.get("eval_passed")
            feedback_score = _safe_float(rp.get("feedback_score")) or 0.0
            # Include as negative if any failure signal is present
            is_negative = (
                has_terminal
                or eval_passed is False
                or feedback_score < -0.5
            )
            if not is_negative:
                continue
            st = _state(item)
            ac = _action(item)
            results.append(
                {
                    "run_id": str(item.get("run_id", _auto_sample_id())),
                    "label": "negative",
                    "user_content": str(st.get("user_content", "")),
                    "assistant_output": str(ac.get("assistant_output", "")),
                    "feedback": str(rp.get("feedback_text", "")),
                    "context": str(st.get("channel", "")),
                    "status": str(st.get("final_status", "error")),
                }
            )
            if len(results) >= max_samples:
                break
        return results

    # --- Strategy 3: tool-contract cases ---

    def build_tool_contract_cases(
        self,
        samples: List[Dict[str, Any]],
        *,
        tool_names: Optional[List[str]] = None,
        min_occurrences: int = 2,
        max_samples: int = 80,
    ) -> List[Dict[str, Any]]:
        """Infer (user_input, expected_tool_status) contract cases from tool events.

        Groups tool-result events by tool name and final_status. For any
        (tool, status) pair seen >= min_occurrences times, emits an unlabelled
        eval case asserting that pattern should hold.
        """
        # Collect: tool -> {status -> count, sample_ids}
        tool_filter = {t.strip().lower() for t in (tool_names or [])} if tool_names else None
        tool_patterns: Dict[str, Dict[str, List[str]]] = {}
        for item in samples:
            if not isinstance(item, dict):
                continue
            run_id = str(item.get("run_id", ""))
            for ev in _tool_events(item):
                tool = str(ev.get("tool", "")).strip().lower()
                status = str(ev.get("status", "")).strip().lower()
                if not tool or not status:
                    continue
                if tool_filter and tool not in tool_filter:
                    continue
                tool_patterns.setdefault(tool, {}).setdefault(status, []).append(run_id)

        results: List[Dict[str, Any]] = []
        for tool, status_map in sorted(tool_patterns.items()):
            for status, run_ids in sorted(status_map.items()):
                if len(run_ids) < min_occurrences:
                    continue
                label = "positive" if status in {"ok", "success"} else "negative"
                # Use representative run_id
                rep_run = run_ids[0]
                results.append(
                    {
                        "run_id": f"contract_{tool}_{status}_{rep_run[:8]}",
                        "label": label,
                        "user_content": f"[tool_contract] tool={tool} expected_status={status}",
                        "assistant_output": f"tool={tool} status={status} occurrences={len(run_ids)}",
                        "feedback": f"contract: {tool} should return {status}",
                        "context": "tool_contract",
                        "status": status,
                    }
                )
                if len(results) >= max_samples:
                    return results
        return results

    # --- Strategy 4: recall query set (hermes-style) ---

    def build_recall_query_set_from_skills(
        self,
        skill_descriptions: Dict[str, str],
        *,
        queries_per_skill: int = 3,
        llm_caller: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        """Generate memory recall query sets from skill/tool descriptions.

        With ``llm_caller`` provided, uses LLM to generate natural-language queries.
        Without LLM, falls back to keyword extraction from the description text.

        The output format is compatible with ``recall_regression.py``'s
        ``recall_query_set.json``.

        Note: ``llm_caller`` must be a **synchronous** callable with signature
        ``(prompt: str) -> str``.  Passing an async coroutine function will cause
        the JSON parse to fail silently, falling back to keyword extraction.
        """
        results: List[Dict[str, Any]] = []
        for skill_name, description in skill_descriptions.items():
            if not description:
                continue
            queries = self._generate_queries_for_skill(
                skill_name=skill_name,
                description=description,
                count=queries_per_skill,
                llm_caller=llm_caller,
            )
            for q in queries:
                results.append(q)
        return results

    def _generate_queries_for_skill(
        self,
        *,
        skill_name: str,
        description: str,
        count: int,
        llm_caller: Optional[Callable],
    ) -> List[Dict[str, Any]]:
        if llm_caller is not None:
            return self._llm_queries(skill_name, description, count, llm_caller)
        return self._keyword_queries(skill_name, description, count)

    @staticmethod
    def _keyword_queries(
        skill_name: str,
        description: str,
        count: int,
    ) -> List[Dict[str, Any]]:
        """Keyword-extraction fallback: derive queries from description tokens."""
        tokens = _tok(description)
        # Filter stopwords and short tokens
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "this", "that", "to", "of", "in", "on", "at", "for", "and",
            "or", "but", "not", "with", "by", "as", "it", "its",
        }
        keywords = [t for t in tokens if t not in stopwords and len(t) >= 3]
        # Deduplicate while preserving order
        seen: set = set()
        unique_kw: List[str] = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_kw.append(kw)

        queries: List[Dict[str, Any]] = []
        safe_name = str(skill_name).replace("/", "_").replace(" ", "_")
        for i in range(min(count, max(1, len(unique_kw) // 2))):
            # Build a 2-3 keyword query
            window = unique_kw[i * 2: i * 2 + 3]
            if not window:
                break
            q_text = f"{skill_name} {' '.join(window)}"
            queries.append(
                {
                    "id": f"q_{safe_name}_{i}",
                    "query": q_text,
                    "expected_terms": window,
                    "expected_category": "skills",
                    "skill": skill_name,
                }
            )
        return queries

    @staticmethod
    def _llm_queries(
        skill_name: str,
        description: str,
        count: int,
        llm_caller: Callable,
    ) -> List[Dict[str, Any]]:
        """Generate queries via LLM meta-prompt."""
        prompt = (
            f"Given this tool/skill name and description, generate {count} concise "
            f"natural-language queries a user might ask that would require recalling "
            f"this skill. Return a JSON array of objects with keys: "
            f'"query" (string) and "expected_terms" (array of 2-4 key strings).\n\n'
            f"Skill: {skill_name}\nDescription: {description}\n\nJSON:"
        )
        try:
            import asyncio
            import json
            raw = llm_caller(prompt)
            # Guard against accidentally passing an async callable
            if asyncio.iscoroutine(raw):
                raw.close()  # prevent ResourceWarning
                raise TypeError("llm_caller returned a coroutine; must be synchronous")
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                return []
            safe_name = str(skill_name).replace("/", "_").replace(" ", "_")
            results: List[Dict[str, Any]] = []
            for i, item in enumerate(parsed[:count]):
                if not isinstance(item, dict):
                    continue
                q = str(item.get("query", "")).strip()
                terms_raw = item.get("expected_terms", [])
                terms = [str(t).strip() for t in terms_raw if str(t).strip()] if isinstance(terms_raw, list) else []
                if q and terms:
                    results.append(
                        {
                            "id": f"q_{safe_name}_{i}",
                            "query": q,
                            "expected_terms": terms,
                            "expected_category": "skills",
                            "skill": skill_name,
                        }
                    )
            return results
        except Exception:
            # Fallback to keyword approach on any LLM/parse error
            return DatasetAutoBuilder._keyword_queries(skill_name, description, count)

    # --- Combined entry point ---

    def build_combined_dataset(
        self,
        samples: List[Dict[str, Any]],
        *,
        include_positive: bool = True,
        include_negative: bool = True,
        include_tool_contracts: bool = True,
        positive_limit: int = 100,
        negative_limit: int = 50,
        contract_limit: int = 80,
    ) -> Dict[str, Any]:
        """Build a combined dataset from all enabled strategies.

        Returns a dict with ``samples`` (list) and ``meta`` (counts per strategy).
        """
        all_samples: List[Dict[str, Any]] = []
        meta: Dict[str, int] = {"positive": 0, "negative": 0, "contract": 0}

        if include_positive:
            pos = self.build_positive_from_trajectories(
                samples, max_samples=positive_limit
            )
            all_samples.extend(pos)
            meta["positive"] = len(pos)

        if include_negative:
            neg = self.build_negative_from_failures(
                samples, max_samples=negative_limit
            )
            all_samples.extend(neg)
            meta["negative"] = len(neg)

        if include_tool_contracts:
            contracts = self.build_tool_contract_cases(
                samples, max_samples=contract_limit
            )
            all_samples.extend(contracts)
            meta["contract"] = len(contracts)

        return {
            "samples": all_samples,
            "total": len(all_samples),
            "meta": meta,
        }
