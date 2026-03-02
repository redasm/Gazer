"""Hybrid index layer: FTS5 full-text + Faiss vector search."""

import sqlite3
import os
import logging
import json
import threading
import numpy as np
import faiss
from datetime import datetime
from typing import List, Tuple, Optional

from llm.embedding import EmbeddingProvider

logger = logging.getLogger("GazerIndex")


class SQLiteIndex:
    """Hybrid memory index backed by SQLite FTS5 and Faiss."""

    def __init__(
        self,
        db_path: str = "data/openviking/legacy_index.db",
        index_path: str = "data/openviking/legacy_vector_index.faiss",
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        self.db_path = db_path
        self.index_path = index_path
        self.embedding_provider = embedding_provider

        dim = embedding_provider.dim if embedding_provider else 1536
        self.dim = dim
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # Use IndexIDMap for incremental add/remove without full rebuild
        self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(self.dim))
        self.id_map: List[Tuple[str, str, str]] = []  # metadata parallel to faiss IDs
        self._next_id: int = 0  # monotonically increasing faiss ID counter
        self._lock = threading.Lock()
        self._dirty = False

        self._init_db()
        self._load_or_rebuild_index()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # Enable WAL mode for better concurrent read/write performance
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_idx USING fts5(
                content,
                sender UNINDEXED,
                timestamp UNINDEXED,
                date UNINDEXED
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_vectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                sender TEXT,
                timestamp TEXT,
                embedding BLOB,
                metadata TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _load_or_rebuild_index(self) -> None:
        if os.path.exists(self.index_path):
            try:
                self.faiss_index = faiss.read_index(self.index_path)
                # Sync dimension from the loaded index
                self.dim = self.faiss_index.d
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT content, sender, timestamp FROM memory_vectors WHERE embedding IS NOT NULL"
                )
                self.id_map = cursor.fetchall()
                self._next_id = len(self.id_map)
                conn.close()
                logger.info(f"Loaded Faiss index ({len(self.id_map)} vectors, dim={self.dim}).")
                return
            except Exception as e:
                logger.error(f"Failed to load Faiss index: {e}, rebuilding...")
        self._rebuild_sync()

    def _rebuild_sync(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content, sender, timestamp, embedding "
            "FROM memory_vectors WHERE embedding IS NOT NULL"
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return

        all_vecs = []
        all_ids = []
        self.id_map = []
        self._next_id = 0
        for content, sender, ts, blob in rows:
            vec = np.frombuffer(blob, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            all_vecs.append(vec)
            all_ids.append(self._next_id)
            self.id_map.append((content, sender, ts))
            self._next_id += 1

        if all_vecs:
            # Auto-detect dimension from stored vectors (may differ from provider default)
            actual_dim = all_vecs[0].shape[0]
            if actual_dim != self.dim:
                logger.warning(f"Vector dimension mismatch: index={self.dim}, stored={actual_dim}. Adapting.")
                self.dim = actual_dim
            self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(self.dim))
            vecs_array = np.array(all_vecs, dtype=np.float32)
            ids_array = np.array(all_ids, dtype=np.int64)
            self.faiss_index.add_with_ids(vecs_array, ids_array)
            faiss.write_index(self.faiss_index, self.index_path)
            logger.info(f"Rebuilt Faiss index with {len(all_vecs)} vectors (dim={self.dim}).")

    async def get_embedding(self, text: str) -> Optional[np.ndarray]:
        if not self.embedding_provider:
            return None
        return await self.embedding_provider.embed(text)

    async def add_memory(
        self,
        content: str,
        sender: str,
        timestamp: datetime,
        metadata: Optional[dict] = None,
    ) -> None:
        embedding = await self.get_embedding(content)
        meta_json = json.dumps(metadata) if metadata else None
        self._save_to_db(
            content,
            sender,
            timestamp,
            embedding.tobytes() if embedding is not None else None,
            meta_json,
        )
        if embedding is not None:
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            with self._lock:
                vec_array = np.array([embedding], dtype=np.float32)
                id_array = np.array([self._next_id], dtype=np.int64)
                self.faiss_index.add_with_ids(vec_array, id_array)
                self.id_map.append(
                    (content, sender, timestamp.strftime("%Y-%m-%d %H:%M:%S"))
                )
                self._next_id += 1
                self._dirty = True

    def _save_to_db(
        self,
        content: str,
        sender: str,
        timestamp: datetime,
        embedding_blob: Optional[bytes],
        metadata_json: Optional[str],
    ):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        date_str = timestamp.strftime("%Y-%m-%d")
        try:
            cursor.execute(
                "INSERT INTO memories_idx(content, sender, timestamp, date) VALUES (?, ?, ?, ?)",
                (content, sender, time_str, date_str),
            )
            cursor.execute(
                "INSERT INTO memory_vectors(content, sender, timestamp, embedding, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (content, sender, time_str, embedding_blob, metadata_json),
            )
            conn.commit()
        finally:
            conn.close()

    def flush(self) -> None:
        """Write dirty faiss index to disk."""
        with self._lock:
            if self._dirty:
                faiss.write_index(self.faiss_index, self.index_path)
                self._dirty = False

    def close(self) -> None:
        """Flush pending writes and release resources."""
        self.flush()

    def fts_search(
        self, query: str, limit: int = 5
    ) -> List[Tuple[str, str, str, float]]:
        safe_query = '"' + query.replace('"', '""') + '"'
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT content, sender, timestamp, rank "
                "FROM memories_idx WHERE memories_idx MATCH ? ORDER BY rank LIMIT ?",
                (safe_query, limit),
            )
            results = []
            for content, sender, ts, rank in cursor.fetchall():
                score = 1.0 / (1.0 + abs(rank))
                results.append((content, sender, ts, score))
            return results
        except Exception as e:
            logger.error(f"FTS search failed: {e}")
            return []
        finally:
            conn.close()

    async def semantic_search(
        self, query: str, limit: int = 5
    ) -> List[Tuple[str, str, str, float]]:
        query_vec = await self.get_embedding(query)
        if query_vec is None or self.faiss_index.ntotal == 0:
            return []

        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm
        query_vec = np.array([query_vec], dtype=np.float32)

        scores, indices = self.faiss_index.search(query_vec, limit)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.id_map):
                continue
            meta = self.id_map[idx]
            results.append((*meta, float(score)))
        return results

    async def hybrid_search(
        self,
        query: str,
        limit: int = 5,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> List[dict]:
        vector_res = await self.semantic_search(query, limit=limit * 3)
        keyword_res = self.fts_search(query, limit=limit * 3)

        combined: dict = {}

        for content, sender, ts, score in vector_res:
            key = (content, ts)
            combined[key] = {
                "content": content,
                "sender": sender,
                "timestamp": ts,
                "vec_score": score,
                "fts_score": 0.0,
            }

        for content, sender, ts, score in keyword_res:
            key = (content, ts)
            if key in combined:
                combined[key]["fts_score"] = score
            else:
                combined[key] = {
                    "content": content,
                    "sender": sender,
                    "timestamp": ts,
                    "vec_score": 0.0,
                    "fts_score": score,
                }

        final_list = []
        for item in combined.values():
            item["score"] = (
                vector_weight * item["vec_score"] + text_weight * item["fts_score"]
            )
            final_list.append(item)

        final_list.sort(key=lambda x: x["score"], reverse=True)
        return final_list[:limit]

    def delete_by_date(self, date_str: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        try:
            cursor.execute("DELETE FROM memories_idx WHERE date = ?", (date_str,))
            cursor.execute(
                "DELETE FROM memory_vectors WHERE timestamp LIKE ?", (f"{date_str}%",)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to delete memories for {date_str}: {e}")
        finally:
            conn.close()
        self._rebuild_sync()

    def clear(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories_idx")
            cursor.execute("DELETE FROM memory_vectors")
            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(self.dim))
            self.id_map = []
            self._next_id = 0
            self._dirty = False
        if os.path.exists(self.index_path):
            os.remove(self.index_path)
