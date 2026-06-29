# 中文自建种子评估汇总

- 评估范围：仅用户自建中文种子（22 轮）
- 外部数据集：未使用
- 模型：`deepseek-v4-pro:floor`
- Embedding：`gemini-embedding-2-preview`
- 严格在线模式：`True`
- 原始观测：`/Users/mac/workspace/Cross-Agent/eval2/chinese_seed_api_conversation.json`

## 汇总

```json
{
  "memory_intent": {
    "counts": {
      "none": 13,
      "beneficial": 2,
      "required": 7
    },
    "decision_source_counts": {
      "llm": 17,
      "rule": 5
    },
    "skipped_rounds": 13,
    "retrieval_rounds": 9
  },
  "strict_online_audit": {
    "passed": true,
    "rule_fallback_count": 0,
    "embedding_error_count": 0,
    "token_fallback_item_count": 0,
    "empty_answer_count": 0,
    "completed_answer_api_calls": 22,
    "evidence_verifier_required_rounds": 9,
    "evidence_verifier_completed_rounds": 9,
    "evidence_verifier_error_count": 0
  },
  "architecture_audit": {
    "passed": true,
    "checks": [
      {
        "name": "round_1_not_false_history_reference",
        "passed": true
      },
      {
        "name": "third_party_private_content_not_stored",
        "passed": true
      },
      {
        "name": "third_party_private_content_not_retrieved_later",
        "passed": true
      },
      {
        "name": "query_rounds_not_persisted",
        "passed": true
      },
      {
        "name": "one_canonical_completed_cross_agent_task",
        "passed": true
      },
      {
        "name": "no_expired_active_record",
        "passed": true
      },
      {
        "name": "unknown_japan_hotel_has_empty_evidence",
        "passed": true
      },
      {
        "name": "answers_do_not_expose_internal_ids",
        "passed": true
      }
    ]
  },
  "evidence": {
    "rounds_with_evidence": 2,
    "total_evidence_items": 3,
    "probe_counts": {
      "applied": 2,
      "accepted": 2
    }
  },
  "writes": {
    "operation_counts": {
      "create": 19,
      "reject": 9,
      "supersede": 3
    }
  },
  "final_memory": {
    "record_count": 22,
    "status_counts": {
      "active": 19,
      "superseded": 3
    },
    "type_counts": {
      "fact": 1,
      "event": 10,
      "preference": 9,
      "task": 2
    },
    "event_count": 31
  }
}
```

## 逐轮门控结果

| 轮次 | intent | 来源 | rule score | confidence | 跳过检索 | evidence | probe | verifier |
|---:|---|---|---:|---:|---|---:|---|---|
| 1 | none | llm | 0.7200 | 0.9500 | 是 | 0 | - | - |
| 2 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 3 | none | llm | 0.5800 | 0.9500 | 是 | 0 | - | - |
| 4 | none | llm | 0.7200 | 0.9500 | 是 | 0 | - | - |
| 5 | beneficial | llm | 0.4500 | 0.7000 | 否 | 0 | 0.6233/0.20 (通过) | 1.0000 (拒绝) |
| 6 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 7 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 8 | none | llm | 0.7200 | 0.9500 | 是 | 0 | - | - |
| 9 | none | rule | 0.0500 | 0.9500 | 是 | 0 | - | - |
| 10 | none | rule | 0.0500 | 0.9500 | 是 | 0 | - | - |
| 11 | required | rule | 0.8600 | 0.8600 | 否 | 0 | - | 0.9500 (拒绝) |
| 12 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 13 | none | llm | 0.5200 | 0.9500 | 是 | 0 | - | - |
| 14 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 15 | beneficial | llm | 0.4500 | 0.7000 | 否 | 0 | 1.0000/0.20 (通过) | 0.7000 (拒绝) |
| 16 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 17 | required | rule | 0.8800 | 0.8800 | 否 | 0 | - | 0.9000 (拒绝) |
| 18 | required | llm | 0.4500 | 0.9500 | 否 | 2 | - | 0.9500 (通过) |
| 19 | required | llm | 0.4500 | 0.9500 | 否 | 0 | - | 0.7000 (拒绝) |
| 20 | required | llm | 0.4500 | 0.9500 | 否 | 1 | - | 0.9500 (通过) |
| 21 | required | llm | 0.4500 | 0.9000 | 否 | 0 | - | 1.0000 (拒绝) |
| 22 | required | rule | 0.9800 | 0.9800 | 否 | 0 | - | 1.0000 (拒绝) |
