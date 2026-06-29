"""Domain models shared by the memory modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    TASK = "task"
    RELATION = "relation"
    EVENT = "event"
    SUMMARY = "summary"
    SENSITIVE = "sensitive"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    TENTATIVE = "tentative"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemoryIntent(str, Enum):
    REQUIRED = "required"
    BENEFICIAL = "beneficial"
    NONE = "none"


class OperationType(str, Enum):
    CREATE = "create"
    CREATE_HISTORICAL = "create_historical"
    CREATE_TENTATIVE = "create_tentative"
    PROMOTE_TENTATIVE = "promote_tentative"
    REINFORCE = "reinforce"
    SUPERSEDE = "supersede"
    ARCHIVE = "archive"
    REJECT = "reject"


@dataclass(frozen=True)
class Turn:
    turn_id: str
    role: str
    content: str
    occurred_at: Optional[str] = None


@dataclass(frozen=True)
class Session:
    session_id: str
    tenant_id: str
    user_id: str
    occurred_at: Optional[str]
    turns: List[Turn]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryCandidate:
    tenant_id: str
    user_id: str
    memory_type: MemoryType
    subject: str
    predicate: str
    value: Dict[str, Any]
    scope: str
    assertion_mode: str
    literalness: str
    confidence: float
    importance: float
    sensitivity: str
    source_turn_ids: List[str]
    source_session_id: Optional[str]
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    extraction_source: str = "rule"
    write_score: Optional[float] = None


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    tenant_id: str
    user_id: str
    memory_type: MemoryType
    subject: str
    predicate: str
    value: Dict[str, Any]
    scope: str
    assertion_mode: str
    literalness: str
    confidence: float
    importance: float
    sensitivity: str
    status: MemoryStatus
    source_turn_ids: List[str]
    source_session_id: Optional[str]
    valid_from: Optional[str]
    valid_to: Optional[str]
    created_at: str
    updated_at: str
    supersedes: Optional[str] = None
    extraction_source: str = "unknown"
    write_score: Optional[float] = None


@dataclass(frozen=True)
class MemoryOperation:
    operation: OperationType
    candidate: MemoryCandidate
    target_memory_id: Optional[str] = None
    secondary_memory_id: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class SearchRequest:
    tenant_id: str
    user_id: str
    query: str
    occurred_at: Optional[str] = None
    top_k: int = 5
    allow_sensitive: bool = False
    recent_context: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class QueryPlan:
    needs_memory: bool
    memory_types: List[MemoryType]
    query: str
    expanded_terms: List[str]
    top_k: int
    allow_sensitive: bool
    reason: str
    intent_confidence: float = 1.0
    decision_source: str = "rule"
    rule_score: float = 0.0
    memory_intent: MemoryIntent = MemoryIntent.REQUIRED


@dataclass(frozen=True)
class EvidenceItem:
    memory: MemoryRecord
    score: float
    lexical_score: float
    semantic_score: float
    token_cosine_score: float
    temporal_score: float
    reason: str
    snippet: str


@dataclass(frozen=True)
class EvidenceBundle:
    request: SearchRequest
    plan: QueryPlan
    items: List[EvidenceItem]
    trace: Dict[str, Any]


@dataclass(frozen=True)
class GuardedAnswer:
    answer: str
    used_memory_ids: List[str]
    abstained: bool
    trace: Dict[str, Any]


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
