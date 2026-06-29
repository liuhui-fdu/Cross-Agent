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
      "none": 11,
      "required": 10,
      "beneficial": 1
    },
    "decision_source_counts": {
      "llm": 17,
      "rule": 5
    },
    "skipped_rounds": 11,
    "retrieval_rounds": 11
  },
  "strict_online_audit": {
    "passed": true,
    "rule_fallback_count": 0,
    "embedding_error_count": 0,
    "token_fallback_item_count": 0,
    "empty_answer_count": 0,
    "completed_answer_api_calls": 22,
    "evidence_verifier_required_rounds": 11,
    "evidence_verifier_completed_rounds": 11,
    "evidence_verifier_error_count": 0
  },
  "architecture_audit": {
    "passed": false,
    "checks": [
      {
        "name": "round_1_not_false_history_reference",
        "passed": true
      },
      {
        "name": "name_from_round_1_is_stored",
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
        "name": "round_17_composite_query_has_all_current_slots",
        "passed": false
      },
      {
        "name": "round_19_keeps_both_same_session_preferences",
        "passed": true
      },
      {
        "name": "answers_do_not_expose_internal_ids",
        "passed": true
      }
    ]
  },
  "evidence": {
    "rounds_with_evidence": 7,
    "total_evidence_items": 19,
    "probe_counts": {
      "applied": 1,
      "accepted": 1
    }
  },
  "writes": {
    "operation_counts": {
      "create": 22,
      "supersede": 3,
      "reject": 4
    }
  },
  "final_memory": {
    "record_count": 25,
    "status_counts": {
      "active": 22,
      "superseded": 3
    },
    "type_counts": {
      "event": 11,
      "fact": 2,
      "preference": 10,
      "task": 2
    },
    "event_count": 29
  }
}
```

## 逐轮门控结果

| 轮次 | intent | 来源 | rule score | confidence | 跳过检索 | evidence | probe | verifier |
|---:|---|---|---:|---:|---|---:|---|---|
| 1 | none | llm | 0.7200 | 0.9500 | 是 | 0 | - | - |
| 2 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 3 | none | llm | 0.5800 | 0.9500 | 是 | 0 | - | - |
| 4 | none | llm | 0.7200 | 0.9000 | 是 | 0 | - | - |
| 5 | required | llm | 0.4500 | 0.9000 | 否 | 0 | - | 1.0000 (拒绝) |
| 6 | none | llm | 0.4500 | 0.9000 | 是 | 0 | - | - |
| 7 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 8 | none | llm | 0.7200 | 1.0000 | 是 | 0 | - | - |
| 9 | none | rule | 0.0500 | 0.9500 | 是 | 0 | - | - |
| 10 | none | rule | 0.0500 | 0.9500 | 是 | 0 | - | - |
| 11 | required | rule | 0.8600 | 0.8600 | 否 | 0 | - | 1.0000 (拒绝) |
| 12 | required | llm | 0.4500 | 0.9500 | 否 | 2 | - | 0.9000 (通过) |
| 13 | none | llm | 0.5200 | 0.9500 | 是 | 0 | - | - |
| 14 | none | llm | 0.4500 | 0.9500 | 是 | 0 | - | - |
| 15 | beneficial | llm | 0.4500 | 0.8000 | 否 | 2 | 1.0000/0.20 (通过) | 0.9500 (通过) |
| 16 | required | llm | 0.4500 | 0.8000 | 否 | 3 | - | 0.9500 (通过) |
| 17 | required | rule | 0.8800 | 0.8800 | 否 | 4 | - | 1.0000 (通过) |
| 18 | required | llm | 0.4500 | 0.9500 | 否 | 3 | - | 0.9500 (通过) |
| 19 | required | llm | 0.4500 | 0.9500 | 否 | 3 | - | 0.9500 (通过) |
| 20 | required | llm | 0.4500 | 0.9500 | 否 | 2 | - | 0.9500 (通过) |
| 21 | required | llm | 0.4500 | 0.9500 | 否 | 0 | - | 0.9900 (拒绝) |
| 22 | required | rule | 0.9800 | 0.9800 | 否 | 0 | - | 0.9500 (拒绝) |
