"""Write admission policy separated from extraction and persistence."""

from __future__ import annotations

import json
import re

from cross_agent.config import WriterConfig
from cross_agent.models import MemoryCandidate


class WritePolicy:
    def __init__(self, config: WriterConfig):
        self._config = config

    def admits(self, candidate: MemoryCandidate) -> tuple[bool, str]:
        valid, reason = self._valid_shape(candidate)
        if not valid:
            return False, reason
        if candidate.confidence < self._config.min_confidence:
            return False, "confidence_below_threshold"
        if (
            candidate.assertion_mode == "inferred"
            and candidate.confidence < self._config.min_inferred_confidence
        ):
            return False, "inferred_confidence_below_threshold"
        if candidate.literalness not in set(self._config.literalness_allowlist):
            return False, f"literalness_not_allowed:{candidate.literalness}"
        return True, "admitted"

    def _valid_shape(self, candidate: MemoryCandidate) -> tuple[bool, str]:
        if not candidate.tenant_id or not candidate.user_id:
            return False, "missing_identity_boundary"
        if candidate.subject != candidate.user_id:
            return False, "subject_outside_user_boundary"
        if not _valid_slot_part(candidate.predicate):
            return False, "invalid_predicate"
        if not _valid_slot_part(candidate.scope):
            return False, "invalid_scope"
        if candidate.assertion_mode not in {
            "explicit",
            "inferred",
            "quotation",
            "forget",
            "do_not_store",
        }:
            return False, "invalid_assertion_mode"
        if candidate.sensitivity not in {
            "low",
            "medium",
            "high",
            "sensitive",
            "forbidden",
        }:
            return False, "invalid_sensitivity"
        if not candidate.source_session_id or not candidate.source_turn_ids:
            return False, "missing_provenance"
        if not isinstance(candidate.value, dict) or not candidate.value:
            return False, "empty_or_invalid_value"
        payload = json.dumps(candidate.value, ensure_ascii=False, sort_keys=True)
        if len(payload) > self._config.max_candidate_chars:
            return False, "candidate_payload_too_large"
        if not 0.0 <= candidate.confidence <= 1.0:
            return False, "confidence_out_of_range"
        if not 0.0 <= candidate.importance <= 1.0:
            return False, "importance_out_of_range"
        return True, "valid"


def _valid_slot_part(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.*:-]{1,120}", value or ""))
