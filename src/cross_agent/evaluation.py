"""ActMem evaluation utilities."""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

from cross_agent.config import Settings
from cross_agent.models import SearchRequest, Session, Turn
from cross_agent.pipeline import CrossAgentApp
from cross_agent.utils.jsonio import read_json, write_json
from cross_agent.utils.text import tokenize


def run_actmem_eval(settings: Settings, limit: int | None = None) -> Dict[str, Any]:
    output_dir = Path(settings.evaluation.output_dir)
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    app = CrossAgentApp(settings)
    app.initialize(reset=True)

    dataset = read_json(settings.evaluation.dataset_path)
    selected = dataset[: (limit or settings.evaluation.limit)]
    per_item: List[Dict[str, Any]] = []
    aggregate = {
        "items": 0,
        "recall_at_k_sum": 0.0,
        "mrr_sum": 0.0,
        "answer_token_f1_sum": 0.0,
    }

    for item in selected:
        counts_before = app.debug_counts()
        ingest_stats, ingest_trace = _ingest_item(app, settings, item)
        counts_after_ingest = app.debug_counts()
        request = SearchRequest(
            tenant_id=settings.app.tenant_id,
            user_id=settings.app.user_id,
            query=item["question"],
            occurred_at=item.get("question_date"),
            top_k=settings.evaluation.top_k,
            allow_sensitive=False,
        )
        evidence = app.search(request)
        counts_after_search = app.debug_counts()
        answer = app.response_guard.verify(
            app.answer_generator.generate(item["question"], evidence), evidence
        )
        retrieved_sessions = [ev.memory.source_session_id for ev in evidence.items]
        gold_sessions = item.get("answer_session_ids", [])
        metrics = _retrieval_metrics(retrieved_sessions, gold_sessions, settings.evaluation.top_k)
        token_f1 = _token_f1(answer.answer, item.get("answer", ""))
        aggregate["items"] += 1
        aggregate["recall_at_k_sum"] += metrics["recall_at_k"]
        aggregate["mrr_sum"] += metrics["mrr"]
        aggregate["answer_token_f1_sum"] += token_f1
        per_item.append(
            {
                "question_id": item["question_id"],
                "question": item["question"],
                "gold_answer": item.get("answer", ""),
                "prediction": answer.answer,
                "gold_session_ids": gold_sessions,
                "retrieved_session_ids": retrieved_sessions,
                "metrics": {**metrics, "answer_token_f1": token_f1},
                "ingest_stats": ingest_stats,
                "state_transition": {
                    "before_ingest": counts_before,
                    "after_ingest": counts_after_ingest,
                    "after_search": counts_after_search,
                },
                "ingest_trace": ingest_trace,
                "evidence_items": [
                    {
                        "rank": rank,
                        "memory_id": ev.memory.memory_id,
                        "source_session_id": ev.memory.source_session_id,
                        "score": ev.score,
                        "lexical_score": ev.lexical_score,
                        "semantic_score": ev.semantic_score,
                        "token_cosine_score": ev.token_cosine_score,
                        "temporal_score": ev.temporal_score,
                        "sensitivity": ev.memory.sensitivity,
                        "snippet": ev.snippet,
                    }
                    for rank, ev in enumerate(evidence.items, start=1)
                ],
                "answer_trace": answer.trace,
                "trace": evidence.trace,
            }
        )

    n = max(1, aggregate["items"])
    summary = {
        "dataset_path": settings.evaluation.dataset_path,
        "limit": len(selected),
        "top_k": settings.evaluation.top_k,
        "answer_generator": app.answer_generator.__class__.__name__,
        "recall_at_k": aggregate["recall_at_k_sum"] / n,
        "mrr": aggregate["mrr_sum"] / n,
        "answer_token_f1": aggregate["answer_token_f1_sum"] / n,
    }
    result = {"summary": summary, "items": per_item}
    write_json(output_dir / "actmem_eval_results.json", result)
    write_json(output_dir / "actmem_eval_summary.json", summary)
    return result


def _ingest_item(app: CrossAgentApp, settings: Settings, item: Dict[str, Any]) -> tuple[Dict[str, int], Dict[str, Any]]:
    stats = {
        "extracted": 0,
        "selected": 0,
        "filtered": 0,
        "dropped": 0,
        "created": 0,
        "reinforced": 0,
        "historical": 0,
        "tentative": 0,
        "promoted": 0,
        "archived": 0,
        "rejected": 0,
    }
    gold_set = set(item.get("answer_session_ids", []))
    trace: Dict[str, Any] = {
        "haystack_session_count": len(item.get("haystack_sessions", [])),
        "gold_session_ids": item.get("answer_session_ids", []),
        "gold_session_write_states": [],
        "total_session_write_states_sample": [],
    }
    sessions = item.get("haystack_sessions", [])
    session_ids = item.get("haystack_session_ids", [])
    dates = item.get("haystack_dates", [])
    for index, turns in enumerate(sessions):
        session_id = session_ids[index] if index < len(session_ids) else f"session_{index}"
        occurred_at = dates[index] if index < len(dates) else None
        session = Session(
            session_id=session_id,
            tenant_id=settings.app.tenant_id,
            user_id=settings.app.user_id,
            occurred_at=occurred_at,
            turns=[
                Turn(
                    turn_id=f"{session_id}_turn_{turn_index}",
                    role=turn.get("role", "unknown"),
                    content=turn.get("content", ""),
                    occurred_at=occurred_at,
                )
                for turn_index, turn in enumerate(turns)
            ],
            metadata={
                "question_id": item.get("question_id"),
                "force_session_evidence": True,
            },
        )
        delta = app.ingest_session(session)
        for key, value in delta.items():
            stats[key] += value
        session_state = {
            "session_id": session_id,
            "occurred_at": occurred_at,
            **delta,
            "candidate_resolution": app.last_ingest_trace,
        }
        if session_id in gold_set:
            trace["gold_session_write_states"].append(session_state)
        if len(trace["total_session_write_states_sample"]) < 8:
            trace["total_session_write_states_sample"].append(session_state)
    return stats, trace


def _retrieval_metrics(retrieved: List[str | None], gold: List[str], k: int) -> Dict[str, float]:
    top = [r for r in retrieved[:k] if r]
    gold_set = set(gold)
    hits = [sid for sid in top if sid in gold_set]
    recall = len(set(hits)) / len(gold_set) if gold_set else 1.0
    reciprocal = 0.0
    for rank, sid in enumerate(top, start=1):
        if sid in gold_set:
            reciprocal = 1.0 / rank
            break
    precision = len(hits) / len(top) if top else 0.0
    return {
        "recall_at_k": recall,
        "precision_at_k": precision,
        "mrr": reciprocal,
        "hit_count": float(len(set(hits))),
    }


def _token_f1(prediction: str, reference: str) -> float:
    pred_tokens = tokenize(_strip_ids(prediction))
    ref_tokens = tokenize(reference)
    if not pred_tokens and not ref_tokens:
        return 1.0
    if not pred_tokens or not ref_tokens:
        return 0.0
    pred_counts: Dict[str, int] = {}
    for token in pred_tokens:
        pred_counts[token] = pred_counts.get(token, 0) + 1
    overlap = 0
    for token in ref_tokens:
        current = pred_counts.get(token, 0)
        if current:
            overlap += 1
            pred_counts[token] = current - 1
    precision = overlap / len(pred_tokens)
    recall = overlap / len(ref_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _strip_ids(text: str) -> str:
    return re.sub(r"\[[^\]]+\]", " ", text)
