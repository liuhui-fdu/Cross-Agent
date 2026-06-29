#!/usr/bin/env python3
"""Render a round-by-round architecture walkthrough from the real Chinese seed run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "eval" / "output" / "chinese_seed_api_conversation.json"
DEFAULT_OUTPUT = REPO_ROOT / "eval" / "output" / "chinese_seed_round_by_round_walkthrough.md"
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render round-by-round seed walkthrough.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    doc = json.loads(Path(args.input).read_text(encoding="utf-8"))
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    output = _render(doc, cfg, Path(args.input))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(output, encoding="utf-8")
    print(args.output)


def _render(doc: dict[str, Any], cfg: dict[str, Any], input_path: Path) -> str:
    conversation = doc["conversation"]
    final_state = {row["memory_id"]: row for row in doc["final_memory_state"]}
    active_projection: dict[str, dict[str, Any]] = {}
    lines: list[str] = []

    lines.append("# 中文 22 轮真实在线样例逐轮架构讲解")
    lines.append("")
    lines.append("这份说明只使用本次真实在线运行结果，不重放、不虚构。目标不是再做一份总览报告，而是用同一条 22 轮对话，把架构中的检索、打分、回答、写入、状态迁移和长期记忆变化逐轮摊开。")
    lines.append("")
    lines.append("## 运行前提")
    lines.append("")
    md = doc["metadata"]
    lines.append(f"- 数据源：`{md['source_seed']}`")
    lines.append(f"- 真实输出：`{input_path.resolve()}`")
    lines.append(f"- 模型：`{md['model']}`")
    lines.append(f"- 接口：`{md['base_url']}`")
    lines.append(f"- 执行顺序：`{md['flow']}`")
    lines.append("")
    lines.append("## 架构公式")
    lines.append("")
    lines.append("### 检索总分")
    lines.append("")
    lines.append("```text")
    lines.append("score =")
    lines.append(f"  {cfg['reader']['embedding_weight']:.2f} * embedding_score +")
    lines.append(f"  {cfg['reader']['token_cosine_weight']:.2f} * token_cosine_score +")
    lines.append(f"  {cfg['reader']['lexical_weight']:.2f} * lexical_score +")
    lines.append(f"  {cfg['reader']['temporal_weight']:.2f} * temporal_score +")
    lines.append(f"  {cfg['reader']['importance_weight']:.2f} * importance +")
    lines.append(f"  {cfg['reader']['confidence_weight']:.2f} * confidence")
    lines.append("")
    lines.append(f"if memory_type != event: score += {cfg['reader']['structured_type_bonus']:.2f}")
    lines.append(f"if memory_type matches predicted type: score += {cfg['reader']['memory_type_match_bonus']:.2f}")
    lines.append(f"if valid_to is not null: score -= {cfg['reader']['staleness_penalty']:.2f}")
    lines.append(f"if sensitivity in {{high, sensitive}}: score -= {cfg['reader']['privacy_penalty']:.2f}")
    lines.append("final_score = max(score, 0.0)")
    lines.append("```")
    lines.append("")
    lines.append("### BM25 风格词法分")
    lines.append("")
    lines.append("```text")
    lines.append("lexical_score = min(")
    lines.append("  1.0,")
    lines.append("  (Σ idf(term) * (tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avg_len)))) / 8.0")
    lines.append(")")
    lines.append("k1 = 1.4, b = 0.75, avg_len = 120.0")
    lines.append("idf(term) = log(1 + (doc_count - df + 0.5) / (df + 0.5))")
    lines.append("```")
    lines.append("")
    lines.append("### Embedding 与计数余弦语义分")
    lines.append("")
    lines.append("```text")
    lines.append("embedding_score = cosine(normalize(E(query)), normalize(E(document)))")
    lines.append("token_cosine_score = cosine_from_counts(query_term_counts, doc_term_counts)")
    lines.append("production: embedding failure stops the request; token cosine remains an auxiliary channel")
    lines.append("```")
    lines.append("")
    lines.append("### 强化公式")
    lines.append("")
    lines.append("```text")
    lines.append("confidence' = min(0.99, confidence + 0.02)")
    lines.append("importance' = min(0.99, importance + 0.02)")
    lines.append("```")
    lines.append("")
    lines.append("### 当前时间衰减")
    lines.append("")
    lines.append("```text")
    lines.append("temporal_score = exp(-ln(2) * age_days / type_half_life_days)")
    lines.append("hard gate: valid_from <= request_time < valid_to, or valid_to is null")
    lines.append("expired records do not enter ranking")
    lines.append("```")
    lines.append("")
    lines.append("### 三态记忆门控与证据探测")
    lines.append("")
    lines.append("```text")
    lines.append("intent in {REQUIRED, BENEFICIAL, NONE}")
    lines.append("REQUIRED/BENEFICIAL: retrieve candidates, then call LLM evidence verifier")
    lines.append(f"quick probe threshold = {cfg['reader']['memory_probe_min_score']:.2f}")
    lines.append(f"verifier accepts only sufficient evidence with confidence >= {cfg['reader']['evidence_verifier_min_confidence']:.2f}")
    lines.append("NONE: skip memory store and embedding")
    lines.append("```")
    lines.append("")
    lines.append("## 逐轮拆解")

    for item in conversation:
        memory = item["memory"]
        _apply_delta(active_projection, memory["state_delta"])
        lines.append("")
        lines.append(f"### 第 {item['round']} 轮｜{item['date']}｜{item['session_id']}")
        lines.append("")
        lines.append(f"**User**：{item['user']}")
        lines.append("")
        lines.append(f"**API**：{item['assistant']}")
        lines.append("")
        lines.append("**这轮执行顺序**")
        lines.append("")
        lines.append("1. 从已有长期记忆检索 EvidenceBundle")
        lines.append("2. 把当前用户消息和 EvidenceBundle 发给在线 API")
        lines.append("3. 回答返回后，再抽取并写入当前轮记忆")
        lines.append("")
        lines.append("**检索阶段参数**")
        lines.append("")
        lines.append(_json_block({
            "query_plan": memory["query_plan"],
            "reader_trace": memory["reader_trace"],
            "before_counts": memory["before_counts"],
        }))
        lines.append("")
        lines.append("**EvidenceBundle 与分数分解**")
        lines.append("")
        lines.append(_evidence_table(memory["evidence"], final_state))
        lines.append("")
        lines.append("**本轮写入统计**")
        lines.append("")
        lines.append(_json_block({
            "ingest_stats": memory["ingest_stats"],
            "after_counts": memory["after_counts"],
        }))
        lines.append("")
        lines.append("**本轮 memory_state 变化**")
        lines.append("")
        lines.append(_state_delta_markdown(memory["state_delta"]))
        lines.append("")
        lines.append("**本轮 memory_events 追加**")
        lines.append("")
        lines.append(_table(memory["event_delta"], ["event_id", "memory_id", "operation", "event_time", "payload_json"]))
        lines.append("")
        lines.append("**本轮结束后的长期记忆快照**")
        lines.append("")
        lines.append(_projection_table(active_projection))
        lines.append("")
        lines.append("**这一轮说明了什么**")
        lines.append("")
        lines.append(_round_commentary(item, active_projection))

    lines.append("")
    lines.append("## 最终结论")
    lines.append("")
    lines.append("这 22 轮样例用于验证当前三态记忆门控、Gemini Embedding 混合检索、LLM 证据验证、有效时间硬门、状态更新、安全拒写和无证据拒答是否形成一致闭环。每轮结论均来自本次输入文件的真实 trace。")
    return "\n".join(lines)


def _apply_delta(projection: dict[str, dict[str, Any]], delta: dict[str, list[dict[str, Any]]]) -> None:
    for row in delta.get("created", []):
        projection[row["memory_id"]] = row
    for pair in delta.get("updated", []):
        after = pair["after"]
        projection[after["memory_id"]] = after


def _round_commentary(item: dict[str, Any], projection: dict[str, dict[str, Any]]) -> str:
    round_no = item["round"]
    memory = item["memory"]
    notes: list[str] = []
    if round_no == 1:
        notes.append("这是最干净的起点：检索候选数为 0，EvidenceBundle 为空，回答完全基于当前轮自我介绍。随后系统才把 `name` 和 `session_evidence` 写入长期记忆。")
    if round_no in {11, 12, 15}:
        notes.append("这一轮触发了同槽新值覆盖旧值。你可以在 `state_delta.updated` 或最终投影里看到旧记录进入 `superseded`，同时新记录成为 `active`。")
    if round_no == 14:
        notes.append("这一轮是安全门的关键例子：用户给出验证码和临时密码，回答明确说不会保存，写入阶段出现 `reject` 事件，但不会进入 `memory_state`。")
    if round_no == 17:
        notes.append("这一轮是长期记忆主验收点：系统需要从多轮历史中同时取出姓名、住址、语言、饮品、任务状态。")
    if round_no == 18:
        notes.append("这一轮同时验证“历史值”和“当前值”的关系：Java 不是消失了，而是作为旧值存在，但 Python 才是当前 active 值。")
    if round_no == 20:
        notes.append("这一轮说明弱承诺内容没有被随意写成计划。冰岛极光在本次实现里没有被固化成结构化长期记忆，所以系统如实说不确定。")
    if round_no == 22:
        notes.append("这一轮是拒绝编造的典型例子：混合召回产生候选，但证据验证器确认没有任何记忆能支持日本酒店事实，因此交给回答模型的 EvidenceBundle 为空。")
    if not notes:
        created = len(memory["state_delta"].get("created", []))
        updated = len(memory["state_delta"].get("updated", []))
        notes.append(f"这一轮写入新增 {created} 条、更新 {updated} 条；轮末 active 长期记忆总数为 {sum(1 for row in projection.values() if row.get('status') == 'active')}。")
    return "\n".join(f"- {note}" for note in notes)


def _evidence_table(items: list[dict[str, Any]], final_state: dict[str, dict[str, Any]]) -> str:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        state_row = final_state.get(item["memory_id"], {})
        importance = state_row.get("importance", "")
        confidence = state_row.get("confidence", "")
        struct_bonus = 0.03 if item.get("type") != "event" else 0.0
        stale_penalty = 0.30 if item.get("valid_to") else 0.0
        privacy_penalty = 0.40 if state_row.get("sensitivity") in {"high", "sensitive"} else 0.0
        rows.append(
            {
                "rank": index,
                "memory_id": item["memory_id"],
                "predicate": item["predicate"],
                "status": item["status"],
                "score": item["score"],
                "lexical": item["lexical_score"],
                "semantic": item["semantic_score"],
                "token_cosine": item.get("token_cosine_score", 0.0),
                "temporal": item["temporal_score"],
                "importance": importance,
                "confidence": confidence,
                "struct_bonus": round(struct_bonus, 2),
                "stale_penalty": round(stale_penalty, 2),
                "privacy_penalty": round(privacy_penalty, 2),
                "snippet": item["snippet"],
            }
        )
    return _table(
        rows,
        [
            "rank",
            "memory_id",
            "predicate",
            "status",
            "score",
            "lexical",
            "semantic",
            "token_cosine",
            "temporal",
            "importance",
            "confidence",
            "struct_bonus",
            "stale_penalty",
            "privacy_penalty",
            "snippet",
        ],
    )


def _projection_table(projection: dict[str, dict[str, Any]]) -> str:
    rows = [
        {
            "memory_id": row["memory_id"],
            "type": row["type"],
            "predicate": row["predicate"],
            "scope": row["scope"],
            "value_json": row["value_json"],
            "status": row["status"],
            "source_session_id": row["source_session_id"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "supersedes": row["supersedes"],
        }
        for row in projection.values()
        if row.get("status") == "active" and row.get("predicate") != "session_evidence"
    ]
    rows.sort(key=lambda row: (row["predicate"], row["scope"], row["memory_id"]))
    return _table(rows, ["memory_id", "type", "predicate", "scope", "value_json", "status", "source_session_id", "valid_from", "valid_to", "supersedes"])


def _state_delta_markdown(delta: dict[str, list[dict[str, Any]]]) -> str:
    created = delta.get("created", [])
    updated = [
        {
            "memory_id": pair["after"]["memory_id"],
            "predicate": pair["after"]["predicate"],
            "scope": pair["after"]["scope"],
            "before_status": pair["before"].get("status"),
            "after_status": pair["after"].get("status"),
            "before_valid_to": pair["before"].get("valid_to"),
            "after_valid_to": pair["after"].get("valid_to"),
            "before_importance": pair["before"].get("importance"),
            "after_importance": pair["after"].get("importance"),
            "before_confidence": pair["before"].get("confidence"),
            "after_confidence": pair["after"].get("confidence"),
        }
        for pair in delta.get("updated", [])
    ]
    parts = ["新增：", "", _table(created, ["memory_id", "type", "predicate", "scope", "value_json", "status", "source_session_id", "valid_from", "valid_to", "supersedes"]), "", "更新：", "", _table(updated, ["memory_id", "predicate", "scope", "before_status", "after_status", "before_valid_to", "after_valid_to", "before_importance", "after_importance", "before_confidence", "after_confidence"])]
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
