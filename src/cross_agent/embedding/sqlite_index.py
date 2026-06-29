"""Persistent SQLite vector cache with exact cosine search and lazy backfill."""

from __future__ import annotations

import hashlib
import sqlite3
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cross_agent.config import EmbeddingConfig
from cross_agent.embedding.client import EmbeddingProvider
from cross_agent.models import MemoryRecord, utc_now_iso
from cross_agent.reader.scoring import record_text


@dataclass(frozen=True)
class VectorSearchResult:
    scores: dict[str, float]
    cache_hits: int
    embedded_count: int
    error: str | None = None


class VectorIndex(Protocol):
    def initialize(self) -> None:
        ...

    def clear(self) -> None:
        ...

    def sync_records(self, records: list[MemoryRecord]) -> VectorSearchResult:
        ...

    def search(
        self,
        query: str,
        records: list[MemoryRecord],
    ) -> VectorSearchResult:
        ...


class SQLiteVectorIndex:
    def __init__(
        self,
        sqlite_path: str,
        provider: EmbeddingProvider,
        config: EmbeddingConfig,
    ):
        self._path = Path(sqlite_path)
        self._provider = provider
        self._config = config

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    vector_blob BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (memory_id, model, dimensions)
                )
                """
            )

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memory_embeddings")

    def sync_records(self, records: list[MemoryRecord]) -> VectorSearchResult:
        if not records:
            return VectorSearchResult({}, 0, 0)
        documents = {
            record.memory_id: self._document_text(record)
            for record in records
        }
        hashes = {
            memory_id: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for memory_id, text in documents.items()
        }
        cached = self._load_cached(list(documents))
        missing = [
            memory_id
            for memory_id in documents
            if memory_id not in cached or cached[memory_id][0] != hashes[memory_id]
        ]
        if not missing:
            return VectorSearchResult({}, len(records), 0)
        try:
            vectors = self._provider.embed_texts(
                [documents[memory_id] for memory_id in missing]
            )
            self._upsert(
                [
                    (memory_id, hashes[memory_id], vector)
                    for memory_id, vector in zip(missing, vectors)
                ]
            )
            return VectorSearchResult({}, len(records) - len(missing), len(missing))
        except RuntimeError as exc:
            if self._config.required:
                raise RuntimeError(f"required document embedding failed: {exc}") from exc
            return VectorSearchResult(
                {},
                len(records) - len(missing),
                0,
                str(exc),
            )

    def search(
        self,
        query: str,
        records: list[MemoryRecord],
    ) -> VectorSearchResult:
        sync = self.sync_records(records)
        try:
            [query_vector] = self._provider.embed_texts(
                [self._config.query_prefix + query]
            )
        except RuntimeError as exc:
            if self._config.required:
                raise RuntimeError(f"required query embedding failed: {exc}") from exc
            return VectorSearchResult(
                {},
                sync.cache_hits,
                sync.embedded_count,
                str(exc),
            )
        cached = self._load_cached([record.memory_id for record in records])
        scores: dict[str, float] = {}
        for record in records:
            row = cached.get(record.memory_id)
            if row is None:
                continue
            vector = row[1]
            scores[record.memory_id] = max(
                0.0,
                min(1.0, sum(a * b for a, b in zip(query_vector, vector))),
            )
        return VectorSearchResult(
            scores,
            sync.cache_hits,
            sync.embedded_count,
            sync.error,
        )

    def _document_text(self, record: MemoryRecord) -> str:
        return self._config.document_template.format(
            title=record.predicate,
            text=record_text(record),
        )[: self._config.max_input_chars]

    def _load_cached(
        self,
        memory_ids: list[str],
    ) -> dict[str, tuple[str, list[float]]]:
        if not memory_ids:
            return {}
        placeholders = ",".join("?" for _ in memory_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT memory_id, content_hash, vector_blob
                FROM memory_embeddings
                WHERE model=? AND dimensions=?
                  AND memory_id IN ({placeholders})
                """,
                (self._provider.model, self._provider.dimensions, *memory_ids),
            ).fetchall()
        return {
            row["memory_id"]: (
                row["content_hash"],
                _decode_vector(row["vector_blob"]),
            )
            for row in rows
        }

    def _upsert(self, rows: list[tuple[str, str, list[float]]]) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO memory_embeddings
                (memory_id, model, dimensions, content_hash, vector_blob, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id, model, dimensions) DO UPDATE SET
                  content_hash=excluded.content_hash,
                  vector_blob=excluded.vector_blob,
                  updated_at=excluded.updated_at
                """,
                [
                    (
                        memory_id,
                        self._provider.model,
                        self._provider.dimensions,
                        content_hash,
                        array("f", vector).tobytes(),
                        now,
                    )
                    for memory_id, content_hash, vector in rows
                ],
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn


def _decode_vector(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)
