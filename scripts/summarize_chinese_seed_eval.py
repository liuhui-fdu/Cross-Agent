#!/usr/bin/env python3
"""Summarize one Chinese seed observation run without external datasets."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Chinese seed evaluation.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--json", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()

    source_path = Path(args.input)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    summary = build_summary(source, source_path)
    json_path = Path(args.json)
    markdown_path = Path(args.markdown)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    print(json_path)
    print(markdown_path)


def build_summary(source: dict[str, Any], source_path: Path) -> dict[str, Any]:
    intents: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    operations: Counter[str] = Counter()
    memory_types: Counter[str] = Counter()
    probe = Counter()
    evidence_rounds = 0
    retrieved_items = 0
    skipped_rounds = 0
    fallback_decisions = 0
    embedding_errors = 0
    token_fallback_items = 0
    empty_answers = 0
    verifier_required_rounds = 0
    verifier_completed_rounds = 0
    verifier_errors = 0

    round_results: list[dict[str, Any]] = []
    for item in source["conversation"]:
        memory = item["memory"]
        plan = memory["query_plan"]
        trace = memory["reader_trace"]
        intent = str(plan.get("memory_intent", "unknown"))
        source_name = str(plan.get("decision_source", "unknown"))
        intents[intent] += 1
        sources[source_name] += 1
        fallback_decisions += int(source_name == "rule_fallback")
        embedding_errors += int(
            bool((trace.get("embedding") or {}).get("error"))
        )
        embedding_trace = trace.get("embedding") or {}
        evidence_rows = memory.get("evidence", [])
        round_token_fallback = sum(
            "token_fallback" in str(evidence.get("reason", ""))
            for evidence in evidence_rows
        )
        if evidence_rows and not embedding_trace.get("available"):
            round_token_fallback = max(round_token_fallback, len(evidence_rows))
        token_fallback_items += round_token_fallback
        empty_answers += int(not str(item.get("assistant", "")).strip())
        if trace.get("skipped"):
            skipped_rounds += 1
        evidence_count = len(memory.get("evidence", []))
        evidence_rounds += int(evidence_count > 0)
        retrieved_items += evidence_count
        probe_trace = trace.get("evidence_probe") or {}
        verification_trace = trace.get("evidence_verification") or {}
        verifier_required = bool(
            plan.get("needs_memory") and trace.get("candidate_count", 0) > 0
        )
        verifier_required_rounds += int(verifier_required)
        verifier_completed_rounds += int(
            verifier_required and verification_trace.get("applied")
        )
        verifier_errors += int(
            str(verification_trace.get("reason", "")).startswith("verifier_error")
        )
        if probe_trace.get("applied"):
            probe["applied"] += 1
            probe["accepted" if probe_trace.get("accepted") else "rejected"] += 1
        for operation in (memory.get("candidate_resolution") or {}).get("operations", []):
            operations[str(operation.get("operation", "unknown"))] += 1
        round_results.append(
            {
                "round": item["round"],
                "query": item["user"],
                "intent": intent,
                "decision_source": source_name,
                "rule_score": plan.get("rule_score"),
                "intent_confidence": plan.get("intent_confidence"),
                "retrieval_skipped": bool(trace.get("skipped")),
                "evidence_count": evidence_count,
                "probe": probe_trace if probe_trace.get("applied") else None,
                "verification": verification_trace if verification_trace.get("applied") else None,
            }
        )

    for row in source.get("final_memory_state", []):
        memory_types[str(row.get("type", "unknown"))] += 1

    final_statuses = Counter(
        str(row.get("status", "unknown"))
        for row in source.get("final_memory_state", [])
    )
    architecture_checks = _architecture_checks(source)
    return {
        "metadata": {
            "evaluation_scope": "user_authored_chinese_seed_only",
            "source": str(source_path.resolve()),
            "conversation_id": source["metadata"].get("conversation_id"),
            "round_count": len(source["conversation"]),
            "model": source["metadata"].get("model"),
            "embedding_model": "gemini-embedding-2-preview",
            "external_dataset_used": False,
            "strict_online_mode": bool(
                source["metadata"].get("strict_online_mode")
            ),
        },
        "strict_online_audit": {
            "passed": bool(
                source["metadata"].get("strict_online_mode")
                and fallback_decisions == 0
                and embedding_errors == 0
                and token_fallback_items == 0
                and empty_answers == 0
                and verifier_errors == 0
                and verifier_completed_rounds == verifier_required_rounds
            ),
            "rule_fallback_count": fallback_decisions,
            "embedding_error_count": embedding_errors,
            "token_fallback_item_count": token_fallback_items,
            "empty_answer_count": empty_answers,
            "completed_answer_api_calls": len(source["conversation"]) - empty_answers,
            "evidence_verifier_required_rounds": verifier_required_rounds,
            "evidence_verifier_completed_rounds": verifier_completed_rounds,
            "evidence_verifier_error_count": verifier_errors,
        },
        "architecture_audit": {
            "passed": all(check["passed"] for check in architecture_checks),
            "checks": architecture_checks,
        },
        "memory_intent": {
            "counts": dict(intents),
            "decision_source_counts": dict(sources),
            "skipped_rounds": skipped_rounds,
            "retrieval_rounds": len(source["conversation"]) - skipped_rounds,
        },
        "evidence": {
            "rounds_with_evidence": evidence_rounds,
            "total_evidence_items": retrieved_items,
            "probe_counts": dict(probe),
        },
        "writes": {"operation_counts": dict(operations)},
        "final_memory": {
            "record_count": len(source.get("final_memory_state", [])),
            "status_counts": dict(final_statuses),
            "type_counts": dict(memory_types),
            "event_count": len(source.get("final_memory_events", [])),
        },
        "rounds": round_results,
    }


def _architecture_checks(source: dict[str, Any]) -> list[dict[str, Any]]:
    state = source.get("final_memory_state", [])
    conversation = source.get("conversation", [])
    by_round = {int(item["round"]): item for item in conversation}
    active_tasks = [
        row for row in state
        if row.get("status") == "active" and row.get("type") == "task"
        and row.get("scope") == "task:cross_agent_architecture_demo"
    ]
    active_names = [
        row for row in state
        if row.get("status") == "active" and row.get("predicate") == "name"
    ]
    round_17_evidence = by_round[17]["memory"].get("evidence", [])
    round_19_evidence = by_round[19]["memory"].get("evidence", [])
    query_sessions = {str(by_round[round_no]["session_id"]) for round_no in range(17, 23)}
    later_payloads = json.dumps(
        [item.get("api_call", {}).get("messages_sent_to_api", []) for item in conversation if int(item["round"]) > 13],
        ensure_ascii=False,
    )
    answers = "\n".join(str(item.get("assistant", "")) for item in conversation)
    reference_time = max(
        (_parse_time(item.get("date")) for item in conversation),
        default=datetime.min,
    )
    checks = [
        (
            "round_1_not_false_history_reference",
            "explicit_history_reference" not in str(by_round[1]["memory"]["query_plan"].get("reason", "")),
        ),
        (
            "name_from_round_1_is_stored",
            len(active_names) == 1 and "林澈" in str(active_names[0].get("value_json", "")),
        ),
        (
            "third_party_private_content_not_stored",
            not any("阿远" in str(row.get("value_json", "")) for row in state),
        ),
        (
            "third_party_private_content_not_retrieved_later",
            "阿远" not in later_payloads,
        ),
        (
            "query_rounds_not_persisted",
            not any(str(row.get("source_session_id")) in query_sessions for row in state),
        ),
        (
            "one_canonical_completed_cross_agent_task",
            len(active_tasks) == 1
            and active_tasks[0].get("predicate") == "user_task"
            and json.loads(active_tasks[0].get("value_json") or "{}").get("state") == "done",
        ),
        (
            "no_expired_active_record",
            not any(
                row.get("status") == "active"
                and row.get("valid_to")
                and _parse_time(row.get("valid_to")) <= reference_time
                for row in state
            ),
        ),
        (
            "unknown_japan_hotel_has_empty_evidence",
            len(by_round[22]["memory"].get("evidence", [])) == 0,
        ),
        (
            "round_17_composite_query_has_all_current_slots",
            {"name", "current_residence", "preferred_programming_language", "preferred_drink", "user_task"}
            <= {str(row.get("predicate")) for row in round_17_evidence},
        ),
        (
            "round_19_keeps_both_same_session_preferences",
            {"work_or_writing", "social_gathering"}
            <= {str(row.get("scope")) for row in round_19_evidence},
        ),
        (
            "answers_do_not_expose_internal_ids",
            "memory_id" not in answers.lower() and "mem_" not in answers.lower()
            and "source_session_id" not in answers.lower(),
        ),
    ]
    return [
        {"name": name, "passed": bool(passed)}
        for name, passed in checks
    ]


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return datetime.min
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def render_markdown(summary: dict[str, Any]) -> str:
    meta = summary["metadata"]
    lines = [
        "# 中文自建种子评估汇总",
        "",
        f"- 评估范围：仅用户自建中文种子（{meta['round_count']} 轮）",
        "- 外部数据集：未使用",
        f"- 模型：`{meta['model']}`",
        f"- Embedding：`{meta['embedding_model']}`",
        f"- 严格在线模式：`{meta['strict_online_mode']}`",
        f"- 原始观测：`{meta['source']}`",
        "",
        "## 汇总",
        "",
        "```json",
        json.dumps(
            {
                "memory_intent": summary["memory_intent"],
                "strict_online_audit": summary["strict_online_audit"],
                "architecture_audit": summary["architecture_audit"],
                "evidence": summary["evidence"],
                "writes": summary["writes"],
                "final_memory": summary["final_memory"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 逐轮门控结果",
        "",
        "| 轮次 | intent | 来源 | rule score | confidence | 跳过检索 | evidence | probe | verifier |",
        "|---:|---|---|---:|---:|---|---:|---|---|",
    ]
    for row in summary["rounds"]:
        probe = row["probe"]
        probe_text = "-"
        if probe:
            probe_text = f"{probe.get('top_score', 0):.4f}/{probe.get('threshold', 0):.2f} ({'通过' if probe.get('accepted') else '拒绝'})"
        verification = row["verification"]
        verifier_text = "-"
        if verification:
            verifier_text = f"{float(verification.get('confidence') or 0):.4f} ({'通过' if verification.get('accepted') else '拒绝'})"
        lines.append(
            f"| {row['round']} | {row['intent']} | {row['decision_source']} | "
            f"{float(row['rule_score'] or 0):.4f} | {float(row['intent_confidence'] or 0):.4f} | "
            f"{'是' if row['retrieval_skipped'] else '否'} | {row['evidence_count']} | {probe_text} | {verifier_text} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
