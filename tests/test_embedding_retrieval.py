from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.embedding.sqlite_index import SQLiteVectorIndex, VectorSearchResult
from cross_agent.models import MemoryRecord, MemoryStatus, MemoryType, SearchRequest
from cross_agent.policies.privacy import PrivacyPolicy
from cross_agent.reader.memory_reader import MemoryReader
from cross_agent.reader.query_planner import QueryPlanner
from cross_agent.reader.evidence_verifier import EvidenceVerification


class FakeEvidenceVerifier:
    def __init__(self, verification=None, error=None):
        self.verification = verification
        self.error = error
        self.calls = 0

    def verify(self, query, items):
        self.calls += 1
        if self.error:
            raise self.error
        return self.verification


class FakeEmbeddingProvider:
    model = "fake-embedding"
    dimensions = 3

    def __init__(self):
        self.embedded_texts: list[str] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        if "滑雪" in text or "winter trip" in lowered or "北海道" in text:
            return [1.0, 0.0, 0.0]
        if "咖啡" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("provider unavailable")


class FakeStore:
    def __init__(self, records: list[MemoryRecord]):
        self.records = records

    def list_active(self, request: SearchRequest) -> list[MemoryRecord]:
        return self.records


class StaticVectorIndex:
    def __init__(self, result: VectorSearchResult):
        self.result = result

    def search(self, query: str, records: list[MemoryRecord]) -> VectorSearchResult:
        return self.result


class EmbeddingRetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings(REPO_ROOT / "configs" / "default.json")

    def test_sqlite_vector_cache_reuses_and_invalidates_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeEmbeddingProvider()
            index = SQLiteVectorIndex(
                str(Path(tmp) / "vectors.sqlite3"),
                provider,
                replace(self.settings.embedding, dimensions=3),
            )
            index.initialize()
            record = self._record("m1", "annual_trip", "每年冬天去北海道滑雪")

            first = index.sync_records([record])
            second = index.sync_records([record])
            changed = index.sync_records(
                [replace(record, value={"text": "每年冬天去长野滑雪"})]
            )

            self.assertEqual(1, first.embedded_count)
            self.assertEqual(1, second.cache_hits)
            self.assertEqual(1, changed.embedded_count)
            self.assertEqual(2, len(provider.embedded_texts))

    def test_required_embedding_failure_never_uses_token_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = SQLiteVectorIndex(
                str(Path(tmp) / "vectors.sqlite3"),
                FailingEmbeddingProvider(),
                replace(self.settings.embedding, dimensions=3, required=True),
            )
            index.initialize()

            with self.assertRaisesRegex(RuntimeError, "required document embedding"):
                index.sync_records(
                    [self._record("m1", "preferred_drink", "早上喝咖啡")]
                )

    def test_embedding_score_retrieves_semantic_match(self) -> None:
        ski = self._record("ski", "annual_trip", "每年冬天去北海道滑雪")
        coffee = self._record("coffee", "preferred_drink", "早上喝咖啡")
        vector_index = StaticVectorIndex(
            VectorSearchResult({"ski": 0.96, "coffee": 0.05}, 2, 0)
        )
        reader = MemoryReader(
            FakeStore([coffee, ski]),
            QueryPlanner(self.settings.reader),
            PrivacyPolicy(self.settings.writer, self.settings.guard),
            self.settings.reader,
            self.settings.writer.temporal_half_life_days,
            vector_index,
        )

        evidence = reader.search(
            SearchRequest(
                tenant_id="local",
                user_id="u1",
                query="Where should I take my winter trip?",
                occurred_at="2026-06-29",
                top_k=2,
            )
        )

        self.assertEqual("ski", evidence.items[0].memory.memory_id)
        self.assertEqual(0.96, evidence.items[0].semantic_score)
        self.assertTrue(evidence.trace["embedding"]["available"])

    def test_embedding_failure_uses_token_cosine_fallback(self) -> None:
        record = self._record("python", "language", "python interface examples")
        vector_index = StaticVectorIndex(
            VectorSearchResult({}, 0, 0, "provider unavailable")
        )
        reader = MemoryReader(
            FakeStore([record]),
            QueryPlanner(self.settings.reader),
            PrivacyPolicy(self.settings.writer, self.settings.guard),
            self.settings.reader,
            self.settings.writer.temporal_half_life_days,
            vector_index,
        )

        evidence = reader.search(
            SearchRequest(
                tenant_id="local",
                user_id="u1",
                query="my python interface examples",
                top_k=1,
            )
        )

        self.assertEqual(0.0, evidence.items[0].semantic_score)
        self.assertGreater(evidence.items[0].token_cosine_score, 0.0)
        self.assertIn("token_fallback", evidence.items[0].reason)

    def test_beneficial_intent_rejects_unrelated_evidence(self) -> None:
        record = self._record("coffee", "preferred_drink", "早上喝咖啡")
        reader = MemoryReader(
            FakeStore([record]),
            QueryPlanner(self.settings.reader),
            PrivacyPolicy(self.settings.writer, self.settings.guard),
            self.settings.reader,
            self.settings.writer.temporal_half_life_days,
            StaticVectorIndex(VectorSearchResult({"coffee": 0.05}, 1, 0)),
        )

        evidence = reader.search(
            SearchRequest(
                tenant_id="local",
                user_id="u1",
                query="Which option fits me best?",
            )
        )

        self.assertEqual([], evidence.items)
        self.assertTrue(evidence.trace["evidence_probe"]["applied"])
        self.assertFalse(evidence.trace["evidence_probe"]["accepted"])

    def test_required_intent_bypasses_evidence_probe_threshold(self) -> None:
        record = self._record("coffee", "preferred_drink", "早上喝咖啡")
        reader = MemoryReader(
            FakeStore([record]),
            QueryPlanner(self.settings.reader),
            PrivacyPolicy(self.settings.writer, self.settings.guard),
            self.settings.reader,
            self.settings.writer.temporal_half_life_days,
            StaticVectorIndex(VectorSearchResult({"coffee": 0.01}, 1, 0)),
        )

        evidence = reader.search(
            SearchRequest(
                tenant_id="local",
                user_id="u1",
                query="你还记得我上次说了什么吗？",
            )
        )

        self.assertEqual(1, len(evidence.items))
        self.assertFalse(evidence.trace["evidence_probe"]["applied"])

    def test_verifier_removes_unrelated_required_evidence(self) -> None:
        record = self._record("coffee", "preferred_drink", "早上喝咖啡")
        verifier = FakeEvidenceVerifier(
            EvidenceVerification(False, 0.98, (), "hotel_fact_absent")
        )
        config = replace(self.settings.reader, evidence_verifier_enabled=True)
        reader = MemoryReader(
            FakeStore([record]),
            QueryPlanner(config),
            PrivacyPolicy(self.settings.writer, self.settings.guard),
            config,
            self.settings.writer.temporal_half_life_days,
            StaticVectorIndex(VectorSearchResult({"coffee": 0.86}, 1, 0)),
            verifier,
        )

        evidence = reader.search(
            SearchRequest("local", "u1", "我上次去日本住的是哪家酒店？")
        )

        self.assertEqual([], evidence.items)
        self.assertEqual(1, verifier.calls)
        self.assertTrue(evidence.trace["evidence_verification"]["applied"])

    def test_required_verifier_failure_never_falls_back(self) -> None:
        record = self._record("coffee", "preferred_drink", "早上喝咖啡")
        verifier = FakeEvidenceVerifier(error=RuntimeError("provider unavailable"))
        config = replace(
            self.settings.reader,
            evidence_verifier_enabled=True,
            evidence_verifier_required=True,
        )
        reader = MemoryReader(
            FakeStore([record]), QueryPlanner(config),
            PrivacyPolicy(self.settings.writer, self.settings.guard), config,
            self.settings.writer.temporal_half_life_days,
            StaticVectorIndex(VectorSearchResult({"coffee": 0.86}, 1, 0)), verifier,
        )

        with self.assertRaisesRegex(RuntimeError, "required evidence verification"):
            reader.search(SearchRequest("local", "u1", "你还记得我的酒店吗？"))

    def test_distinct_structured_memories_from_same_session_survive_dedupe(self) -> None:
        quiet = replace(
            self._record("quiet", "preferred_environment", "写文档时喜欢安静"),
            scope="work_or_writing",
            source_session_id="same_session",
        )
        lively = replace(
            self._record("lively", "preferred_environment", "朋友聚会喜欢热闹"),
            scope="social_gathering",
            source_session_id="same_session",
        )
        verifier = FakeEvidenceVerifier(
            EvidenceVerification(True, 0.98, ("quiet", "lively"), "both_preferences_match")
        )
        config = replace(self.settings.reader, evidence_verifier_enabled=True)
        reader = MemoryReader(
            FakeStore([quiet, lively]), QueryPlanner(config),
            PrivacyPolicy(self.settings.writer, self.settings.guard), config,
            self.settings.writer.temporal_half_life_days,
            StaticVectorIndex(VectorSearchResult({"quiet": 0.9, "lively": 0.9}, 2, 0)),
            verifier,
        )

        evidence = reader.search(
            SearchRequest("local", "u1", "根据长期记忆中的习惯推荐办公和聚会地点", top_k=10)
        )

        self.assertEqual({"quiet", "lively"}, {item.memory.memory_id for item in evidence.items})

    def _record(
        self,
        memory_id: str,
        predicate: str,
        text: str,
    ) -> MemoryRecord:
        return MemoryRecord(
            memory_id=memory_id,
            tenant_id="local",
            user_id="u1",
            memory_type=MemoryType.FACT,
            subject="u1",
            predicate=predicate,
            value={"text": text},
            scope=predicate,
            assertion_mode="explicit",
            literalness="literal",
            confidence=0.85,
            importance=0.70,
            sensitivity="low",
            status=MemoryStatus.ACTIVE,
            source_turn_ids=[f"{memory_id}_turn"],
            source_session_id=f"{memory_id}_session",
            valid_from="2026-06-20",
            valid_to=None,
            created_at="2026-06-20T00:00:00Z",
            updated_at="2026-06-20T00:00:00Z",
        )


if __name__ == "__main__":
    unittest.main()
