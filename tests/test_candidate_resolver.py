from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.models import MemoryCandidate, MemoryType, Session, Turn
from cross_agent.policies.privacy import PrivacyPolicy
from cross_agent.policies.write_policy import WritePolicy
from cross_agent.writer.resolver import CandidateResolver


class CandidateResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings(REPO_ROOT / "configs" / "default.json")
        privacy = PrivacyPolicy(self.settings.writer, self.settings.guard)
        write = WritePolicy(self.settings.writer)
        self.resolver = CandidateResolver(self.settings.writer, write, privacy)
        self.session = Session(
            session_id="resolver_session",
            tenant_id="local",
            user_id="u1",
            occurred_at="2026-06-29",
            turns=[Turn("t1", "user", "这些是需要长期记住的信息。")],
        )

    def test_rule_and_llm_candidates_share_one_top_ten_ranking(self) -> None:
        candidates = [
            self._candidate(
                predicate=f"rule_fact_{index}",
                value=f"rule-{index}",
                source="rule",
                confidence=0.40,
                importance=0.40,
            )
            for index in range(6)
        ] + [
            self._candidate(
                predicate=f"llm_fact_{index}",
                value=f"llm-{index}",
                source="llm",
                confidence=0.99,
                importance=0.99,
            )
            for index in range(8)
        ]

        result = self.resolver.resolve(candidates, self.session)

        self.assertEqual(10, len(result.selected))
        self.assertEqual(4, result.dropped_count)
        self.assertEqual(
            8,
            sum(item.extraction_source == "llm" for item in result.selected),
        )
        scores = [item.write_score for item in result.selected]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_duplicate_rule_and_llm_candidate_merges_as_hybrid(self) -> None:
        candidates = [
            self._candidate("preferred_city", "杭州", "rule", 0.82, 0.70),
            self._candidate("preferred_city", "杭州", "llm", 0.88, 0.72),
        ]

        result = self.resolver.resolve(candidates, self.session)

        self.assertEqual(1, len(result.selected))
        selected = result.selected[0]
        self.assertEqual("hybrid", selected.extraction_source)
        self.assertEqual(0.93, selected.confidence)
        self.assertIsNotNone(selected.write_score)

    def test_near_tie_slot_conflict_is_dropped_as_ambiguous(self) -> None:
        candidates = [
            self._candidate("preferred_city", "杭州", "rule", 0.88, 0.70),
            self._candidate("preferred_city", "上海", "llm", 0.88, 0.70),
        ]

        result = self.resolver.resolve(candidates, self.session)

        self.assertEqual([], result.selected)
        self.assertEqual(2, result.dropped_count)
        self.assertEqual("dropped_ambiguous", result.trace["conflicts"][0]["resolution"])

    def _candidate(
        self,
        predicate: str,
        value: str,
        source: str,
        confidence: float,
        importance: float,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            tenant_id="local",
            user_id="u1",
            memory_type=MemoryType.FACT,
            subject="u1",
            predicate=predicate,
            value={"text": value},
            scope=predicate,
            assertion_mode="explicit",
            literalness="literal",
            confidence=confidence,
            importance=importance,
            sensitivity="low",
            source_turn_ids=["t1"],
            source_session_id="resolver_session",
            valid_from="2026-06-29",
            extraction_source=source,
        )


if __name__ == "__main__":
    unittest.main()
