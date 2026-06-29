from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.models import MemoryType, Session, Turn
from cross_agent.policies.write_policy import WritePolicy
from cross_agent.writer.extractor import (
    HeuristicSessionExtractor,
    HybridMemoryExtractor,
    LLMSemanticMemoryExtractor,
)


class FakeChatClient:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    def complete(self, messages, temperature, max_tokens):
        self.calls += 1
        return self.response


class FailingExtractor:
    def extract(self, session):
        raise RuntimeError("provider unavailable")


class HybridExtractorTest(unittest.TestCase):
    def test_semantic_candidate_is_parsed_then_rule_validated(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        client = FakeChatClient(
            """
            ```json
            {"candidates":[{
              "action":"store",
              "type":"fact",
              "predicate":"annual_ski_destination",
              "scope":"travel",
              "value":{"text":"北海道"},
              "assertion_mode":"explicit",
              "literalness":"literal",
              "confidence":0.91,
              "importance":0.66,
              "sensitivity":"low",
              "source_turn_ids":["t1"]
            }]}
            ```
            """
        )
        semantic = LLMSemanticMemoryExtractor(settings.writer, settings.llm, client)
        candidates = semantic.extract(
            Session(
                session_id="semantic_1",
                tenant_id="local",
                user_id="u1",
                occurred_at="2026-06-29",
                turns=[Turn("t1", "user", "我每年冬天会去北海道滑雪。")],
            )
        )
        self.assertEqual(1, len(candidates))
        self.assertEqual(MemoryType.FACT, candidates[0].memory_type)
        self.assertEqual({"text": "北海道"}, candidates[0].value)
        self.assertTrue(WritePolicy(settings.writer).admits(candidates[0])[0])

    def test_forbidden_session_never_calls_semantic_extractor(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        client = FakeChatClient('{"candidates":[]}')
        semantic = LLMSemanticMemoryExtractor(settings.writer, settings.llm, client)
        hybrid = HybridMemoryExtractor(
            HeuristicSessionExtractor(settings.writer),
            semantic,
            settings.writer.max_candidates_per_session,
        )
        candidates = hybrid.extract(
            Session(
                session_id="secret_1",
                tenant_id="local",
                user_id="u1",
                occurred_at="2026-06-29",
                turns=[Turn("t1", "user", "验证码是 123456。")],
            )
        )
        self.assertEqual(0, client.calls)
        self.assertEqual("forbidden", candidates[0].sensitivity)

    def test_strict_semantic_failure_never_falls_back(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        hybrid = HybridMemoryExtractor(
            HeuristicSessionExtractor(settings.writer),
            FailingExtractor(),
            settings.writer.max_candidates_per_session,
            semantic_required=True,
        )
        session = Session(
            session_id="strict_1",
            tenant_id="local",
            user_id="u1",
            occurred_at="2026-06-29",
            turns=[Turn("t1", "user", "我喜欢喝茶。")],
        )

        with self.assertRaisesRegex(RuntimeError, "required semantic extraction"):
            hybrid.extract(session)

    def test_retrieval_only_request_never_calls_semantic_extractor(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        client = FakeChatClient('{"candidates":[]}')
        hybrid = HybridMemoryExtractor(
            HeuristicSessionExtractor(settings.writer),
            LLMSemanticMemoryExtractor(settings.writer, settings.llm, client),
            settings.writer.max_candidates_per_session,
        )
        candidates = hybrid.extract(
            Session("q1", "local", "u1", "2026-06-29", [
                Turn("t1", "user", "我上次去日本住的是哪家酒店？")
            ])
        )
        self.assertEqual([], candidates)
        self.assertEqual(0, client.calls)

    def test_private_third_party_content_is_blocked_before_semantic_api(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        client = FakeChatClient('{"candidates":[]}')
        hybrid = HybridMemoryExtractor(
            HeuristicSessionExtractor(settings.writer),
            LLMSemanticMemoryExtractor(settings.writer, settings.llm, client),
            settings.writer.max_candidates_per_session,
        )
        candidates = hybrid.extract(
            Session("private1", "local", "u1", "2026-06-29", [
                Turn("t1", "user", "阿远压力很大，我会私下问问，但别在公开总结里提。")
            ])
        )
        self.assertEqual(0, client.calls)
        self.assertEqual("sensitive", candidates[0].sensitivity)

    def test_future_offer_to_help_is_a_declaration_not_retrieval_request(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        client = FakeChatClient('{"candidates":[]}')
        hybrid = HybridMemoryExtractor(
            HeuristicSessionExtractor(settings.writer),
            LLMSemanticMemoryExtractor(settings.writer, settings.llm, client),
            settings.writer.max_candidates_per_session,
        )
        candidates = hybrid.extract(
            Session("intro", "local", "u1", "2026-06-29", [
                Turn("t1", "user", "你好，我叫林澈，后面可能会经常让你帮我看设计。")
            ])
        )
        self.assertEqual(1, client.calls)
        self.assertTrue(any(item.predicate == "name" for item in candidates))


if __name__ == "__main__":
    unittest.main()
