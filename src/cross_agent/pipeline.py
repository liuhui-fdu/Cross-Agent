"""Application assembly and orchestration."""

from __future__ import annotations

import os
from pathlib import Path

from cross_agent.answer.generator import ExtractiveAnswerGenerator, LLMAnswerGenerator
from cross_agent.config import Settings
from cross_agent.embedding.client import OpenAICompatibleEmbeddingClient
from cross_agent.embedding.sqlite_index import SQLiteVectorIndex
from cross_agent.governor.governor import RuleBasedMemoryGovernor
from cross_agent.guard.response_guard import ResponseGuard
from cross_agent.llm.openai_compatible import OpenAICompatibleChatClient
from cross_agent.models import (
    GuardedAnswer,
    MemoryOperation,
    MemoryStatus,
    OperationType,
    SearchRequest,
    Session,
)
from cross_agent.policies.privacy import PrivacyPolicy
from cross_agent.policies.write_policy import WritePolicy
from cross_agent.reader.memory_reader import MemoryReader
from cross_agent.reader.evidence_verifier import LLMEvidenceVerifier
from cross_agent.reader.query_planner import LLMMemoryIntentClassifier, QueryPlanner
from cross_agent.store.sqlite_store import SQLiteMemoryStore
from cross_agent.writer.extractor import (
    HeuristicSessionExtractor,
    HybridMemoryExtractor,
    LLMSemanticMemoryExtractor,
)
from cross_agent.writer.resolver import CandidateResolver


class CrossAgentApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        sqlite_path = Path(settings.storage.sqlite_path)
        if not sqlite_path.is_absolute():
            sqlite_path = Path.cwd() / sqlite_path
        self.store = SQLiteMemoryStore(
            str(sqlite_path),
            redact_rejected_event_payload=settings.writer.redact_rejected_event_payload,
        )
        self._llm_client = self._build_llm_client(settings)
        self.embedding_index = self._build_embedding_index(settings, str(sqlite_path))
        rule_extractor = HeuristicSessionExtractor(settings.writer)
        semantic_extractor = None
        if settings.writer.semantic_extraction_enabled and self._llm_client is not None:
            semantic_extractor = LLMSemanticMemoryExtractor(
                settings.writer,
                settings.llm,
                self._llm_client,
            )
        self.extractor = HybridMemoryExtractor(
            rule_extractor,
            semantic_extractor,
            settings.writer.max_candidates_per_session,
            settings.writer.semantic_extraction_required,
        )
        self.privacy_policy = PrivacyPolicy(settings.writer, settings.guard)
        self.write_policy = WritePolicy(settings.writer)
        self.candidate_resolver = CandidateResolver(
            settings.writer,
            self.write_policy,
            self.privacy_policy,
        )
        self.governor = RuleBasedMemoryGovernor(
            self.store,
            self.write_policy,
            self.privacy_policy,
            settings.writer,
        )
        intent_classifier = None
        if settings.reader.memory_intent_llm_enabled and self._llm_client is not None:
            intent_classifier = LLMMemoryIntentClassifier(
                settings.reader,
                settings.llm,
                self._llm_client,
            )
        evidence_verifier = None
        if settings.reader.evidence_verifier_enabled and self._llm_client is not None:
            evidence_verifier = LLMEvidenceVerifier(
                settings.reader,
                settings.llm,
                self._llm_client,
            )
        self.reader = MemoryReader(
            self.store,
            QueryPlanner(settings.reader, intent_classifier),
            self.privacy_policy,
            settings.reader,
            settings.writer.temporal_half_life_days,
            self.embedding_index,
            evidence_verifier,
        )
        self.answer_generator = self._build_answer_generator(settings)
        self.response_guard = ResponseGuard(settings.guard)
        self.last_ingest_trace = None

    def initialize(self, reset: bool = False) -> None:
        self.store.initialize()
        if self.embedding_index is not None:
            self.embedding_index.initialize()
        if reset:
            self.store.clear()
            if self.embedding_index is not None:
                self.embedding_index.clear()

    def ingest_session(self, session: Session) -> dict[str, int]:
        extracted = self.extractor.extract(session)
        resolution = self.candidate_resolver.resolve(extracted, session)
        created = reinforced = historical = tentative = promoted = archived = rejected = 0
        operation_trace = []
        active_records = []
        for candidate, reason in resolution.rejected:
            operation = MemoryOperation(
                OperationType.REJECT,
                candidate,
                reason=reason,
            )
            self.store.apply(operation)
            rejected += 1
            operation_trace.append(
                {
                    "operation": operation.operation.value,
                    "reason": reason,
                    "predicate": candidate.predicate,
                    "write_score": candidate.write_score,
                }
            )
        for candidate in resolution.selected:
            operation = self.governor.decide(candidate)
            record = self.store.apply(operation)
            if operation.operation.value == "reject":
                rejected += 1
            elif operation.operation.value == "reinforce":
                reinforced += 1
            elif operation.operation.value == "archive":
                archived += 1
            elif operation.operation.value == "create_historical":
                historical += 1
            elif operation.operation.value == "create_tentative":
                tentative += 1
            elif operation.operation.value == "promote_tentative":
                promoted += 1
            elif record is not None:
                created += 1
            if record is not None and record.status == MemoryStatus.ACTIVE:
                active_records.append(record)
            operation_trace.append(
                {
                    "operation": operation.operation.value,
                    "reason": operation.reason,
                    "predicate": candidate.predicate,
                    "write_score": candidate.write_score,
                }
            )
        embedding_sync = None
        if (
            self.embedding_index is not None
            and self.settings.embedding.index_on_write
            and active_records
        ):
            sync = self.embedding_index.sync_records(active_records)
            embedding_sync = {
                "cache_hits": sync.cache_hits,
                "embedded_count": sync.embedded_count,
                "error": sync.error,
            }
        self.last_ingest_trace = {
            **resolution.trace,
            "operations": operation_trace,
            "embedding_sync": embedding_sync,
        }
        return {
            "extracted": len(extracted),
            "selected": len(resolution.selected),
            "filtered": len(resolution.rejected),
            "dropped": resolution.dropped_count,
            "created": created,
            "reinforced": reinforced,
            "historical": historical,
            "tentative": tentative,
            "promoted": promoted,
            "archived": archived,
            "rejected": rejected,
        }

    def answer(self, request: SearchRequest) -> GuardedAnswer:
        evidence = self.reader.search(request)
        draft = self.answer_generator.generate(request.query, evidence)
        return self.response_guard.verify(draft, evidence)

    def search(self, request: SearchRequest):
        return self.reader.search(request)

    def debug_counts(self) -> dict[str, int]:
        return self.store.debug_counts(self.settings.app.tenant_id, self.settings.app.user_id)

    def _build_answer_generator(self, settings: Settings):
        if not settings.llm.enabled:
            return ExtractiveAnswerGenerator(settings.answer)
        if self._llm_client is None:
            raise RuntimeError("LLM is enabled but no chat client is available")
        return LLMAnswerGenerator(settings.answer, settings.llm, self._llm_client)

    def _build_llm_client(self, settings: Settings):
        if not settings.llm.enabled:
            return None
        api_key = os.environ.get(settings.llm.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"LLM is enabled but environment variable {settings.llm.api_key_env} is not set"
            )
        return OpenAICompatibleChatClient(
            base_url=settings.llm.base_url,
            api_key=api_key,
            model=settings.llm.model,
            timeout_seconds=settings.llm.timeout_seconds,
            max_retries=settings.llm.max_retries,
            retry_base_seconds=settings.llm.retry_base_seconds,
        )

    def _build_embedding_index(self, settings: Settings, sqlite_path: str):
        if not settings.embedding.enabled:
            return None
        api_key = os.environ.get(settings.embedding.api_key_env)
        if not api_key:
            raise RuntimeError(
                "Embedding is enabled but environment variable "
                f"{settings.embedding.api_key_env} is not set"
            )
        provider = OpenAICompatibleEmbeddingClient(settings.embedding, api_key)
        return SQLiteVectorIndex(sqlite_path, provider, settings.embedding)
