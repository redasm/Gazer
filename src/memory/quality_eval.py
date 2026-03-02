"""Quality evaluation helpers for OpenViking memory artifacts."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_jsonl_tail(path: Path, limit: int = 5000) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 20000))
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-safe_limit:]
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        row = line.strip()
        if not row:
            continue
        try:
            payload = json.loads(row)
        except Exception:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _parse_time_like(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0
    text = str(raw or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return 0.0


def _split_rows_by_dual_windows(
    rows: List[Dict[str, Any]],
    *,
    window_days: int,
    ts_getter: Any,
) -> Dict[str, Any]:
    now = time.time()
    window_seconds = float(max(1, int(window_days or 7)) * 86400)
    current_start = now - window_seconds
    previous_start = current_start - window_seconds
    current: List[Dict[str, Any]] = []
    previous: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _parse_time_like(ts_getter(row))
        if current_start <= ts <= now:
            current.append(row)
        elif previous_start <= ts < current_start:
            previous.append(row)
    return {
        "now": now,
        "window_days": max(1, int(window_days or 7)),
        "current_start": current_start,
        "previous_start": previous_start,
        "current": current,
        "previous": previous,
    }


def _tokenize(text: str) -> List[str]:
    raw = str(text or "").lower().strip()
    if not raw:
        return []

    tokens: List[str] = []
    words = [w for w in re.split(r"[^a-z0-9\u4e00-\u9fff]+", raw) if w]
    for word in words:
        if len(word) >= 2 and word not in tokens:
            tokens.append(word)

    cjk_text = "".join(ch for ch in raw if "\u4e00" <= ch <= "\u9fff")
    if len(cjk_text) >= 2:
        for idx in range(len(cjk_text) - 1):
            bigram = cjk_text[idx : idx + 2]
            if bigram not in tokens:
                tokens.append(bigram)
    return tokens[:128]


def _jaccard_similarity(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    inter = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    if union <= 0:
        return 0.0
    return round(inter / union, 4)


def _load_long_term_memory(backend_dir: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    long_term_dir = backend_dir / "long_term"
    if not long_term_dir.is_dir():
        return {}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for path in sorted(long_term_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            continue
        category_bucket: Dict[str, Dict[str, Any]] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                category_bucket[str(key)] = value
        out[path.stem] = category_bucket
    return out


def _long_term_item_count(long_term: Dict[str, Dict[str, Dict[str, Any]]]) -> int:
    return int(sum(len(items) for items in long_term.values()))


def _summarize_window(
    *,
    now_ts: float,
    events: List[Dict[str, Any]],
    decisions: List[Dict[str, Any]],
    long_term: Dict[str, Dict[str, Dict[str, Any]]],
    stale_days: int,
    include_samples: bool,
    sample_limit: int,
) -> Dict[str, Any]:
    event_total = len(events)
    decision_total = len(decisions)
    accepted_decisions = {"CREATE", "MERGE", "UPDATE"}

    event_by_source_ts: Dict[str, Dict[str, Any]] = {}
    for event in events:
        source_ts = str(event.get("timestamp", event.get("date", ""))).strip()
        if source_ts and source_ts not in event_by_source_ts:
            event_by_source_ts[source_ts] = event

    accepted_total = 0
    source_bound_total = 0
    bound_event_timestamps: set[str] = set()
    overlap_scores: List[float] = []
    overlap_samples: List[Dict[str, Any]] = []
    key_decisions: Dict[str, List[str]] = {}
    key_rows: Dict[str, List[Dict[str, Any]]] = {}
    churn_total = 0
    duplicate_hits = 0

    for decision in decisions:
        marker = (
            f"{str(decision.get('category', '')).strip()}::"
            f"{str(decision.get('key', '')).strip()}"
        )
        key_decisions.setdefault(marker, []).append(
            str(decision.get("decision", "")).strip().upper()
        )
        key_rows.setdefault(marker, []).append(decision)

    for marker, rows in key_rows.items():
        if len(rows) > 1:
            duplicate_hits += len(rows) - 1

    for item in decisions:
        decision = str(item.get("decision", "")).strip().upper()
        if decision in {"MERGE", "UPDATE"}:
            churn_total += 1

        source_ts = str(item.get("source_timestamp", item.get("timestamp", ""))).strip()
        source_event = event_by_source_ts.get(source_ts)
        if source_event:
            source_bound_total += 1
            if source_ts:
                bound_event_timestamps.add(source_ts)

        if decision not in accepted_decisions:
            continue
        accepted_total += 1

        category = str(item.get("category", "")).strip()
        key = str(item.get("key", "")).strip()
        stored = long_term.get(category, {}).get(key, {})
        stored_content = str(stored.get("content", "")).strip()
        source_content = str((source_event or {}).get("content", "")).strip()
        overlap = _jaccard_similarity(source_content, stored_content)
        if source_content and stored_content:
            overlap_scores.append(overlap)
            overlap_samples.append(
                {
                    "category": category,
                    "key": key,
                    "decision": decision,
                    "source_timestamp": source_ts,
                    "overlap": overlap,
                }
            )

    relevance_yield = round(accepted_total / max(1, event_total), 4)
    relevance_yield_capped = round(min(1.0, relevance_yield), 4)
    decision_acceptance_rate = round(accepted_total / max(1, decision_total), 4)
    source_binding_rate = round(source_bound_total / max(1, decision_total), 4)
    event_binding_rate = round(len(bound_event_timestamps) / max(1, event_total), 4)
    decision_per_event_rate = round(decision_total / max(1, event_total), 4)
    alignment_avg = (
        round(sum(overlap_scores) / len(overlap_scores), 4) if overlap_scores else 0.0
    )
    relevance_score = round(
        min(
            1.0,
            (0.30 * relevance_yield_capped)
            + (0.30 * source_binding_rate)
            + (0.20 * event_binding_rate)
            + (0.20 * alignment_avg),
        ),
        4,
    )

    ages_days: List[float] = []
    stale_cutoff = max(1, int(stale_days or 14))
    stale_samples: List[Dict[str, Any]] = []
    for event in events:
        ts = _parse_time_like(event.get("timestamp", event.get("date", "")))
        if ts <= 0:
            continue
        age_days = max(0.0, (now_ts - ts) / 86400.0)
        ages_days.append(age_days)
        if age_days >= stale_cutoff:
            stale_samples.append(
                {
                    "timestamp": str(event.get("timestamp", event.get("date", ""))),
                    "sender": str(event.get("sender", "")),
                    "age_days": round(age_days, 3),
                    "content_preview": str(event.get("content", ""))[:120],
                }
            )
    ages_days_sorted = sorted(ages_days)
    if ages_days_sorted:
        middle = len(ages_days_sorted) // 2
        median_age = (
            ages_days_sorted[middle]
            if len(ages_days_sorted) % 2 == 1
            else (ages_days_sorted[middle - 1] + ages_days_sorted[middle]) / 2.0
        )
    else:
        median_age = 0.0
    stale_ratio = round(
        len([age for age in ages_days if age >= stale_cutoff]) / max(1, len(ages_days)),
        4,
    )
    fresh_ratio = round(
        len([age for age in ages_days if age <= 2.0]) / max(1, len(ages_days)),
        4,
    )
    timeliness_score = round(
        max(
            0.0,
            min(
                1.0,
                1.0
                - (0.7 * stale_ratio)
                - (0.3 * min(1.0, median_age / max(1.0, float(stale_cutoff)))),
            ),
        ),
        4,
    )

    conflicting_keys: List[Dict[str, Any]] = []
    for marker, decisions_for_key in key_decisions.items():
        labels = {label for label in decisions_for_key if label}
        has_skip = "SKIP" in labels
        accepted_count = len(labels & accepted_decisions)
        if accepted_count >= 2 or (has_skip and accepted_count >= 1):
            conflicting_keys.append(
                {
                    "key": marker,
                    "decisions": sorted(labels),
                    "event_count": len(decisions_for_key),
                }
            )
    conflict_rate = round(
        len(conflicting_keys) / max(1, len([key for key in key_decisions.keys() if key != "::"])),
        4,
    )
    duplicate_key_ratio = round(duplicate_hits / max(1, decision_total), 4)
    churn_ratio = round(churn_total / max(1, accepted_total), 4)
    instability = min(
        1.0,
        (0.45 * conflict_rate) + (0.30 * duplicate_key_ratio) + (0.25 * churn_ratio),
    )
    stability_score = round(max(0.0, 1.0 - instability), 4)

    quality_score = round(
        (0.40 * relevance_score) + (0.30 * timeliness_score) + (0.30 * stability_score),
        4,
    )
    quality_level = "healthy"
    if quality_score < 0.50:
        quality_level = "critical"
    elif quality_score < 0.75:
        quality_level = "warning"

    top_issues: List[Dict[str, Any]] = []
    if relevance_score < 0.60:
        top_issues.append(
            {
                "code": "low_relevance",
                "severity": "high" if relevance_score < 0.45 else "medium",
                "detail": (
                    f"relevance_score={relevance_score}, "
                    f"yield_raw={relevance_yield}, yield_capped={relevance_yield_capped}, "
                    f"source_binding={source_binding_rate}, event_binding={event_binding_rate}"
                ),
            }
        )
    if stale_ratio > 0.40:
        top_issues.append(
            {
                "code": "stale_memory",
                "severity": "high" if stale_ratio > 0.65 else "medium",
                "detail": f"stale_ratio={stale_ratio}, median_age_days={round(median_age, 3)}",
            }
        )
    if conflict_rate > 0.25:
        top_issues.append(
            {
                "code": "high_conflict_rate",
                "severity": "high" if conflict_rate > 0.45 else "medium",
                "detail": (
                    f"conflict_rate={conflict_rate}, duplicate_key_ratio={duplicate_key_ratio}, "
                    f"churn_ratio={churn_ratio}"
                ),
            }
        )

    out = {
        "counts": {
            "events": event_total,
            "decisions": decision_total,
            "accepted_decisions": accepted_total,
            "unique_decision_keys": len([key for key in key_decisions.keys() if key != "::"]),
        },
        "scores": {
            "quality_score": quality_score,
            "quality_level": quality_level,
            "relevance_score": relevance_score,
            "timeliness_score": timeliness_score,
            "stability_score": stability_score,
        },
        "metrics": {
            "relevance": {
                "yield_rate": relevance_yield,
                "yield_rate_capped": relevance_yield_capped,
                "decision_acceptance_rate": decision_acceptance_rate,
                "source_binding_rate": source_binding_rate,
                "event_binding_rate": event_binding_rate,
                "decision_per_event_rate": decision_per_event_rate,
                "alignment_avg": alignment_avg,
            },
            "timeliness": {
                "stale_days_threshold": stale_cutoff,
                "median_age_days": round(median_age, 3),
                "fresh_ratio": fresh_ratio,
                "stale_ratio": stale_ratio,
            },
            "conflict": {
                "conflict_rate": conflict_rate,
                "duplicate_key_ratio": duplicate_key_ratio,
                "churn_ratio": churn_ratio,
            },
        },
        "top_issues": top_issues,
    }

    if include_samples:
        overlap_samples.sort(key=lambda item: float(item.get("overlap", 1.0)))
        stale_samples.sort(key=lambda item: float(item.get("age_days", 0.0)), reverse=True)
        conflicting_keys.sort(key=lambda item: int(item.get("event_count", 0)), reverse=True)
        out["samples"] = {
            "low_alignment": overlap_samples[:sample_limit],
            "stale_events": stale_samples[:sample_limit],
            "conflicting_keys": conflicting_keys[:sample_limit],
        }
    return out


def build_memory_quality_report(
    backend_dir: Path | str,
    *,
    window_days: int = 7,
    stale_days: int = 14,
    limit: int = 5000,
    include_samples: bool = False,
    sample_limit: int = 10,
) -> Dict[str, Any]:
    backend_root = Path(backend_dir)
    window = max(1, min(int(window_days or 7), 30))
    safe_stale_days = max(1, min(int(stale_days or 14), 365))
    safe_sample_limit = max(1, min(int(sample_limit or 10), 50))

    events = _read_jsonl_tail(backend_root / "memory_events.jsonl", limit=limit)
    decisions = [
        row
        for row in _read_jsonl_tail(backend_root / "extraction_decisions.jsonl", limit=limit)
        if str(row.get("kind", "")).strip() == "memory_extraction"
    ]
    long_term = _load_long_term_memory(backend_root)

    event_windows = _split_rows_by_dual_windows(
        events,
        window_days=window,
        ts_getter=lambda row: row.get("timestamp", row.get("date", "")),
    )
    decision_windows = _split_rows_by_dual_windows(
        decisions,
        window_days=window,
        ts_getter=lambda row: row.get("timestamp", row.get("source_timestamp", "")),
    )

    current = _summarize_window(
        now_ts=event_windows["now"],
        events=event_windows["current"],
        decisions=decision_windows["current"],
        long_term=long_term,
        stale_days=safe_stale_days,
        include_samples=include_samples,
        sample_limit=safe_sample_limit,
    )
    previous = _summarize_window(
        now_ts=event_windows["now"],
        events=event_windows["previous"],
        decisions=decision_windows["previous"],
        long_term=long_term,
        stale_days=safe_stale_days,
        include_samples=False,
        sample_limit=safe_sample_limit,
    )

    current_quality = float(current.get("scores", {}).get("quality_score", 0.0))
    previous_quality = float(previous.get("scores", {}).get("quality_score", 0.0))
    quality_delta = round(current_quality - previous_quality, 4)
    previous_counts = previous.get("counts", {}) if isinstance(previous.get("counts"), dict) else {}
    baseline_events = int(previous_counts.get("events", 0) or 0)
    baseline_decisions = int(previous_counts.get("decisions", 0) or 0)
    baseline_sufficient = baseline_events >= 20 and baseline_decisions >= 20
    if quality_delta > 0.02:
        direction = "improving"
    elif quality_delta < -0.02:
        direction = "worse"
    else:
        direction = "stable"

    result: Dict[str, Any] = {
        "status": "ok",
        "generated_at": event_windows["now"],
        "window_days": window,
        "backend_dir": str(backend_root),
        "counts": {
            "long_term_total": _long_term_item_count(long_term),
            "long_term_by_category": {name: len(items) for name, items in sorted(long_term.items())},
            "events_total_loaded": len(events),
            "decisions_total_loaded": len(decisions),
        },
        "current_window": current,
        "previous_window": previous,
        "trend": {
            "quality_score_delta": quality_delta,
            "direction": direction,
            "baseline_sufficient": baseline_sufficient,
            "baseline_events": baseline_events,
            "baseline_decisions": baseline_decisions,
            "interpretation": "normal" if baseline_sufficient else "limited_baseline",
        },
    }
    return result
