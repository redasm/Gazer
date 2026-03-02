"""Hierarchical AGENTS.md resolver for workspace directories."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional


_SKILL_PRIORITY_RE = re.compile(r"^\s*skills?_priority\s*:\s*(.+?)\s*$", re.IGNORECASE)
_SKILL_TOKEN_RE = re.compile(r"\$([A-Za-z0-9][A-Za-z0-9_\-]*)")
_STRUCTURED_FIELD_RE = re.compile(r"^\s*(allowed-tools|deny-tools|routing-hints)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_STRUCTURED_LIST_ITEM_RE = re.compile(r"^\s*[-*]\s*(.+?)\s*$")
_GENERIC_KEY_LINE_RE = re.compile(r"^\s*[A-Za-z0-9_\-]+\s*:\s*")


def _normalize_name(name: str) -> str:
    return str(name or "").strip()


def _skill_priority_from_text(text: str) -> List[str]:
    names: List[str] = []
    for line in str(text or "").splitlines():
        m = _SKILL_PRIORITY_RE.match(line)
        if not m:
            continue
        for item in m.group(1).split(","):
            value = _normalize_name(item)
            if value and value not in names:
                names.append(value)
    if names:
        return names

    # Fallback: infer mentioned skills from "$SkillName" tokens in file body.
    inferred: List[str] = []
    for match in _SKILL_TOKEN_RE.finditer(str(text or "")):
        token = _normalize_name(match.group(1))
        if token and token not in inferred:
            inferred.append(token)
    return inferred


def _split_structured_values(raw: str) -> List[str]:
    text = _normalize_name(raw)
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    values: List[str] = []
    for item in text.split(","):
        token = _normalize_name(item).strip("\"'").lower()
        if token and token not in values:
            values.append(token)
    return values


def _structured_fields_from_text(text: str) -> Dict[str, List[str]]:
    lines = str(text or "").splitlines()
    fields: Dict[str, List[str]] = {
        "allowed_tools": [],
        "deny_tools": [],
        "routing_hints": [],
    }
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = _STRUCTURED_FIELD_RE.match(line)
        if not match:
            idx += 1
            continue
        key = _normalize_name(match.group(1)).lower().replace("-", "_")
        raw_value = _normalize_name(match.group(2))
        parsed_values: List[str] = []
        if raw_value:
            parsed_values.extend(_split_structured_values(raw_value))
        else:
            cursor = idx + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                stripped = candidate.strip()
                if not stripped:
                    cursor += 1
                    continue
                if _STRUCTURED_FIELD_RE.match(candidate) or _GENERIC_KEY_LINE_RE.match(candidate):
                    break
                bullet = _STRUCTURED_LIST_ITEM_RE.match(candidate)
                if not bullet:
                    break
                parsed_values.extend(_split_structured_values(bullet.group(1)))
                cursor += 1
            idx = max(idx, cursor - 1)
        if key in fields and parsed_values:
            fields[key] = parsed_values
        idx += 1
    return fields


def resolve_agents_overlay(workspace: Path, target_dir: Optional[Path] = None) -> Dict[str, object]:
    """Resolve AGENTS.md files along workspace -> target directory chain.

    Rules:
    - Aggregate files from shallow to deep.
    - Child directory can override skill priority if it declares one.
    """
    root = workspace.resolve()
    target = (target_dir or workspace).resolve()
    if root not in {target, *target.parents}:
        target = root

    chain: List[Path] = []
    cur = target
    while True:
        chain.append(cur)
        if cur == root:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    chain.reverse()

    files: List[Dict[str, str]] = []
    sections: List[str] = []
    skill_priority: List[str] = []
    allowed_tools: List[str] = []
    deny_tools: List[str] = []
    routing_hints: List[str] = []
    conflicts: List[Dict[str, str]] = []
    allow_source: Dict[str, str] = {}
    deny_source: Dict[str, str] = {}
    conflict_keys: set[tuple[str, str, str]] = set()
    debug: List[Dict[str, object]] = []
    for idx, directory in enumerate(chain):
        ag = directory / "AGENTS.md"
        if not ag.is_file():
            continue
        try:
            raw = ag.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        rel = ag.relative_to(root).as_posix()
        files.append({"path": rel, "content": raw})
        sections.append(f"## AGENTS ({rel})\n{raw}")
        parsed_priority = _skill_priority_from_text(raw)
        if parsed_priority:
            skill_priority = parsed_priority
        structured = _structured_fields_from_text(raw)
        parsed_allowed_tools = structured.get("allowed_tools", [])
        parsed_deny_tools = structured.get("deny_tools", [])
        parsed_routing_hints = structured.get("routing_hints", [])
        if parsed_allowed_tools:
            allowed_tools = parsed_allowed_tools
        if parsed_deny_tools:
            deny_tools = parsed_deny_tools
        if parsed_routing_hints:
            routing_hints = parsed_routing_hints
        overlap_current = sorted(set(parsed_allowed_tools) & set(parsed_deny_tools))
        for tool_name in overlap_current:
            key = ("same_scope", rel, tool_name)
            if key not in conflict_keys:
                conflicts.append(
                    {
                        "type": "allow_deny_conflict",
                        "tool": tool_name,
                        "allowed_in": rel,
                        "denied_in": rel,
                        "scope": "directory_overlay",
                    }
                )
                conflict_keys.add(key)
        for tool_name in parsed_allowed_tools:
            denied_in = deny_source.get(tool_name)
            if denied_in:
                key = ("cross_scope", f"{denied_in}->{rel}", tool_name)
                if key not in conflict_keys:
                    conflicts.append(
                        {
                            "type": "allow_deny_conflict",
                            "tool": tool_name,
                            "allowed_in": rel,
                            "denied_in": denied_in,
                            "scope": "directory_overlay",
                        }
                    )
                    conflict_keys.add(key)
            allow_source[tool_name] = rel
        for tool_name in parsed_deny_tools:
            allowed_in = allow_source.get(tool_name)
            if allowed_in:
                key = ("cross_scope", f"{allowed_in}->{rel}", tool_name)
                if key not in conflict_keys:
                    conflicts.append(
                        {
                            "type": "allow_deny_conflict",
                            "tool": tool_name,
                            "allowed_in": allowed_in,
                            "denied_in": rel,
                            "scope": "directory_overlay",
                        }
                    )
                    conflict_keys.add(key)
            deny_source[tool_name] = rel
        debug.append(
            {
                "path": rel,
                "depth": idx,
                "parsed_skill_priority": parsed_priority,
                "overrode_skill_priority": bool(parsed_priority),
                "parsed_allowed_tools": parsed_allowed_tools,
                "parsed_deny_tools": parsed_deny_tools,
                "parsed_routing_hints": parsed_routing_hints,
                "overrode_allowed_tools": bool(parsed_allowed_tools),
                "overrode_deny_tools": bool(parsed_deny_tools),
                "overrode_routing_hints": bool(parsed_routing_hints),
                "conflict_count": len(overlap_current),
            }
        )

    return {
        "files": files,
        "combined_text": "\n\n".join(sections).strip(),
        "skill_priority": skill_priority,
        "allowed_tools": allowed_tools,
        "deny_tools": deny_tools,
        "routing_hints": routing_hints,
        "conflicts": conflicts,
        "debug": debug,
        "target_dir": target.relative_to(root).as_posix() if target != root else ".",
    }
