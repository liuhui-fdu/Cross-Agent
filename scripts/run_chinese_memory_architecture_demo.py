#!/usr/bin/env python3
"""Run a 10-turn Chinese memory architecture demo and write a Markdown trace."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.models import SearchRequest, Session, Turn
from cross_agent.pipeline import CrossAgentApp
from cross_agent.utils.env import load_env_file


DEFAULT_CONFIG = REPO_ROOT / "configs" / "yunai.json"
DEFAULT_SQLITE = REPO_ROOT / "eval" / "output" / "chinese_memory_architecture_demo.sqlite3"
DEFAULT_MARKDOWN = REPO_ROOT / "eval" / "output" / "chinese_memory_architecture_demo.md"
DEFAULT_JSON = REPO_ROOT / "eval" / "output" / "chinese_memory_architecture_demo.json"


DEMO_TURNS = [
    {
        "date": "2026-06-01",
        "user": "你好，我叫林澈，现在住在杭州滨江。",
        "purpose": "抽取 name、current_residence，并保存原始 session evidence。",
    },
    {
        "date": "2026-06-02",
        "user": "我平时写接口示例偏好 Java，代码示例默认用 Java。",
        "purpose": "抽取 preferred_programming_language=java。",
    },
    {
        "date": "2026-06-03",
        "user": "我喜欢喝咖啡，尤其是早上做设计评审前。",
        "purpose": "抽取 preferred_drink=咖啡。",
    },
    {
        "date": "2026-06-04",
        "user": "记住我需要周五前整理 Cross-Agent 的架构演示材料。",
        "purpose": "抽取 user_task，并保存任务状态。",
    },
    {
        "date": "2026-06-05",
        "user": "哈哈我最喜欢凌晨三点改需求了，开玩笑的。",
        "purpose": "触发非字面表达，结构化槽位不写入，只保留低置信 session evidence。",
    },
    {
        "date": "2026-06-06",
        "user": "团队技术栈改了，我现在主要用 Python，之后接口示例优先用 Python。",
        "purpose": "同槽位 preferred_programming_language 从 java supersede 为 python。",
    },
    {
        "date": "2026-06-07",
        "user": "顺便记住我的验证码 123456 和密码 apple-pass-9988。",
        "purpose": "隐私写入门拒绝凭证类信息。",
    },
    {
        "date": "2026-06-08",
        "user": "最近胃不舒服，我现在改喝茶，别再默认推荐咖啡。",
        "purpose": "同槽位 preferred_drink 从 咖啡 supersede 为 茶。",
    },
    {
        "date": "2026-06-09",
        "user": "请根据长期记忆告诉我：我现在写接口示例应该用什么语言、默认喝什么、住在哪，以及还有什么任务？不要使用已经失效的信息。",
        "purpose": "检验长期记忆读取、active 状态、隐私披露门、证据包与 API 回答。",
    },
    {
        "date": "2026-06-10",
        "user": "我以前是不是偏好 Java？现在为什么不应该再默认 Java？另外可用长期记忆状态里有没有保存验证码或密码？",
        "purpose": "检验事件日志追溯、superseded 旧值、禁存信息不进入可用状态和边界说明。",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Chinese Cross-Agent memory demo.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Config path, defaults to yunai.json.")
    parser.add_argument("--sqlite", default=str(DEFAULT_SQLITE), help="Output SQLite path.")
    parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN), help="Output Markdown path.")
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="Output JSON path.")
    args = parser.parse_args()

    load_env_file(REPO_ROOT / ".env")
    settings = load_settings(Path(args.config))
    sqlite_path = Path(args.sqlite)
    markdown_path = Path(args.markdown)
    json_path = Path(args.json)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    settings = replace(
        settings,
        app=replace(settings.app, tenant_id="demo-cn", user_id="lin-che"),
        storage=replace(settings.storage, sqlite_path=str(sqlite_path)),
        answer=replace(settings.answer, max_evidence_items=8),
    )

    app = CrossAgentApp(settings)
    app.initialize(reset=True)

    rounds: list[dict[str, Any]] = []
    for index, item in enumerate(DEMO_TURNS, start=1):
        session_id = f"demo_cn_{index:02d}"
        session = Session(
            session_id=session_id,
            tenant_id=settings.app.tenant_id,
            user_id=settings.app.user_id,
            occurred_at=item["date"],
            turns=[
                Turn(
                    turn_id=f"{session_id}_turn_01",
                    role="user",
                    content=item["user"],
                    occurred_at=item["date"],
                )
            ],
            metadata={"demo_round": index, "purpose": item["purpose"]},
        )
        request = SearchRequest(
            tenant_id=settings.app.tenant_id,
            user_id=settings.app.user_id,
            query=item["user"],
            occurred_at=item["date"],
            top_k=8 if index >= 9 else 5,
            allow_sensitive=False,
        )
        before_counts = app.debug_counts()
        before_state = _snapshot_by_id(sqlite_path, "memory_state", "memory_id")
        before_events = _snapshot_by_id(sqlite_path, "memory_events", "event_id")
        if index >= 9:
            evidence = app.search(request)
            draft = app.answer_generator.generate(item["user"], evidence)
            model_interaction = getattr(app.answer_generator, "last_interaction", None)
            guarded = app.response_guard.verify(draft, evidence)
            ingest_stats = app.ingest_session(session)
            after_counts = app.debug_counts()
        else:
            ingest_stats = app.ingest_session(session)
            after_counts = app.debug_counts()
            evidence = app.search(request)
            draft = app.answer_generator.generate(item["user"], evidence)
            model_interaction = getattr(app.answer_generator, "last_interaction", None)
            guarded = app.response_guard.verify(draft, evidence)
        after_state = _snapshot_by_id(sqlite_path, "memory_state", "memory_id")
        after_events = _snapshot_by_id(sqlite_path, "memory_events", "event_id")
        rounds.append(
            {
                "round": index,
                "date": item["date"],
                "session_id": session_id,
                "purpose": item["purpose"],
                "user": item["user"],
                "assistant": guarded.answer,
                "model_interaction": model_interaction,
                "abstained": guarded.abstained,
                "used_memory_ids": guarded.used_memory_ids,
                "ingest_stats": ingest_stats,
                "candidate_resolution": app.last_ingest_trace,
                "before_counts": before_counts,
                "after_counts": after_counts,
                "state_delta": _state_delta(before_state, after_state),
                "event_delta": _new_rows(before_events, after_events),
                "plan": evidence.plan.__dict__,
                "trace": evidence.trace,
                "evidence": [_evidence_to_dict(ev) for ev in evidence.items],
            }
        )

    memory_state = _fetch_rows(sqlite_path, "SELECT * FROM memory_state ORDER BY created_at, memory_id")
    memory_events = _fetch_rows(sqlite_path, "SELECT * FROM memory_events ORDER BY system_time, event_id")
    markdown = _render_markdown(settings, sqlite_path, rounds, memory_state, memory_events)
    markdown_path.write_text(markdown, encoding="utf-8")
    json_doc = _render_json(settings, sqlite_path, markdown_path, rounds, memory_state, memory_events)
    json_path.write_text(json.dumps(json_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(markdown_path))
    print(str(json_path))


def _evidence_to_dict(ev: Any) -> dict[str, Any]:
    memory = ev.memory
    return {
        "memory_id": memory.memory_id,
        "source_session_id": memory.source_session_id,
        "type": memory.memory_type.value,
        "predicate": memory.predicate,
        "scope": memory.scope,
        "status": memory.status.value,
        "sensitivity": memory.sensitivity,
        "score": ev.score,
        "lexical_score": ev.lexical_score,
        "semantic_score": ev.semantic_score,
        "temporal_score": ev.temporal_score,
        "snippet": ev.snippet,
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


def _render_markdown(
    settings: Any,
    sqlite_path: Path,
    rounds: list[dict[str, Any]],
    memory_state: list[dict[str, Any]],
    memory_events: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# Cross-Agent 中文长期记忆架构演示")
    lines.append("")
    lines.append("## 运行配置")
    lines.append("")
    lines.append(f"- tenant_id: `{settings.app.tenant_id}`")
    lines.append(f"- user_id: `{settings.app.user_id}`")
    lines.append(f"- sqlite: `{sqlite_path}`")
    lines.append(f"- answer_generator: `{settings.llm.model}` via `{settings.llm.base_url}`")
    lines.append("- 对话轮数: `10`")
    lines.append("")
    lines.append("## 完整历史对话总览")
    lines.append("")
    for item in rounds:
        lines.append(f"### 第 {item['round']} 轮｜{item['date']}｜{item['session_id']}")
        lines.append("")
        lines.append(f"**用户**：{item['user']}")
        lines.append("")
        lines.append(f"**API 回答**：{item['assistant']}")
        lines.append("")
    lines.append("## 10 轮连续问答")
    for item in rounds:
        lines.append("")
        lines.append(f"### 第 {item['round']} 轮｜{item['date']}｜{item['session_id']}")
        lines.append("")
        lines.append(f"**检验目的**：{item['purpose']}")
        lines.append("")
        lines.append("**用户**")
        lines.append("")
        lines.append(f"> {item['user']}")
        lines.append("")
        lines.append("**API 回答**")
        lines.append("")
        lines.append(item["assistant"])
        lines.append("")
        lines.append("**写入统计**")
        lines.append("")
        lines.append(_json_block(item["ingest_stats"]))
        lines.append("")
        lines.append("**状态计数变化**")
        lines.append("")
        lines.append(_json_block({"before": item["before_counts"], "after": item["after_counts"]}))
        lines.append("")
        lines.append("**本轮写入的 memory_state 变化**")
        lines.append("")
        lines.append(_state_delta_markdown(item["state_delta"]))
        lines.append("")
        lines.append("**本轮追加的 memory_events**")
        lines.append("")
        lines.append(_events_markdown(item["event_delta"]))
        lines.append("")
        lines.append("**检索计划与 trace**")
        lines.append("")
        lines.append(_json_block({"plan": item["plan"], "trace": item["trace"]}))
        lines.append("")
        lines.append("**EvidenceBundle Top-K**")
        lines.append("")
        if item["evidence"]:
            lines.append("| rank | memory_id | source_session | type | predicate | status | score | sensitivity | snippet |")
            lines.append("|---:|---|---|---|---|---|---:|---|---|")
            for rank, ev in enumerate(item["evidence"], start=1):
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(rank),
                            _cell(ev["memory_id"]),
                            _cell(ev["source_session_id"]),
                            _cell(ev["type"]),
                            _cell(ev["predicate"]),
                            _cell(ev["status"]),
                            f"{ev['score']:.4f}",
                            _cell(ev["sensitivity"]),
                            _cell(ev["snippet"]),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("无证据。")
        lines.append("")
        lines.append("**ResponseGuard 使用的 memory_id**")
        lines.append("")
        lines.append(_json_block(item["used_memory_ids"]))

    lines.append("")
    lines.append("## memory_state 当前状态投影")
    lines.append("")
    lines.append("| memory_id | type | predicate | scope | value_json | status | sensitivity | source_session | valid_from | valid_to | supersedes |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in memory_state:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row.get("memory_id")),
                    _cell(row.get("type")),
                    _cell(row.get("predicate")),
                    _cell(row.get("scope")),
                    _cell(row.get("value_json")),
                    _cell(row.get("status")),
                    _cell(row.get("sensitivity")),
                    _cell(row.get("source_session_id")),
                    _cell(row.get("valid_from")),
                    _cell(row.get("valid_to")),
                    _cell(row.get("supersedes")),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## memory_events 追加式事件日志")
    lines.append("")
    lines.append("| event_id | memory_id | operation | event_time | source_turn_ids | payload |")
    lines.append("|---|---|---|---|---|---|")
    for row in memory_events:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row.get("event_id")),
                    _cell(row.get("memory_id")),
                    _cell(row.get("operation")),
                    _cell(row.get("event_time")),
                    _cell(row.get("source_turn_ids_json")),
                    _cell(row.get("payload_json")),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## 最后两轮重点观察")
    lines.append("")
    lines.append("- 第 9 轮检验 active current_state：Python 应替代 Java，茶应替代咖啡，同时仍能召回住址和任务。")
    lines.append("- 第 10 轮检验事件日志追溯：旧 Java 通过 session evidence 可追溯，但结构化 active 槽位应只保留 Python。")
    lines.append("- 第 7 轮凭证信息应出现在 rejected event 中，不应进入 active memory_state。")
    lines.append("- 第 8 轮检验饮品偏好改口：咖啡应变为 superseded，茶应成为 active。")
    lines.append("")
    return "\n".join(lines)


def _render_json(
    settings: Any,
    sqlite_path: Path,
    markdown_path: Path,
    rounds: list[dict[str, Any]],
    memory_state: list[dict[str, Any]],
    memory_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "metadata": {
            "tenant_id": settings.app.tenant_id,
            "user_id": settings.app.user_id,
            "sqlite_path": str(sqlite_path),
            "markdown_path": str(markdown_path),
            "base_url": settings.llm.base_url,
            "model": settings.llm.model,
            "api_key_recorded": False,
            "round_count": len(rounds),
            "note": "This file records the actual demo conversation, model prompts/responses, evidence bundles, and memory write deltas. API keys are intentionally omitted.",
        },
        "conversation": [
            {
                "round": item["round"],
                "date": item["date"],
                "session_id": item["session_id"],
                "purpose": item["purpose"],
                "messages": [
                    {"role": "user", "content": item["user"]},
                    {"role": "assistant", "content": item["assistant"]},
                ],
                "model_interaction": item["model_interaction"],
                "memory": {
                    "ingest_stats": item["ingest_stats"],
                    "before_counts": item["before_counts"],
                    "after_counts": item["after_counts"],
                    "state_delta": item["state_delta"],
                    "event_delta": item["event_delta"],
                    "query_plan": item["plan"],
                    "reader_trace": item["trace"],
                    "evidence": item["evidence"],
                    "used_memory_ids": item["used_memory_ids"],
                    "abstained": item["abstained"],
                },
            }
            for item in rounds
        ],
        "final_memory_state": memory_state,
        "final_memory_events": memory_events,
    }


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def _state_delta_markdown(delta: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    created = delta.get("created", [])
    updated = delta.get("updated", [])
    if not created and not updated:
        return "无 memory_state 新增或更新。"
    if created:
        lines.append("新增记录：")
        lines.append("")
        lines.append("| memory_id | type | predicate | scope | value_json | status | source_session | valid_from | valid_to | supersedes |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for row in created:
            lines.append(_state_row(row))
        lines.append("")
    if updated:
        lines.append("更新记录：")
        lines.append("")
        lines.append("| memory_id | 字段 | before | after |")
        lines.append("|---|---|---|---|")
        for change in updated:
            before = change["before"]
            after = change["after"]
            for key in ["status", "valid_to", "updated_at", "confidence", "importance", "supersedes"]:
                if before.get(key) != after.get(key):
                    lines.append(
                        "| "
                        + " | ".join(
                            [
                                _cell(after.get("memory_id")),
                                _cell(key),
                                _cell(before.get(key)),
                                _cell(after.get(key)),
                            ]
                        )
                        + " |"
                    )
        lines.append("")
    return "\n".join(lines).rstrip()


def _state_row(row: dict[str, Any]) -> str:
    return (
        "| "
        + " | ".join(
            [
                _cell(row.get("memory_id")),
                _cell(row.get("type")),
                _cell(row.get("predicate")),
                _cell(row.get("scope")),
                _cell(row.get("value_json")),
                _cell(row.get("status")),
                _cell(row.get("source_session_id")),
                _cell(row.get("valid_from")),
                _cell(row.get("valid_to")),
                _cell(row.get("supersedes")),
            ]
        )
        + " |"
    )


def _events_markdown(events: list[dict[str, Any]]) -> str:
    if not events:
        return "无追加事件。"
    lines = [
        "| event_id | memory_id | operation | event_time | source_turn_ids | payload |",
        "|---|---|---|---|---|---|",
    ]
    for row in events:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row.get("event_id")),
                    _cell(row.get("memory_id")),
                    _cell(row.get("operation")),
                    _cell(row.get("event_time")),
                    _cell(row.get("source_turn_ids_json")),
                    _cell(row.get("payload_json")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", "<br>").replace("|", "\\|")
    return text


if __name__ == "__main__":
    main()
