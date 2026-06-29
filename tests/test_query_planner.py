from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.models import MemoryIntent, MemoryType, SearchRequest
from cross_agent.reader.query_planner import (
    LLMMemoryIntentClassifier,
    MemoryIntentDecision,
    QueryPlanner,
)


class FakeIntentClassifier:
    def __init__(
        self,
        decision: MemoryIntentDecision | None = None,
        error: Exception | None = None,
    ):
        self.decision = decision
        self.error = error
        self.calls: list[tuple[str, float, list[str]]] = []

    def classify(
        self,
        query: str,
        rule_score: float,
        recent_context: list[str],
    ) -> MemoryIntentDecision:
        self.calls.append((query, rule_score, recent_context))
        if self.error is not None:
            raise self.error
        assert self.decision is not None
        return self.decision


class FakeChatClient:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def complete(self, messages, temperature: float, max_tokens: int) -> str:
        self.calls.append((messages, temperature, max_tokens))
        return self.response


class QueryPlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        self.config = settings.reader

    def request(self, query: str) -> SearchRequest:
        return SearchRequest(tenant_id="local", user_id="u1", query=query)

    def test_explicit_history_reference_is_rule_yes(self) -> None:
        plan = QueryPlanner(self.config).plan(
            self.request("我上次去日本住的是哪家酒店？")
        )

        self.assertTrue(plan.needs_memory)
        self.assertEqual(MemoryIntent.REQUIRED, plan.memory_intent)
        self.assertEqual("rule", plan.decision_source)
        self.assertGreaterEqual(plan.rule_score, 0.8)

    def test_long_term_memory_project_topic_is_not_history_reference(self) -> None:
        plan = QueryPlanner(self.config).plan(
            self.request("我最近在做一个长期记忆 Agent 项目。")
        )

        self.assertNotEqual(MemoryIntent.REQUIRED, plan.memory_intent)
        self.assertNotIn("explicit_history_reference", plan.reason)

    def test_generic_first_person_question_is_rule_no(self) -> None:
        plan = QueryPlanner(self.config).plan(
            self.request("我想知道法国首都是什么")
        )

        self.assertFalse(plan.needs_memory)
        self.assertEqual(MemoryIntent.NONE, plan.memory_intent)
        self.assertEqual("rule", plan.decision_source)
        self.assertIn("generic_first_person", plan.reason)

    def test_general_knowledge_without_personal_context_is_rule_no(self) -> None:
        plan = QueryPlanner(self.config).plan(self.request("Explain quicksort"))

        self.assertFalse(plan.needs_memory)
        self.assertEqual("rule", plan.decision_source)

    def test_project_artifact_query_uses_offline_rule_fallback(self) -> None:
        plan = QueryPlanner(self.config).plan(self.request("Project Alpha note"))

        self.assertTrue(plan.needs_memory)
        self.assertEqual("rule_fallback", plan.decision_source)
        self.assertEqual(MemoryIntent.BENEFICIAL, plan.memory_intent)

        first_person_plan = QueryPlanner(self.config).plan(
            self.request("I want to know the Project Alpha note")
        )
        self.assertTrue(first_person_plan.needs_memory)

    def test_chinese_personal_state_question_uses_memory(self) -> None:
        plan = QueryPlanner(self.config).plan(self.request("我喜欢喝什么"))

        self.assertTrue(plan.needs_memory)
        self.assertEqual("rule_fallback", plan.decision_source)
        self.assertEqual(MemoryIntent.BENEFICIAL, plan.memory_intent)

    def test_ambiguous_personal_query_uses_llm(self) -> None:
        classifier = FakeIntentClassifier(
            MemoryIntentDecision(
                intent=MemoryIntent.REQUIRED,
                confidence=0.91,
                memory_types=(MemoryType.PREFERENCE,),
                reason="requires_user_preference",
            )
        )
        config = replace(self.config, memory_intent_llm_enabled=True)
        plan = QueryPlanner(config, classifier).plan(
            self.request("Which option fits my workflow best?")
        )

        self.assertTrue(plan.needs_memory)
        self.assertEqual("llm", plan.decision_source)
        self.assertEqual(MemoryIntent.REQUIRED, plan.memory_intent)
        self.assertEqual([MemoryType.PREFERENCE], plan.memory_types)
        self.assertEqual(1, len(classifier.calls))

    def test_llm_failure_uses_deterministic_fallback(self) -> None:
        classifier = FakeIntentClassifier(error=RuntimeError("provider unavailable"))
        config = replace(self.config, memory_intent_llm_enabled=True)
        plan = QueryPlanner(config, classifier).plan(
            self.request("Which option fits my workflow best?")
        )

        self.assertTrue(plan.needs_memory)
        self.assertEqual("rule_fallback", plan.decision_source)
        self.assertEqual(MemoryIntent.BENEFICIAL, plan.memory_intent)
        self.assertIn("llm_fallback", plan.reason)

    def test_strict_llm_failure_never_falls_back(self) -> None:
        classifier = FakeIntentClassifier(error=RuntimeError("provider unavailable"))
        config = replace(
            self.config,
            memory_intent_llm_enabled=True,
            memory_intent_llm_required=True,
        )

        with self.assertRaisesRegex(RuntimeError, "required memory intent"):
            QueryPlanner(config, classifier).plan(
                self.request("Which option fits my workflow best?")
            )

    def test_low_confidence_llm_result_uses_rule_fallback(self) -> None:
        classifier = FakeIntentClassifier(
            MemoryIntentDecision(
                intent=MemoryIntent.NONE,
                confidence=0.40,
                memory_types=(MemoryType.FACT,),
                reason="uncertain",
            )
        )
        config = replace(self.config, memory_intent_llm_enabled=True)
        plan = QueryPlanner(config, classifier).plan(
            self.request("Which option fits my workflow best?")
        )

        self.assertTrue(plan.needs_memory)
        self.assertEqual("rule_fallback", plan.decision_source)
        self.assertEqual(MemoryIntent.BENEFICIAL, plan.memory_intent)
        self.assertIn("llm_low_confidence", plan.reason)

    def test_llm_classifier_validates_and_parses_fenced_json(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        client = FakeChatClient(
            "```json\n"
            '{"decision":"beneficial","confidence":0.93,'
            '"memory_types":["preference","invalid"],"reason":"uses preference"}'
            "\n```"
        )
        classifier = LLMMemoryIntentClassifier(
            replace(settings.reader, memory_intent_llm_enabled=True),
            settings.llm,
            client,
        )

        decision = classifier.classify(
            "Which one do I usually choose?",
            0.58,
            ["We are comparing two editors."],
        )

        self.assertTrue(decision.needs_memory)
        self.assertEqual(MemoryIntent.BENEFICIAL, decision.intent)
        self.assertEqual(0.93, decision.confidence)
        self.assertEqual((MemoryType.PREFERENCE,), decision.memory_types)
        self.assertEqual(1, len(client.calls))

    def test_current_input_processing_overrides_possessive_cue(self) -> None:
        plan = QueryPlanner(self.config).plan(
            self.request("Summarize this: my project uses a queue")
        )

        self.assertFalse(plan.needs_memory)
        self.assertIn("current_input_only", plan.reason)

    def test_explicit_no_memory_directive_has_highest_priority(self) -> None:
        plan = QueryPlanner(self.config).plan(
            self.request("不要使用长期记忆，告诉我上次的答案")
        )

        self.assertFalse(plan.needs_memory)
        self.assertEqual(MemoryIntent.NONE, plan.memory_intent)
        self.assertEqual("policy", plan.decision_source)
        self.assertEqual("explicit_no_memory", plan.reason)

    def test_implicit_personal_query_enters_semantic_router(self) -> None:
        classifier = FakeIntentClassifier(
            MemoryIntentDecision(
                intent=MemoryIntent.BENEFICIAL,
                confidence=0.88,
                memory_types=(MemoryType.PREFERENCE,),
                reason="personalization_would_help",
            )
        )
        config = replace(self.config, memory_intent_llm_enabled=True)
        plan = QueryPlanner(config, classifier).plan(
            self.request("你觉得哪个更适合我？")
        )

        self.assertEqual(MemoryIntent.BENEFICIAL, plan.memory_intent)
        self.assertEqual("llm", plan.decision_source)
        self.assertEqual(1, len(classifier.calls))

        generic_opening = QueryPlanner(config, classifier).plan(
            self.request("我想知道哪个方案更适合我")
        )
        self.assertEqual(MemoryIntent.BENEFICIAL, generic_opening.memory_intent)
        self.assertEqual("llm", generic_opening.decision_source)

    def test_current_session_context_prevents_long_term_lookup(self) -> None:
        request = SearchRequest(
            tenant_id="local",
            user_id="u1",
            query="继续刚才的内容",
            recent_context=["刚才我们正在修改检索策略。"],
        )
        plan = QueryPlanner(self.config).plan(request)

        self.assertFalse(plan.needs_memory)
        self.assertEqual("policy", plan.decision_source)
        self.assertEqual("current_session_context_available", plan.reason)


if __name__ == "__main__":
    unittest.main()
