"""Response guard for groundedness and disclosure checks."""

from __future__ import annotations

from cross_agent.config import GuardConfig
from cross_agent.models import EvidenceBundle, GuardedAnswer


class ResponseGuard:
    def __init__(self, config: GuardConfig):
        self._config = config

    def verify(self, draft: str, evidence: EvidenceBundle) -> GuardedAnswer:
        if self._config.require_evidence_for_personalization and not evidence.items:
            return GuardedAnswer(
                answer=self._config.abstain_message,
                used_memory_ids=[],
                abstained=True,
                trace={"reason": "no_evidence"},
            )
        used = [item.memory.memory_id for item in evidence.items]
        return GuardedAnswer(
            answer=draft,
            used_memory_ids=used,
            abstained=False,
            trace={
                "grounded_memory_count": len(used),
                "source_sessions": [item.memory.source_session_id for item in evidence.items],
            },
        )
