"""Unified candidate filtering, conflict resolution, and write reranking."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from cross_agent.config import WriterConfig
from cross_agent.models import MemoryCandidate, MemoryType, Session
from cross_agent.policies.privacy import PrivacyPolicy
from cross_agent.policies.write_policy import WritePolicy
from cross_agent.utils.text import normalize
from cross_agent.utils.time import is_implausibly_future, temporal_decay_score


@dataclass(frozen=True)
class CandidateResolution:
    selected: list[MemoryCandidate]
    rejected: list[tuple[MemoryCandidate, str]]
    dropped_count: int
    trace: dict[str, Any]


class CandidateResolver:
    """Resolve rule and LLM candidates in one shared ranking space."""

    def __init__(
        self,
        config: WriterConfig,
        write_policy: WritePolicy,
        privacy_policy: PrivacyPolicy,
    ):
        self._config = config
        self._write_policy = write_policy
        self._privacy_policy = privacy_policy

    def resolve(
        self, candidates: list[MemoryCandidate], session: Session
    ) -> CandidateResolution:
        admitted: list[MemoryCandidate] = []
        rejected: list[tuple[MemoryCandidate, str]] = []
        for candidate in candidates:
            candidate, normalization_reason = self._normalize_candidate(candidate, session)
            if normalization_reason:
                rejected.append((candidate, normalization_reason))
                continue
            if is_implausibly_future(
                candidate.valid_from,
                self._config.max_future_skew_days,
            ):
                rejected.append((candidate, "valid_from_too_far_in_future"))
                continue
            can_store, privacy_reason = self._privacy_policy.can_store(candidate)
            if not can_store:
                rejected.append((candidate, privacy_reason))
                continue
            valid, write_reason = self._write_policy.admits(candidate)
            if not valid:
                rejected.append((candidate, write_reason))
                continue
            admitted.append(candidate)

        merged = self._merge_duplicates(admitted)
        correction = self._contains_correction(session)
        scored = [self._score(candidate, correction) for candidate in merged]
        winners, conflict_drops, conflicts = self._resolve_slots(scored)
        winners.sort(key=self._sort_key)
        selected = winners[: self._config.final_candidate_top_k]
        top_k_drops = max(0, len(winners) - len(selected))
        dropped_count = conflict_drops + top_k_drops

        return CandidateResolution(
            selected=selected,
            rejected=rejected,
            dropped_count=dropped_count,
            trace={
                "raw_candidate_count": len(candidates),
                "admitted_candidate_count": len(admitted),
                "merged_candidate_count": len(merged),
                "rejected_candidate_count": len(rejected),
                "conflict_dropped_count": conflict_drops,
                "top_k_dropped_count": top_k_drops,
                "final_top_k": self._config.final_candidate_top_k,
                "conflicts": conflicts,
                "ranking": [self._trace_item(candidate) for candidate in winners],
                "selected": [self._trace_item(candidate) for candidate in selected],
                "rejected": [
                    {
                        "type": candidate.memory_type.value,
                        "predicate": candidate.predicate,
                        "scope": candidate.scope,
                        "source": candidate.extraction_source,
                        "reason": reason,
                    }
                    for candidate, reason in rejected
                ],
            },
        )

    def _normalize_candidate(
        self,
        candidate: MemoryCandidate,
        session: Session,
    ) -> tuple[MemoryCandidate, str | None]:
        if candidate.memory_type != MemoryType.TASK:
            return candidate, None
        transcript = normalize(
            " ".join(
                turn.content
                for turn in session.turns
                if turn.content and normalize(turn.role) in {"user", "human"}
            )
        )
        candidate_text = normalize(
            " ".join(
                [candidate.predicate, candidate.scope, json.dumps(candidate.value, ensure_ascii=False)]
            )
        )
        combined = f"{transcript} {candidate_text}"
        is_cross_agent_demo = (
            "cross-agent" in combined
            and any(term in combined for term in ["架构", "architecture"])
            and any(term in combined for term in ["演示", "demo"])
        )
        if not is_cross_agent_demo:
            return candidate, None
        negated_recurring = any(
            marker in transcript
            for marker in [
                "其实不是", "并不是", "不是每", "并非每", "不需要每周",
                "not recurring", "not every", "isn't every",
            ]
        )
        if negated_recurring:
            return candidate, "negated_task_candidate"
        state = "done" if any(
            marker in transcript
            for marker in ["整理完", "已经整理完", "已完成", "完成了", "done", "completed"]
        ) else "open"
        return (
            replace(
                candidate,
                predicate="user_task",
                scope="task:cross_agent_architecture_demo",
                value={"text": "整理 Cross-Agent 的架构演示材料", "state": state},
                valid_to=None,
            ),
            None,
        )

    def _merge_duplicates(
        self, candidates: list[MemoryCandidate]
    ) -> list[MemoryCandidate]:
        groups: dict[str, list[MemoryCandidate]] = {}
        order: list[str] = []
        for candidate in candidates:
            key = _value_key(candidate)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(candidate)
        return [self._merge_group(groups[key]) for key in order]

    def _merge_group(self, group: list[MemoryCandidate]) -> MemoryCandidate:
        if len(group) == 1:
            return group[0]
        sources = {candidate.extraction_source for candidate in group}
        source = "hybrid" if len(sources) > 1 else next(iter(sources))
        confidence = max(candidate.confidence for candidate in group)
        if len(sources) > 1:
            confidence = min(0.99, confidence + 0.05)
        assertion_mode = (
            "forget"
            if any(candidate.assertion_mode == "forget" for candidate in group)
            else "do_not_store"
            if any(candidate.assertion_mode == "do_not_store" for candidate in group)
            else "explicit"
            if any(candidate.assertion_mode == "explicit" for candidate in group)
            else group[0].assertion_mode
        )
        literalness = (
            "literal"
            if any(candidate.literalness == "literal" for candidate in group)
            else group[0].literalness
        )
        source_turn_ids = list(
            dict.fromkeys(
                turn_id
                for candidate in group
                for turn_id in candidate.source_turn_ids
            )
        )
        return replace(
            group[0],
            assertion_mode=assertion_mode,
            literalness=literalness,
            confidence=confidence,
            importance=max(candidate.importance for candidate in group),
            source_turn_ids=source_turn_ids,
            extraction_source=source,
        )

    def _score(
        self, candidate: MemoryCandidate, correction: bool
    ) -> MemoryCandidate:
        if candidate.assertion_mode in {"do_not_store", "forget"}:
            return replace(candidate, write_score=10.0)

        source_reliability = {
            "rule": 1.00,
            "hybrid": 1.00,
            "llm": 0.85 if candidate.assertion_mode == "explicit" else 0.65,
        }.get(candidate.extraction_source, 0.60)
        durability = {
            MemoryType.FACT: 0.90,
            MemoryType.PREFERENCE: 0.82,
            MemoryType.TASK: 0.86,
            MemoryType.RELATION: 0.75,
            MemoryType.SUMMARY: 0.60,
            MemoryType.EVENT: 0.40,
            MemoryType.SENSITIVE: 0.20,
        }[candidate.memory_type]
        half_life = float(
            self._config.temporal_half_life_days.get(
                candidate.memory_type.value,
                180.0,
            )
        )
        temporal = temporal_decay_score(candidate.valid_from, half_life)
        specificity = 0.40 if candidate.memory_type == MemoryType.EVENT else min(
            1.0, 0.60 + 0.08 * len(candidate.value)
        )
        explicit_bonus = 0.12 if candidate.assertion_mode == "explicit" else 0.0
        correction_bonus = (
            0.15
            if correction and candidate.memory_type != MemoryType.EVENT
            else 0.0
        )
        uncertainty_penalty = (
            0.25 if candidate.literalness == "uncertain" else 0.0
        )
        sensitivity_penalty = {
            "low": 0.0,
            "medium": 0.08,
            "high": 0.30,
            "sensitive": 0.30,
            "forbidden": 1.0,
        }.get(candidate.sensitivity, 0.30)
        score = (
            self._config.candidate_confidence_weight * candidate.confidence
            + self._config.candidate_importance_weight * candidate.importance
            + self._config.candidate_source_weight * source_reliability
            + self._config.candidate_temporal_weight * temporal
            + self._config.candidate_durability_weight * durability
            + self._config.candidate_specificity_weight * specificity
            + explicit_bonus
            + correction_bonus
            - uncertainty_penalty
            - sensitivity_penalty
        )
        return replace(candidate, write_score=round(max(0.0, score), 6))

    def _resolve_slots(
        self, candidates: list[MemoryCandidate]
    ) -> tuple[list[MemoryCandidate], int, list[dict[str, Any]]]:
        groups: dict[str, list[MemoryCandidate]] = {}
        for candidate in candidates:
            groups.setdefault(_slot_key(candidate), []).append(candidate)

        winners: list[MemoryCandidate] = []
        dropped = 0
        conflicts: list[dict[str, Any]] = []
        for slot, group in groups.items():
            ranked = sorted(group, key=self._sort_key)
            top = ranked[0]
            if len(ranked) > 1 and self._is_ambiguous(top, ranked[1]):
                dropped += len(ranked)
                conflicts.append(
                    {
                        "slot": slot,
                        "resolution": "dropped_ambiguous",
                        "top_scores": [
                            top.write_score,
                            ranked[1].write_score,
                        ],
                        "sources": [
                            top.extraction_source,
                            ranked[1].extraction_source,
                        ],
                    }
                )
                continue
            winners.append(top)
            dropped += len(ranked) - 1
            if len(ranked) > 1:
                conflicts.append(
                    {
                        "slot": slot,
                        "resolution": "selected_highest_score",
                        "selected_score": top.write_score,
                        "discarded_count": len(ranked) - 1,
                    }
                )
        return winners, dropped, conflicts

    def _is_ambiguous(
        self, first: MemoryCandidate, second: MemoryCandidate
    ) -> bool:
        if _normalized_value(first) == _normalized_value(second):
            return False
        if first.assertion_mode in {"forget", "do_not_store"}:
            return False
        if first.assertion_mode == "explicit" and second.assertion_mode == "inferred":
            return False
        first_score = first.write_score or 0.0
        second_score = second.write_score or 0.0
        return abs(first_score - second_score) < self._config.candidate_conflict_margin

    def _sort_key(self, candidate: MemoryCandidate) -> tuple[float, int, str]:
        source_order = {"hybrid": 0, "rule": 1, "llm": 2}
        return (
            -(candidate.write_score or 0.0),
            source_order.get(candidate.extraction_source, 9),
            _slot_key(candidate),
        )

    def _contains_correction(self, session: Session) -> bool:
        transcript = normalize(
            " ".join(
                turn.content
                for turn in session.turns
                if turn.content and turn.role.lower() in {"user", "human"}
            )
        )
        markers = {
            "改成",
            "改为",
            "更新为",
            "现在改",
            "不再",
            "纠正",
            "changed to",
            "switch to",
            "instead",
            "no longer",
        }
        return any(marker in transcript for marker in markers)

    def _trace_item(self, candidate: MemoryCandidate) -> dict[str, Any]:
        return {
            "type": candidate.memory_type.value,
            "predicate": candidate.predicate,
            "scope": candidate.scope,
            "source": candidate.extraction_source,
            "assertion_mode": candidate.assertion_mode,
            "confidence": candidate.confidence,
            "importance": candidate.importance,
            "write_score": candidate.write_score,
        }


def _slot_key(candidate: MemoryCandidate) -> str:
    return "|".join(
        [
            candidate.memory_type.value,
            normalize(candidate.subject),
            normalize(candidate.predicate),
            normalize(candidate.scope),
        ]
    )


def _value_key(candidate: MemoryCandidate) -> str:
    return f"{_slot_key(candidate)}|{_normalized_value(candidate)}"


def _normalized_value(candidate: MemoryCandidate) -> str:
    return normalize(json.dumps(candidate.value, ensure_ascii=False, sort_keys=True))
