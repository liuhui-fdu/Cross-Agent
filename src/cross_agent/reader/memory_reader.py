"""Memory Reader: hybrid recall, reranking, dedupe, and evidence packaging."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Mapping

from cross_agent.config import ReaderConfig
from cross_agent.embedding.sqlite_index import VectorIndex, VectorSearchResult
from cross_agent.models import (
    EvidenceBundle,
    EvidenceItem,
    MemoryIntent,
    MemoryRecord,
    MemoryType,
    SearchRequest,
)
from cross_agent.policies.privacy import PrivacyPolicy
from cross_agent.reader.query_planner import QueryPlanner
from cross_agent.reader.evidence_verifier import EvidenceVerifier
from cross_agent.reader.scoring import CorpusScorer, record_text
from cross_agent.store.base import MemoryStore
from cross_agent.utils.text import best_snippet, distinctive_terms, tokenize
from cross_agent.utils.time import parse_datetime, temporal_decay_score


class MemoryReader:
    def __init__(
        self,
        store: MemoryStore,
        planner: QueryPlanner,
        privacy_policy: PrivacyPolicy,
        config: ReaderConfig,
        temporal_half_life_days: Mapping[str, float],
        vector_index: VectorIndex | None = None,
        evidence_verifier: EvidenceVerifier | None = None,
    ):
        self._store = store
        self._planner = planner
        self._privacy_policy = privacy_policy
        self._config = config
        self._temporal_half_life_days = temporal_half_life_days
        self._vector_index = vector_index
        self._evidence_verifier = evidence_verifier

    def search(self, request: SearchRequest) -> EvidenceBundle:
        plan = self._planner.plan(request)
        if not plan.needs_memory:
            return EvidenceBundle(
                request,
                plan,
                [],
                {
                    "skipped": True,
                    "reason": plan.reason,
                    "memory_intent": self._intent_trace(plan),
                },
            )
        records = self._store.list_active(request)
        vector_result = (
            self._vector_index.search(plan.query, records)
            if self._vector_index is not None
            else VectorSearchResult({}, 0, 0, "embedding_disabled")
        )
        embedding_available = bool(vector_result.scores)
        scorer = CorpusScorer(records)
        base_terms = tokenize(plan.query) + plan.expanded_terms
        reference_time = parse_datetime(request.occurred_at)
        initial = self._rank(
            records,
            scorer,
            base_terms,
            plan.query,
            reference_time,
            vector_result.scores,
            embedding_available,
            set(plan.memory_types),
        )
        initial_disclosable = self._privacy_policy.filter_disclosable(
            initial,
            request.allow_sensitive,
        )
        probe_score = self._probe_score(initial_disclosable, embedding_available)
        probe_applied = plan.memory_intent == MemoryIntent.BENEFICIAL
        probe_accepted = (
            not probe_applied
            or probe_score >= self._config.memory_probe_min_score
        )
        feedback_terms = self._feedback_terms(initial[: self._config.feedback_top_n])
        final_terms = list(dict.fromkeys(base_terms + feedback_terms))
        reranked = self._rank(
            records,
            scorer,
            final_terms,
            plan.query,
            reference_time,
            vector_result.scores,
            embedding_available,
            set(plan.memory_types),
        )
        filtered = self._privacy_policy.filter_disclosable(reranked, request.allow_sensitive)
        candidates = self._dedupe(filtered)[: self._config.evidence_verifier_max_candidates]
        evidence = candidates[: plan.top_k] if probe_accepted else []
        verification_trace: dict[str, object] = {
            "applied": False,
            "sufficient": bool(evidence),
            "confidence": None,
            "relevant_memory_ids": [],
            "reason": "verifier_disabled",
        }
        if candidates and self._config.evidence_verifier_enabled:
            if self._evidence_verifier is None:
                if self._config.evidence_verifier_required:
                    raise RuntimeError("required evidence verifier is unavailable")
            else:
                try:
                    verification = self._evidence_verifier.verify(plan.query, candidates)
                    accepted = (
                        verification.sufficient
                        and verification.confidence
                        >= self._config.evidence_verifier_min_confidence
                    )
                    relevant_ids = set(verification.relevant_memory_ids)
                    evidence = (
                        [item for item in candidates if item.memory.memory_id in relevant_ids][
                            : plan.top_k
                        ]
                        if accepted
                        else []
                    )
                    verification_trace = {
                        "applied": True,
                        "sufficient": verification.sufficient,
                        "accepted": accepted,
                        "confidence": round(verification.confidence, 6),
                        "threshold": self._config.evidence_verifier_min_confidence,
                        "relevant_memory_ids": list(verification.relevant_memory_ids),
                        "reason": verification.reason,
                    }
                    if self._config.evidence_verifier_required and not accepted:
                        # Insufficient evidence is a valid model decision, not an API failure.
                        verification_trace["required_api_succeeded"] = True
                except (RuntimeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    if self._config.evidence_verifier_required:
                        raise RuntimeError(f"required evidence verification failed: {exc}") from exc
                    verification_trace = {
                        "applied": True,
                        "sufficient": False,
                        "accepted": False,
                        "confidence": None,
                        "relevant_memory_ids": [],
                        "reason": f"verifier_error:{type(exc).__name__}",
                    }
                    evidence = []
        return EvidenceBundle(
            request=request,
            plan=plan,
            items=evidence,
            trace={
                "memory_intent": self._intent_trace(plan),
                "candidate_count": len(records),
                "initial_top_sessions": [i.memory.source_session_id for i in initial[: plan.top_k]],
                "feedback_terms": feedback_terms,
                "final_terms": final_terms,
                "final_top_sessions": [i.memory.source_session_id for i in evidence],
                "evidence_probe": {
                    "applied": probe_applied,
                    "top_score": round(probe_score, 6),
                    "threshold": self._config.memory_probe_min_score,
                    "accepted": probe_accepted,
                },
                "evidence_verification": verification_trace,
                "embedding": {
                    "enabled": self._vector_index is not None,
                    "available": embedding_available,
                    "cache_hits": vector_result.cache_hits,
                    "embedded_count": vector_result.embedded_count,
                    "error": vector_result.error,
                },
            },
        )

    @staticmethod
    def _intent_trace(plan: QueryPlan) -> dict[str, object]:
        return {
            "needs_memory": plan.needs_memory,
            "decision_source": plan.decision_source,
            "confidence": plan.intent_confidence,
            "rule_score": plan.rule_score,
            "intent": plan.memory_intent.value,
            "reason": plan.reason,
            "memory_types": [item.value for item in plan.memory_types],
        }

    def _rank(
        self,
        records: list[MemoryRecord],
        scorer: CorpusScorer,
        terms: list[str],
        original_query: str,
        reference_time: datetime | None,
        embedding_scores: dict[str, float],
        embedding_available: bool,
        preferred_types: set[MemoryType],
    ) -> list[EvidenceItem]:
        ranked: list[EvidenceItem] = []
        for record in records:
            lexical = scorer.lexical_score(terms, record)
            token_cosine = scorer.token_cosine_score(terms, record)
            embedding = embedding_scores.get(record.memory_id, 0.0)
            temporal = self._temporal_score(record, reference_time)
            semantic_score = (
                self._config.embedding_weight * embedding
                + self._config.token_cosine_weight * token_cosine
                if embedding_available
                else (
                    self._config.embedding_weight
                    + self._config.token_cosine_weight
                )
                * token_cosine
            )
            score = (
                semantic_score
                + self._config.lexical_weight * lexical
                + self._config.temporal_weight * temporal
                + self._config.importance_weight * record.importance
                + self._config.confidence_weight * record.confidence
            )
            if record.memory_type != MemoryType.EVENT:
                score += self._config.structured_type_bonus
            if (
                len(preferred_types) < 6
                and record.memory_type in preferred_types
            ):
                score += self._config.memory_type_match_bonus
            if record.valid_to:
                score -= self._config.staleness_penalty
            if record.sensitivity in {"high", "sensitive"}:
                score -= self._config.privacy_penalty
            ranked.append(
                EvidenceItem(
                    memory=record,
                    score=round(max(score, 0.0), 6),
                    lexical_score=round(lexical, 6),
                    semantic_score=round(embedding, 6),
                    token_cosine_score=round(token_cosine, 6),
                    temporal_score=temporal,
                    reason=(
                        "hybrid_rerank embedding"
                        if embedding_available
                        else "hybrid_rerank token_fallback"
                    ),
                    snippet=best_snippet(record_text(record), original_query),
                )
            )
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked

    @staticmethod
    def _probe_score(
        items: list[EvidenceItem],
        embedding_available: bool,
    ) -> float:
        if not items:
            return 0.0
        best = 0.0
        for item in items:
            channels = [item.lexical_score, item.token_cosine_score]
            if embedding_available:
                channels.append(item.semantic_score)
            best = max(best, *channels)
        return best

    def _feedback_terms(self, items: list[EvidenceItem]) -> list[str]:
        terms: list[str] = []
        for item in items:
            text = record_text(item.memory)
            terms.extend(distinctive_terms(text, self._config.feedback_terms_per_doc))
        return list(dict.fromkeys(terms))

    def _temporal_score(
        self,
        record: MemoryRecord,
        reference_time: datetime | None,
    ) -> float:
        if record.valid_to:
            return 0.0
        half_life = float(
            self._temporal_half_life_days.get(record.memory_type.value, 180.0)
        )
        return temporal_decay_score(record.valid_from, half_life, reference_time)

    def _dedupe(self, items: list[EvidenceItem]) -> list[EvidenceItem]:
        seen: set[str] = set()
        result: list[EvidenceItem] = []
        for item in items:
            key = (
                f"event:{item.memory.source_session_id or item.memory.memory_id}"
                if item.memory.memory_type == MemoryType.EVENT
                else f"memory:{item.memory.memory_id}"
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result
