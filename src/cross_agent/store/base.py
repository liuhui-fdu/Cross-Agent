"""Storage abstractions."""

from __future__ import annotations

from typing import Protocol

from cross_agent.models import MemoryOperation, MemoryRecord, SearchRequest


class MemoryStore(Protocol):
    def initialize(self) -> None:
        ...

    def clear(self) -> None:
        ...

    def apply(self, operation: MemoryOperation) -> MemoryRecord | None:
        ...

    def find_active_by_source_session(
        self, tenant_id: str, user_id: str, source_session_id: str | None
    ) -> MemoryRecord | None:
        ...

    def find_active_by_slot(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        subject: str,
        predicate: str,
        scope: str,
    ) -> MemoryRecord | None:
        ...

    def find_tentative_by_slot(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        subject: str,
        predicate: str,
        scope: str,
    ) -> MemoryRecord | None:
        ...

    def list_active(self, request: SearchRequest) -> list[MemoryRecord]:
        ...

    def debug_counts(self, tenant_id: str, user_id: str) -> dict[str, int]:
        ...
