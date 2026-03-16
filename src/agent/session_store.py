"""Session persistence -- JSONL-based session storage.

Replaces the in-memory ``_history_cache`` in AgentLoop with durable,
append-only JSONL files.  Each session gets its own file under
``~/.gazer/sessions/``.
"""

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SessionStore")


def _safe_filename(session_key: str) -> str:
    """Encode session key as a filesystem-safe base64url filename."""
    return base64.urlsafe_b64encode(session_key.encode("utf-8")).decode("ascii") + ".jsonl"


def _decode_filename(stem: str) -> str:
    """Decode a base64url-encoded filename stem back to a session key."""
    return base64.urlsafe_b64decode(stem.encode("ascii")).decode("utf-8")


class SessionStore:
    """Durable session storage backed by JSONL files.

    Each message is appended as a single JSON line.  On startup the last
    *N* lines are loaded to restore context.

    Thread-safety: not required -- Gazer is single-event-loop async.
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir or Path.home() / ".gazer" / "sessions"
        self._base.mkdir(parents=True, exist_ok=True)
        # In-memory LRU-style cache so hot sessions don't read disk
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_limit = 50  # Max cached messages per session

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def append(
        self,
        session_key: str,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Append a message to a session (writes to disk immediately)."""
        record: Dict[str, Any] = {
            "ts": time.time(),
            "role": role,
            "content": content,
        }
        if tool_calls:
            record["tool_calls"] = tool_calls

        path = self._base / _safe_filename(session_key)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("Failed to append session %s: %s", session_key, exc)

        # Update cache
        if session_key not in self._cache:
            self._cache[session_key] = []
        self._cache[session_key].append({"role": role, "content": content})
        if len(self._cache[session_key]) > self._cache_limit:
            self._cache[session_key] = self._cache[session_key][-self._cache_limit:]

    def load(self, session_key: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Load the last *limit* messages for a session.

        Reads from cache first; falls back to disk.
        """
        if session_key in self._cache:
            return list(self._cache[session_key][-limit:])

        path = self._base / _safe_filename(session_key)
        if not path.is_file():
            return []

        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return []

        messages: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                rec = json.loads(line)
                msg: Dict[str, Any] = {"role": rec["role"], "content": rec.get("content", "")}
                if "tool_calls" in rec:
                    msg["tool_calls"] = rec["tool_calls"]
                messages.append(msg)
            except (json.JSONDecodeError, KeyError):
                continue

        self._cache[session_key] = messages[-self._cache_limit:]
        return messages

    def list_sessions(self) -> List[str]:
        """List all persisted session keys."""
        sessions = []
        for f in self._base.glob("*.jsonl"):
            try:
                sessions.append(_decode_filename(f.stem))
            except Exception:
                # Legacy filenames that weren't base64-encoded
                sessions.append(f.stem)
        return sessions

    def prune(self, session_key: str, keep_last: int = 50) -> int:
        """Prune old messages, keeping only the last *keep_last*.

        Returns the number of messages removed.
        """
        path = self._base / _safe_filename(session_key)
        if not path.is_file():
            return 0
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return 0

        if len(lines) <= keep_last:
            return 0

        removed = len(lines) - keep_last
        kept = lines[-keep_last:]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.replace(path)

        # Refresh cache
        self._cache.pop(session_key, None)
        logger.info("Pruned %s messages from session %s", removed, session_key)
        return removed

    def delete_session(self, session_key: str) -> bool:
        """Delete a session entirely."""
        path = self._base / _safe_filename(session_key)
        self._cache.pop(session_key, None)
        if path.is_file():
            path.unlink()
            return True
        # Also remove stale meta
        meta_path = self._meta_path(session_key)
        if meta_path.is_file():
            meta_path.unlink(missing_ok=True)
        return False

    # ------------------------------------------------------------------
    # Session metadata (model/provider override, etc.)
    # ------------------------------------------------------------------

    def _meta_path(self, session_key: str) -> Path:
        stem = base64.urlsafe_b64encode(session_key.encode("utf-8")).decode("ascii")
        return self._base / f"{stem}.meta.json"

    def get_session_meta(self, session_key: str) -> Dict[str, Any]:
        """Load persisted metadata for *session_key* (empty dict if none)."""
        path = self._meta_path(session_key)
        if not path.is_file():
            return {}
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    def set_session_meta(self, session_key: str, meta: Dict[str, Any]) -> None:
        """Persist *meta* for *session_key* (overwrites existing)."""
        path = self._meta_path(session_key)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.error("Failed to write session meta %s: %s", session_key, exc)

    def apply_model_override(
        self,
        session_key: str,
        provider: str,
        model: str,
    ) -> Dict[str, Any]:
        """Persist a per-session model/provider override and return the updated meta.

        When the selection changes, stale runtime fields (``last_model``,
        ``context_tokens``) are cleared so the next turn reflects the new choice
        immediately — mirroring OpenClaw's ``applyModelOverrideToSessionEntry``.
        """
        meta = self.get_session_meta(session_key)
        changed = (
            meta.get("model_override") != model
            or meta.get("provider_override") != provider
        )
        meta["model_override"] = model
        meta["provider_override"] = provider
        meta["updated_at"] = time.time()
        if changed:
            # Clear stale runtime fields that are derived from the active model
            meta.pop("last_model", None)
            meta.pop("context_tokens", None)
        self.set_session_meta(session_key, meta)
        return meta

    def clear_model_override(self, session_key: str) -> Dict[str, Any]:
        """Remove any stored model/provider override for *session_key*."""
        meta = self.get_session_meta(session_key)
        changed = bool(meta.pop("model_override", None) or meta.pop("provider_override", None))
        if changed:
            meta.pop("last_model", None)
            meta.pop("context_tokens", None)
            meta["updated_at"] = time.time()
            self.set_session_meta(session_key, meta)
        return meta

    def get_model_override(self, session_key: str) -> Tuple[Optional[str], Optional[str]]:
        """Return ``(provider, model)`` override for *session_key*, or ``(None, None)``."""
        meta = self.get_session_meta(session_key)
        return meta.get("provider_override"), meta.get("model_override")
