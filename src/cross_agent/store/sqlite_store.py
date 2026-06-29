"""SQLite implementation of the Memory Store."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable

from cross_agent.models import (
    MemoryCandidate,
    MemoryOperation,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    OperationType,
    SearchRequest,
    utc_now_iso,
)
from cross_agent.utils.time import compare_timestamps


class SQLiteMemoryStore:
    def __init__(self, sqlite_path: str, redact_rejected_event_payload: bool = True):
        self._path = Path(sqlite_path)
        self._redact_rejected_event_payload = redact_rejected_event_payload
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_state (
                    memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    assertion_mode TEXT NOT NULL,
                    literalness TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    sensitivity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_turn_ids_json TEXT NOT NULL,
                    source_session_id TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    supersedes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    extraction_source TEXT NOT NULL DEFAULT 'unknown',
                    write_score REAL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_state_active
                  ON memory_state(tenant_id, user_id, status, type);
                CREATE INDEX IF NOT EXISTS idx_memory_state_slot
                  ON memory_state(tenant_id, user_id, status, type, subject, predicate, scope);
                CREATE INDEX IF NOT EXISTS idx_memory_state_source
                  ON memory_state(tenant_id, user_id, source_session_id);

                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY,
                    memory_id TEXT,
                    operation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_turn_ids_json TEXT NOT NULL,
                    event_time TEXT,
                    system_time TEXT NOT NULL,
                    actor TEXT NOT NULL
                );
                """
            )
            self._ensure_memory_state_columns(conn)

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memory_events")
            conn.execute("DELETE FROM memory_state")

    def apply(self, operation: MemoryOperation) -> MemoryRecord | None:
        if operation.operation == OperationType.REJECT:
            self._append_event(None, operation)
            return None
        if operation.operation == OperationType.REINFORCE and operation.target_memory_id:
            return self._reinforce(operation)
        if operation.operation == OperationType.SUPERSEDE and operation.target_memory_id:
            return self._supersede(operation)
        if operation.operation == OperationType.ARCHIVE and operation.target_memory_id:
            return self._archive(operation)
        if operation.operation == OperationType.CREATE_HISTORICAL:
            return self._create_historical(operation)
        if operation.operation == OperationType.CREATE_TENTATIVE:
            return self._create_record(
                operation.candidate,
                operation,
                status=MemoryStatus.TENTATIVE,
            )
        if operation.operation == OperationType.PROMOTE_TENTATIVE:
            return self._promote_tentative(operation)
        if operation.operation == OperationType.CREATE:
            return self._create(operation.candidate, operation)
        raise ValueError(f"Unsupported operation: {operation.operation}")

    def find_active_by_source_session(
        self, tenant_id: str, user_id: str, source_session_id: str | None
    ) -> MemoryRecord | None:
        if not source_session_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_state
                WHERE tenant_id=? AND user_id=? AND source_session_id=?
                  AND type=? AND predicate=?
                  AND status IN (?, ?)
                LIMIT 1
                """,
                (
                    tenant_id,
                    user_id,
                    source_session_id,
                    MemoryType.EVENT.value,
                    "session_evidence",
                    MemoryStatus.ACTIVE.value,
                    MemoryStatus.TENTATIVE.value,
                ),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def find_active_by_slot(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        subject: str,
        predicate: str,
        scope: str,
    ) -> MemoryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_state
                WHERE tenant_id=? AND user_id=? AND type=? AND subject=?
                  AND predicate=? AND scope=? AND status=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (
                    tenant_id,
                    user_id,
                    memory_type,
                    subject,
                    predicate,
                    scope,
                    MemoryStatus.ACTIVE.value,
                ),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def find_tentative_by_slot(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        subject: str,
        predicate: str,
        scope: str,
    ) -> MemoryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_state
                WHERE tenant_id=? AND user_id=? AND type=? AND subject=?
                  AND predicate=? AND scope=? AND status=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (
                    tenant_id,
                    user_id,
                    memory_type,
                    subject,
                    predicate,
                    scope,
                    MemoryStatus.TENTATIVE.value,
                ),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_active(self, request: SearchRequest) -> list[MemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_state
                WHERE tenant_id=? AND user_id=? AND status=?
                ORDER BY importance DESC, confidence DESC, updated_at DESC
                """,
                (request.tenant_id, request.user_id, MemoryStatus.ACTIVE.value),
            ).fetchall()
        records = [self._row_to_record(row) for row in rows]
        reference = request.occurred_at or utc_now_iso()
        records = [
            record
            for record in records
            if (
                compare_timestamps(record.valid_from, reference) in {None, -1, 0}
                and compare_timestamps(record.valid_to, reference) not in {-1, 0}
            )
        ]
        if request.allow_sensitive:
            return records
        return [r for r in records if r.sensitivity not in {"high", "sensitive"}]

    def debug_counts(self, tenant_id: str, user_id: str) -> dict[str, int]:
        with self._connect() as conn:
            active = conn.execute(
                """
                SELECT COUNT(*) FROM memory_state
                WHERE tenant_id=? AND user_id=? AND status=?
                """,
                (tenant_id, user_id, MemoryStatus.ACTIVE.value),
            ).fetchone()[0]
            all_state = conn.execute(
                """
                SELECT COUNT(*) FROM memory_state
                WHERE tenant_id=? AND user_id=?
                """,
                (tenant_id, user_id),
            ).fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]
        return {"active_memories": int(active), "all_memories": int(all_state), "events": int(events)}

    def _create(self, candidate: MemoryCandidate, operation: MemoryOperation) -> MemoryRecord:
        return self._create_record(candidate, operation, status=MemoryStatus.ACTIVE)

    def _create_historical(self, operation: MemoryOperation) -> MemoryRecord:
        target = (
            self._find_by_id(operation.target_memory_id)
            if operation.target_memory_id
            else None
        )
        valid_to = operation.candidate.valid_to
        if valid_to is None and target is not None:
            valid_to = target.valid_from
        return self._create_record(
            operation.candidate,
            operation,
            status=MemoryStatus.SUPERSEDED,
            valid_to=valid_to,
        )

    def _create_record(
        self,
        candidate: MemoryCandidate,
        operation: MemoryOperation,
        status: MemoryStatus,
        valid_to: str | None = None,
    ) -> MemoryRecord:
        now = utc_now_iso()
        memory_id = "mem_" + uuid.uuid4().hex[:16]
        record = MemoryRecord(
            memory_id=memory_id,
            tenant_id=candidate.tenant_id,
            user_id=candidate.user_id,
            memory_type=candidate.memory_type,
            subject=candidate.subject,
            predicate=candidate.predicate,
            value=candidate.value,
            scope=candidate.scope,
            assertion_mode=candidate.assertion_mode,
            literalness=candidate.literalness,
            confidence=candidate.confidence,
            importance=candidate.importance,
            sensitivity=candidate.sensitivity,
            status=status,
            source_turn_ids=candidate.source_turn_ids,
            source_session_id=candidate.source_session_id,
            valid_from=candidate.valid_from,
            valid_to=valid_to if valid_to is not None else candidate.valid_to,
            supersedes=None,
            created_at=now,
            updated_at=now,
            extraction_source=candidate.extraction_source,
            write_score=candidate.write_score,
        )
        with self._connect() as conn:
            self._insert_record(conn, record)
        self._append_event(record.memory_id, operation)
        return record

    def _reinforce(self, operation: MemoryOperation) -> MemoryRecord:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_state WHERE memory_id=?",
                (operation.target_memory_id,),
            ).fetchone()
            if not row:
                return self._create(operation.candidate, operation)
            record = self._row_to_record(row)
            confidence = min(0.99, record.confidence + 0.02)
            importance = min(0.99, record.importance + 0.02)
            write_score = max(
                record.write_score or 0.0,
                operation.candidate.write_score or 0.0,
            )
            sources = {record.extraction_source, operation.candidate.extraction_source}
            extraction_source = (
                "hybrid"
                if len(sources - {"unknown"}) > 1
                else operation.candidate.extraction_source
                if record.extraction_source == "unknown"
                else record.extraction_source
            )
            conn.execute(
                """
                UPDATE memory_state
                SET confidence=?, importance=?, extraction_source=?,
                    write_score=?, updated_at=?
                WHERE memory_id=?
                """,
                (
                    confidence,
                    importance,
                    extraction_source,
                    write_score,
                    now,
                    record.memory_id,
                ),
            )
        self._append_event(record.memory_id, operation)
        refreshed = self._find_by_id(record.memory_id)
        if refreshed is None:
            raise RuntimeError("reinforced memory disappeared")
        return refreshed

    def _find_by_id(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_state WHERE memory_id=?",
                (memory_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def _supersede(self, operation: MemoryOperation) -> MemoryRecord:
        now = utc_now_iso()
        new_memory_id = "mem_" + uuid.uuid4().hex[:16]
        candidate = operation.candidate
        replacement = MemoryRecord(
            memory_id=new_memory_id,
            tenant_id=candidate.tenant_id,
            user_id=candidate.user_id,
            memory_type=candidate.memory_type,
            subject=candidate.subject,
            predicate=candidate.predicate,
            value=candidate.value,
            scope=candidate.scope,
            assertion_mode=candidate.assertion_mode,
            literalness=candidate.literalness,
            confidence=candidate.confidence,
            importance=candidate.importance,
            sensitivity=candidate.sensitivity,
            status=MemoryStatus.ACTIVE,
            source_turn_ids=candidate.source_turn_ids,
            source_session_id=candidate.source_session_id,
            valid_from=candidate.valid_from,
            valid_to=candidate.valid_to,
            supersedes=operation.target_memory_id,
            created_at=now,
            updated_at=now,
            extraction_source=candidate.extraction_source,
            write_score=candidate.write_score,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_state
                SET status=?, valid_to=COALESCE(valid_to, ?), updated_at=?
                WHERE memory_id=? AND status=?
                """,
                (
                    MemoryStatus.SUPERSEDED.value,
                    candidate.valid_from or now,
                    now,
                    operation.target_memory_id,
                    MemoryStatus.ACTIVE.value,
                ),
            )
            conn.execute(
                """
                UPDATE memory_state
                SET status=?, valid_to=COALESCE(valid_to, ?), updated_at=?
                WHERE tenant_id=? AND user_id=? AND type=? AND subject=?
                  AND predicate=? AND scope=? AND status=?
                """,
                (
                    MemoryStatus.ARCHIVED.value,
                    candidate.valid_from or now,
                    now,
                    candidate.tenant_id,
                    candidate.user_id,
                    candidate.memory_type.value,
                    candidate.subject,
                    candidate.predicate,
                    candidate.scope,
                    MemoryStatus.TENTATIVE.value,
                ),
            )
            self._insert_record(conn, replacement)
        self._append_event(replacement.memory_id, operation)
        return replacement

    def _promote_tentative(self, operation: MemoryOperation) -> MemoryRecord:
        if not operation.target_memory_id or not operation.secondary_memory_id:
            raise ValueError("promote_tentative requires tentative and active ids")
        now = utc_now_iso()
        tentative = self._find_by_id(operation.target_memory_id)
        active = self._find_by_id(operation.secondary_memory_id)
        if tentative is None or active is None:
            raise RuntimeError("tentative promotion target disappeared")
        candidate = operation.candidate
        confidence = min(
            0.99,
            max(tentative.confidence, candidate.confidence) + 0.05,
        )
        importance = min(
            0.99,
            max(tentative.importance, candidate.importance) + 0.02,
        )
        write_score = max(
            tentative.write_score or 0.0,
            candidate.write_score or 0.0,
        )
        sources = {tentative.extraction_source, candidate.extraction_source}
        extraction_source = "hybrid" if len(sources) > 1 else next(iter(sources))
        source_turn_ids = list(
            dict.fromkeys(tentative.source_turn_ids + candidate.source_turn_ids)
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_state
                SET status=?, valid_to=COALESCE(valid_to, ?), updated_at=?
                WHERE memory_id=? AND status=?
                """,
                (
                    MemoryStatus.SUPERSEDED.value,
                    candidate.valid_from or now,
                    now,
                    active.memory_id,
                    MemoryStatus.ACTIVE.value,
                ),
            )
            conn.execute(
                """
                UPDATE memory_state
                SET status=?, confidence=?, importance=?, source_turn_ids_json=?,
                    source_session_id=?, valid_from=?, valid_to=NULL, supersedes=?,
                    extraction_source=?, write_score=?, updated_at=?
                WHERE memory_id=? AND status=?
                """,
                (
                    MemoryStatus.ACTIVE.value,
                    confidence,
                    importance,
                    json.dumps(source_turn_ids, ensure_ascii=False),
                    candidate.source_session_id,
                    candidate.valid_from or tentative.valid_from,
                    active.memory_id,
                    extraction_source,
                    write_score,
                    now,
                    tentative.memory_id,
                    MemoryStatus.TENTATIVE.value,
                ),
            )
            conn.execute(
                """
                UPDATE memory_state
                SET status=?, valid_to=COALESCE(valid_to, ?), updated_at=?
                WHERE tenant_id=? AND user_id=? AND type=? AND subject=?
                  AND predicate=? AND scope=? AND status=? AND memory_id<>?
                """,
                (
                    MemoryStatus.ARCHIVED.value,
                    candidate.valid_from or now,
                    now,
                    candidate.tenant_id,
                    candidate.user_id,
                    candidate.memory_type.value,
                    candidate.subject,
                    candidate.predicate,
                    candidate.scope,
                    MemoryStatus.TENTATIVE.value,
                    tentative.memory_id,
                ),
            )
        self._append_event(tentative.memory_id, operation)
        promoted = self._find_by_id(tentative.memory_id)
        if promoted is None:
            raise RuntimeError("promoted tentative memory disappeared")
        return promoted

    def _archive(self, operation: MemoryOperation) -> MemoryRecord:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_state
                SET status=?, valid_to=COALESCE(valid_to, ?), updated_at=?
                WHERE memory_id=? AND status=?
                """,
                (
                    MemoryStatus.ARCHIVED.value,
                    operation.candidate.valid_from or now,
                    now,
                    operation.target_memory_id,
                    MemoryStatus.ACTIVE.value,
                ),
            )
        self._append_event(operation.target_memory_id, operation)
        archived = self._find_by_id(operation.target_memory_id)
        if archived is None:
            raise RuntimeError("archived memory disappeared")
        return archived

    def _insert_record(self, conn: sqlite3.Connection, record: MemoryRecord) -> None:
        conn.execute(
            """
            INSERT INTO memory_state
            (memory_id, tenant_id, user_id, type, subject, predicate, value_json,
             scope, assertion_mode, literalness, confidence, importance,
             sensitivity, status, source_turn_ids_json, source_session_id,
             valid_from, valid_to, supersedes, created_at, updated_at,
             extraction_source, write_score)
            VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._record_values(record),
        )

    def _ensure_memory_state_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(memory_state)").fetchall()
        }
        if "extraction_source" not in columns:
            conn.execute(
                "ALTER TABLE memory_state ADD COLUMN extraction_source "
                "TEXT NOT NULL DEFAULT 'unknown'"
            )
        if "write_score" not in columns:
            conn.execute("ALTER TABLE memory_state ADD COLUMN write_score REAL")

    def _append_event(self, memory_id: str | None, operation: MemoryOperation) -> None:
        candidate_payload: Any = operation.candidate.value
        if (
            operation.operation == OperationType.REJECT
            and self._redact_rejected_event_payload
        ):
            candidate_payload = {
                "redacted": True,
                "type": operation.candidate.memory_type.value,
                "predicate": operation.candidate.predicate,
                "scope": operation.candidate.scope,
                "assertion_mode": operation.candidate.assertion_mode,
                "sensitivity": operation.candidate.sensitivity,
            }
        payload = {
            "operation": operation.operation.value,
            "reason": operation.reason,
            "candidate": candidate_payload,
            "candidate_meta": {
                "extraction_source": operation.candidate.extraction_source,
                "write_score": operation.candidate.write_score,
            },
            "target_memory_id": operation.target_memory_id,
            "secondary_memory_id": operation.secondary_memory_id,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_" + uuid.uuid4().hex[:16],
                    memory_id,
                    operation.operation.value,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(operation.candidate.source_turn_ids, ensure_ascii=False),
                    operation.candidate.valid_from,
                    utc_now_iso(),
                    "system",
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _record_values(self, record: MemoryRecord) -> Iterable[Any]:
        return (
            record.memory_id,
            record.tenant_id,
            record.user_id,
            record.memory_type.value,
            record.subject,
            record.predicate,
            json.dumps(record.value, ensure_ascii=False),
            record.scope,
            record.assertion_mode,
            record.literalness,
            record.confidence,
            record.importance,
            record.sensitivity,
            record.status.value,
            json.dumps(record.source_turn_ids, ensure_ascii=False),
            record.source_session_id,
            record.valid_from,
            record.valid_to,
            record.supersedes,
            record.created_at,
            record.updated_at,
            record.extraction_source,
            record.write_score,
        )

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            memory_type=MemoryType(row["type"]),
            subject=row["subject"],
            predicate=row["predicate"],
            value=json.loads(row["value_json"]),
            scope=row["scope"],
            assertion_mode=row["assertion_mode"],
            literalness=row["literalness"],
            confidence=float(row["confidence"]),
            importance=float(row["importance"]),
            sensitivity=row["sensitivity"],
            status=MemoryStatus(row["status"]),
            source_turn_ids=json.loads(row["source_turn_ids_json"]),
            source_session_id=row["source_session_id"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            supersedes=row["supersedes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            extraction_source=row["extraction_source"],
            write_score=(
                float(row["write_score"])
                if row["write_score"] is not None
                else None
            ),
        )
