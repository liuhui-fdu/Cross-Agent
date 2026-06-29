"""Memory Governor: converts candidates into storage operations."""

from __future__ import annotations

import json
from typing import Protocol

from cross_agent.config import WriterConfig
from cross_agent.models import (
    MemoryCandidate,
    MemoryOperation,
    MemoryRecord,
    MemoryType,
    OperationType,
)
from cross_agent.policies.privacy import PrivacyPolicy
from cross_agent.policies.write_policy import WritePolicy
from cross_agent.store.base import MemoryStore
from cross_agent.utils.text import normalize
from cross_agent.utils.time import compare_timestamps


class MemoryGovernor(Protocol):
    def decide(self, candidate: MemoryCandidate) -> MemoryOperation:
        ...


class RuleBasedMemoryGovernor:
    def __init__(
        self,
        store: MemoryStore,
        write_policy: WritePolicy,
        privacy_policy: PrivacyPolicy,
        config: WriterConfig,
    ):
        self._store = store
        self._write_policy = write_policy
        self._privacy_policy = privacy_policy
        self._config = config

    def decide(self, candidate: MemoryCandidate) -> MemoryOperation:
        if candidate.assertion_mode == "do_not_store":
            return MemoryOperation(
                OperationType.REJECT,
                candidate,
                reason="user_requested_no_store",
            )
        can_store, privacy_reason = self._privacy_policy.can_store(candidate)
        if not can_store:
            return MemoryOperation(OperationType.REJECT, candidate, reason=privacy_reason)
        admitted, write_reason = self._write_policy.admits(candidate)
        if not admitted:
            return MemoryOperation(OperationType.REJECT, candidate, reason=write_reason)
        if candidate.assertion_mode == "forget":
            target = self._store.find_active_by_slot(
                candidate.tenant_id,
                candidate.user_id,
                candidate.memory_type.value,
                candidate.subject,
                candidate.predicate,
                candidate.scope,
            )
            if target is None:
                return MemoryOperation(
                    OperationType.REJECT,
                    candidate,
                    reason="forget_target_not_found",
                )
            return MemoryOperation(
                OperationType.ARCHIVE,
                candidate,
                target_memory_id=target.memory_id,
                reason="user_requested_forget",
            )
        if candidate.memory_type == MemoryType.EVENT and candidate.predicate == "session_evidence":
            existing = self._store.find_active_by_source_session(
                candidate.tenant_id, candidate.user_id, candidate.source_session_id
            )
            if existing:
                return MemoryOperation(
                    OperationType.REINFORCE,
                    candidate,
                    target_memory_id=existing.memory_id,
                    reason="same_source_session",
                )
            return MemoryOperation(OperationType.CREATE, candidate, reason="new_session_evidence")

        slot_record = self._store.find_active_by_slot(
            candidate.tenant_id,
            candidate.user_id,
            candidate.memory_type.value,
            candidate.subject,
            candidate.predicate,
            candidate.scope,
        )
        if not slot_record:
            return MemoryOperation(OperationType.CREATE, candidate, reason="new_structured_slot")
        time_order = compare_timestamps(candidate.valid_from, slot_record.valid_from)
        if _same_value(slot_record.value, candidate.value):
            if time_order == -1:
                return MemoryOperation(
                    OperationType.REJECT,
                    candidate,
                    target_memory_id=slot_record.memory_id,
                    reason="stale_duplicate_evidence",
                )
            return MemoryOperation(
                OperationType.REINFORCE,
                candidate,
                target_memory_id=slot_record.memory_id,
                reason="same_slot_same_value",
            )
        tentative = self._store.find_tentative_by_slot(
            candidate.tenant_id,
            candidate.user_id,
            candidate.memory_type.value,
            candidate.subject,
            candidate.predicate,
            candidate.scope,
        )
        if tentative and _same_value(tentative.value, candidate.value):
            tentative_time_order = compare_timestamps(
                candidate.valid_from,
                tentative.valid_from,
            )
            if tentative_time_order != -1:
                if candidate.source_session_id == tentative.source_session_id:
                    return MemoryOperation(
                        OperationType.REJECT,
                        candidate,
                        target_memory_id=tentative.memory_id,
                        reason="same_source_tentative_duplicate",
                    )
                independent_support = (
                    _candidate_score(candidate)
                    >= _record_score(tentative)
                    - self._config.candidate_conflict_margin
                )
                if candidate.assertion_mode == "explicit" or independent_support:
                    return MemoryOperation(
                        OperationType.PROMOTE_TENTATIVE,
                        candidate,
                        target_memory_id=tentative.memory_id,
                        secondary_memory_id=slot_record.memory_id,
                        reason="tentative_confirmed_by_new_evidence",
                    )
                return MemoryOperation(
                    OperationType.REINFORCE,
                    candidate,
                    target_memory_id=tentative.memory_id,
                    reason="tentative_received_weak_support",
                )
        return self._resolve_value_change(candidate, slot_record, time_order)

    def _resolve_value_change(
        self,
        candidate: MemoryCandidate,
        existing: MemoryRecord,
        time_order: int | None,
    ) -> MemoryOperation:
        target = existing.memory_id
        if time_order == -1:
            return MemoryOperation(
                OperationType.CREATE_HISTORICAL,
                candidate,
                target_memory_id=target,
                reason="stale_value_preserved_as_history",
            )

        new_score = _candidate_score(candidate)
        old_score = _record_score(existing)
        margin = self._config.candidate_conflict_margin
        if time_order == 1:
            if (
                candidate.assertion_mode == "inferred"
                and new_score < old_score + margin
            ):
                return MemoryOperation(
                    OperationType.CREATE_TENTATIVE,
                    candidate,
                    target_memory_id=target,
                    reason="newer_inference_not_strong_enough",
                )
            return MemoryOperation(
                OperationType.SUPERSEDE,
                candidate,
                target_memory_id=target,
                reason="newer_value_supersedes_active",
            )

        if time_order == 0:
            if (
                candidate.assertion_mode == "explicit"
                and new_score >= old_score + margin
            ):
                return MemoryOperation(
                    OperationType.SUPERSEDE,
                    candidate,
                    target_memory_id=target,
                    reason="same_time_higher_score_supersedes_active",
                )
            return MemoryOperation(
                OperationType.CREATE_TENTATIVE,
                candidate,
                target_memory_id=target,
                reason="same_time_unresolved_conflict",
            )

        if candidate.valid_from and not existing.valid_from:
            if candidate.assertion_mode == "explicit":
                return MemoryOperation(
                    OperationType.SUPERSEDE,
                    candidate,
                    target_memory_id=target,
                    reason="timed_explicit_value_replaces_undated_active",
                )
            return MemoryOperation(
                OperationType.CREATE_TENTATIVE,
                candidate,
                target_memory_id=target,
                reason="timed_inference_conflicts_with_undated_active",
            )

        if not candidate.valid_from and existing.valid_from:
            return MemoryOperation(
                OperationType.CREATE_TENTATIVE,
                candidate,
                target_memory_id=target,
                reason="undated_value_cannot_replace_timed_active",
            )

        if (
            candidate.assertion_mode == "explicit"
            and new_score >= old_score + margin
        ):
            return MemoryOperation(
                OperationType.SUPERSEDE,
                candidate,
                target_memory_id=target,
                reason="undated_higher_score_supersedes_active",
            )
        return MemoryOperation(
            OperationType.CREATE_TENTATIVE,
            candidate,
            target_memory_id=target,
            reason="undated_unresolved_conflict",
        )


def _same_value(left: object, right: object) -> bool:
    left_text = json.dumps(left, ensure_ascii=False, sort_keys=True)
    right_text = json.dumps(right, ensure_ascii=False, sort_keys=True)
    return normalize(left_text) == normalize(right_text)


def _candidate_score(candidate: MemoryCandidate) -> float:
    if candidate.write_score is not None:
        return candidate.write_score
    return 0.50 * candidate.confidence + 0.30 * candidate.importance + 0.20


def _record_score(record: MemoryRecord) -> float:
    if record.write_score is not None:
        return record.write_score
    return 0.50 * record.confidence + 0.30 * record.importance + 0.20
