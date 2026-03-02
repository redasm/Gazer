"""Prompt segment cache utilities.

This module tracks reusable prompt prefixes and exposes hit/miss telemetry.
It is provider-agnostic and safe to enable/disable at runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class _CacheEntry:
    expires_at: float
    estimated_tokens: int


class PromptSegmentCache:
    """A small in-memory cache for reusable prompt segments."""

    _SENSITIVE_KEY_MARKERS = (
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
    )
    _SENSITIVE_STRING_PATTERNS = (
        re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
        re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
        re.compile(
            r"(?i)\b(api[_\-]?key|token|secret|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{6,}['\"]?"
        ),
    )

    def __init__(
        self,
        *,
        enabled: bool = False,
        ttl_seconds: int = 300,
        max_items: int = 512,
        segment_policy: str = "stable_prefix",
        chars_per_token: float = 4.0,
        scope_fields: Optional[List[str]] = None,
        sanitize_sensitive: bool = True,
    ) -> None:
        self.enabled = bool(enabled)
        self.ttl_seconds = max(10, int(ttl_seconds or 300))
        self.max_items = max(32, int(max_items or 512))
        self.segment_policy = str(segment_policy or "stable_prefix").strip().lower() or "stable_prefix"
        self.chars_per_token = max(1.0, float(chars_per_token or 4.0))
        default_scope_fields = ["session_key", "channel", "sender_id"]
        self.scope_fields = [
            str(item).strip()
            for item in (scope_fields if isinstance(scope_fields, list) else default_scope_fields)
            if str(item).strip()
        ]
        self.sanitize_sensitive = bool(sanitize_sensitive)
        self._entries: "OrderedDict[str, _CacheEntry]" = OrderedDict()
        self._lookups = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._saved_prompt_tokens = 0

    def observe(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        scope: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Track one prompt lookup and update cache counters."""
        if not self.enabled:
            return {"enabled": False, "hit": False, "key": "", "estimated_tokens": 0}

        self._lookups += 1
        self._prune_expired()
        key, estimated_tokens = self._build_key(
            messages=messages,
            tools=tools or [],
            model=model,
            scope=scope or {},
        )
        now = time.time()
        existing = self._entries.get(key)
        if existing and existing.expires_at > now:
            self._hits += 1
            self._saved_prompt_tokens += max(0, int(existing.estimated_tokens))
            self._entries.move_to_end(key)
            return {"enabled": True, "hit": True, "key": key, "estimated_tokens": existing.estimated_tokens}

        self._misses += 1
        self._entries[key] = _CacheEntry(
            expires_at=now + float(self.ttl_seconds),
            estimated_tokens=max(0, int(estimated_tokens)),
        )
        self._entries.move_to_end(key)
        self._enforce_max_items()
        return {"enabled": True, "hit": False, "key": key, "estimated_tokens": estimated_tokens}

    def summary(self) -> Dict[str, Any]:
        """Return cache runtime telemetry."""
        hit_rate = 0.0
        if self._lookups > 0:
            hit_rate = round(float(self._hits) / float(self._lookups), 4)
        return {
            "enabled": bool(self.enabled),
            "segment_policy": self.segment_policy,
            "scope_fields": list(self.scope_fields),
            "sanitize_sensitive": bool(self.sanitize_sensitive),
            "ttl_seconds": int(self.ttl_seconds),
            "max_items": int(self.max_items),
            "cached_items": int(len(self._entries)),
            "lookups": int(self._lookups),
            "hits": int(self._hits),
            "misses": int(self._misses),
            "hit_rate": hit_rate,
            "evictions": int(self._evictions),
            "estimated_saved_prompt_tokens": int(self._saved_prompt_tokens),
        }

    def _enforce_max_items(self) -> None:
        while len(self._entries) > self.max_items:
            self._entries.popitem(last=False)
            self._evictions += 1

    def _prune_expired(self) -> None:
        if not self._entries:
            return
        now = time.time()
        expired_keys = [key for key, item in self._entries.items() if item.expires_at <= now]
        for key in expired_keys:
            self._entries.pop(key, None)

    def _build_key(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: Optional[str],
        scope: Dict[str, Any],
    ) -> tuple[str, int]:
        normalized_messages = self._select_messages(messages)
        normalized_tools = self._select_tools(tools)
        payload = {
            "segment_policy": self.segment_policy,
            "model": str(model or ""),
            "scope": self._normalize_scope(scope),
            "messages": [self._normalize_message(item) for item in normalized_messages],
            "tools": normalized_tools,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        estimated_tokens = int(len(raw) / self.chars_per_token)
        return digest, max(0, estimated_tokens)

    def _select_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.segment_policy != "stable_prefix":
            return list(messages)
        last_user_index = -1
        for idx, msg in enumerate(messages):
            if str(msg.get("role", "")).strip().lower() == "user":
                last_user_index = idx
        if last_user_index < 0:
            return list(messages)
        return list(messages[: last_user_index + 1])

    def _normalize_scope(self, scope: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(scope, dict):
            return {}
        normalized: Dict[str, str] = {}
        for key in self.scope_fields:
            if key not in scope:
                continue
            value = scope.get(key)
            if value is None:
                continue
            value_str = str(value).strip()
            if not value_str:
                continue
            normalized[str(key)] = self._sanitize_string(value_str)
        return normalized

    def _normalize_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        role = str(message.get("role", "")).strip().lower()
        normalized: Dict[str, Any] = {"role": role}
        for key in ("name", "tool_call_id"):
            if key in message and message.get(key) is not None:
                normalized[key] = str(message.get(key))
        if "tool_calls" in message and message.get("tool_calls") is not None:
            normalized["tool_calls"] = self._normalize_content(message.get("tool_calls"))
        normalized["content"] = self._normalize_content(message.get("content"))
        return normalized

    def _normalize_content(self, content: Any) -> Any:
        if isinstance(content, str):
            return self._sanitize_string(content)
        if isinstance(content, list):
            return [self._normalize_content(item) for item in content]
        if isinstance(content, dict):
            normalized_dict: Dict[str, Any] = {}
            for key, value in sorted(content.items(), key=lambda item: str(item[0])):
                key_str = str(key)
                if self._is_sensitive_key(key_str):
                    normalized_dict[key_str] = "[REDACTED]"
                    continue
                normalized_dict[key_str] = self._normalize_content(value)
            return normalized_dict
        if content is None:
            return ""
        return self._sanitize_string(str(content))

    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        marker = str(key or "").strip().lower()
        if not marker:
            return False
        return any(token in marker for token in cls._SENSITIVE_KEY_MARKERS)

    def _sanitize_string(self, value: str) -> str:
        text = str(value or "")
        if not self.sanitize_sensitive:
            return text
        for pattern in self._SENSITIVE_STRING_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    @staticmethod
    def _select_tools(tools: List[Dict[str, Any]]) -> List[str]:
        names: List[str] = []
        for item in tools:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            if isinstance(function, dict):
                name = str(function.get("name", "")).strip()
                if name:
                    names.append(name)
                    continue
            name = str(item.get("name", "")).strip()
            if name:
                names.append(name)
        return sorted(set(names))
