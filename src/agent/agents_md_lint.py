"""Lint helpers for hierarchical AGENTS.md overlays."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.agents_md import resolve_agents_overlay

_STRUCTURED_FIELD_RE = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*:\s*(.*?)\s*$")
_STRUCTURED_LIST_ITEM_RE = re.compile(r"^\s*[-*]\s*(.+?)\s*$")

_KNOWN_FIELDS = {
    "allowed-tools": "allowed_tools",
    "deny-tools": "deny_tools",
    "routing-hints": "routing_hints",
    "skills_priority": "skills_priority",
    "skill_priority": "skills_priority",
}
_TOOL_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_:\-]+$")
_HINT_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _split_values(raw: str) -> List[str]:
    text = _as_text(raw)
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    out: List[str] = []
    for token in text.split(","):
        item = _as_text(token).strip("\"'")
        if item:
            out.append(item)
    return out


def _new_issue(
    *,
    severity: str,
    code: str,
    message: str,
    path: str,
    line: int,
    field: str = "",
    value: str = "",
) -> Dict[str, Any]:
    return {
        "severity": _as_text(severity) or "warning",
        "code": _as_text(code) or "unknown",
        "message": _as_text(message),
        "path": _as_text(path),
        "line": max(1, int(line or 1)),
        "field": _as_text(field),
        "value": _as_text(value),
    }


def _lint_single_agents_file(path: str, text: str) -> Dict[str, Any]:
    lines = str(text or "").splitlines()
    issues: List[Dict[str, Any]] = []
    parsed = {
        "allowed_tools": [],
        "deny_tools": [],
        "routing_hints": [],
        "skills_priority": [],
    }
    line_idx = 0
    while line_idx < len(lines):
        line = lines[line_idx]
        match = _STRUCTURED_FIELD_RE.match(line)
        if not match:
            line_idx += 1
            continue

        raw_field = _as_text(match.group(1))
        field_key = raw_field.lower()
        normalized_field = _KNOWN_FIELDS.get(field_key, "")
        raw_value = _as_text(match.group(2))

        if not normalized_field:
            if any(token in field_key for token in ("tool", "routing", "skill")):
                issues.append(
                    _new_issue(
                        severity="warning",
                        code="unknown_field",
                        message=f"Unknown structured field '{raw_field}'.",
                        path=path,
                        line=line_idx + 1,
                        field=raw_field,
                    )
                )
            line_idx += 1
            continue

        values = _split_values(raw_value)
        if not values:
            cursor = line_idx + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                stripped = candidate.strip()
                if not stripped:
                    cursor += 1
                    continue
                if _STRUCTURED_FIELD_RE.match(candidate):
                    break
                bullet = _STRUCTURED_LIST_ITEM_RE.match(candidate)
                if not bullet:
                    break
                values.extend(_split_values(bullet.group(1)))
                cursor += 1
            line_idx = max(line_idx, cursor - 1)
        if not values:
            issues.append(
                _new_issue(
                    severity="warning",
                    code="empty_structured_field",
                    message=f"Structured field '{raw_field}' has no values.",
                    path=path,
                    line=line_idx + 1,
                    field=raw_field,
                )
            )
            line_idx += 1
            continue

        deduped: List[str] = []
        seen: set[str] = set()
        for item in values:
            normalized = _as_text(item).lower()
            if not normalized:
                continue
            if normalized in seen:
                issues.append(
                    _new_issue(
                        severity="info",
                        code="duplicate_value",
                        message=f"Duplicate value '{item}' in '{raw_field}'.",
                        path=path,
                        line=line_idx + 1,
                        field=raw_field,
                        value=item,
                    )
                )
                continue
            seen.add(normalized)
            deduped.append(normalized)
        parsed[normalized_field] = deduped
        line_idx += 1

    for tool in parsed["allowed_tools"] + parsed["deny_tools"]:
        if not _TOOL_TOKEN_RE.match(tool):
            issues.append(
                _new_issue(
                    severity="warning",
                    code="invalid_tool_token",
                    message=f"Tool token '{tool}' contains unsupported characters.",
                    path=path,
                    line=1,
                    field="allowed-tools/deny-tools",
                    value=tool,
                )
            )
    for hint in parsed["routing_hints"]:
        if not _HINT_TOKEN_RE.match(hint):
            issues.append(
                _new_issue(
                    severity="warning",
                    code="invalid_routing_hint",
                    message=f"Routing hint '{hint}' contains unsupported characters.",
                    path=path,
                    line=1,
                    field="routing-hints",
                    value=hint,
                )
            )

    overlap = sorted(set(parsed["allowed_tools"]) & set(parsed["deny_tools"]))
    for tool in overlap:
        issues.append(
            _new_issue(
                severity="error",
                code="allow_deny_conflict_same_file",
                message=f"Tool '{tool}' is present in both allowed-tools and deny-tools.",
                path=path,
                line=1,
                field="allowed-tools/deny-tools",
                value=tool,
            )
        )

    return {
        "path": path,
        "issues": issues,
        "parsed": parsed,
    }


def lint_agents_overlay(workspace: Path, target_dir: Optional[Path] = None) -> Dict[str, Any]:
    root = workspace.resolve()
    payload = resolve_agents_overlay(root, target_dir)
    files = payload.get("files", []) if isinstance(payload.get("files", []), list) else []

    file_reports: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = _as_text(item.get("path"))
        content = str(item.get("content", ""))
        report = _lint_single_agents_file(path, content)
        file_reports.append(report)
        all_issues.extend(report.get("issues", []))

    overlay_conflicts = payload.get("conflicts", []) if isinstance(payload.get("conflicts", []), list) else []
    for conflict in overlay_conflicts:
        if not isinstance(conflict, dict):
            continue
        all_issues.append(
            _new_issue(
                severity="warning",
                code=str(conflict.get("type", "overlay_conflict")),
                message=(
                    f"allow/deny conflict: {conflict.get('tool', conflict.get('value', 'unknown'))} "
                    f"({conflict.get('allowed_in', '?')} vs {conflict.get('denied_in', '?')})"
                ),
                path=str(conflict.get("denied_in", conflict.get("allowed_in", "."))),
                line=1,
                field="overlay",
                value=str(conflict.get("tool", conflict.get("value", ""))),
            )
        )

    severity_order = {"error": 0, "warning": 1, "info": 2}
    all_issues.sort(key=lambda item: (severity_order.get(str(item.get("severity", "warning")), 9), item.get("path", "")))
    summary = {"error": 0, "warning": 0, "info": 0}
    for issue in all_issues:
        level = str(issue.get("severity", "warning")).lower()
        if level not in summary:
            level = "warning"
        summary[level] += 1

    return {
        "status": "ok",
        "target_dir": payload.get("target_dir", "."),
        "files": file_reports,
        "effective": {
            "skill_priority": payload.get("skill_priority", []),
            "allowed_tools": payload.get("allowed_tools", []),
            "deny_tools": payload.get("deny_tools", []),
            "routing_hints": payload.get("routing_hints", []),
        },
        "issues": all_issues,
        "summary": {
            "total": int(sum(summary.values())),
            "error": summary["error"],
            "warning": summary["warning"],
            "info": summary["info"],
        },
    }
