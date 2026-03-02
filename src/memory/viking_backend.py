"""OpenViking-backed memory storage and search adapters."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("GazerVikingMemory")


class OpenVikingSearchIndex:
    """Compatibility search/index adapter consumed by existing recall/admin code."""

    def __init__(self, backend: "OpenVikingMemoryBackend") -> None:
        self._backend = backend

    async def add_memory(
        self,
        content: str,
        sender: str,
        timestamp: datetime,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._backend.add_memory(
            content=content,
            sender=sender,
            timestamp=timestamp,
            metadata=metadata,
            from_reindex=False,
        )

    async def semantic_search(self, query: str, limit: int = 5) -> List[Tuple[str, str, str, float]]:
        rows = await self.hybrid_search(query, limit=limit)
        out: List[Tuple[str, str, str, float]] = []
        for row in rows:
            out.append(
                (
                    str(row.get("content", "")),
                    str(row.get("sender", "")),
                    str(row.get("timestamp", "")),
                    float(row.get("score", 0.0) or 0.0),
                )
            )
        return out

    async def hybrid_search(
        self,
        query: str,
        limit: int = 5,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> List[Dict[str, Any]]:
        return await self._backend.hybrid_search(
            query=query,
            limit=limit,
            vector_weight=vector_weight,
            text_weight=text_weight,
        )

    def fts_search(self, query: str, limit: int = 5) -> List[Tuple[str, str, str, float]]:
        return self._backend.fts_search(query=query, limit=limit)

    def delete_by_date(self, date_str: str) -> None:
        self._backend.delete_by_date(date_str)

    def clear(self) -> None:
        self._backend.clear()

    def close(self) -> None:
        self._backend.close()


class OpenVikingMemoryBackend:
    """Memory backend that persists records and forwards conversation to OpenViking sessions."""

    def __init__(
        self,
        *,
        data_dir: Path,
        session_prefix: str = "gazer",
        default_user: str = "owner",
        config_file: str = "",
        commit_every_messages: int = 8,
        enable_client: bool = False,
        client: Any = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.session_prefix = str(session_prefix or "gazer").strip() or "gazer"
        self.default_user = str(default_user or "owner").strip() or "owner"
        self.config_file = str(config_file or "").strip()
        self.commit_every_messages = max(1, int(commit_every_messages or 1))
        self.enable_client = bool(enable_client)

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._records_path = self.data_dir / "memory_events.jsonl"
        self._decision_log_path = self.data_dir / "extraction_decisions.jsonl"
        self._long_term_dir = self.data_dir / "long_term"
        self._long_term_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []
        self._active_session_id: str = ""
        self._messages_since_commit = 0
        self._long_term_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._client = client
        self.index = OpenVikingSearchIndex(self)

        self._load_records()
        if self.enable_client:
            self._initialize_client()

    def _load_records(self) -> None:
        if not self._records_path.is_file():
            self._records = []
            return
        items: List[Dict[str, Any]] = []
        try:
            for line in self._records_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict):
                    items.append(rec)
        except Exception as exc:
            logger.warning("Failed to load memory records from %s: %s", self._records_path, exc)
            items = []
        self._records = items

    def _append_record(self, item: Dict[str, Any]) -> None:
        with open(self._records_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _persist_all(self) -> None:
        with open(self._records_path, "w", encoding="utf-8") as f:
            for item in self._records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _append_decision(self, payload: Dict[str, Any]) -> None:
        with open(self._decision_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _safe_iso(timestamp: datetime) -> str:
        try:
            return timestamp.isoformat()
        except Exception:
            return datetime.now().isoformat()

    @staticmethod
    def _role_from_sender(sender: str) -> str:
        marker = str(sender or "").strip().lower()
        if marker in {"user", "owner", "human"}:
            return "user"
        return "assistant"

    @staticmethod
    def _extract_session_id(payload: Any) -> str:
        if isinstance(payload, dict):
            if payload.get("session_id"):
                return str(payload["session_id"])
            result = payload.get("result")
            if isinstance(result, dict) and result.get("session_id"):
                return str(result["session_id"])
        return ""

    def _initialize_client(self) -> None:
        if self._client is not None:
            return
        if self.config_file:
            cfg_path = Path(self.config_file).expanduser()
            if not cfg_path.is_absolute():
                cfg_path = (Path.cwd() / cfg_path).resolve()
            os.environ.setdefault("OPENVIKING_CONFIG_FILE", str(cfg_path))
        try:
            import openviking as ov
            self._client = ov.OpenViking(path=str(self.data_dir / "store"))
            self._client.initialize()
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize OpenViking client: {exc}") from exc

    def _ensure_active_session(self) -> str:
        if self._client is None:
            return ""
        if self._active_session_id:
            return self._active_session_id
        payload = self._client.create_session()
        session_id = self._extract_session_id(payload)
        if not session_id:
            raise RuntimeError(f"OpenViking create_session returned invalid payload: {payload}")
        self._active_session_id = session_id
        return self._active_session_id

    def _forward_to_openviking(self, *, sender: str, content: str) -> None:
        if self._client is None:
            return
        session_id = self._ensure_active_session()
        role = self._role_from_sender(sender)
        self._client.add_message(session_id=session_id, role=role, content=content)
        self._messages_since_commit += 1
        if self._messages_since_commit >= self.commit_every_messages:
            self._commit_active_session(reason="message_threshold")

    def _commit_active_session(self, *, reason: str) -> None:
        if self._client is None or not self._active_session_id:
            return
        try:
            self._client.commit_session(self._active_session_id)
            self._append_decision(
                {
                    "timestamp": datetime.now().isoformat(),
                    "kind": "session_commit",
                    "session_id": self._active_session_id,
                    "reason": reason,
                }
            )
        finally:
            self._active_session_id = ""
            self._messages_since_commit = 0

    @staticmethod
    def _category_mergeable(category: str) -> bool:
        return category in {"profile", "preferences", "entities", "patterns"}

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(str(text or "").strip().split())

    @classmethod
    def _build_search_terms(cls, query: str) -> List[str]:
        normalized = cls._normalize_text(str(query or "").lower())
        if not normalized:
            return []

        terms: List[str] = [normalized]

        split_tokens = [
            token
            for token in re.split(r"[\s,，。！？;；:：()\[\]{}\"'`]+", normalized)
            if token
        ]
        for token in split_tokens:
            if len(token) >= 2 and token not in terms:
                terms.append(token)

        cjk_only = re.sub(r"[^\u4e00-\u9fff]", "", normalized)
        if len(cjk_only) >= 2:
            for i in range(len(cjk_only) - 1):
                bi = cjk_only[i : i + 2]
                if bi not in terms:
                    terms.append(bi)

        return terms[:32]

    @staticmethod
    def _detect_category(record: Dict[str, Any]) -> str:
        sender = str(record.get("sender", "")).strip().lower()
        content = str(record.get("content", "")).strip().lower()
        metadata = record.get("metadata", {}) if isinstance(record.get("metadata"), dict) else {}
        if metadata.get("tool_name") or metadata.get("tool_call") or "tool execution [" in content:
            return "cases"
        if sender in {"user", "owner", "human"}:
            if any(item in content for item in [" prefer ", "喜欢", "偏好", "favorite"]):
                return "preferences"
            if any(item in content for item in ["i am ", "i'm ", "我是", "我叫", "my name is"]):
                return "profile"
            if any(item in content for item in ["project", "团队", "同事", "friend", "family", "客户"]):
                return "entities"
            return "events"
        if any(item in content for item in ["pattern", "workflow", "strategy", "模板", "模式"]):
            return "patterns"
        return "events"

    @classmethod
    def _build_memory_key(cls, category: str, record: Dict[str, Any]) -> str:
        content = cls._normalize_text(str(record.get("content", "")))
        metadata = record.get("metadata", {}) if isinstance(record.get("metadata"), dict) else {}
        explicit_key = str(metadata.get("memory_key", "")).strip().lower()
        if explicit_key:
            return explicit_key
        if category in {"profile", "preferences", "entities", "patterns"}:
            return content[:72].lower() or f"{category}:unknown"
        stamp = str(record.get("timestamp", "")).strip() or datetime.now().isoformat()
        return f"{category}:{stamp}"

    def _get_category_store(self, category: str) -> Dict[str, Dict[str, Any]]:
        if category in self._long_term_cache:
            return self._long_term_cache[category]
        path = self._long_term_dir / f"{category}.json"
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    store = {
                        str(k): v
                        for k, v in loaded.items()
                        if isinstance(v, dict)
                    }
                else:
                    store = {}
            except Exception:
                store = {}
        else:
            store = {}
        self._long_term_cache[category] = store
        return store

    def _persist_category_store(self, category: str) -> None:
        path = self._long_term_dir / f"{category}.json"
        store = self._long_term_cache.get(category, {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

    def _extract_long_term_memory(self, record: Dict[str, Any]) -> None:
        category = self._detect_category(record)
        key = self._build_memory_key(category, record)
        content = self._normalize_text(str(record.get("content", "")))
        store = self._get_category_store(category)
        existing = copy.deepcopy(store.get(key)) if key in store else None

        decision = "CREATE"
        payload: Dict[str, Any]
        if existing is None:
            payload = {
                "content": content,
                "sender": str(record.get("sender", "")),
                "updated_at": datetime.now().isoformat(),
                "source_timestamp": str(record.get("timestamp", "")),
            }
        else:
            old_content = self._normalize_text(str(existing.get("content", "")))
            if old_content == content:
                decision = "SKIP"
                payload = existing
            elif self._category_mergeable(category):
                decision = "MERGE"
                merged = [line for line in [old_content, content] if line]
                unique_lines: List[str] = []
                for line in merged:
                    if line not in unique_lines:
                        unique_lines.append(line)
                payload = dict(existing)
                payload["content"] = "\n".join(unique_lines)
                payload["updated_at"] = datetime.now().isoformat()
                payload["source_timestamp"] = str(record.get("timestamp", ""))
            else:
                decision = "UPDATE"
                payload = dict(existing)
                payload["content"] = content
                payload["updated_at"] = datetime.now().isoformat()
                payload["source_timestamp"] = str(record.get("timestamp", ""))

        if decision != "SKIP":
            store[key] = payload
            self._persist_category_store(category)

        self._append_decision(
            {
                "timestamp": datetime.now().isoformat(),
                "kind": "memory_extraction",
                "category": category,
                "key": key,
                "decision": decision,
                "source_timestamp": str(record.get("timestamp", "")),
            }
        )

    def add_memory(
        self,
        *,
        content: str,
        sender: str,
        timestamp: datetime,
        metadata: Optional[Dict[str, Any]] = None,
        from_reindex: bool = False,
    ) -> None:
        iso_ts = self._safe_iso(timestamp)
        try:
            date_str = datetime.fromisoformat(iso_ts).strftime("%Y-%m-%d")
        except ValueError:
            date_str = datetime.now().strftime("%Y-%m-%d")
        record = {
            "content": str(content or ""),
            "sender": str(sender or ""),
            "timestamp": iso_ts,
            "date": date_str,
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            self._records.append(record)
            self._append_record(record)
        if not from_reindex:
            self._forward_to_openviking(sender=record["sender"], content=record["content"])
            self._extract_long_term_memory(record)

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit))
        with self._lock:
            return [dict(item) for item in self._records[-safe_limit:]]

    def fts_search(self, query: str, limit: int = 5) -> List[Tuple[str, str, str, float]]:
        terms = self._build_search_terms(query)
        if not terms:
            return []
        scored: List[Tuple[str, str, str, float]] = []
        with self._lock:
            rows = list(self._records)
        for idx, item in enumerate(reversed(rows)):
            content = str(item.get("content", ""))
            content_lower = content.lower()
            matched = [term for term in terms if term in content_lower]
            if not matched:
                continue
            coverage = len(set(matched)) / max(1, len(terms))
            recency_boost = max(0.0, 1.0 - (idx * 0.02))
            score = round(min(1.0, 0.35 + (coverage * 0.45) + (recency_boost * 0.2)), 4)
            scored.append((content, str(item.get("sender", "")), str(item.get("timestamp", "")), score))
        scored.sort(key=lambda item: item[3], reverse=True)
        return scored[: max(1, int(limit))]

    async def hybrid_search(
        self,
        *,
        query: str,
        limit: int = 5,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> List[Dict[str, Any]]:
        text_rows = self.fts_search(query=query, limit=max(1, int(limit)))
        if not text_rows:
            return []
        out: List[Dict[str, Any]] = []
        for content, sender, ts, score in text_rows:
            fts_score = float(score)
            vec_score = float(score) * 0.9
            blended = round((vector_weight * vec_score) + (text_weight * fts_score), 4)
            out.append(
                {
                    "content": content,
                    "sender": sender,
                    "timestamp": ts,
                    "vec_score": round(vec_score, 4),
                    "fts_score": round(fts_score, 4),
                    "score": blended,
                }
            )
        return out[: max(1, int(limit))]

    def delete_by_date(self, date_str: str) -> None:
        marker = str(date_str or "").strip()
        if not marker:
            return
        with self._lock:
            self._records = [item for item in self._records if str(item.get("date", "")) != marker]
            self._persist_all()

    def clear(self) -> None:
        with self._lock:
            self._records = []
            self._persist_all()

    def close(self) -> None:
        if self._client is None:
            return
        try:
            if self._active_session_id:
                self._commit_active_session(reason="shutdown")
        except Exception:
            logger.debug("Failed to commit active OpenViking session during close", exc_info=True)
        finally:
            try:
                self._client.close()
            except Exception:
                logger.debug("Failed to close OpenViking client cleanly", exc_info=True)
