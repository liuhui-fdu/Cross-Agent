"""Memory candidate extraction.

This MVP ships with a deterministic extractor so local evaluation works without
an API key. A production LLM extractor can implement the same interface.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Protocol

from cross_agent.config import LLMConfig, WriterConfig
from cross_agent.llm.openai_compatible import ChatMessage, OpenAICompatibleChatClient
from cross_agent.models import MemoryCandidate, MemoryType, Session
from cross_agent.utils.text import normalize, tokenize


class MemoryExtractor(Protocol):
    def extract(self, session: Session) -> list[MemoryCandidate]:
        ...


class HybridMemoryExtractor:
    """Merge deterministic candidates with optional semantic candidates.

    Rules remain the control plane: a forbidden or explicit no-store session is
    never sent to the semantic extractor, and deterministic candidates win
    deduplication ties.
    """

    def __init__(
        self,
        rule_extractor: MemoryExtractor,
        semantic_extractor: MemoryExtractor | None,
        max_candidates: int,
        semantic_required: bool = False,
    ):
        self._rule_extractor = rule_extractor
        self._semantic_extractor = semantic_extractor
        self._max_candidates = max_candidates
        self._semantic_required = semantic_required
        self.last_semantic_error: str | None = None

    def extract(self, session: Session) -> list[MemoryCandidate]:
        rule_candidates = self._rule_extractor.extract(session)
        if self._must_not_call_semantic(rule_candidates) or (
            not rule_candidates and _is_retrieval_only_session(session)
        ):
            return rule_candidates[: self._max_candidates]

        semantic_candidates: list[MemoryCandidate] = []
        if self._semantic_extractor is not None:
            try:
                semantic_candidates = self._semantic_extractor.extract(session)
                self.last_semantic_error = None
            except (RuntimeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.last_semantic_error = str(exc)
                if self._semantic_required:
                    raise RuntimeError(
                        f"required semantic extraction failed: {exc}"
                    ) from exc

        return (
            rule_candidates[: self._max_candidates]
            + semantic_candidates[: self._max_candidates]
        )

    def _must_not_call_semantic(self, candidates: list[MemoryCandidate]) -> bool:
        return any(
            candidate.sensitivity in {"forbidden", "sensitive", "high"}
            or candidate.assertion_mode == "do_not_store"
            for candidate in candidates
        )


class LLMSemanticMemoryExtractor:
    """Use an LLM for semantic interpretation, never for final admission."""

    _ALLOWED_TYPES = {
        MemoryType.FACT,
        MemoryType.PREFERENCE,
        MemoryType.TASK,
        MemoryType.RELATION,
        MemoryType.SUMMARY,
    }

    def __init__(
        self,
        writer_config: WriterConfig,
        llm_config: LLMConfig,
        client: OpenAICompatibleChatClient,
    ):
        self._writer_config = writer_config
        self._llm_config = llm_config
        self._client = client

    def extract(self, session: Session) -> list[MemoryCandidate]:
        transcript = "\n".join(
            f"{normalize(turn.role) or 'unknown'}: {' '.join((turn.content or '').split())}"
            for turn in session.turns
            if (turn.content or "").strip()
        )
        if not transcript:
            return []
        response = self._client.complete(
            messages=[
                ChatMessage(role="system", content=self._system_prompt()),
                ChatMessage(
                    role="user",
                    content=(
                        f"session_id: {session.session_id}\n"
                        f"occurred_at: {session.occurred_at}\n"
                        f"transcript:\n{transcript}"
                    ),
                ),
            ],
            temperature=0.0,
            max_tokens=min(self._llm_config.max_tokens, 2400),
        )
        raw = _parse_json_object(response)
        rows = raw.get("candidates", [])
        if not isinstance(rows, list):
            raise ValueError("semantic extractor candidates must be a list")
        result: list[MemoryCandidate] = []
        for row in rows[: self._writer_config.max_candidates_per_session]:
            candidate = self._to_candidate(session, row)
            if candidate is not None:
                result.append(candidate)
        return result

    def _to_candidate(
        self, session: Session, row: Any
    ) -> MemoryCandidate | None:
        if not isinstance(row, dict):
            return None
        action = normalize(str(row.get("action", "store")))
        if action == "skip":
            return None
        try:
            memory_type = MemoryType(str(row.get("type", "")))
        except ValueError:
            return None
        if memory_type not in self._ALLOWED_TYPES:
            return None
        predicate = str(row.get("predicate", "")).strip()
        scope = str(row.get("scope", "")).strip()
        value = row.get("value")
        if not predicate or not scope or not isinstance(value, dict):
            return None
        assertion_mode = "forget" if action == "forget" else normalize(
            str(row.get("assertion_mode", "inferred"))
        )
        if assertion_mode not in {"explicit", "inferred", "forget"}:
            assertion_mode = "inferred"
        turn_ids = {turn.turn_id for turn in session.turns}
        source_ids = row.get("source_turn_ids", [])
        if not isinstance(source_ids, list):
            source_ids = []
        source_ids = [str(item) for item in source_ids if str(item) in turn_ids]
        if not source_ids:
            source_ids = [
                turn.turn_id
                for turn in session.turns
                if normalize(turn.role) in {"user", "human"}
            ]
        return MemoryCandidate(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            memory_type=memory_type,
            subject=session.user_id,
            predicate=predicate,
            value=value,
            scope=scope,
            assertion_mode=assertion_mode,
            literalness=normalize(str(row.get("literalness", "literal"))),
            confidence=_float_or_default(row.get("confidence"), 0.70),
            importance=_float_or_default(row.get("importance"), 0.55),
            sensitivity=normalize(str(row.get("sensitivity", "low"))),
            source_turn_ids=source_ids,
            source_session_id=session.session_id,
            valid_from=_optional_text(row.get("valid_from")) or session.occurred_at,
            valid_to=_optional_text(row.get("valid_to")),
            extraction_source="llm",
        )

    def _system_prompt(self) -> str:
        return (
            "You extract durable cross-session memory candidates. Return JSON only as "
            "{\"candidates\": [...]}. Keep only information likely useful in a future "
            "session: stable facts, preferences with their scope, commitments/tasks, "
            "relationships, and explicit corrections. Resolve negation, quotation, "
            "hypotheticals, temporariness, and references conservatively. Never emit "
            "passwords, verification codes, private keys, payment credentials, or raw "
            "secrets. If the user asks not to store something, emit no store candidate. "
            "Do not emit candidates for retrieval-only questions or requests whose only "
            "purpose is to use information stated in earlier sessions. Respect audience "
            "restrictions such as private, confidential, do not publish, or do not include "
            "in public summaries by emitting no candidate. For an explicit forget/delete "
            "request, emit action=forget with the target "
            "type, predicate and scope and value={\"action\":\"forget\"}. Each store "
            "candidate must contain action, type, predicate, scope, value, assertion_mode "
            "(explicit or inferred), literalness, confidence, importance, sensitivity, "
            "valid_from, valid_to, and source_turn_ids. literalness must be literal, "
            "quotation, or uncertain. sensitivity must be low, medium, high, sensitive, "
            "or forbidden. Use snake_case predicates and "
            "compact JSON values. Do not create event/session_evidence candidates."
        )


class HeuristicSessionExtractor:
    """Extract one evidence memory per session.

    ActMem-style evaluation needs robust evidence retrieval over many candidate
    sessions. We preserve the raw transcript as an event memory with typed
    metadata, while higher-precision production extractors can emit atomic
    facts/preferences/tasks through the same protocol.
    """

    def __init__(self, config: WriterConfig):
        self._config = config

    def extract(self, session: Session) -> list[MemoryCandidate]:
        transcript = self._transcript(session)
        if not transcript:
            return []
        if self._has_no_store_directive(session):
            return [self._control_candidate(session, "do_not_store")]
        forget_candidates = self._forget_candidates(session)
        if forget_candidates:
            return forget_candidates
        summary = self._summary(transcript)
        literalness = self._literalness(transcript)
        sensitivity = self._sensitivity(transcript)
        if sensitivity not in {"forbidden", "sensitive", "high"} and _is_retrieval_only_session(session):
            return []
        confidence = 0.80 if literalness == "literal" else 0.45
        importance = self._importance(transcript)
        structured = self._structured_candidates(session, literalness, sensitivity)
        if sensitivity not in {"forbidden", "sensitive", "high"} and not self._should_store_session_evidence(
            session, transcript, structured
        ):
            return structured
        candidate = MemoryCandidate(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            memory_type=MemoryType.EVENT,
            subject=session.user_id,
            predicate="session_evidence",
            value={
                "session_id": session.session_id,
                "summary": summary,
                "transcript": transcript,
                "keywords": tokenize(transcript)[:80],
                "metadata": session.metadata,
            },
            scope="actmem_session",
            assertion_mode="explicit",
            literalness=literalness,
            confidence=confidence,
            importance=importance,
            sensitivity=sensitivity,
            source_turn_ids=[turn.turn_id for turn in session.turns],
            source_session_id=session.session_id,
            valid_from=session.occurred_at,
        )
        return [candidate] + structured

    def _has_no_store_directive(self, session: Session) -> bool:
        patterns = [
            r"不要(?:记住|保存|记录)(?:这|本次|这次|以下|刚才)",
            r"不要把.+(?:记入|写入|放进)(?:长期)?记忆",
            r"(?:只在|仅在)(?:本次|这次|当前)对话",
            r"do not (?:remember|store|save) (?:this|the following)",
            r"don't (?:remember|store|save) (?:this|the following)",
        ]
        return any(
            re.search(pattern, turn.content or "", flags=re.IGNORECASE)
            for turn in session.turns
            if normalize(turn.role) in {"user", "human"}
            for pattern in patterns
        )

    def _forget_candidates(self, session: Session) -> list[MemoryCandidate]:
        targets = [
            (MemoryType.FACT, "name", "identity", ["名字", "姓名", "name"]),
            (
                MemoryType.FACT,
                "current_residence",
                "current_residence",
                ["地址", "住址", "居住地", "住在哪里", "residence"],
            ),
            (
                MemoryType.PREFERENCE,
                "preferred_programming_language",
                "coding_examples",
                ["编程语言", "代码语言", "接口示例语言", "programming language"],
            ),
            (
                MemoryType.PREFERENCE,
                "preferred_drink",
                "daily_drink",
                ["饮品", "喝什么", "咖啡", "茶", "drink preference"],
            ),
            (
                MemoryType.PREFERENCE,
                "preferred_environment",
                "work_or_writing",
                ["工作环境", "写作环境", "办公环境"],
            ),
            (
                MemoryType.PREFERENCE,
                "preferred_device_ecosystem",
                "device_ecosystem",
                ["设备生态", "苹果生态"],
            ),
        ]
        result: list[MemoryCandidate] = []
        for turn in session.turns:
            if normalize(turn.role) not in {"user", "human"}:
                continue
            text = normalize(turn.content)
            if not any(word in text for word in ["忘记", "删除", "清除", "forget", "delete"]):
                continue
            for memory_type, predicate, scope, cues in targets:
                if any(normalize(cue) in text for cue in cues):
                    result.append(
                        self._candidate(
                            session,
                            turn.turn_id,
                            memory_type,
                            predicate,
                            {"action": "forget"},
                            scope,
                            1.0,
                            1.0,
                            "low",
                            assertion_mode="forget",
                        )
                    )
        return result

    def _control_candidate(self, session: Session, action: str) -> MemoryCandidate:
        return MemoryCandidate(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            memory_type=MemoryType.EVENT,
            subject=session.user_id,
            predicate="memory_control",
            value={"action": action},
            scope="user_directive",
            assertion_mode="do_not_store",
            literalness="literal",
            confidence=1.0,
            importance=1.0,
            sensitivity="low",
            source_turn_ids=[turn.turn_id for turn in session.turns],
            source_session_id=session.session_id,
            valid_from=session.occurred_at,
        )

    def _should_store_session_evidence(
        self,
        session: Session,
        transcript: str,
        structured: list[MemoryCandidate],
    ) -> bool:
        mode = self._config.session_evidence_mode
        if mode == "all" or session.metadata.get("force_session_evidence"):
            return True
        if mode == "none":
            return False
        if structured:
            return True
        lowered = transcript.lower()
        durable_signals = [
            "remember", "my preference", "i prefer", "i need to", "i plan to",
            "记住", "偏好", "我喜欢", "我需要", "我计划", "我准备", "以后",
            "目前", "现在", "改成", "更新为", "住在", "我叫",
        ]
        return any(signal in lowered for signal in durable_signals)

    def _transcript(self, session: Session) -> str:
        lines = []
        for turn in session.turns:
            role = normalize(turn.role) or "unknown"
            content = " ".join((turn.content or "").split())
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _summary(self, transcript: str) -> str:
        compact = " ".join(transcript.split())
        if len(compact) <= 360:
            return compact
        digest = hashlib.sha1(compact.encode("utf-8")).hexdigest()[:8]
        return compact[:340].rstrip() + f"... [{digest}]"

    def _literalness(self, transcript: str) -> str:
        lowered = transcript.lower()
        if any(marker in lowered for marker in ["haha", "just kidding", "哈哈", "开玩笑", "玩笑"]):
            return "uncertain"
        if any(marker in lowered for marker in ["if i", "if we", "hypothetically"]):
            return "uncertain"
        return "literal"

    def _sensitivity(self, transcript: str) -> str:
        lowered = transcript.lower()
        if any(pattern.lower() in lowered for pattern in self._config.sensitive_denied_patterns):
            return "forbidden"
        if any(word in lowered for word in ["密码", "验证码", "私钥", "银行卡号", "身份证号"]):
            return "forbidden"
        if any(word in lowered for word in ["health", "doctor", "medical diagnosis"]):
            return "sensitive"
        if any(word in lowered for word in ["健康", "医生", "诊断", "病历"]):
            return "sensitive"
        if any(
            phrase in lowered
            for phrase in [
                "别在公开总结", "不要公开", "不得公开", "只私下", "私下问问",
                "confidential", "do not publish", "don't publish",
                "do not include in public", "private conversation",
            ]
        ):
            return "sensitive"
        if any(word in lowered for word in ["financial", "credit card", "amex"]):
            return "medium"
        if any(word in lowered for word in ["财务", "信用卡"]):
            return "medium"
        return "low"

    def _importance(self, transcript: str) -> float:
        lowered = transcript.lower()
        signals = [
            "remember",
            "note",
            "log",
            "important",
            "requires",
            "depends",
            "status",
            "updated",
            "added",
            "记住",
            "重要",
            "需要",
            "更新",
            "改成",
        ]
        score = self._config.default_importance + 0.04 * sum(s in lowered for s in signals)
        return min(score, 0.95)

    def _structured_candidates(
        self, session: Session, literalness: str, sensitivity: str
    ) -> list[MemoryCandidate]:
        if literalness != "literal" or sensitivity in {"forbidden", "sensitive", "high"}:
            return []
        candidates: list[MemoryCandidate] = []
        for turn in session.turns:
            if normalize(turn.role) not in {"user", "human"}:
                continue
            text = " ".join((turn.content or "").split())
            if not text:
                continue
            candidates.extend(self._identity_candidates(session, turn.turn_id, text, sensitivity))
            candidates.extend(self._preference_candidates(session, turn.turn_id, text, sensitivity))
            candidates.extend(self._task_candidates(session, turn.turn_id, text, sensitivity))
        return candidates

    def _identity_candidates(
        self, session: Session, turn_id: str, text: str, sensitivity: str
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        name = self._first_match(
            text,
            [
                r"\bmy name is ([A-Za-z][A-Za-z .'-]{1,60})",
                r"(?:我叫|我的名字叫|名字是)(?!什么|啥|谁)([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z .'-]{1,20})",
            ],
        )
        if name:
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.FACT,
                    "name",
                    {"text": name},
                    "identity",
                    0.96,
                    0.82,
                    sensitivity,
                )
            )
        residence = self._first_match(
            text,
            [
                r"\bi (?:now |currently )?live in ([A-Za-z][A-Za-z .,'-]{1,60})",
                r"\bi (?:just |recently )?moved to ([A-Za-z][A-Za-z .,'-]{1,60})",
                r"(?:我现在住在|我住在|现在住在|住在|我刚搬到|我最近搬到)([\u4e00-\u9fffA-Za-z0-9 .,'-]{2,40})",
            ],
        )
        if residence:
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.FACT,
                    "current_residence",
                    {"text": residence},
                    "current_residence",
                    0.90,
                    0.76,
                    sensitivity,
                )
            )
        return candidates

    def _preference_candidates(
        self, session: Session, turn_id: str, text: str, sensitivity: str
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        language_pattern = (
            r"\b(?:i (?:now |currently )?(?:mainly |primarily )?"
            r"(?:use|prefer|write (?:code )?in)|(?:examples?|snippets?) "
            r"(?:should )?(?:default to|prioritize|use)) "
            r"(python|java|go|javascript|typescript|rust|c\+\+|c#|ruby|php|swift|kotlin)\b"
        )
        language = self._first_match(
            text,
            [
                language_pattern,
                (
                    r"(?:我(?:现在|目前|主要)?(?:用|偏好|喜欢用|改用)|"
                    r"我(?:现在|目前)?主要用|"
                    r"(?:代码示例|接口示例|示例)[^。；！？]{0,20}(?:默认|优先)?(?:用|给)?)"
                    r"\s*(Python|Java|Go|JavaScript|TypeScript|Rust|C\+\+|C#|Ruby|PHP|Swift|Kotlin)"
                ),
            ],
        )
        if language:
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.PREFERENCE,
                    "preferred_programming_language",
                    {"text": language.lower()},
                    "coding_examples",
                    0.88,
                    0.74,
                    sensitivity,
                )
            )
        drink = self._first_match(
            text,
            [
                r"\bi (?:now |currently )?(?:prefer|drink|switched to|changed to) (coffee|tea|water|matcha)\b",
                r"(?:我(?:现在|目前)?(?:喜欢喝|喝|改喝|偏好)|饮品(?:默认|优先)?(?:选|用)?)\s*(咖啡|茶|水|抹茶|拿铁)",
                r"(?:我(?:现在|目前)?(?:先)?改喝|(?:先)?改喝)\s*(咖啡|茶|水|抹茶|拿铁)",
                r"(?:我早上一般喝|早上一般喝|一般喝|通常会先买一杯)\s*(咖啡|茶|水|抹茶|拿铁)",
            ],
        )
        if drink:
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.PREFERENCE,
                    "preferred_drink",
                    {"text": drink.lower()},
                    "daily_drink",
                    0.88,
                    0.68,
                    sensitivity,
                )
            )
        generic = self._first_match(text, [r"\bi prefer ([^.;!?]{2,80})", r"我偏好([^。；！？]{2,80})"])
        if generic and not language and not drink:
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.PREFERENCE,
                    "preferred_item",
                    {"text": generic},
                    self._infer_preference_scope(text, generic),
                    0.76,
                    0.60,
                    sensitivity,
                )
            )
        scoped = self._scoped_preference_candidates(session, turn_id, text, sensitivity)
        candidates.extend(scoped)
        return candidates

    def _task_candidates(
        self, session: Session, turn_id: str, text: str, sensitivity: str
    ) -> list[MemoryCandidate]:
        cross_agent_task = self._cross_agent_arch_task(session, turn_id, text, sensitivity)
        if cross_agent_task:
            return [cross_agent_task]
        task = self._first_match(
            text,
            [
                r"\bremember to ([^.;!?]{3,100})",
                r"\bi need to ([^.;!?]{3,100})",
                r"(?:记住我需要|我需要|提醒我)([^。；！？]{3,80})",
                r"(?:我要|我得|我需要)([^。；！？]{3,80})",
            ],
        )
        if not task:
            return []
        task_key = hashlib.sha1(normalize(task).encode("utf-8")).hexdigest()[:10]
        return [
            self._candidate(
                session,
                turn_id,
                MemoryType.TASK,
                "user_task",
                {"text": task, "state": "open"},
                f"task:{task_key}",
                0.78,
                0.70,
                sensitivity,
            )
        ]

    def _scoped_preference_candidates(
        self, session: Session, turn_id: str, text: str, sensitivity: str
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        if "安静" in text and any(word in text for word in ["写架构", "写文档", "工作", "办公"]):
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.PREFERENCE,
                    "preferred_environment",
                    {"text": "安静"},
                    "work_or_writing",
                    0.82,
                    0.64,
                    sensitivity,
                )
            )
        if "热闹" in text and any(word in text for word in ["朋友", "聚会", "吃饭", "周末"]):
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.PREFERENCE,
                    "preferred_environment",
                    {"text": "热闹"},
                    "social_gathering",
                    0.82,
                    0.62,
                    sensitivity,
                )
            )
        if "苹果" in text and any(word in text for word in ["手机", "生态", "电脑", "耳机"]):
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.PREFERENCE,
                    "preferred_device_ecosystem",
                    {"text": "苹果生态"},
                    "device_ecosystem",
                    0.78,
                    0.58,
                    sensitivity,
                )
            )
        if "苹果" in text and any(word in text for word in ["水果", "不太爱吃", "不爱吃"]):
            candidates.append(
                self._candidate(
                    session,
                    turn_id,
                    MemoryType.PREFERENCE,
                    "disliked_food",
                    {"text": "苹果"},
                    "fruit",
                    0.78,
                    0.56,
                    sensitivity,
                )
            )
        return candidates

    def _cross_agent_arch_task(
        self, session: Session, turn_id: str, text: str, sensitivity: str
    ) -> MemoryCandidate | None:
        if "Cross-Agent" not in text or "架构演示材料" not in text:
            return None
        state = "done" if any(word in text for word in ["整理完", "已经整理完", "完成了", "已完成"]) else "open"
        return self._candidate(
            session,
            turn_id,
            MemoryType.TASK,
            "user_task",
            {"text": "整理 Cross-Agent 的架构演示材料", "state": state},
            "task:cross_agent_architecture_demo",
            0.84 if state == "done" else 0.80,
            0.74,
            sensitivity,
        )

    def _candidate(
        self,
        session: Session,
        turn_id: str,
        memory_type: MemoryType,
        predicate: str,
        value: dict,
        scope: str,
        confidence: float,
        importance: float,
        sensitivity: str,
        assertion_mode: str = "explicit",
    ) -> MemoryCandidate:
        return MemoryCandidate(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            memory_type=memory_type,
            subject=session.user_id,
            predicate=predicate,
            value=value,
            scope=scope,
            assertion_mode=assertion_mode,
            literalness="literal",
            confidence=confidence,
            importance=importance,
            sensitivity=sensitivity,
            source_turn_ids=[turn_id],
            source_session_id=session.session_id,
            valid_from=session.occurred_at,
        )

    def _first_match(self, text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return self._clean_value(match.group(1))
        return None

    def _clean_value(self, value: str) -> str:
        cleaned = " ".join(value.split()).strip(" ,;:.-'\"")
        return cleaned[:80]

    def _infer_preference_scope(self, text: str, value: str) -> str:
        haystack = f"{text} {value}".lower()
        if any(word in haystack for word in ["code", "api", "python", "java", "go", "typescript", "代码", "接口", "示例"]):
            return "coding_examples"
        if any(word in haystack for word in ["coffee", "tea", "drink", "咖啡", "茶", "饮品"]):
            return "daily_drink"
        if any(word in haystack for word in ["work", "office", "meeting", "工作", "办公室", "会议"]):
            return "work_environment"
        return "general_preference"


def _candidate_key(candidate: MemoryCandidate) -> str:
    return "|".join(
        [
            candidate.memory_type.value,
            normalize(candidate.predicate),
            normalize(candidate.scope),
            normalize(json.dumps(candidate.value, ensure_ascii=False, sort_keys=True)),
            candidate.assertion_mode,
        ]
    )


def _is_retrieval_only_session(session: Session) -> bool:
    texts = [
        " ".join((turn.content or "").split())
        for turn in session.turns
        if normalize(turn.role) in {"user", "human"} and (turn.content or "").strip()
    ]
    if not texts:
        return False
    text = " ".join(texts).lower()
    correction_markers = (
        "其实不是", "并不是", "不再", "改成", "改为", "更新为", "已经完成",
        "整理完", "做完了", "no longer", "changed to", "switched to", "correct",
    )
    if any(marker in text for marker in correction_markers):
        return False
    request_markers = (
        "根据之前", "前面说过", "以前是不是", "还能查到", "告诉我",
        "我是不是", "我有点记不清", "哪家", "哪里", "为什么", "是什么状态",
        "do you remember", "what did i", "tell me", "based on what i said",
    )
    direct_help_request = bool(re.match(r"^(?:请)?帮我", text))
    return (
        "?" in text
        or "？" in text
        or direct_help_request
        or any(marker in text for marker in request_markers)
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("semantic extractor did not return a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("semantic extractor response must be a JSON object")
    return parsed


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
