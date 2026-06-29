"""Pure scoring utilities for retrieval and evaluation."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Iterable, List

from cross_agent.models import MemoryRecord
from cross_agent.utils.text import cosine_from_counts, term_frequencies, tokenize


class CorpusScorer:
    def __init__(self, records: Iterable[MemoryRecord]):
        self._records = list(records)
        self._doc_freq = self._document_frequency(self._records)
        self._doc_count = max(1, len(self._records))

    def lexical_score(self, query_terms: List[str], record: MemoryRecord) -> float:
        text_counts = term_frequencies(record_text(record))
        if not query_terms or not text_counts:
            return 0.0
        score = 0.0
        doc_len = sum(text_counts.values())
        avg_len = 120.0
        k1 = 1.4
        b = 0.75
        for term in query_terms:
            tf = text_counts.get(term, 0)
            if not tf:
                continue
            df = self._doc_freq.get(term, 0)
            idf = math.log(1 + (self._doc_count - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * doc_len / avg_len)
            score += idf * (tf * (k1 + 1) / denom)
        return min(score / 8.0, 1.0)

    def token_cosine_score(self, query_terms: List[str], record: MemoryRecord) -> float:
        query_counts = Counter(query_terms)
        doc_counts = term_frequencies(record_text(record))
        return cosine_from_counts(query_counts, doc_counts)

    def _document_frequency(self, records: Iterable[MemoryRecord]) -> dict[str, int]:
        df: dict[str, int] = {}
        for record in records:
            for term in set(tokenize(record_text(record))):
                df[term] = df.get(term, 0) + 1
        return df


def record_text(record: MemoryRecord) -> str:
    value = record.value
    labels = {
        "current_residence": "住在哪 住在 地址 居住地 current residence",
        "name": "名字 姓名 name identity",
        "preferred_drink": "默认喝什么 饮品 喝 咖啡 茶 preferred drink",
        "preferred_programming_language": "接口示例 代码示例 语言 Python Java preferred programming language",
        "preferred_environment": "环境 办公地点 写文档 工作 聚会 朋友 安静 热闹 preferred environment",
        "preferred_device_ecosystem": "手机 设备 生态 苹果 device ecosystem",
        "disliked_food": "水果 食物 不爱吃 不喜欢 disliked food",
        "user_task": "任务 待办 需要 做 task todo",
        "session_evidence": "会话 证据 历史 session evidence",
    }
    parts = [
        record.subject,
        record.predicate,
        record.scope,
        labels.get(record.predicate, ""),
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        str(value.get("summary", "")),
        str(value.get("transcript", "")),
        " ".join(value.get("keywords", [])[:80]) if isinstance(value.get("keywords"), list) else "",
    ]
    return "\n".join(parts)
