"""Recall regression report for OpenViking memory retrieval quality."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_QUERY_SET_FILENAME = "recall_query_set.json"
DEFAULT_RUN_HISTORY_FILENAME = "recall_regression_runs.jsonl"


def _read_jsonl_tail(path: Path, limit: int = 5000) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 20000))
    if not path.is_file():
        return []
    try:
        rows = path.read_text(encoding="utf-8").splitlines()[-safe_limit:]
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        line = row.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
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


def _tokenize(text: str) -> List[str]:
    raw = str(text or "").lower().strip()
    if not raw:
        return []
    tokens: List[str] = []
    words = [word for word in re.split(r"[^a-z0-9\u4e00-\u9fff]+", raw) if word]
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


def _jaccard_score(query_tokens: List[str], candidate_tokens: List[str]) -> float:
    qset = set(query_tokens)
    cset = set(candidate_tokens)
    if not qset or not cset:
        return 0.0
    inter = len(qset & cset)
    union = len(qset | cset)
    if union <= 0:
        return 0.0
    return round(inter / union, 4)


def _safe_preview(text: str, max_chars: int = 140) -> str:
    content = str(text or "").strip()
    if len(content) <= max_chars:
        return content
    return content[: max(0, max_chars - 3)].rstrip() + "..."


def _load_candidates(backend_dir: Path) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    events = _read_jsonl_tail(backend_dir / "memory_events.jsonl", limit=10000)
    for item in events:
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        candidates.append(
            {
                "source": "event",
                "category": str(item.get("category", "events")).strip().lower() or "events",
                "key": str(item.get("timestamp", "")),
                "content": content,
                "sender": str(item.get("sender", "")),
                "timestamp": str(item.get("timestamp", item.get("date", ""))),
            }
        )

    long_term_dir = backend_dir / "long_term"
    if long_term_dir.is_dir():
        for path in sorted(long_term_dir.glob("*.json")):
            category = str(path.stem).strip().lower()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                continue
            for key, value in payload.items():
                if not isinstance(value, dict):
                    continue
                content = str(value.get("content", "")).strip()
                if not content:
                    continue
                candidates.append(
                    {
                        "source": "long_term",
                        "category": category or "unknown",
                        "key": str(key),
                        "content": content,
                        "sender": str(value.get("sender", "")),
                        "timestamp": str(value.get("source_timestamp", value.get("updated_at", ""))),
                    }
                )
    return candidates


def _normalize_query_set(query_set: Any) -> List[Dict[str, Any]]:
    if not isinstance(query_set, list):
        return []
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(query_set):
        if isinstance(item, str):
            query = item.strip()
            expected_terms: List[str] = []
            expected_category = ""
            query_id = f"q_{idx + 1}"
        elif isinstance(item, dict):
            query = str(item.get("query", "")).strip()
            terms_raw = item.get("expected_terms", item.get("expected", []))
            expected_terms = []
            if isinstance(terms_raw, list):
                expected_terms = [str(term).strip().lower() for term in terms_raw if str(term).strip()]
            expected_category = str(item.get("expected_category", "")).strip().lower()
            query_id = str(item.get("id", f"q_{idx + 1}")).strip() or f"q_{idx + 1}"
        else:
            continue
        if not query:
            continue
        out.append(
            {
                "id": query_id,
                "query": query,
                "expected_terms": expected_terms[:20],
                "expected_category": expected_category,
            }
        )
    return out


def _load_query_set(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return _normalize_query_set(payload)


def _candidate_matches_expectation(candidate: Dict[str, Any], query_item: Dict[str, Any]) -> bool:
    content = str(candidate.get("content", "")).lower()
    expected_terms = [
        str(term).strip().lower()
        for term in (query_item.get("expected_terms", []) if isinstance(query_item, dict) else [])
        if str(term).strip()
    ]
    expected_category = str(query_item.get("expected_category", "")).strip().lower()
    if expected_category:
        category = str(candidate.get("category", "")).strip().lower()
        if category != expected_category:
            return False
    if expected_terms:
        return any(term in content for term in expected_terms)
    return True


def _evaluate_query(
    query_item: Dict[str, Any],
    *,
    candidates: List[Dict[str, Any]],
    top_k: int,
    min_match_score: float,
) -> Dict[str, Any]:
    query = str(query_item.get("query", "")).strip()
    query_tokens = _tokenize(query)
    scored: List[Dict[str, Any]] = []
    for candidate in candidates:
        content = str(candidate.get("content", "")).strip()
        if not content:
            continue
        score = _jaccard_score(query_tokens, _tokenize(content))
        if score <= 0:
            continue
        scored.append(
            {
                "score": score,
                "source": str(candidate.get("source", "")),
                "category": str(candidate.get("category", "")),
                "key": str(candidate.get("key", "")),
                "timestamp": str(candidate.get("timestamp", "")),
                "sender": str(candidate.get("sender", "")),
                "preview": _safe_preview(content, max_chars=140),
                "content": content,
            }
        )
    scored.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    top_hits = scored[: max(1, int(top_k))]
    matched_hits = [item for item in top_hits if float(item.get("score", 0.0)) >= float(min_match_score)]
    recall_hit = bool(matched_hits)
    expectation_hit = any(_candidate_matches_expectation(item, query_item) for item in matched_hits)
    precision_hit = recall_hit and expectation_hit
    return {
        "id": str(query_item.get("id", "")),
        "query": query,
        "expected_terms": list(query_item.get("expected_terms", [])),
        "expected_category": str(query_item.get("expected_category", "")),
        "top_hits": [
            {
                "score": item.get("score", 0.0),
                "source": item.get("source", ""),
                "category": item.get("category", ""),
                "key": item.get("key", ""),
                "timestamp": item.get("timestamp", ""),
                "sender": item.get("sender", ""),
                "preview": item.get("preview", ""),
            }
            for item in top_hits
        ],
        "recall_hit": recall_hit,
        "precision_hit": precision_hit,
        "expectation_hit": expectation_hit,
    }


def _build_alerts(
    *,
    precision_proxy: float,
    recall_proxy: float,
    quality_score: float,
    quality_delta: float,
    min_precision_proxy: float,
    min_recall_proxy: float,
    warning_drop: float,
    critical_drop: float,
) -> Dict[str, Any]:
    alerts: List[Dict[str, Any]] = []
    quality_drop = max(0.0, -float(quality_delta))

    if precision_proxy < min_precision_proxy:
        alerts.append(
            {
                "code": "precision_proxy_below_threshold",
                "severity": "critical" if precision_proxy < max(0.0, min_precision_proxy - 0.2) else "warning",
                "detail": (
                    f"precision_proxy={round(precision_proxy, 4)} "
                    f"< min_precision_proxy={round(min_precision_proxy, 4)}"
                ),
            }
        )
    if recall_proxy < min_recall_proxy:
        alerts.append(
            {
                "code": "recall_proxy_below_threshold",
                "severity": "critical" if recall_proxy < max(0.0, min_recall_proxy - 0.2) else "warning",
                "detail": (
                    f"recall_proxy={round(recall_proxy, 4)} "
                    f"< min_recall_proxy={round(min_recall_proxy, 4)}"
                ),
            }
        )
    if quality_drop >= critical_drop:
        alerts.append(
            {
                "code": "quality_drop_critical",
                "severity": "critical",
                "detail": f"quality_score_drop={round(quality_drop, 4)} >= {round(critical_drop, 4)}",
            }
        )
    elif quality_drop >= warning_drop:
        alerts.append(
            {
                "code": "quality_drop_warning",
                "severity": "warning",
                "detail": f"quality_score_drop={round(quality_drop, 4)} >= {round(warning_drop, 4)}",
            }
        )

    level = "healthy"
    if any(item.get("severity") == "critical" for item in alerts):
        level = "critical"
    elif alerts:
        level = "warning"

    return {
        "level": level,
        "alerts": alerts,
        "quality_score": round(quality_score, 4),
        "recommend_block_high_risk": level == "critical",
    }


def build_memory_recall_regression_report(
    backend_dir: Path | str,
    *,
    query_set_path: Optional[Path | str] = None,
    window_days: int = 7,
    top_k: int = 5,
    min_match_score: float = 0.18,
    min_precision_proxy: float = 0.45,
    min_recall_proxy: float = 0.45,
    warning_drop: float = 0.05,
    critical_drop: float = 0.12,
    include_samples: bool = False,
    sample_limit: int = 10,
    persist: bool = True,
) -> Dict[str, Any]:
    backend_root = Path(backend_dir)
    safe_window_days = max(1, min(int(window_days or 7), 30))
    safe_top_k = max(1, min(int(top_k or 5), 20))
    safe_min_match_score = max(0.0, min(float(min_match_score or 0.18), 1.0))
    safe_sample_limit = max(1, min(int(sample_limit or 10), 50))
    now = time.time()

    query_path = (
        Path(str(query_set_path))
        if str(query_set_path or "").strip()
        else (backend_root / DEFAULT_QUERY_SET_FILENAME)
    )
    run_history_path = backend_root / DEFAULT_RUN_HISTORY_FILENAME

    query_set = _load_query_set(query_path)
    candidates = _load_candidates(backend_root)
    results = [
        _evaluate_query(
            item,
            candidates=candidates,
            top_k=safe_top_k,
            min_match_score=safe_min_match_score,
        )
        for item in query_set
    ]

    query_total = len(results)
    matched_queries = len([item for item in results if bool(item.get("recall_hit", False))])
    precision_hits = len([item for item in results if bool(item.get("precision_hit", False))])

    recall_proxy = round(matched_queries / max(1, query_total), 4)
    precision_proxy = round(precision_hits / max(1, matched_queries), 4)
    quality_score = round((0.55 * precision_proxy) + (0.45 * recall_proxy), 4)

    history_rows = _read_jsonl_tail(run_history_path, limit=1000)
    previous_rows = [
        row for row in history_rows if _parse_time_like(row.get("timestamp", row.get("generated_at", 0.0))) < now
    ]
    previous_rows.sort(
        key=lambda row: _parse_time_like(row.get("timestamp", row.get("generated_at", 0.0))),
        reverse=True,
    )
    baseline = previous_rows[0] if previous_rows else {}
    baseline_metrics = baseline.get("metrics", {}) if isinstance(baseline.get("metrics"), dict) else {}
    previous_quality = float(baseline_metrics.get("quality_score", 0.0) or 0.0)
    previous_recall = float(baseline_metrics.get("recall_proxy", 0.0) or 0.0)
    previous_precision = float(baseline_metrics.get("precision_proxy", 0.0) or 0.0)

    quality_delta = round(quality_score - previous_quality, 4)
    recall_delta = round(recall_proxy - previous_recall, 4)
    precision_delta = round(precision_proxy - previous_precision, 4)

    alert_state = _build_alerts(
        precision_proxy=precision_proxy,
        recall_proxy=recall_proxy,
        quality_score=quality_score,
        quality_delta=quality_delta,
        min_precision_proxy=max(0.0, min(float(min_precision_proxy or 0.45), 1.0)),
        min_recall_proxy=max(0.0, min(float(min_recall_proxy or 0.45), 1.0)),
        warning_drop=max(0.0, min(float(warning_drop or 0.05), 1.0)),
        critical_drop=max(0.0, min(float(critical_drop or 0.12), 1.0)),
    )

    if quality_delta > 0.02:
        direction = "improving"
    elif quality_delta < -0.02:
        direction = "worse"
    else:
        direction = "stable"

    report: Dict[str, Any] = {
        "status": "ok",
        "version": "memory-recall-regression.v1",
        "generated_at": now,
        "window_days": safe_window_days,
        "backend_dir": str(backend_root),
        "query_set_path": str(query_path),
        "run_history_path": str(run_history_path),
        "settings": {
            "top_k": safe_top_k,
            "min_match_score": safe_min_match_score,
            "min_precision_proxy": max(0.0, min(float(min_precision_proxy or 0.45), 1.0)),
            "min_recall_proxy": max(0.0, min(float(min_recall_proxy or 0.45), 1.0)),
            "warning_drop": max(0.0, min(float(warning_drop or 0.05), 1.0)),
            "critical_drop": max(0.0, min(float(critical_drop or 0.12), 1.0)),
        },
        "current_window": {
            "query_total": query_total,
            "matched_queries": matched_queries,
            "precision_hits": precision_hits,
            "metrics": {
                "recall_proxy": recall_proxy,
                "precision_proxy": precision_proxy,
                "quality_score": quality_score,
                "quality_level": alert_state["level"],
            },
        },
        "previous_window": {
            "query_total": int(baseline.get("query_total", 0) or 0),
            "matched_queries": int(baseline.get("matched_queries", 0) or 0),
            "precision_hits": int(baseline.get("precision_hits", 0) or 0),
            "metrics": {
                "recall_proxy": round(previous_recall, 4),
                "precision_proxy": round(previous_precision, 4),
                "quality_score": round(previous_quality, 4),
                "quality_level": str(baseline.get("level", "unknown")),
            },
        },
        "trend": {
            "direction": direction,
            "quality_score_delta": quality_delta,
            "recall_proxy_delta": recall_delta,
            "precision_proxy_delta": precision_delta,
        },
        "alerts": alert_state["alerts"],
        "gate": {
            "level": alert_state["level"],
            "recommend_block_high_risk": bool(alert_state["recommend_block_high_risk"]),
        },
    }

    if query_total <= 0:
        report["status"] = "warning"
        report["alerts"].append(
            {
                "code": "empty_query_set",
                "severity": "warning",
                "detail": f"query_set is empty or unavailable: {query_path}",
            }
        )
        report["current_window"]["metrics"]["quality_level"] = "warning"
        report["gate"]["level"] = "warning"
        report["gate"]["recommend_block_high_risk"] = False

    if include_samples:
        misses = [
            {
                "id": item.get("id", ""),
                "query": item.get("query", ""),
                "expected_terms": item.get("expected_terms", []),
                "expected_category": item.get("expected_category", ""),
                "top_hit": (item.get("top_hits", [None])[0] if item.get("top_hits") else None),
            }
            for item in results
            if not bool(item.get("precision_hit", False))
        ]
        misses.sort(key=lambda item: str(item.get("id", "")))
        report["samples"] = {
            "failed_queries": misses[:safe_sample_limit],
            "query_outcomes": [
                {
                    "id": item.get("id", ""),
                    "query": item.get("query", ""),
                    "recall_hit": bool(item.get("recall_hit", False)),
                    "precision_hit": bool(item.get("precision_hit", False)),
                }
                for item in results[:safe_sample_limit]
            ],
        }

    if persist:
        snapshot = {
            "timestamp": now,
            "generated_at": now,
            "window_days": safe_window_days,
            "query_total": query_total,
            "matched_queries": matched_queries,
            "precision_hits": precision_hits,
            "level": str(report.get("gate", {}).get("level", "healthy")),
            "metrics": {
                "recall_proxy": recall_proxy,
                "precision_proxy": precision_proxy,
                "quality_score": quality_score,
            },
        }
        run_history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(run_history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    return report
