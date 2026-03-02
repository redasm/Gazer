"""Structured task-complexity scoring for router policy decisions."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_DEFAULT_COMPLEX_MARKERS = (
    "multi-step",
    "step by step",
    "architecture",
    "design",
    "refactor",
    "migrate",
    "benchmark",
    "parallel",
    "orchestrate",
    "workflow",
    "分步",
    "多步骤",
    "架构",
    "重构",
    "迁移",
    "并行",
    "工作流",
)

_DEFAULT_SIMPLE_MARKERS = (
    "hi",
    "hello",
    "thanks",
    "thank you",
    "translate",
    "summarize",
    "what is",
    "你好",
    "谢谢",
    "翻译",
    "总结",
)

_LIST_LINE_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)")


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _as_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed >= minimum else default


def _as_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return _clamp(parsed, low=minimum, high=maximum)


def _as_str_list(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: List[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if text:
            result.append(text)
    return tuple(result)


def _normalize_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    raw = policy if isinstance(policy, dict) else {}
    feature_weights_raw = raw.get("weights", {})
    feature_weights = feature_weights_raw if isinstance(feature_weights_raw, dict) else {}

    marker_feature_enabled = bool(raw.get("marker_feature_enabled", False))
    marker_weight = (
        _as_float(raw.get("marker_weight", 0.06), default=0.06, minimum=0.0, maximum=0.2)
        if marker_feature_enabled
        else 0.0
    )
    base_budget = max(0.0, 1.0 - marker_weight)

    base_defaults = {
        "message_size": 0.33,
        "structure_density": 0.23,
        "history_depth": 0.16,
        "tool_need": 0.12,
        "context_pressure": 0.08,
        "failure_pressure": 0.08,
    }
    base_raw: Dict[str, float] = {}
    for name, fallback in base_defaults.items():
        base_raw[name] = max(
            0.0,
            _as_float(feature_weights.get(name, fallback), default=fallback, minimum=0.0, maximum=1.0),
        )
    total_raw = sum(base_raw.values())
    if total_raw <= 0:
        total_raw = sum(base_defaults.values())
        base_raw = dict(base_defaults)
    scale = base_budget / total_raw if total_raw > 0 else 0.0
    weights = {name: value * scale for name, value in base_raw.items()}
    weights["marker_hint"] = marker_weight

    return {
        "complex_threshold": _as_float(raw.get("complex_threshold", 0.52), default=0.52, minimum=0.05, maximum=0.95),
        "message_chars_complex": _as_int(raw.get("message_chars_complex", 220), default=220, minimum=40),
        "history_messages_complex": _as_int(raw.get("history_messages_complex", 8), default=8, minimum=1),
        "line_breaks_complex": _as_int(raw.get("line_breaks_complex", 3), default=3, minimum=1),
        "list_lines_complex": _as_int(raw.get("list_lines_complex", 3), default=3, minimum=1),
        "tool_history_events_complex": _as_int(
            raw.get("tool_history_events_complex", 2),
            default=2,
            minimum=1,
        ),
        "context_window_tokens": _as_int(raw.get("context_window_tokens", 32000), default=32000, minimum=512),
        "chars_per_token_estimate": _as_float(
            raw.get("chars_per_token_estimate", 4.0),
            default=4.0,
            minimum=1.0,
            maximum=12.0,
        ),
        "recent_failure_rate": _as_float(raw.get("recent_failure_rate", 0.0), default=0.0, minimum=0.0, maximum=1.0),
        "marker_feature_enabled": marker_feature_enabled,
        "marker_complex_terms": _as_str_list(raw.get("marker_complex_terms"))
        or _DEFAULT_COMPLEX_MARKERS,
        "marker_simple_terms": _as_str_list(raw.get("marker_simple_terms"))
        or _DEFAULT_SIMPLE_MARKERS,
        "weights": weights,
    }


def classify_task_complexity(messages: List[Dict[str, Any]], policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Classify complexity using structured signals and return feature breakdown."""
    cfg = _normalize_policy(policy or {})
    user_text = _extract_user_text(messages).strip()

    total_chars = 0
    non_system_messages = 0
    tool_history_events = 0
    multimodal_parts = 0

    for item in messages:
        role = str(item.get("role", "")).strip().lower()
        content = item.get("content", "")
        if role != "system":
            non_system_messages += 1
        if role == "tool":
            tool_history_events += 1
        if role == "assistant":
            tool_calls = item.get("tool_calls")
            if isinstance(tool_calls, list):
                tool_history_events += len(tool_calls)

        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).strip().lower()
                if part_type and part_type not in {"text", "input_text"}:
                    multimodal_parts += 1
                text = str(part.get("text", "") or "")
                if text:
                    total_chars += len(text)
        else:
            total_chars += len(str(content or ""))

    history_messages = max(0, non_system_messages - 1)
    line_breaks = user_text.count("\n")
    list_lines = sum(1 for line in user_text.splitlines() if _LIST_LINE_RE.match(line or ""))

    message_size = _clamp(float(len(user_text)) / float(cfg["message_chars_complex"]))
    structure_density = _clamp(
        max(
            float(line_breaks) / float(cfg["line_breaks_complex"]),
            float(list_lines) / float(cfg["list_lines_complex"]),
        )
    )
    history_depth = _clamp(float(history_messages) / float(cfg["history_messages_complex"]))
    tool_signal_events = tool_history_events + multimodal_parts
    tool_need = _clamp(float(tool_signal_events) / float(cfg["tool_history_events_complex"]))
    estimated_input_tokens = float(total_chars) / float(cfg["chars_per_token_estimate"])
    context_pressure = _clamp(estimated_input_tokens / float(cfg["context_window_tokens"]))
    failure_pressure = _clamp(float(cfg["recent_failure_rate"]))

    marker_complex_hits = 0
    marker_simple_hits = 0
    marker_hint = 0.0
    if cfg["marker_feature_enabled"]:
        lowered = user_text.lower()
        marker_complex_hits = sum(1 for marker in cfg["marker_complex_terms"] if marker in lowered)
        marker_simple_hits = sum(1 for marker in cfg["marker_simple_terms"] if marker in lowered)
        marker_hint = _clamp((marker_complex_hits * 0.45) - (marker_simple_hits * 0.35))

    features = {
        "message_size": message_size,
        "structure_density": structure_density,
        "history_depth": history_depth,
        "tool_need": tool_need,
        "context_pressure": context_pressure,
        "failure_pressure": failure_pressure,
        "marker_hint": marker_hint,
    }
    weights = cfg["weights"]
    score = _clamp(sum(float(features.get(name, 0.0)) * float(weights.get(name, 0.0)) for name in features))

    feature_breakdown: Dict[str, Dict[str, float]] = {}
    for name, value in features.items():
        weight = float(weights.get(name, 0.0))
        contribution = value * weight
        feature_breakdown[name] = {
            "value": round(value, 4),
            "weight": round(weight, 4),
            "contribution": round(contribution, 4),
        }

    level = "complex" if score >= float(cfg["complex_threshold"]) else "simple"
    return {
        "level": level,
        "score": round(score, 4),
        "text_len": len(user_text),
        "history_messages": int(history_messages),
        "tool_signal_events": int(tool_signal_events),
        "marker_complex_hits": int(marker_complex_hits),
        "marker_simple_hits": int(marker_simple_hits),
        "estimated_input_tokens": round(estimated_input_tokens, 2),
        "context_pressure": round(context_pressure, 4),
        "feature_breakdown": feature_breakdown,
        "signals": {
            "line_breaks": int(line_breaks),
            "list_lines": int(list_lines),
            "multimodal_parts": int(multimodal_parts),
            "recent_failure_rate": round(failure_pressure, 4),
            "marker_feature_enabled": bool(cfg["marker_feature_enabled"]),
            "complex_threshold": round(float(cfg["complex_threshold"]), 4),
        },
    }


def _extract_user_text(messages: List[Dict[str, Any]]) -> str:
    for item in reversed(messages):
        if str(item.get("role", "")).strip().lower() != "user":
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = str(part.get("text", "") or "").strip()
                    if text:
                        chunks.append(text)
            if chunks:
                return "\n".join(chunks)
        return str(content or "")
    return ""
