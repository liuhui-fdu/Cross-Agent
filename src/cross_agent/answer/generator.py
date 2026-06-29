"""Answer generation interfaces and implementations."""

from __future__ import annotations

from typing import Protocol

from cross_agent.config import AnswerConfig, LLMConfig
from cross_agent.llm.openai_compatible import ChatMessage, OpenAICompatibleChatClient
from cross_agent.models import EvidenceBundle


class AnswerGenerator(Protocol):
    def generate(self, question: str, evidence: EvidenceBundle) -> str:
        ...


class ExtractiveAnswerGenerator:
    def __init__(self, config: AnswerConfig):
        self._config = config
        self.last_interaction = None

    def generate(self, question: str, evidence: EvidenceBundle) -> str:
        if not evidence.items:
            answer = "I do not have reliable memory evidence for that."
            self.last_interaction = {
                "type": "extractive",
                "question": question,
                "evidence_memory_ids": [],
                "response": answer,
            }
            return answer
        snippets = []
        for index, item in enumerate(evidence.items[: self._config.max_evidence_items], start=1):
            snippet = item.snippet[: self._config.max_snippet_chars]
            snippets.append(f"[{index}] {snippet}")
        joined = " ".join(snippets)
        answer = f"Based on the available memory evidence: {joined}"
        self.last_interaction = {
            "type": "extractive",
            "question": question,
            "evidence_memory_ids": [item.memory.memory_id for item in evidence.items],
            "response": answer,
        }
        return answer


class LLMAnswerGenerator:
    def __init__(
        self,
        answer_config: AnswerConfig,
        llm_config: LLMConfig,
        client: OpenAICompatibleChatClient,
    ):
        self._answer_config = answer_config
        self._llm_config = llm_config
        self._client = client
        self.last_interaction = None

    def generate(self, question: str, evidence: EvidenceBundle) -> str:
        if not evidence.items:
            answer = "I do not have reliable memory evidence for that."
            self.last_interaction = {
                "type": "openai_compatible_chat",
                "base_url": self._llm_config.base_url,
                "model": self._llm_config.model,
                "temperature": self._llm_config.temperature,
                "max_tokens": self._llm_config.max_tokens,
                "messages": [],
                "response": answer,
                "note": "no API call was made because EvidenceBundle was empty",
            }
            return answer
        evidence_text = self._format_evidence(evidence)
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a cross-session memory assistant. Answer only from the "
                    "provided EvidenceBundle and the user's current request. If the "
                    "evidence is insufficient or conflicting, say you do not have "
                    "reliable memory evidence. Do not invent personal facts. Use a "
                    "natural, concise answer; mention sources only when useful."
                    " Never reveal internal memory IDs, source session IDs, predicate or "
                    "scope labels, status field names, storage metadata, or retrieval scores "
                    "unless explicitly asked. Do not name a specific business, venue, or "
                    "brand unless it appears in the current request or EvidenceBundle. "
                    "Paraphrase machine-generated enum values naturally; never quote JSON "
                    "field names or internal English status labels."
                ),
            ),
            ChatMessage(
                role="user",
                content=f"Current request:\n{question}\n\nEvidenceBundle:\n{evidence_text}",
            ),
        ]
        answer = self._client.complete(
            messages=messages,
            temperature=self._llm_config.temperature,
            max_tokens=self._llm_config.max_tokens,
        )
        self.last_interaction = {
            "type": "openai_compatible_chat",
            "base_url": self._llm_config.base_url,
            "model": self._llm_config.model,
            "temperature": self._llm_config.temperature,
            "max_tokens": self._llm_config.max_tokens,
            "messages": [message.__dict__ for message in messages],
            "response": answer,
        }
        return answer

    def _format_evidence(self, evidence: EvidenceBundle) -> str:
        rows = []
        for index, item in enumerate(evidence.items[: self._answer_config.max_evidence_items], start=1):
            memory = item.memory
            rows.append(
                "\n".join(
                    [
                        f"Evidence {index}",
                        f"type: {memory.memory_type.value}",
                        f"topic: {memory.predicate}",
                        f"snippet: {item.snippet[: self._answer_config.max_snippet_chars]}",
                    ]
                )
            )
        return "\n\n".join(rows)
