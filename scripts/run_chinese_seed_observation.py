#!/usr/bin/env python3
"""Run the realistic Chinese user seed through API chat and memory observation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.llm.openai_compatible import ChatMessage, OpenAICompatibleChatClient
from cross_agent.models import EvidenceBundle, SearchRequest, Session, Turn
from cross_agent.pipeline import CrossAgentApp
from cross_agent.utils.env import load_env_file


DEFAULT_CONFIG = REPO_ROOT / "configs" / "yunai.json"
DEFAULT_SEED = REPO_ROOT / "eval" / "output" / "chinese_user_only_conversation_seed.json"
DEFAULT_SQLITE = REPO_ROOT / "eval" / "output" / "chinese_seed_observation.sqlite3"
DEFAULT_JSON = REPO_ROOT / "eval" / "output" / "chinese_seed_api_conversation.json"
DEFAULT_MARKDOWN = REPO_ROOT / "eval" / "output" / "chinese_seed_memory_observation.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Chinese user-only seed observation.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--sqlite", default=str(DEFAULT_SQLITE))
    parser.add_argument("--json", default=str(DEFAULT_JSON))
    parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))
    parser.add_argument(
        "--strict-online",
        action="store_true",
        help="Require LLM intent, evidence verification, semantic extraction, and embedding without fallback.",
    )
    args = parser.parse_args()

    load_env_file(REPO_ROOT / ".env")
    seed_path = Path(args.seed)
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    settings = load_settings(Path(args.config))
    if args.strict_online:
        settings = replace(
            settings,
            writer=replace(
                settings.writer,
                semantic_extraction_enabled=True,
                semantic_extraction_required=True,
            ),
            reader=replace(
                settings.reader,
                memory_intent_llm_enabled=True,
                memory_intent_llm_required=True,
                evidence_verifier_enabled=True,
                evidence_verifier_required=True,
            ),
            embedding=replace(
                settings.embedding,
                enabled=True,
                required=True,
            ),
        )
    sqlite_path = Path(args.sqlite)
    json_path = Path(args.json)
    markdown_path = Path(args.markdown)
    for path in [sqlite_path, json_path, markdown_path]:
        path.parent.mkdir(parents=True, exist_ok=True)

    settings = replace(
        settings,
        app=replace(settings.app, tenant_id="seed-cn", user_id=seed.get("user_id", "seed-user")),
        storage=replace(settings.storage, sqlite_path=str(sqlite_path)),
        answer=replace(settings.answer, max_evidence_items=10, max_snippet_chars=700),
    )
    app = CrossAgentApp(settings)
    app.initialize(reset=True)
    client = _build_client(settings)

    rounds: list[dict[str, Any]] = []
    for turn in seed["turns"]:
        round_no = int(turn["round"])
        session_id = turn["session_id"]
        user_text = turn["user"]
        request = SearchRequest(
            tenant_id=settings.app.tenant_id,
            user_id=settings.app.user_id,
            query=user_text,
            occurred_at=turn.get("date"),
            top_k=10,
            allow_sensitive=False,
        )

        before_counts = app.debug_counts()
        before_state = _snapshot_by_id(sqlite_path, "memory_state", "memory_id")
        before_events = _snapshot_by_id(sqlite_path, "memory_events", "event_id")
        evidence = app.search(request)
        messages = _build_api_messages(user_text, evidence)
        answer = _complete_with_retries(
            client,
            messages=messages,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )
        session = Session(
            session_id=session_id,
            tenant_id=settings.app.tenant_id,
            user_id=settings.app.user_id,
            occurred_at=turn.get("date"),
            turns=[
                Turn(
                    turn_id=f"{session_id}_turn_01",
                    role="user",
                    content=user_text,
                    occurred_at=turn.get("date"),
                )
            ],
            metadata={"seed_round": round_no, "conversation_id": seed.get("conversation_id")},
        )
        ingest_stats = app.ingest_session(session)
        _assert_strict_online_round(settings, evidence, app)
        after_counts = app.debug_counts()
        after_state = _snapshot_by_id(sqlite_path, "memory_state", "memory_id")
        after_events = _snapshot_by_id(sqlite_path, "memory_events", "event_id")

        rounds.append(
            {
                "round": round_no,
                "date": turn.get("date"),
                "session_id": session_id,
                "user": user_text,
                "assistant": answer,
                "api_call": {
                    "type": "openai_compatible_chat",
                    "base_url": settings.llm.base_url,
                    "model": settings.llm.model,
                    "temperature": settings.llm.temperature,
                    "max_tokens": settings.llm.max_tokens,
                    "messages_sent_to_api": [message.__dict__ for message in messages],
                    "response_text": answer,
                },
                "memory": {
                    "before_counts": before_counts,
                    "after_counts": after_counts,
                    "ingest_stats": ingest_stats,
                    "candidate_resolution": app.last_ingest_trace,
                    "query_plan": evidence.plan.__dict__,
                    "reader_trace": evidence.trace,
                    "evidence": [_evidence_to_dict(item) for item in evidence.items],
                    "state_delta": _state_delta(before_state, after_state),
                    "event_delta": _new_rows(before_events, after_events),
                },
            }
        )

    memory_state = _fetch_rows(sqlite_path, "SELECT * FROM memory_state ORDER BY created_at, memory_id")
    memory_events = _fetch_rows(sqlite_path, "SELECT * FROM memory_events ORDER BY system_time, event_id")
    result = {
        "metadata": {
            "source_seed": str(seed_path),
            "conversation_id": seed.get("conversation_id"),
            "tenant_id": settings.app.tenant_id,
            "user_id": settings.app.user_id,
            "sqlite_path": str(sqlite_path),
            "markdown_path": str(markdown_path),
            "round_count": len(rounds),
            "base_url": settings.llm.base_url,
            "model": settings.llm.model,
            "api_key_recorded": False,
            "flow": "search_existing_memory -> API answer -> ingest_current_turn",
            "strict_online_mode": bool(
                settings.writer.semantic_extraction_required
                and settings.reader.memory_intent_llm_required
                and settings.reader.evidence_verifier_required
                and settings.embedding.required
            ),
        },
        "conversation": rounds,
        "final_memory_state": memory_state,
        "final_memory_events": memory_events,
        "acceptance_observations": _acceptance_observations(memory_state, memory_events),
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(result), encoding="utf-8")
    print(str(json_path))
    print(str(markdown_path))


def _build_client(settings: Any) -> OpenAICompatibleChatClient:
    api_key = os.environ.get(settings.llm.api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {settings.llm.api_key_env}")
    return OpenAICompatibleChatClient(
        base_url=settings.llm.base_url,
        api_key=api_key,
        model=settings.llm.model,
        timeout_seconds=settings.llm.timeout_seconds,
        max_retries=settings.llm.max_retries,
        retry_base_seconds=settings.llm.retry_base_seconds,
    )


def _assert_strict_online_round(
    settings: Any,
    evidence: EvidenceBundle,
    app: CrossAgentApp,
) -> None:
    if not (
        settings.writer.semantic_extraction_required
        and settings.reader.memory_intent_llm_required
        and settings.reader.evidence_verifier_required
        and settings.embedding.required
    ):
        return
    if evidence.plan.decision_source == "rule_fallback":
        raise RuntimeError("strict online evaluation forbids memory intent fallback")
    embedding = evidence.trace.get("embedding") or {}
    if (
        not evidence.trace.get("skipped")
        and evidence.trace.get("candidate_count", 0) > 0
        and (not embedding.get("available") or embedding.get("error"))
    ):
        raise RuntimeError(
            f"strict online evaluation requires embedding success: {embedding}"
        )
    if any("token_fallback" in item.reason for item in evidence.items):
        raise RuntimeError("strict online evaluation forbids token-only fallback")
    verification = evidence.trace.get("evidence_verification") or {}
    if evidence.trace.get("candidate_count", 0) > 0 and evidence.plan.needs_memory:
        if not verification.get("applied"):
            raise RuntimeError("strict online evaluation requires evidence verifier API")
        if str(verification.get("reason", "")).startswith("verifier_error"):
            raise RuntimeError(f"strict online evidence verification failed: {verification}")
    semantic_error = getattr(app.extractor, "last_semantic_error", None)
    if semantic_error:
        raise RuntimeError(
            f"strict online evaluation requires semantic extraction: {semantic_error}"
        )


def _complete_with_retries(
    client: OpenAICompatibleChatClient,
    messages: list[ChatMessage],
    temperature: float,
    max_tokens: int,
    attempts: int = 3,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.complete(messages=messages, temperature=temperature, max_tokens=max_tokens)
        except Exception as exc:  # network providers can close long-running demo calls mid-stream
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(2 * attempt)
    raise RuntimeError(f"chat completion failed after {attempts} attempts: {last_error}") from last_error


def _build_api_messages(user_text: str, evidence: EvidenceBundle) -> list[ChatMessage]:
    evidence_text = _format_evidence(evidence)
    system = (
        "你是一个带长期记忆检索能力的中文助手。当前请求可以直接回答；"
        "如果提供了 EvidenceBundle，只能把其中的内容当作长期记忆证据使用。"
        "如果 EvidenceBundle 为空，不要声称自己记得长期信息，只根据当前用户消息自然回应。"
        "如果证据不足、冲突或已失效，要说明不确定，不能编造用户事实。"
        "不要展示内部 memory_id、session_id、predicate、scope、状态字段名、检索分数或存储元数据。"
        "不要提及 EvidenceBundle 和当前消息中都不存在的具体品牌、店名、场所名、经历或现实事实；"
        "缺少外部检索证据时，只能给地点类型、选择标准或明确说明无法确认。"
        "不要披露或复述验证码、密码、私钥等凭证；如果当前用户消息包含这类凭证，"
        "必须明确说明不能保存、不会记入长期记忆，也不要说“已记下”或“已保存”。"
        "回答涉及用户偏好时，优先使用结构化 active 记忆；看到 superseded 或 valid_to 非空的旧值时，"
        "只能作为历史说明，不能当作当前偏好。"
    )
    user = f"当前用户消息：\n{user_text}\n\nEvidenceBundle：\n{evidence_text}"
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def _format_evidence(evidence: EvidenceBundle) -> str:
    if not evidence.items:
        return "EMPTY"
    rows = []
    for index, item in enumerate(evidence.items, start=1):
        memory = item.memory
        rows.append(
            "\n".join(
                [
                    f"{index}. 证据",
                    f"type: {memory.memory_type.value}",
                    f"predicate: {memory.predicate}",
                    f"scope: {memory.scope}",
                    f"snippet: {item.snippet}",
                ]
            )
        )
    return "\n\n".join(rows)


def _evidence_to_dict(item: Any) -> dict[str, Any]:
    memory = item.memory
    return {
        "memory_id": memory.memory_id,
        "type": memory.memory_type.value,
        "predicate": memory.predicate,
        "scope": memory.scope,
        "status": memory.status.value,
        "source_session_id": memory.source_session_id,
        "valid_from": memory.valid_from,
        "valid_to": memory.valid_to,
        "supersedes": memory.supersedes,
        "sensitivity": memory.sensitivity,
        "score": item.score,
        "lexical_score": item.lexical_score,
        "semantic_score": item.semantic_score,
        "token_cosine_score": item.token_cosine_score,
        "temporal_score": item.temporal_score,
        "reason": item.reason,
        "snippet": item.snippet,
    }


def _fetch_rows(sqlite_path: Path, query: str) -> list[dict[str, Any]]:
    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query).fetchall()]


def _snapshot_by_id(sqlite_path: Path, table: str, id_column: str) -> dict[str, dict[str, Any]]:
    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            return {}
    return {str(row[id_column]): dict(row) for row in rows}


def _new_rows(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row_id, row in after.items() if row_id not in before]


def _state_delta(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    created = _new_rows(before, after)
    updated: list[dict[str, Any]] = []
    for row_id, after_row in after.items():
        before_row = before.get(row_id)
        if before_row and before_row != after_row:
            updated.append({"before": before_row, "after": after_row})
    return {"created": created, "updated": updated}


def _acceptance_observations(memory_state: list[dict[str, Any]], memory_events: list[dict[str, Any]]) -> dict[str, Any]:
    active = [row for row in memory_state if row.get("status") == "active"]
    superseded = [row for row in memory_state if row.get("status") == "superseded"]
    rejected = [row for row in memory_events if row.get("operation") == "reject"]
    return {
        "active_count": len(active),
        "superseded_count": len(superseded),
        "reject_event_count": len(rejected),
        "active_structured_records": [
            row
            for row in active
            if row.get("predicate") != "session_evidence"
        ],
        "superseded_structured_records": [
            row
            for row in superseded
            if row.get("predicate") != "session_evidence"
        ],
        "rejected_events": rejected,
    }


def _render_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    md = result["metadata"]
    lines.append("# 中文 Seed 长期记忆架构观测报告")
    lines.append("")
    lines.append("## 运行信息")
    lines.append("")
    for key in ["source_seed", "conversation_id", "tenant_id", "user_id", "sqlite_path", "round_count", "model", "flow"]:
        lines.append(f"- {key}: `{md.get(key)}`")
    lines.append("")
    lines.append("## 完整对话记录")
    for item in result["conversation"]:
        lines.append("")
        lines.append(f"### 第 {item['round']} 轮｜{item['date']}｜{item['session_id']}")
        lines.append("")
        lines.append(f"**User**：{item['user']}")
        lines.append("")
        lines.append(f"**API Assistant**：{item['assistant']}")
    lines.append("")
    lines.append("## 逐轮架构状态观测")
    for item in result["conversation"]:
        memory = item["memory"]
        lines.append("")
        lines.append(f"### 第 {item['round']} 轮｜{item['session_id']}")
        lines.append("")
        lines.append("**真实执行顺序**：检索已有长期记忆 -> API 回答 -> 写入当前 user turn")
        lines.append("")
        lines.append("**API messages**")
        lines.append("")
        lines.append(_json_block(item["api_call"]["messages_sent_to_api"]))
        lines.append("")
        lines.append("**Reader QueryPlan / Trace**")
        lines.append("")
        lines.append(_json_block({"query_plan": memory["query_plan"], "reader_trace": memory["reader_trace"]}))
        lines.append("")
        lines.append("**EvidenceBundle**")
        lines.append("")
        lines.append(_table(memory["evidence"], ["memory_id", "type", "predicate", "scope", "status", "source_session_id", "score", "sensitivity", "snippet"]))
        lines.append("")
        lines.append("**写入统计与计数变化**")
        lines.append("")
        lines.append(_json_block({"ingest_stats": memory["ingest_stats"], "before_counts": memory["before_counts"], "after_counts": memory["after_counts"]}))
        lines.append("")
        lines.append("**本轮 memory_state 变化**")
        lines.append("")
        lines.append(_state_delta_markdown(memory["state_delta"]))
        lines.append("")
        lines.append("**本轮 memory_events 追加**")
        lines.append("")
        lines.append(_table(memory["event_delta"], ["event_id", "memory_id", "operation", "event_time", "source_turn_ids_json", "payload_json"]))
    lines.append("")
    lines.append("## 最终 memory_state")
    lines.append("")
    lines.append(_table(result["final_memory_state"], ["memory_id", "type", "predicate", "scope", "value_json", "status", "sensitivity", "source_session_id", "valid_from", "valid_to", "supersedes"]))
    lines.append("")
    lines.append("## 最终 memory_events")
    lines.append("")
    lines.append(_table(result["final_memory_events"], ["event_id", "memory_id", "operation", "event_time", "source_turn_ids_json", "payload_json"]))
    lines.append("")
    lines.append("## 验收观察")
    lines.append("")
    lines.append(_json_block(result["acceptance_observations"]))
    return "\n".join(lines)


def _state_delta_markdown(delta: dict[str, list[dict[str, Any]]]) -> str:
    parts: list[str] = []
    parts.append("新增：")
    parts.append("")
    parts.append(_table(delta.get("created", []), ["memory_id", "type", "predicate", "scope", "value_json", "status", "source_session_id", "valid_from", "valid_to", "supersedes"]))
    parts.append("")
    parts.append("更新：")
    parts.append("")
    parts.append(_json_block(delta.get("updated", [])))
    return "\n".join(parts)


def _table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "无。"
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(column)) for column in columns) + " |")
    return "\n".join(lines)


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("\n", "<br>").replace("|", "\\|")


if __name__ == "__main__":
    main()
